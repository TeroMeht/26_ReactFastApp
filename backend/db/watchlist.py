"""
Watchlist persistence — replaces the legacy tickers/watchlist.txt flow.

Two tables in the shared `livestreaming` database:

    watchlist
      id         SERIAL PK
      symbol     TEXT UNIQUE                -- stored uppercase
      created_at TIMESTAMPTZ DEFAULT NOW()

    watchlist_strategies
      id            SERIAL PK
      watchlist_id  INT FK -> watchlist(id) ON DELETE CASCADE
      strategy_name TEXT                    -- one of the entry strategies
      UNIQUE (watchlist_id, strategy_name)

The 22_WatchlistStreamer reads these tables at startup (see
src/database/watchlist.py in that project). The FastAPI side (this module +
routers/watchlist.py) is the writer.
"""
from __future__ import annotations

from typing import Dict, Iterable, List

import asyncpg


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

async def create_watchlist_tables(db_conn: asyncpg.Connection) -> None:
    """
    Idempotent table + index creation. Called from main.py's lifespan so the
    schema is guaranteed to exist before the first request.
    """
    await db_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watchlist (
            id          SERIAL PRIMARY KEY,
            symbol      TEXT NOT NULL UNIQUE,
            created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        """
    )
    await db_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watchlist_strategies (
            id            SERIAL PRIMARY KEY,
            watchlist_id  INTEGER NOT NULL REFERENCES watchlist(id) ON DELETE CASCADE,
            strategy_name TEXT NOT NULL,
            UNIQUE (watchlist_id, strategy_name)
        );
        """
    )
    await db_conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_watchlist_strategies_wid
            ON watchlist_strategies(watchlist_id);
        """
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm_symbol(symbol: str) -> str:
    sym = (symbol or "").strip().upper()
    if not sym:
        raise ValueError("symbol cannot be empty")
    return sym


def _norm_strategies(strategies: Iterable[str]) -> List[str]:
    # Preserve order from the caller (matters for stable UI ordering) but dedupe.
    seen: set[str] = set()
    cleaned: List[str] = []
    for s in strategies or []:
        s = (s or "").strip()
        if s and s not in seen:
            seen.add(s)
            cleaned.append(s)
    return cleaned


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

async def list_watchlist(db_conn: asyncpg.Connection) -> List[Dict]:
    """
    Return all watchlist rows as
    ``[{"id": int, "symbol": str, "strategies": list[str], "created_at": ...}, ...]``.

    A LEFT JOIN ensures a symbol with no strategies is still returned (with an
    empty list). Order is stable: alphabetical by symbol, alphabetical strategies.
    """
    rows = await db_conn.fetch(
        """
        SELECT
            w.id          AS id,
            w.symbol      AS symbol,
            w.created_at  AS created_at,
            COALESCE(
                array_remove(array_agg(ws.strategy_name ORDER BY ws.strategy_name), NULL),
                ARRAY[]::TEXT[]
            )             AS strategies
        FROM watchlist w
        LEFT JOIN watchlist_strategies ws ON ws.watchlist_id = w.id
        GROUP BY w.id, w.symbol, w.created_at
        ORDER BY w.symbol ASC;
        """
    )
    return [
        {
            "id": r["id"],
            "symbol": r["symbol"],
            "strategies": list(r["strategies"] or []),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


async def get_watchlist_entry(db_conn: asyncpg.Connection, symbol: str) -> Dict | None:
    sym = _norm_symbol(symbol)
    row = await db_conn.fetchrow(
        """
        SELECT
            w.id, w.symbol, w.created_at,
            COALESCE(
                array_remove(array_agg(ws.strategy_name ORDER BY ws.strategy_name), NULL),
                ARRAY[]::TEXT[]
            ) AS strategies
        FROM watchlist w
        LEFT JOIN watchlist_strategies ws ON ws.watchlist_id = w.id
        WHERE w.symbol = $1
        GROUP BY w.id, w.symbol, w.created_at;
        """,
        sym,
    )
    if not row:
        return None
    return {
        "id": row["id"],
        "symbol": row["symbol"],
        "strategies": list(row["strategies"] or []),
        "created_at": row["created_at"],
    }


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

async def add_watchlist_entry(
    db_conn: asyncpg.Connection,
    symbol: str,
    strategies: Iterable[str],
) -> Dict | None:
    """
    Strict insert: add a brand-new symbol with the given strategy set.

    Returns the inserted row, or ``None`` if the symbol already exists in the
    table (caller chooses 409 vs. update). This avoids clobbering strategies a
    user has already configured when a different code path (e.g. the scanner's
    right-click "Add to watchlist") tries to add the same symbol again.

    Transactional: symbol row + strategy rows are written atomically.
    """
    sym = _norm_symbol(symbol)
    strat_list = _norm_strategies(strategies)

    async with db_conn.transaction():
        # ON CONFLICT DO NOTHING returns no row if the symbol already exists,
        # which is exactly how we detect collision.
        row = await db_conn.fetchrow(
            """
            INSERT INTO watchlist (symbol)
            VALUES ($1)
            ON CONFLICT (symbol) DO NOTHING
            RETURNING id, symbol, created_at;
            """,
            sym,
        )
        if row is None:
            return None

        watchlist_id = row["id"]
        if strat_list:
            await db_conn.executemany(
                """
                INSERT INTO watchlist_strategies (watchlist_id, strategy_name)
                VALUES ($1, $2);
                """,
                [(watchlist_id, s) for s in strat_list],
            )

    return {
        "id": watchlist_id,
        "symbol": sym,
        "strategies": strat_list,
        "created_at": row["created_at"],
    }


async def update_strategies(
    db_conn: asyncpg.Connection,
    symbol: str,
    strategies: Iterable[str],
) -> Dict | None:
    """
    Replace the strategy set for an existing symbol. Returns None if the
    symbol isn't in the watchlist (caller can choose 404 vs upsert).
    """
    sym = _norm_symbol(symbol)
    strat_list = _norm_strategies(strategies)

    async with db_conn.transaction():
        wid = await db_conn.fetchval(
            "SELECT id FROM watchlist WHERE symbol = $1;", sym
        )
        if wid is None:
            return None

        await db_conn.execute(
            "DELETE FROM watchlist_strategies WHERE watchlist_id = $1;", wid
        )
        if strat_list:
            await db_conn.executemany(
                """
                INSERT INTO watchlist_strategies (watchlist_id, strategy_name)
                VALUES ($1, $2);
                """,
                [(wid, s) for s in strat_list],
            )

    return await get_watchlist_entry(db_conn, sym)


async def delete_watchlist_entry(
    db_conn: asyncpg.Connection, symbol: str
) -> Dict | None:
    """
    Remove a symbol (and, via FK CASCADE, its strategies). Returns the
    deleted row, or None if the symbol wasn't in the watchlist.
    """
    sym = _norm_symbol(symbol)
    row = await db_conn.fetchrow(
        """
        DELETE FROM watchlist
        WHERE symbol = $1
        RETURNING id, symbol, created_at;
        """,
        sym,
    )
    if not row:
        return None
    return {
        "id": row["id"],
        "symbol": row["symbol"],
        "strategies": [],  # already gone via CASCADE
        "created_at": row["created_at"],
    }
