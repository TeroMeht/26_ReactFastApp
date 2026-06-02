"""
Business logic for the watchlist + strategy-binding endpoints.

Thin wrapper around backend/db/watchlist.py — the service exists mainly so the
router stays unaware of asyncpg details and so we have a clear seam for adding
side-effects later (e.g. publishing a Postgres NOTIFY when adding live mode).
"""
from __future__ import annotations

from typing import Dict, Iterable, List

import asyncpg

from db import watchlist as watchlist_db
from schemas.api_schemas import ENTRY_STRATEGY_NAMES


async def list_watchlist(db_conn: asyncpg.Connection) -> List[Dict]:
    return await watchlist_db.list_watchlist(db_conn)


async def add_watchlist_entry(
    db_conn: asyncpg.Connection,
    symbol: str,
    strategies: Iterable[str],
) -> Dict | None:
    """
    Insert a brand-new ticker with the given strategy set. Returns None if the
    ticker is already in the watchlist (router converts that into a 409). The
    caller should use PUT /api/watchlist/{symbol} to replace strategies for an
    existing ticker.
    """
    return await watchlist_db.add_watchlist_entry(db_conn, symbol, strategies)


async def update_watchlist_strategies(
    db_conn: asyncpg.Connection,
    symbol: str,
    strategies: Iterable[str],
) -> Dict | None:
    return await watchlist_db.update_strategies(db_conn, symbol, strategies)


async def delete_watchlist_entry(
    db_conn: asyncpg.Connection, symbol: str
) -> Dict | None:
    return await watchlist_db.delete_watchlist_entry(db_conn, symbol)


def get_available_entry_strategies() -> List[str]:
    """
    Names the UI's strategy picker should show. Kept in schemas.api_schemas so
    request validation and this getter agree on the same source of truth.
    """
    return list(ENTRY_STRATEGY_NAMES)
