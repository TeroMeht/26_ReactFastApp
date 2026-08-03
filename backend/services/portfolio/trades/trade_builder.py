"""
Trade builder — pure functions that turn raw fills into completed trades.

A "trade" here is a flat-to-flat position cycle on a single symbol. This
module owns everything about:

  - classifying a fill's signed contribution (`_signed_qty`),
  - detecting where cycles start and close (`_split_into_cycles`,
    `count_entries_from_fills`),
  - aggregating a cycle's fills into one completed-trade row (`_cycle_row`,
    `build_completed_trades`),
  - summing realized PnL over those rows (`aggregate_realized_pnl`).

Everything is a pure function over in-memory Fill objects. No IB, no DB,
no time source. The snapshot layer (trades_snapshot) calls these once per
snapshot build.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from services.portfolio.ib_client import Fill


def _signed_qty(action: str, qty: float) -> int:
    a = (action or "").upper()
    if a in ("BOT", "BUY"):
        return int(qty)
    if a in ("SLD", "SELL"):
        return -int(qty)
    return 0


def count_entries_from_fills(fills: Iterable[Fill]) -> int:
    """
    Count "entries" in a chronologically sorted fill list for one symbol.
    An entry is a fill that takes net position from flat to non-flat. Adds,
    stop fills and exits don't count.
    """
    entries = 0
    net = 0
    for fill in fills:
        signed = _signed_qty(fill.action, fill.quantity)
        if signed == 0:
            continue
        if net == 0:
            entries += 1
        net += signed
    return entries


def _split_into_cycles(fills: list[Fill]) -> list[list[Fill]]:
    """
    Partition one symbol's fills into flat-to-flat cycles. Each returned
    list is the fills of one closed cycle in time order. Fills belonging
    to an open trailing position are dropped (no cycle closes).
    """
    cycles: list[list[Fill]] = []
    current: list[Fill] = []
    net = 0
    for fill in sorted(fills, key=lambda x: x.time):
        signed = _signed_qty(fill.action, fill.quantity)
        if signed == 0:
            continue
        current.append(fill)
        net += signed
        if net == 0:
            cycles.append(current)
            current = []
    return cycles


def _cycle_row(symbol: str, cycle: list[Fill]) -> dict:
    """
    Aggregate one flat-to-flat cycle into a completed-trade row.

    Direction is fixed by the first fill (guaranteed signed since
    _split_into_cycles filters zero-signed fills). Entry/exit-side
    classification is by same-sidedness relative to that direction.
    """
    direction = 1 if _signed_qty(cycle[0].action, cycle[0].quantity) > 0 else -1

    entry_value = exit_value = 0.0
    entry_qty = exit_qty = 0.0
    for fill in cycle:
        signed = _signed_qty(fill.action, fill.quantity)
        value = fill.quantity * fill.price
        if (signed > 0) == (direction > 0):
            entry_value += value
            entry_qty += fill.quantity
        else:
            exit_value += value
            exit_qty += fill.quantity

    # For a long, pnl = exit - entry. For a short, entry fills are sells
    # (proceeds) and exit fills are buys (cost), so pnl = entry - exit.
    pnl = (exit_value - entry_value) if direction > 0 else (entry_value - exit_value)

    avg_entry = entry_value / entry_qty if entry_qty else 0.0
    avg_exit = exit_value / exit_qty if exit_qty else 0.0

    return {
        "symbol":      symbol,
        "entry_time":  cycle[0].time,
        "exit_time":   cycle[-1].time,
        "entry_price": round(avg_entry, 4),
        "exit_price":  round(avg_exit, 4),
        "quantity":    exit_qty,
        "pnl":         round(pnl, 4),
        "is_loss":     pnl < 0,
    }

def aggregate_realized_pnl(completed_trades: list[dict]) -> tuple[float, dict[str, float]]:
    """
    Single pass over the completed cycles: return (total, per_symbol).
    Open positions contribute nothing (no cycle to close).
    """
    per_symbol: dict[str, float] = defaultdict(float)
    for t in completed_trades:
        per_symbol[t["symbol"]] += t["pnl"]
    total = round(sum(per_symbol.values()), 4)
    return total, dict(per_symbol)


def build_completed_trades(fills_by_symbol: dict[str, list[Fill]]) -> list[dict]:
    """
    Emit one completed trade per flat-to-flat position cycle per symbol,
    sorted by exit_time.

    A "cycle" is the stretch from net position == 0 to net position == 0
    again. All fills inside the cycle -- the initial entry, any adds, any
    partial trims, and the final exit -- collapse into one row. This is
    the user's mental model of a trade: one decision, one outcome, even
    if it took several fills to enter and exit.

    Why not per-leg FIFO. The previous implementation emitted one row for
    every buy_queue entry a sell consumed. If you added to a position and
    then got stopped out at a single price between your adds, that one
    stop fill produced N losses (one per add) -- enough to trip the
    consecutive-loss lockout from a single trade. Aggregating by cycle
    fixes that and also makes shorts work (SELL-open, BUY-close), which
    the FIFO matcher silently dropped.

    Position flips on a single fill (e.g. one SELL that closes a long
    AND opens a short past zero) are not split -- that's exotic for this
    flow and would muddy cycle direction. If it ever matters, the fix is
    to split the flipping fill into a close portion and an open portion.

    Open positions at end-of-day are not emitted: net never returns to
    zero so no cycle closes.
    """
    completed_trades = [
        _cycle_row(symbol, cycle)
        for symbol, fills in fills_by_symbol.items()
        for cycle in _split_into_cycles(fills)
    ]
    completed_trades.sort(key=lambda x: x["exit_time"])
    return completed_trades


