"""
Entry-attempts view — per-symbol entry counts for today plus the daily
total cap. Powers the "how close am I to MAX_ATTEMPTS" widget on the
Trade Manager UI.

Sourced from TradesSnapshot.entry_counts (populated during
build_today_snapshot), so the numbers can't drift from the entry/risk
flow's own attempts check.

Follows the same layering pattern as trade_log.py: a small view on top
of the snapshot, returning a typed dataclass the router converts to the
Pydantic response. Business logic (row shape, remaining-clamp, totals)
lives here; the router just does HTTP translation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from core.risk_manager_config import risk_settings
from services.portfolio.ib_client import IbClient
from services.portfolio.trades.trades_snapshot import build_today_snapshot

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EntryAttemptsEntry:
    """
    One symbol's row. `remaining` is clamped at zero so the UI never
    shows a negative "budget" if the config cap is lowered mid-session.
    """
    symbol: str
    attempts: int
    max_attempts: int
    remaining: int


@dataclass(frozen=True)
class EntryAttempts:
    """
    Entry-attempts view: per-symbol rows plus the daily total-cap
    aggregate. Rows sorted alphabetically for a stable UI table.
    """
    rows: list[EntryAttemptsEntry]
    total_attempts: int
    max_total: int
    total_remaining: int


async def build_entry_attempts(client: IbClient) -> EntryAttempts:
    """
    Build the entry-attempts view over today's snapshot. Only symbols
    with at least one attempt today appear (that's the semantics of
    TradesSnapshot.entry_counts). Exceptions from IB propagate to the
    router; they should surface as 500s, not be silently swallowed into
    an empty result.
    """
    snapshot = await build_today_snapshot(client)
    counts = snapshot.entry_counts

    max_attempts = risk_settings.MAX_ATTEMPTS_PER_SYMBOL_PER_DAY
    max_total = risk_settings.MAX_TOTAL_ENTRIES_PER_DAY

    rows = [
        EntryAttemptsEntry(
            symbol=symbol,
            attempts=count,
            max_attempts=max_attempts,
            remaining=max(0, max_attempts - count),
        )
        for symbol, count in sorted(counts.items())
    ]

    total_attempts = sum(counts.values())

    return EntryAttempts(
        rows=rows,
        total_attempts=total_attempts,
        max_total=max_total,
        total_remaining=max(0, max_total - total_attempts),
    )
