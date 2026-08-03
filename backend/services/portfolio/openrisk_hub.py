"""
Event-driven broadcaster for the open-risk table.

Instead of the frontend polling /open-risk-table, subscribers connect once
via SSE and the hub pushes a fresh snapshot whenever anything that could
change the table happens:

  - IB fill (execDetailsEvent) → position size / avg cost changed
  - IB order update (openOrderEvent) → STP resize / price move / new bracket
  - IB account value tick (accountValueEvent) → NetLiq shifts the allocation column
  - User arms or disarms an exit_request (called from the exits router)

Debounced ~100ms so a bracket placement (which fires openOrderEvent for
parent and child, plus execDetailsEvent when the parent fills) collapses
into one rebuild rather than three.

Wired at startup via core.startup.openrisk_hub_setup.wire_openrisk_hub.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, List, Optional

from ib_async import IB
from asyncpg import Pool

from services.portfolio.flows.open_risk import process_openrisktable
from services.portfolio.ib_client import IbClient

logger = logging.getLogger(__name__)


# Coalesce bursts of triggers within this window into one rebuild.
DEBOUNCE_SECONDS = 0.1


class OpenRiskHub:
    """
    Single-instance broadcaster. Owns:
      - `_subscribers`: one asyncio.Queue per connected SSE client
      - `_notify_pending`: debounce flag so bursts of events fire ONE rebuild
      - a rebuild coroutine that reads positions/orders/summary via IbClient
        and pushes the resulting snapshot to every subscriber
    """

    def __init__(self, ib: IB, db_pool: Pool) -> None:
        self.ib = ib
        self.db_pool = db_pool
        self._subscribers: List[asyncio.Queue] = []
        self._notify_pending = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    # SSE subscription plumbing
    # ------------------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._subscribers.append(q)
        logger.info("OpenRiskHub SSE client connected (n=%d)", len(self._subscribers))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass
        logger.info(
            "OpenRiskHub SSE client disconnected (n=%d)", len(self._subscribers)
        )

    def subscriber_count(self) -> int:
        return len(self._subscribers)

    # ------------------------------------------------------------------
    # Trigger — safe to call from sync ib_async callbacks or async code
    # ------------------------------------------------------------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the FastAPI loop so sync IB callbacks can schedule the rebuild."""
        self._loop = loop

    def notify(self, *_args: Any, **_kwargs: Any) -> None:
        """
        Cheap sync trigger. Debounces multiple calls within DEBOUNCE_SECONDS
        into one broadcast. Accepts *args so it can be attached directly to
        ib_async events without adapter lambdas.
        """
        # Skip if no one's listening -- rebuild would be wasted work.
        if not self._subscribers:
            return
        if self._notify_pending:
            return
        loop = self._loop
        if loop is None:
            return
        self._notify_pending = True
        try:
            loop.call_later(
                DEBOUNCE_SECONDS,
                lambda: asyncio.ensure_future(self._rebuild_and_broadcast(), loop=loop),
            )
        except Exception:
            self._notify_pending = False
            logger.exception("OpenRiskHub notify scheduling failed")

    # ------------------------------------------------------------------
    # Rebuild + fanout
    # ------------------------------------------------------------------
    async def _rebuild_and_broadcast(self) -> None:
        self._notify_pending = False
        if not self._subscribers:
            return
        try:
            async with self.db_pool.acquire() as conn:
                rows = await process_openrisktable(IbClient(self.ib), conn)
        except Exception:
            logger.exception("OpenRiskHub rebuild failed")
            return

        payload = {
            "type": "snapshot",
            "rows": [r.model_dump() for r in rows],
        }
        self._broadcast(payload)

    def _broadcast(self, payload: dict) -> None:
        dead: List[asyncio.Queue] = []
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Slow consumer -- drop the oldest and try again so we keep
                # current data flowing instead of stalling.
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except Exception:
                    logger.warning("Dropping update for slow SSE consumer")
                    dead.append(q)
        for q in dead:
            self.unsubscribe(q)

    async def snapshot_now(self) -> dict:
        """
        One-off, non-broadcast build. Used by the SSE endpoint to send the
        initial payload to a freshly-connected client without waiting for
        the next event.
        """
        async with self.db_pool.acquire() as conn:
            rows = await process_openrisktable(IbClient(self.ib), conn)
        return {"type": "snapshot", "rows": [r.model_dump() for r in rows]}
