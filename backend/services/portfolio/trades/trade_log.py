"""
Trade log service — per-symbol realized PnL / fill stats for today,
derived entirely from today's executions.

Why not IB's reqPnLSingle. The earlier implementation asked IB's accounting
layer for per-symbol realized PnL because that path stays correct for
positions opened on prior sessions and closed today. This system is a
day-trading flow with a consecutive-loss lockout, per-symbol attempt caps
and a daily-loss cutoff, so cross-session carries don't happen in practice.
Given that, deriving PnL from today's closed cycles (which TradesSnapshot
already computes for the entry/risk flow) is:
  - cheaper: one get_trades() round trip, no N-way reqPnLSingle subscribe/
    unsubscribe dance, no ~2.5s-per-symbol wait for the first tick;
  - simpler: one source of truth for "what happened today" shared with
    entry/risk, so the two views can't disagree;
  - safe from IB pacing violations under load.

If a swing-trade mode is ever added, reintroduce a persistent reqPnLSingle
registry (subscribe once per symbol at first fill, keep it alive until
session close) rather than the per-request pattern this replaces.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from services.portfolio.ib_client import IbClient
from services.portfolio.trades.trades_snapshot import build_today_snapshot

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradeLogEntry:
    """
    One symbol's row in the trade log. Numeric fields are rounded to 4dp
    on construction so downstream (API layer, CLI, tests) doesn't have to.
    """
    symbol: str
    realized_pnl: float
    fills: int
    last_fill_time: datetime | None
    is_loss: bool


@dataclass(frozen=True)
class TradeLog:
    """
    Trade-log view over today's TradesSnapshot: per-symbol PnL and fill
    stats plus session totals. Rows are sorted newest-fill-first.
    """
    rows: list[TradeLogEntry]
    realized_pnl: float
    symbol_count: int


async def build_trade_log(client: IbClient) -> TradeLog:

    snapshot = await build_today_snapshot(client)

    # Sort stats newest-fill-first up front so the row list comes out
    # already ordered. last_fill_time is guaranteed set (stats_by_symbol
    # only emits symbols with at least one fill), but the datetime.min
    # fallback keeps the sort safe against a malformed fill.
    stats_sorted = sorted(
        snapshot.stats_by_symbol().values(),
        key=lambda s: s.last_fill_time or datetime.min,
        reverse=True,
    )

    rows = [
        TradeLogEntry(
            symbol=s.symbol,
            realized_pnl=round(s.realized_pnl, 4),
            fills=s.fills,
            last_fill_time=s.last_fill_time,
            is_loss=s.is_loss,
        )
        for s in stats_sorted
    ]

    return TradeLog(
        rows=rows,
        realized_pnl=snapshot.realized_pnl,
        symbol_count=len(rows),
    )
