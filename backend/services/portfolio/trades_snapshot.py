"""
Today's trade snapshot.

The entry/risk flow used to call IbClient.get_trades() (i.e. reqExecutionsAsync)
several times per request and derive PnL, entry counts, latest-trade-per-symbol
and round-trip PnL each from a fresh fetch. This module pulls fills once and
derives everything as pure functions over the same in-memory data.

Public surface:
    TradesSnapshot          - immutable view of today's fills + derived data
    build_today_snapshot()  - call IB once and build a snapshot
    All derivation helpers (count_entries_from_fills, build_completed_trades,
    sum_realized_pnl) are pure and exported for testing.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable

import pytz

from services.portfolio.ib_client import IbClient

logger = logging.getLogger(__name__)

HELSINKI = pytz.timezone("Europe/Helsinki")


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------
def _parse_time(value) -> datetime | None:
    """Parse an IB-formatted time field into a tz-aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = HELSINKI.localize(dt)
    return dt


def _signed_qty(action: str, qty: float) -> int:
    a = (action or "").upper()
    if a in ("BOT", "BUY"):
        return int(qty)
    if a in ("SLD", "SELL"):
        return -int(qty)
    return 0


def count_entries_from_fills(fills: Iterable[dict]) -> int:
    """
    Count "entries" in a chronologically sorted fill list for one symbol.
    An entry is a fill that takes net position from flat to non-flat. Adds,
    stop fills and exits don't count.
    """
    entries = 0
    net = 0
    for fill in fills:
        signed = _signed_qty(fill.get("action") or "", float(fill.get("quantity") or 0))
        if signed == 0:
            continue
        if net == 0:
            entries += 1
        net += signed
    return entries


def build_completed_trades(fills_by_symbol: dict[str, list[dict]]) -> list[dict]:
    """
    Emit one completed trade per flat-to-flat position cycle per symbol.

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

    Cycle classification:
      direction = sign of the first fill that takes us off flat
      gross     = (sum of exit values) - (sum of entry values), where
                  "entry" fills are in the cycle's direction and "exit"
                  fills are opposite. For a long this is the usual
                  sell_value - buy_value; for a short it's
                  sell_value - buy_value as well (entries are sells,
                  exits are buys, signs invert -- the arithmetic ends up
                  the same).
      is_loss   = gross < 0 (commissions are not part of the lockout
                  classifier; see net_pnl on the row for accounting).

    Position flips on a single fill (e.g. one SELL that closes a long
    AND opens a short past zero) are not split -- that's exotic for this
    flow and would muddy cycle direction. If it ever matters, the fix is
    to split the flipping fill into a close portion and an open portion.

    Open positions at end-of-day are not emitted: net never returns to
    zero so no cycle closes.
    """
    completed: list[dict] = []

    for symbol, fills in fills_by_symbol.items():
        fills_sorted = sorted(fills, key=lambda x: x["time"])

        net = 0
        direction = 0  # +1 long cycle, -1 short cycle, 0 flat
        entry_value = 0.0  # sum of qty*price on entry-side fills
        exit_value = 0.0   # sum of qty*price on exit-side fills
        entry_qty = 0.0
        exit_qty = 0.0
        total_commission = 0.0
        entry_time: str | None = None
        last_fill_time: str | None = None

        for fill in fills_sorted:
            qty = float(fill.get("quantity") or 0)
            price = float(fill.get("price") or 0)
            commission = float(fill.get("commission") or 0)
            action = (fill.get("action") or "").upper()
            time_str = fill.get("time")

            signed = _signed_qty(action, qty)
            if signed == 0:
                continue

            if net == 0:
                # Cycle starts.
                direction = 1 if signed > 0 else -1
                entry_value = qty * price
                exit_value = 0.0
                entry_qty = qty
                exit_qty = 0.0
                total_commission = commission
                entry_time = time_str
            else:
                # Mid-cycle fill -- adds to entry side or trims/closes
                # from exit side depending on direction.
                same_side = (signed > 0 and direction > 0) or (signed < 0 and direction < 0)
                if same_side:
                    entry_value += qty * price
                    entry_qty += qty
                else:
                    exit_value += qty * price
                    exit_qty += qty
                total_commission += commission

            net += signed
            last_fill_time = time_str

            if net == 0 and direction != 0:
                # Cycle closed -- emit one row.
                if direction > 0:
                    gross = exit_value - entry_value
                else:
                    # Short: entry fills are sells (proceeds), exit fills
                    # are buys (cost). Profit = proceeds - cost.
                    gross = entry_value - exit_value

                avg_entry_price = entry_value / entry_qty if entry_qty else 0.0
                avg_exit_price = exit_value / exit_qty if exit_qty else 0.0

                completed.append({
                    "symbol":      symbol,
                    "entry_time":  entry_time,
                    "exit_time":   last_fill_time,
                    "entry_price": round(avg_entry_price, 4),
                    "exit_price":  round(avg_exit_price, 4),
                    "quantity":    exit_qty,
                    "gross_pnl":   round(gross, 4),
                    "commission":  round(total_commission, 4),
                    "net_pnl":     round(gross - total_commission, 4),
                    # Classified on gross so commissions never flip a
                    # winning trade into the streak that drives the
                    # consecutive-loss lockout.
                    "is_loss":     gross < 0,
                })

                # Reset for the next cycle.
                direction = 0
                entry_value = exit_value = 0.0
                entry_qty = exit_qty = 0.0
                total_commission = 0.0
                entry_time = None

    completed.sort(key=lambda x: x["exit_time"])
    return completed


def sum_realized_pnl(completed_trades: list[dict], total_fills: int) -> dict:
    """Aggregate completed-trade PnL into the shape get_realized_pnl_today returned."""
    realized = sum(t["gross_pnl"] for t in completed_trades)
    commission = sum(t["commission"] for t in completed_trades)
    return {
        "realized_pnl":     round(realized, 4),
        "total_commission": round(commission, 4),
        "net_pnl":          round(realized - commission, 4),
        "fills":            total_fills,
    }


# ----------------------------------------------------------------------
# Snapshot
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class TradesSnapshot:
    """Everything the entry/risk flow needs from today's fills, fetched once."""
    today_fills: list[dict] = field(default_factory=list)
    fills_by_symbol: dict[str, list[dict]] = field(default_factory=dict)
    completed_trades: list[dict] = field(default_factory=list)
    entry_counts: dict[str, int] = field(default_factory=dict)
    realized_pnl: dict = field(default_factory=lambda: {
        "realized_pnl": 0.0, "total_commission": 0.0, "net_pnl": 0.0, "fills": 0,
    })

    def latest_fill_for_symbol(self, symbol: str) -> dict | None:
        fills = self.fills_by_symbol.get(symbol.upper())
        if not fills:
            return None
        return max(fills, key=lambda f: f.get("time") or "")

    def position_opened_at(self, symbol: str) -> datetime | None:
        """
        Time of the most recent fill that took net position from flat to
        non-flat for this symbol (i.e. when the currently-open position
        was opened). Returns None if the position was opened before today
        or there are no fills for this symbol today.
        """
        fills = self.fills_by_symbol.get(symbol.upper())
        if not fills:
            return None
        net = 0
        open_time: datetime | None = None
        for fill in fills:
            signed = _signed_qty(
                fill.get("action") or "", float(fill.get("quantity") or 0)
            )
            if signed == 0:
                continue
            if net == 0:
                open_time = _parse_time(fill.get("time"))
            net += signed
        # Only return a time if the position is still open from that fill.
        return open_time if net != 0 else None

    def last_loss(self) -> dict | None:
        """Most recent completed trade that closed at a loss, or None."""
        for trade in reversed(self.completed_trades):
            if trade.get("is_loss"):
                return trade
        return None

    def consecutive_losses(self) -> int:
        """
        Count losses on the tail of today's completed_trades until a win
        breaks the streak. completed_trades is sorted by exit_time, so
        walking from the end gives the current streak. Returns 0 if the
        most recent trade is a win or there are no trades yet.
        """
        streak = 0
        for trade in reversed(self.completed_trades):
            if trade.get("is_loss"):
                streak += 1
            else:
                break
        return streak

    def attempts_for(self, symbol: str) -> int:
        return self.entry_counts.get(symbol.upper(), 0)

    def total_attempts(self) -> int:
        """Total entries today across all symbols."""
        return sum(self.entry_counts.values())


async def build_today_snapshot(client: IbClient) -> TradesSnapshot:
    """
    Single round trip to IB for today's fills, then derive everything.
    Returns an empty snapshot if IB returns no data.
    """
    logger.info("Building today's trade snapshot")

    trades = await client.get_trades()
    today = date.today()

    today_fills = [
        t for t in trades
        if t.get("time") and date.fromisoformat(t["time"][:10]) == today
    ]
    logger.info(
        f"Fetched {len(trades)} fills from IB; {len(today_fills)} are from today ({today.isoformat()})"
    )

    if not today_fills:
        logger.info("No fills for today — returning empty snapshot")
        return TradesSnapshot()

    fills_by_symbol: dict[str, list[dict]] = defaultdict(list)
    for fill in today_fills:
        sym = (fill.get("symbol") or "").upper()
        if sym:
            fills_by_symbol[sym].append(fill)
    for sym in fills_by_symbol:
        fills_by_symbol[sym].sort(key=lambda x: x["time"])
    logger.info(
        f"Grouped today's fills across {len(fills_by_symbol)} symbol(s): "
        f"{ {s: len(f) for s, f in fills_by_symbol.items()} }"
    )

    entry_counts: dict[str, int] = {}
    for sym, fills in fills_by_symbol.items():
        n = count_entries_from_fills(fills)
        if n > 0:
            entry_counts[sym] = n
    logger.info(f"Entry counts today: {entry_counts}")

    completed = build_completed_trades(dict(fills_by_symbol))
    realized = sum_realized_pnl(completed, len(today_fills))
    losses = sum(1 for t in completed if t.get("is_loss"))
    logger.info(
        f"Completed round-trips: {len(completed)} (losses: {losses}); "
        f"realized PnL: gross={realized['realized_pnl']:.4f}, "
        f"commission={realized['total_commission']:.4f}, "
        f"net={realized['net_pnl']:.4f}"
    )

    return TradesSnapshot(
        today_fills=today_fills,
        fills_by_symbol=dict(fills_by_symbol),
        completed_trades=completed,
        entry_counts=entry_counts,
        realized_pnl=realized,
    )
