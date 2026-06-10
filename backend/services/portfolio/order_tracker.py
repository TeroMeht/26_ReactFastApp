"""
Live order tracker.

Holds the current state of every IB order the backend knows about and
fans changes out to SSE subscribers. Updated by ib_async events bound
in main.py's lifespan and by IbClient when it places orders.

State is keyed by IB's permId where available, and orderId for not-yet-
acknowledged orders (the first orderStatusEvent typically carries the
permId, at which point the row is re-keyed).
"""

import asyncio
import logging
import time as _time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from ib_async import IB, Trade

from db.order_log import insert_order_log_event

logger = logging.getLogger(__name__)


TERMINAL_STATUSES: Set[str] = {
    "Filled",
    "Cancelled",
    "ApiCancelled",
    "Inactive",
}

ACTIVE_STATUSES: Set[str] = {
    "PendingSubmit",
    "PendingCancel",
    "PreSubmitted",
    "Submitted",
    "ApiPending",
}


def _trade_snapshot(trade: Trade) -> Dict[str, Any]:
    """Flatten a Trade into the JSON-serializable shape the UI consumes."""
    o = trade.order
    s = trade.orderStatus
    c = trade.contract

    return {
        "perm_id": getattr(o, "permId", 0) or 0,
        "order_id": getattr(o, "orderId", 0) or 0,
        "symbol": getattr(c, "symbol", None) if c else None,
        "sec_type": getattr(c, "secType", None) if c else None,
        "action": getattr(o, "action", None),
        "order_type": getattr(o, "orderType", None),
        "total_qty": float(getattr(o, "totalQuantity", 0) or 0),
        "lmt_price": getattr(o, "lmtPrice", None),
        "aux_price": getattr(o, "auxPrice", None),
        # Carry orderRef through so the custom-exits fill bridge can
        # identify which fills are CUSTOM_EXIT-tagged without a DB lookup.
        "order_ref": getattr(o, "orderRef", None),
        "parent_id": getattr(o, "parentId", 0) or 0,
        "status": getattr(s, "status", None) if s else None,
        "filled": float(getattr(s, "filled", 0) or 0) if s else 0.0,
        "remaining": float(getattr(s, "remaining", 0) or 0) if s else 0.0,
        "avg_fill_price": float(getattr(s, "avgFillPrice", 0) or 0) if s else 0.0,
        "last_error": None,
        "last_error_code": None,
        "submitted_at": _time.time(),
    }


class OrderTracker:
    """
    In-memory store + SSE fanout.

    Public surface:
      - register_trade(trade)          : called after every placeOrder
      - snapshot()                     : full list for GET /order-status
      - subscribe()/unsubscribe(q)     : SSE plumbing
      - is_terminal(perm_id)/state(p)  : used by awaitable cancel
      - bind_events(ib)                : wire ib_async events once at startup
      - seed(ib)                       : pull existing open orders at boot
    """

    def __init__(self, max_log: int = 2000) -> None:
        # Two indices: permId is authoritative, orderId is a fallback for
        # orders that haven't been acknowledged yet.
        self._by_perm: Dict[int, Dict[str, Any]] = {}
        self._by_order: Dict[int, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._subscribers: List[asyncio.Queue] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Append-only event log. Every status transition produces one entry
        # so the /order-log page can show a chronological audit trail.
        # Bounded to avoid unbounded memory in long-running sessions.
        # NOTE: the persistent copy of this log lives in the `order_log`
        # postgres table — _event_log is just the current-session cache.
        self._event_log: List[Dict[str, Any]] = []
        self._max_log = max_log
        # Dedup: same (perm_id, status) within one tick shouldn't double-log.
        self._last_logged_status: Dict[int, str] = {}

        # DB pool used to persist events. Set by main.py at startup via
        # set_db_pool(); when None, events are kept in memory only.
        self._db_pool = None

        # Subscribers notified once when an order transitions into the
        # 'Filled' terminal status. Used by the custom-exits flow to run
        # post-fill STP adjustment. Handlers receive the order snapshot
        # and may be async; sync handlers are also supported.
        self._fill_handlers: List[
            Callable[[Dict[str, Any]], Optional[Awaitable[None]]]
        ] = []
        # Track which (perm_id|order_id) we've already fired 'Filled' for
        # so duplicate IB callbacks don't double-trigger the handler.
        self._fill_fired: Set[int] = set()

    # ------------------------------------------------------------------
    # Persistence wiring
    # ------------------------------------------------------------------
    def set_db_pool(self, pool) -> None:
        """Attach an asyncpg pool so events are persisted to order_log."""
        self._db_pool = pool

    def _persist_event(self, entry: Dict[str, Any]) -> None:
        """
        Fire-and-forget DB write for one event. Safe to call from ib_async
        sync callbacks because we schedule via the bound event loop. If no
        pool is configured (e.g. tests) the call is a no-op.
        """
        pool = self._db_pool
        loop = self._loop
        if pool is None or loop is None:
            return

        async def _write():
            try:
                async with pool.acquire() as conn:
                    await insert_order_log_event(conn, entry)
            except Exception:
                logger.exception("Failed to persist order_log event")

        try:
            if loop.is_running():
                # Same loop (ib_async dispatches on the FastAPI loop).
                asyncio.run_coroutine_threadsafe(_write(), loop)
            else:
                loop.create_task(_write())
        except Exception:
            logger.exception("Failed to schedule order_log persist")

    # ------------------------------------------------------------------
    # Subscription plumbing for SSE
    # ------------------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def _broadcast(self, payload: Dict[str, Any]) -> None:
        dead: List[asyncio.Queue] = []
        for q in self._subscribers:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                logger.warning("SSE subscriber queue full, dropping client")
                dead.append(q)
        for q in dead:
            self.unsubscribe(q)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def snapshot(self) -> List[Dict[str, Any]]:
        # Merge: perm-keyed entries are authoritative; include order-keyed
        # rows whose permId is still unknown (==0).
        rows = list(self._by_perm.values())
        for state in self._by_order.values():
            if not state.get("perm_id"):
                rows.append(state)
        # Newest first by submitted_at
        rows.sort(key=lambda r: r.get("submitted_at", 0), reverse=True)
        return rows

    def state(self, perm_id: int) -> Optional[Dict[str, Any]]:
        return self._by_perm.get(perm_id)

    def is_terminal(self, perm_id: int) -> bool:
        st = self._by_perm.get(perm_id)
        if not st:
            return False
        return (st.get("status") or "") in TERMINAL_STATUSES

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def register_trade(self, trade: Trade) -> None:
        """Idempotent upsert from a Trade object."""
        snap = _trade_snapshot(trade)
        perm = snap["perm_id"]
        oid = snap["order_id"]

        # Carry over any previously recorded error if we already had a row.
        prior = self._by_perm.get(perm) if perm else self._by_order.get(oid)
        if prior:
            snap["last_error"] = prior.get("last_error")
            snap["last_error_code"] = prior.get("last_error_code")
            snap["submitted_at"] = prior.get("submitted_at", snap["submitted_at"])

        if perm:
            self._by_perm[perm] = snap
            # If we previously tracked by orderId, drop that fallback now.
            if oid and oid in self._by_order:
                self._by_order.pop(oid, None)
        elif oid:
            self._by_order[oid] = snap

        # Append to event log when status changes (or first sighting). We
        # dedup on the latest (perm_id, status) pair so repeated callbacks
        # for the same state don't spam the log.
        self._log_event(snap)

        # Notify fill subscribers (custom-exits flow) — once per order.
        self._maybe_fire_fill(snap)

        self._broadcast({"type": "update", "order": snap})

    # ------------------------------------------------------------------
    # Fill-event subscribers
    # ------------------------------------------------------------------
    def add_fill_handler(
        self,
        handler: Callable[[Dict[str, Any]], Optional[Awaitable[None]]],
    ) -> None:
        """
        Register a callback fired exactly once when any order transitions
        into 'Filled'. Handler receives the order snapshot. Async handlers
        are scheduled on the bound event loop; sync handlers run inline.
        """
        self._fill_handlers.append(handler)

    def _maybe_fire_fill(self, snap: Dict[str, Any]) -> None:
        if (snap.get("status") or "") != "Filled":
            return
        key = snap.get("perm_id") or snap.get("order_id") or 0
        if not key or key in self._fill_fired:
            return
        self._fill_fired.add(key)

        for h in self._fill_handlers:
            try:
                result = h(snap)
                if asyncio.iscoroutine(result):
                    loop = self._loop
                    if loop is not None:
                        try:
                            if loop.is_running():
                                asyncio.run_coroutine_threadsafe(result, loop)
                            else:
                                loop.create_task(result)
                        except Exception:
                            logger.exception("Failed to schedule fill handler")
                    else:
                        # No loop bound yet — drop, this only happens before
                        # bind_events(), which is during startup.
                        logger.warning(
                            "Fill handler returned coroutine before "
                            "event loop bound; dropping."
                        )
            except Exception:
                logger.exception("Fill handler raised")

    def _log_event(self, snap: Dict[str, Any]) -> None:
        """Append a chronological event if status changed since last log."""
        status = snap.get("status")
        if not status:
            return

        key = snap.get("perm_id") or -snap.get("order_id", 0)
        if self._last_logged_status.get(key) == status:
            return
        self._last_logged_status[key] = status

        entry = {
            "ts": _time.time(),
            "perm_id": snap.get("perm_id", 0),
            "order_id": snap.get("order_id", 0),
            "symbol": snap.get("symbol"),
            "action": snap.get("action"),
            "order_type": snap.get("order_type"),
            "total_qty": snap.get("total_qty", 0),
            "lmt_price": snap.get("lmt_price"),
            "aux_price": snap.get("aux_price"),
            "status": status,
            "filled": snap.get("filled", 0),
            "remaining": snap.get("remaining", 0),
            "avg_fill_price": snap.get("avg_fill_price", 0),
            "last_error": snap.get("last_error"),
            "last_error_code": snap.get("last_error_code"),
        }
        self._event_log.append(entry)
        # Trim oldest entries if we exceed the cap.
        if len(self._event_log) > self._max_log:
            overflow = len(self._event_log) - self._max_log
            del self._event_log[:overflow]
        # Mirror to the persistent order_log table.
        self._persist_event(entry)

    def event_log(self) -> List[Dict[str, Any]]:
        """Return a copy of the event log, newest first."""
        return list(reversed(self._event_log))

    def _on_status(self, trade: Trade) -> None:
        try:
            self.register_trade(trade)
        except Exception:
            logger.exception("orderStatusEvent handler failed")

    def _on_open(self, trade: Trade) -> None:
        try:
            self.register_trade(trade)
        except Exception:
            logger.exception("openOrderEvent handler failed")

    def _on_exec(self, trade: Trade, fill) -> None:
        try:
            # Fill drives a status update; re-snapshot from the Trade.
            self.register_trade(trade)
        except Exception:
            logger.exception("execDetailsEvent handler failed")

    def _on_error(self, reqId: int, errorCode: int, errorString: str, contract) -> None:
        """
        ib_async surfaces order rejections via errorEvent (codes ~200-210,
        320, 399, ...). Attach the message to whichever row owns this id.
        Order callbacks use the same id space as reqId for placeOrder.
        """
        try:
            # reqId here corresponds to orderId for order-related errors.
            state = self._by_order.get(reqId)
            if state is None:
                # Try to resolve via permId-keyed rows whose orderId matches.
                for s in self._by_perm.values():
                    if s.get("order_id") == reqId:
                        state = s
                        break

            if state is None:
                return  # Not an order-related error.

            state["last_error"] = errorString
            state["last_error_code"] = errorCode

            # Capture error as its own log entry — useful when an order is
            # rejected (code ~200-210) without a follow-up status change.
            err_entry = {
                "ts": _time.time(),
                "perm_id": state.get("perm_id", 0),
                "order_id": state.get("order_id", 0),
                "symbol": state.get("symbol"),
                "action": state.get("action"),
                "order_type": state.get("order_type"),
                "total_qty": state.get("total_qty", 0),
                "lmt_price": state.get("lmt_price"),
                "aux_price": state.get("aux_price"),
                "status": state.get("status"),
                "filled": state.get("filled", 0),
                "remaining": state.get("remaining", 0),
                "avg_fill_price": state.get("avg_fill_price", 0),
                "last_error": errorString,
                "last_error_code": errorCode,
            }
            self._event_log.append(err_entry)
            if len(self._event_log) > self._max_log:
                overflow = len(self._event_log) - self._max_log
                del self._event_log[:overflow]
            self._persist_event(err_entry)

            self._broadcast({"type": "update", "order": state})
        except Exception:
            logger.exception("errorEvent handler failed")

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------
    def bind_events(self, ib: IB) -> None:
        """Wire ib_async events. Safe to call once at startup."""
        self._loop = asyncio.get_event_loop()
        ib.orderStatusEvent += self._on_status
        ib.openOrderEvent += self._on_open
        ib.execDetailsEvent += self._on_exec
        ib.errorEvent += self._on_error
        logger.info("OrderTracker bound to ib_async events")

    async def seed(self, ib: IB) -> None:
        """Pull whatever IB already has open into the tracker."""
        try:
            trades = await ib.reqAllOpenOrdersAsync()
            await asyncio.sleep(0.3)
            for t in trades or []:
                self.register_trade(t)
            logger.info("OrderTracker seeded with %d open orders", len(trades or []))
        except Exception:
            logger.exception("OrderTracker seed failed (non-fatal)")
