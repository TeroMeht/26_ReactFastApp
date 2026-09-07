"""
STP reconciler — the SOLE writer of STP quantity.

Contract
--------
Given a ``PositionChanged`` event from ``PositionLedger``, drive the
symbol's protective native STP to match the new position:

    abs(STP.totalquantity) == abs(position)          if position != 0
    STP is cancelled                                  if position == 0

The reconciler is the ONLY code path that mutates STP quantity or
cancels the STP as a consequence of a fill. Flows (entry/add/exit) no
longer touch the STP after placement — entry.py plants the initial STP
via a bracket, and everything else is enforcement done here.

Race safety
-----------
- Per-symbol ``asyncio.Lock`` serializes STP writes for a symbol. A
  burst of fills produces a queue of events for that symbol; each is
  processed to completion before the next runs. The last event wins
  by definition, and it is always the freshest.
- Different symbols proceed in parallel — locks are not global.
- Idempotent: if the STP already matches the target size, this is a
  no-op. Reconciling twice with the same state does no harm.

What this reconciler will NOT do
--------------------------------
- Create a fresh STP from scratch when one is missing. That would
  paper over a real bug (an entry that failed to place its bracket
  STP). Missing STP with nonzero position is logged as an error so
  it surfaces instead of quietly self-healing to an unknown stop
  price.
"""

from __future__ import annotations

import asyncio
import logging

from services.portfolio.ib_client import (
    IbClient,
    OrderNotFoundError,
)
from services.portfolio.position_ledger import PositionChanged

logger = logging.getLogger(__name__)


# Per-symbol locks. Symbols are keyed uppercase, matching the ledger.
_symbol_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


async def _lock_for(symbol: str) -> asyncio.Lock:
    """Return (creating if needed) the lock for one symbol."""
    async with _locks_guard:
        lock = _symbol_locks.get(symbol)
        if lock is None:
            lock = asyncio.Lock()
            _symbol_locks[symbol] = lock
        return lock


async def reconcile_stp(event: PositionChanged, client: IbClient) -> None:
    """
    Bring the STP for ``event.symbol`` into agreement with the new
    position. Safe to call concurrently across symbols; serialised
    within a symbol via ``_symbol_locks``.
    """
    symbol = event.symbol
    target_abs = abs(int(event.new_qty))

    lock = await _lock_for(symbol)
    async with lock:
        try:
            stp = await client.get_stp_order_by_symbol(symbol)

            if target_abs == 0:
                if stp is None:
                    return
                try:
                    await client.cancel_order_by_id(stp.orderid)
                    logger.info(
                        "STP cancelled after position flat | symbol=%s "
                        "order_id=%s (event: %s -> %s)",
                        symbol, stp.orderid, event.old_qty, event.new_qty,
                    )
                except OrderNotFoundError:
                    # STP became terminal between lookup and cancel
                    # (common when the fill we're reacting to WAS the
                    # STP). Position is already flat; nothing to do.
                    logger.info(
                        "STP already gone at cancel time | symbol=%s "
                        "order_id=%s",
                        symbol, stp.orderid,
                    )
                return

            if stp is None:
                # Nonzero position with no protective stop. This should
                # never happen in the normal flow — entry.py's bracket
                # plants the STP atomically with the parent. If it does
                # happen we prefer to surface the bug than silently
                # invent a stop price. Alerting/paging hook goes here.
                logger.error(
                    "Position without STP after fill | symbol=%s "
                    "qty=%s exec_id=%s source=%s",
                    symbol, event.new_qty, event.exec_id, event.source,
                )
                return

            current = int(round(float(stp.totalqty or 0)))
            if current == target_abs:
                # Already matches — most common case for entry fills,
                # where the bracket's STP was already sized correctly.
                return

            await client.modify_stp_order_by_id(stp.orderid, target_abs)
            logger.info(
                "STP resized | symbol=%s %s -> %s (position %s -> %s, "
                "source=%s)",
                symbol, current, target_abs,
                event.old_qty, event.new_qty, event.source,
            )
        except Exception:
            logger.exception(
                "reconcile_stp failed | symbol=%s new_qty=%s exec_id=%s",
                symbol, event.new_qty, event.exec_id,
            )
