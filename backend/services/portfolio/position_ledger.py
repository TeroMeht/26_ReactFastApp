"""
Authoritative in-process position ledger.

Why this exists
---------------
IB's cached position book (``reqPositionsAsync``) is *eventually*
consistent with fill events. Reading it in a fill handler to compute
"what should the STP be now?" is racy: the read can come back before
the fill you just observed has propagated, and any resize based on that
stale read stomps whatever value was correct.

The ledger fixes that at the root by making the observation of the fill
*be* the source of truth. Every execution IB gives us updates the
ledger before anyone else looks. Reads are never behind the fills that
produced them.

Design invariants
-----------------
- Fills are applied idempotently. Applying the same ``execId`` twice is
  a no-op. Reconnects, replays, and duplicate ``execDetailsEvent``
  callbacks are all safe.
- The internal lock is only held while the ledger's own state mutates.
  Subscriber callbacks are invoked outside the lock so a slow
  subscriber can't block subsequent fills from landing.
- Subscribers receive a ``PositionChanged`` domain event describing the
  transition (old_qty, new_qty). They should not re-derive position by
  calling IB.
- Seeding is one-shot at startup from IB's authoritative snapshot; from
  that point on the ledger is fill-driven.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PositionChanged:
    """Emitted whenever a fill moves a symbol's ledger position."""

    symbol: str
    old_qty: int
    new_qty: int
    # Free-form origin tag ("fill", "seed"). Reconciler doesn't need
    # this today, but it's cheap to carry for logs and future policy.
    source: str
    exec_id: Optional[str] = None


Subscriber = Callable[[PositionChanged], Optional[Awaitable[None]]]


class PositionLedger:
    """
    Signed position per symbol, updated from ``execDetailsEvent``.

    Public surface:
      - seed_symbol(symbol, qty)            : one-shot startup snapshot
      - apply_fill(symbol, action, shares,  : idempotent, emits event
                   exec_id, source="fill")
      - get(symbol)                         : current signed qty
      - subscribe(callback)                 : PositionChanged fanout
    """

    def __init__(self) -> None:
        self._pos: dict[str, int] = {}
        self._seen_execs: set[str] = set()
        self._lock = asyncio.Lock()
        self._subscribers: list[Subscriber] = []

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def get(self, symbol: str) -> int:
        """Current signed position for ``symbol``. Zero when unknown."""
        return self._pos.get(symbol.upper(), 0)

    def snapshot(self) -> dict[str, int]:
        """Copy of every symbol's current signed position."""
        return dict(self._pos)

    # ------------------------------------------------------------------
    # Seeding — called once at startup from IB's position snapshot.
    # ------------------------------------------------------------------
    def seed_symbol(self, symbol: str, qty: int) -> None:
        """
        Set the initial position for a symbol without emitting an event.
        Meant for startup only — after seeding, only ``apply_fill``
        should mutate state.
        """
        self._pos[symbol.upper()] = int(qty)

    # ------------------------------------------------------------------
    # Writes — fill-driven, idempotent by execId.
    # ------------------------------------------------------------------
    async def apply_fill(
        self,
        symbol: str,
        action: str,
        shares: float,
        exec_id: str,
        source: str = "fill",
    ) -> Optional[PositionChanged]:
        """
        Apply one execution. Returns the emitted event, or ``None`` if
        this execId was already seen (dedup no-op).
        """
        if not symbol or not exec_id:
            logger.warning(
                "PositionLedger.apply_fill missing key data | "
                "symbol=%s exec_id=%s action=%s shares=%s",
                symbol, exec_id, action, shares,
            )
            return None

        sym = symbol.upper()
        act = (action or "").upper()
        if act not in ("BUY", "SELL"):
            logger.warning(
                "PositionLedger.apply_fill unknown action | "
                "symbol=%s action=%s exec_id=%s",
                sym, action, exec_id,
            )
            return None

        try:
            shares_i = int(round(float(shares)))
        except (TypeError, ValueError):
            logger.warning(
                "PositionLedger.apply_fill bad shares value | "
                "symbol=%s shares=%r exec_id=%s",
                sym, shares, exec_id,
            )
            return None
        if shares_i <= 0:
            return None

        async with self._lock:
            if exec_id in self._seen_execs:
                return None
            self._seen_execs.add(exec_id)
            old = self._pos.get(sym, 0)
            delta = shares_i if act == "BUY" else -shares_i
            new = old + delta
            self._pos[sym] = new
            event = PositionChanged(
                symbol=sym,
                old_qty=old,
                new_qty=new,
                source=source,
                exec_id=exec_id,
            )

        # Fan out subscribers OUTSIDE the lock. A slow subscriber (an IB
        # round-trip in the reconciler, say) must not block the next
        # fill from landing.
        for cb in list(self._subscribers):
            try:
                result = cb(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception(
                    "PositionLedger subscriber failed | symbol=%s "
                    "old=%s new=%s exec_id=%s",
                    sym, old, new, exec_id,
                )

        return event

    # ------------------------------------------------------------------
    # Fanout
    # ------------------------------------------------------------------
    def subscribe(self, callback: Subscriber) -> None:
        """
        Register a callback fired for every applied fill. Callbacks may
        be sync or return a coroutine; coroutines are awaited before
        the next subscriber runs, so the reconciler can rely on
        strict per-event ordering.
        """
        self._subscribers.append(callback)
