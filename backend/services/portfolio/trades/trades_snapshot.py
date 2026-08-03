"""
Today's trade snapshot.

The entry/risk flow used to call IbClient.get_trades() (i.e. reqExecutionsAsync)
several times per request and derive PnL, entry counts, latest-trade-per-symbol
and round-trip PnL each from a fresh fetch. This module pulls fills once and
derives everything as pure functions over the same in-memory data.

Trade-cycle logic (fills → completed trades → realized PnL) lives in
services.portfolio.trades.trade_builder; this module is the snapshot layer
that orchestrates one IB fetch and exposes query methods over the derived
view.

Public surface:
    TradesSnapshot          - immutable view of today's fills + derived data
    build_today_snapshot()  - call IB once and build a snapshot
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable
from core.config import settings
import pytz
from services.portfolio.ib_client import Fill, IbClient
from services.portfolio.trades.trade_builder import (
    _signed_qty,
    aggregate_realized_pnl,
    build_completed_trades,
    count_entries_from_fills,
)

logger = logging.getLogger(__name__)


TIMEZONE = pytz.timezone(settings.TIMEZONE)


def _latest_fill_time(fills: Iterable[Fill]) -> datetime | None:
    """Newest `time` in a fill sequence, or None if the sequence is empty."""
    return max((f.time for f in fills), default=None)


# ----------------------------------------------------------------------
# Snapshot
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class SymbolTradeStats:

    symbol: str
    realized_pnl: float
    fills: int
    last_fill_time: datetime | None

    @property
    def is_loss(self) -> bool:
        return self.realized_pnl < 0


@dataclass(frozen=True)
class TradesSnapshot:
    """Everything the entry/risk flow needs from today's fills, fetched once."""
    today_fills: list[Fill] = field(default_factory=list)
    fills_by_symbol: dict[str, list[Fill]] = field(default_factory=dict)
    completed_trades: list[dict] = field(default_factory=list)
    entry_counts: dict[str, int] = field(default_factory=dict)
    realized_pnl_by_symbol: dict[str, float] = field(default_factory=dict)
    realized_pnl: float = 0.0

    def stats_by_symbol(self) -> dict[str, SymbolTradeStats]:
        """
        Per-symbol aggregate for the trade log. One entry per symbol with
        at least one fill today. Realized PnL is read from the precomputed
        realized_pnl_by_symbol (closed cycles only); fills and
        last_fill_time come from raw fills, so a symbol that only opened
        a position today still appears.

        The set of symbols matches fills_by_symbol. That's the invariant
        the trade-log view relies on for its "what was active today" list.
        """
        stats: dict[str, SymbolTradeStats] = {}
        for sym, fills in self.fills_by_symbol.items():
            if not fills:
                continue
            stats[sym] = SymbolTradeStats(
                symbol=sym,
                realized_pnl=self.realized_pnl_by_symbol.get(sym, 0.0),
                fills=len(fills),
                last_fill_time=_latest_fill_time(fills),
            )
        return stats

    def latest_fill_for_symbol(self, symbol: str) -> Fill | None:
        fills = self.fills_by_symbol.get(symbol.upper())
        if not fills:
            return None
        return max(fills, key=lambda f: f.time)

    def position_opened_at(self, symbol: str) -> datetime | None:
        fills = self.fills_by_symbol.get(symbol.upper())
        if not fills:
            return None

        net = 0
        open_time = None
        for fill in fills:
            signed = _signed_qty(fill.action, fill.quantity)
            if signed == 0:
                continue
            if net == 0:
                open_time = fill.time
            net += signed

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


def _group_fills_by_symbol(fills: list[Fill]) -> dict[str, list[Fill]]:
    """Group fills by uppercase symbol; each list sorted by time ascending."""
    by_symbol: dict[str, list[Fill]] = defaultdict(list)
    for f in fills:
        sym = f.symbol.upper()
        if sym:
            by_symbol[sym].append(f)
    for sym in by_symbol:
        by_symbol[sym].sort(key=lambda x: x.time)
    return dict(by_symbol)


def _count_entries_per_symbol(fills_by_symbol: dict[str, list[Fill]]) -> dict[str, int]:
    """Non-zero entry counts per symbol."""
    counts: dict[str, int] = {}
    for sym, fills in fills_by_symbol.items():
        n = count_entries_from_fills(fills)
        if n > 0:
            counts[sym] = n
    return counts


async def build_today_snapshot(client: IbClient) -> TradesSnapshot:
    """
    Single round trip to IB for today's fills, then derive everything.
    Returns an empty snapshot if IB returns no data.
    """
    logger.info("\n")
    logger.info("============ Building today's trade snapshot ============")

    today_fills = await client.get_trades()

    if not today_fills:
        logger.info("No fills for today — returning empty snapshot")
        return TradesSnapshot()

    fills_by_symbol = _group_fills_by_symbol(today_fills)
    entry_counts = _count_entries_per_symbol(fills_by_symbol)
    completed_trades = build_completed_trades(fills_by_symbol)
    realized, realized_pnl_by_symbol = aggregate_realized_pnl(completed_trades)

    losses = sum(1 for t in completed_trades if t.get("is_loss"))
    logger.info(f"Entry counts today: {entry_counts}")
    logger.info(
        f"Trade snapshot: {len(completed_trades)} (losses: {losses}); "
        f"realized PnL: {realized:.4f}"
    )

    return TradesSnapshot(
        today_fills=today_fills,
        fills_by_symbol=fills_by_symbol,
        completed_trades=completed_trades,
        entry_counts=entry_counts,
        realized_pnl_by_symbol=realized_pnl_by_symbol,
        realized_pnl=realized,
    )
