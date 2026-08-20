"""
In-memory pub/sub for automatic entry-request approvals.

An automatic entry request (request_type="automatic") runs through the
same guards as a manual one, but instead of calling place_bracket_order
we park the fully-priced order here and broadcast it over SSE. The UI
subscribes once (globally, from the root layout) and pops a modal for
each pending approval. The user's Accept/Decline click travels back
through POST /entry-request/approve, which either places the bracket
order or discards the pending row.

Shape mirrors OpenRiskHub deliberately -- same subscribe / unsubscribe
/ broadcast pattern -- but there is no debounce here: approvals are
low-frequency and the user needs to see every single one.

Event shapes on the SSE stream:
  data: {"type": "snapshot", "pending": [PendingApproval, ...]}
  data: {"type": "add",       "pending": PendingApproval}
  data: {"type": "remove",    "approval_id": "..."}
  data: {"type": "ping"}                                       (every 15s)
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PendingApproval:
    """
    Plain container -- the API layer converts to the Pydantic schema.

    Carries symbol / contract_type / entry_price / stop_price /
    position_size. The size is computed at park time via
    ``calculate_position_size`` (not build_order) so the popup can
    show the user what will be sent, and place_approved_entry replays
    that same frozen size at Accept.
    """

    __slots__ = (
        "approval_id",
        "symbol",
        "contract_type",
        "entry_price",
        "stop_price",
        "position_size",
        "created_at",
        "source_id",
    )

    def __init__(
        self,
        approval_id: str,
        symbol: str,
        contract_type: str,
        entry_price: float,
        stop_price: float,
        position_size: int,
        created_at: str,
        source_id: Optional[str] = None,
    ) -> None:
        self.approval_id = approval_id
        self.symbol = symbol
        self.contract_type = contract_type
        self.entry_price = entry_price
        self.stop_price = stop_price
        self.position_size = position_size
        self.created_at = created_at
        # Optional stable external identifier (e.g. "ALPACA:abc" or
        # "DB:123") so a repeated add_pending for the same upstream row
        # returns the existing approval instead of creating a duplicate.
        # Set to None for one-off scanner-produced approvals.
        self.source_id = source_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "symbol": self.symbol,
            "contract_type": self.contract_type,
            "entry_price": self.entry_price,
            "stop_price": self.stop_price,
            "position_size": self.position_size,
            "created_at": self.created_at,
            "source_id": self.source_id,
        }


class PendingApprovalsHub:
    """
    Single-instance broker for pending automatic entries.

    Storage is a process-local dict keyed by approval_id; subscribers get
    an asyncio.Queue of typed events. Nothing here is persisted -- if the
    backend restarts, pending approvals evaporate (matching the "the user
    just wasn't around to accept" case). Automatic requests are expected
    to be replayed by whatever upstream produces them.
    """

    def __init__(self) -> None:
        self._pending: Dict[str, PendingApproval] = {}
        # source_id -> approval_id for rows registered from a stable
        # upstream (Alpaca / DB pending_orders). Used to dedupe on
        # repeated table fetches so we don't spam a new approval per
        # refresh.
        self._by_source: Dict[str, str] = {}
        self._subscribers: List[asyncio.Queue] = []
        # Protects mutations to _pending. Broadcasts are best-effort and
        # tolerate reorderings, but we don't want two accept clicks to both
        # pop the same row.
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # SSE subscription plumbing
    # ------------------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._subscribers.append(q)
        logger.info(
            "PendingApprovalsHub SSE client connected (n=%d)",
            len(self._subscribers),
        )
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass
        logger.info(
            "PendingApprovalsHub SSE client disconnected (n=%d)",
            len(self._subscribers),
        )

    def snapshot_now(self) -> Dict[str, Any]:
        """
        Initial payload for a newly-connected SSE client.

        Source-tagged rows (Pending Orders table entries) are excluded
        -- those aren't automatic-entry approvals; they're just parked
        for the Send button to reference via /approve. They must not
        surface in the AutomaticEntryApprovalDialog popup.
        """
        return {
            "type": "snapshot",
            "pending": [
                p.to_dict() for p in self._pending.values()
                if p.source_id is None
            ],
        }

    # ------------------------------------------------------------------
    # Producer side -- called from process_entry_request when
    # request_type="automatic" and all guards pass.
    # ------------------------------------------------------------------
    async def add_pending(
        self,
        *,
        symbol: str,
        contract_type: str,
        entry_price: float,
        stop_price: float,
        position_size: int,
        source_id: Optional[str] = None,
    ) -> PendingApproval:
        """
        Park a row awaiting user approval.

        When ``source_id`` is given and an existing row already carries
        that same source_id, the existing row is refreshed (entry_price
        and position_size updated from the incoming values) and
        returned instead of adding a duplicate. This lets a periodic
        producer (e.g. process_open_orders on every table refresh) call
        add_pending idempotently.
        """
        async with self._lock:
            if source_id is not None:
                existing_id = self._by_source.get(source_id)
                if existing_id is not None:
                    existing = self._pending.get(existing_id)
                    if existing is not None:
                        # Refresh the mutable fields; identity stays.
                        existing.entry_price = entry_price
                        existing.position_size = position_size
                        existing.stop_price = stop_price
                        return existing

            approval_id = str(uuid.uuid4())
            created_at = datetime.now(timezone.utc).isoformat()
            row = PendingApproval(
                approval_id=approval_id,
                symbol=symbol,
                contract_type=contract_type,
                entry_price=entry_price,
                stop_price=stop_price,
                position_size=position_size,
                created_at=created_at,
                source_id=source_id,
            )
            self._pending[approval_id] = row
            if source_id is not None:
                self._by_source[source_id] = approval_id

        logger.info(
            "PendingApprovalsHub: parked entry %s for %s "
            "(entry=%s stop=%s qty=%s source=%s)",
            approval_id,
            symbol,
            entry_price,
            stop_price,
            position_size,
            source_id,
        )
        # Source-tagged rows (Pending Orders table) are silent -- they
        # would otherwise trigger the AutomaticEntryApprovalDialog popup
        # for every table refresh. Only true automatic scanner
        # approvals (source_id is None) are broadcast.
        if source_id is None:
            self._broadcast({"type": "add", "pending": row.to_dict()})
        return row

    async def sync_source_ids(self, source_ids: set[str]) -> None:
        """
        Prune source-keyed rows whose source_id is no longer in the
        given set. Called by process_open_orders after building the
        current pending_orders view so hub state doesn't drift when
        upstream rows disappear (Alpaca cancels, DB rows deactivated).
        Rows without a source_id are untouched (they're one-off
        scanner approvals).
        """
        async with self._lock:
            stale_source_ids = [s for s in self._by_source if s not in source_ids]
            for s in stale_source_ids:
                approval_id = self._by_source.pop(s)
                self._pending.pop(approval_id, None)
        # No broadcast: source-tagged rows are silent on this bus
        # (see add_pending / pop_pending). The AutomaticEntryApprovalDialog
        # never saw them added and does not need remove events for them.

    # ------------------------------------------------------------------
    # Consumer side -- called from POST /entry-request/approve.
    # ------------------------------------------------------------------
    async def pop_pending(self, approval_id: str) -> Optional[PendingApproval]:
        """Atomically remove and return the pending row (or None if gone)."""
        async with self._lock:
            row = self._pending.pop(approval_id, None)
            if row is not None and row.source_id is not None:
                self._by_source.pop(row.source_id, None)
        # Same silent policy as add_pending: source-tagged rows never
        # went out over SSE, so their removals shouldn't either.
        if row is not None and row.source_id is None:
            self._broadcast({"type": "remove", "approval_id": approval_id})
        return row

    def get_pending(self, approval_id: str) -> Optional[PendingApproval]:
        return self._pending.get(approval_id)

    # ------------------------------------------------------------------
    # Internal fanout -- same slow-consumer policy as OpenRiskHub.
    # ------------------------------------------------------------------
    def _broadcast(self, payload: Dict[str, Any]) -> None:
        dead: List[asyncio.Queue] = []
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except Exception:
                    logger.warning(
                        "PendingApprovalsHub: dropping slow SSE consumer"
                    )
                    dead.append(q)
        for q in dead:
            self.unsubscribe(q)
