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
    FIFO-match BOT against SLD per symbol and emit one dict per closed leg
    with prorated commission. Mirrors the previous IbClient.get_trades_with_pnl.
    """
    completed: list[dict] = []

    for symbol, fills in fills_by_symbol.items():
        fills_sorted = sorted(fills, key=lambda x: x["time"])
        buy_queue: list[tuple[float, float, str, float]] = []  # qty, price, time, commission

        for fill in fills_sorted:
            qty = float(fill.get("quantity") or 0)
            price = float(fill.get("price") or 0)
            commission = float(fill.get("commission") or 0)
            action = (fill.get("action") or "").upper()
            time_str = fill.get("time")

            if action in ("BUY", "BOT"):
                buy_queue.append((qty, price, time_str, commission))
                continue

            if action not in ("SELL", "SLD"):
                continue

            remaining = qty
            sell_commission = commission

            while remaining > 0 and buy_queue:
                buy_qty, buy_price, buy_time, buy_commission = buy_queue[0]
                matched = min(remaining, buy_qty)
                gross = matched * (price - buy_price)

                prorated_buy = buy_commission * (matched / buy_qty) if buy_qty else 0.0
                prorated_sell = sell_commission * (matched / qty) if qty else 0.0
                total_commission = prorated_buy + prorated_sell
                net = gross - total_commission

                completed.append({
                    "symbol":      symbol,
                    "entry_time":  buy_time,
                    "exit_time":   time_str,
                    "entry_price": buy_price,
                    "exit_price":  price,
                    "quantity":    matched,
                    "gross_pnl":   round(gross, 4),
                    "commission":  round(total_commission, 4),
                    "net_pnl":     round(net, 4),
                    "is_loss":     net < 0,
                })

                remaining -= matched
                if matched == buy_qty:
                    buy_queue.pop(0)
                else:
                    buy_queue[0] = (
                        buy_qty - matched,
                        buy_price,
                        buy_time,
                        buy_commission - prorated_buy,
                    )

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

    def last_loss(self) -> dict | None:
        """Most recent completed trade that closed at a loss, or None."""
        for trade in reversed(self.completed_trades):
            if trade.get("is_loss"):
                return trade
        return None

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
