from typing import List, Dict
import asyncpg
from datetime import datetime
from decimal import Decimal


async def create_exit_requests_table(db_conn: asyncpg.Connection) -> None:
    """
    Ensure the exit_requests table exists. The table keys on
    (symbol, strategy) so a symbol may have multiple armed strategies at once
    (e.g., trim at vwap_exit AND full exit at endofday_exit). Every row is
    implicitly armed; to disarm a strategy the caller deletes the row.

    Data must survive restarts (armed exits are the source of truth for the
    strategy runner), so this uses CREATE IF NOT EXISTS — never DROP. Any
    stale rows for symbols the portfolio no longer holds are cleaned up by
    the "Reconcile exits" flow (services.exits.reconcile_exit_requests_with_positions).
    """
    await db_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS exit_requests (
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            trim_percentage NUMERIC(5, 4) NOT NULL DEFAULT 1.0,
            updated TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
            PRIMARY KEY (symbol, strategy)
        );
        """
    )


async def fetch_exits(db_conn: asyncpg.Connection) -> List[Dict]:
    rows = await db_conn.fetch(
        """
        SELECT symbol, strategy, trim_percentage, updated
        FROM exit_requests
        ORDER BY symbol ASC, strategy ASC
        """
    )
    return [dict(row) for row in rows]


async def fetch_exits_by_symbol(db_conn: asyncpg.Connection, symbol: str) -> List[Dict]:
    """
    Return ALL exit_request rows for a given symbol. May be empty.
    """
    rows = await db_conn.fetch(
        """
        SELECT symbol, strategy, trim_percentage, updated
        FROM exit_requests
        WHERE symbol = $1
        ORDER BY strategy ASC
        """,
        symbol.upper(),
    )
    return [dict(row) for row in rows]


async def fetch_exit_by_symbol_and_strategy(
    db_conn: asyncpg.Connection, symbol: str, strategy: str
) -> Dict | None:
    """
    Return a single row by composite key, or None.
    """
    row = await db_conn.fetchrow(
        """
        SELECT symbol, strategy, trim_percentage, updated
        FROM exit_requests
        WHERE symbol = $1 AND strategy = $2
        """,
        symbol.upper(),
        strategy,
    )
    return dict(row) if row else None


async def clear_exit_requests(db_conn: asyncpg.Connection) -> None:
    """
    Completely removes all rows from exit_requests table.
    Fastest method using TRUNCATE.
    """
    await db_conn.execute("TRUNCATE TABLE exit_requests;")


async def update_exit_request(
    db_conn: asyncpg.Connection,
    symbol: str,
    strategy: str,
    trim_percentage: float = 1.0,
) -> Dict:
    """
    Insert or update a single (symbol, strategy) exit request. Updates
    trim_percentage and the updated timestamp on conflict.
    """
    now = datetime.utcnow()
    row = await db_conn.fetchrow(
        """
        INSERT INTO exit_requests (symbol, strategy, trim_percentage, updated)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (symbol, strategy) DO UPDATE
        SET trim_percentage = EXCLUDED.trim_percentage,
            updated = EXCLUDED.updated
        RETURNING symbol, strategy, trim_percentage, updated;
        """,
        symbol.upper(),
        strategy,
        Decimal(str(trim_percentage)),
        now,
    )
    return dict(row)


async def delete_exit_request(
    db_conn: asyncpg.Connection, symbol: str, strategy: str
) -> Dict | None:
    """
    Delete a single (symbol, strategy) row. Returns deleted row or None.
    """
    row = await db_conn.fetchrow(
        """
        DELETE FROM exit_requests
        WHERE symbol = $1 AND strategy = $2
        RETURNING symbol, strategy, trim_percentage, updated;
        """,
        symbol.upper(),
        strategy,
    )
    return dict(row) if row else None


async def delete_exit_requests_by_symbol(
    db_conn: asyncpg.Connection, symbol: str
) -> List[Dict]:
    """
    Delete every row for a symbol (used after a full exit closes the
    position, so leftover strategies don't fire on a re-entered position).
    Returns the list of deleted rows.
    """
    rows = await db_conn.fetch(
        """
        DELETE FROM exit_requests
        WHERE symbol = $1
        RETURNING symbol, strategy, trim_percentage, updated;
        """,
        symbol.upper(),
    )
    return [dict(row) for row in rows]


async def delete_orphan_exit_requests(
    db_conn: asyncpg.Connection, open_symbols: List[str]
) -> List[Dict]:
    """
    Delete every exit_requests row whose symbol is NOT in ``open_symbols``.

    ``open_symbols`` is the set of tickers the IB portfolio currently holds
    a non-zero position for. Any armed exit request outside that set is
    stale (the position it targeted is already closed) and would fire on
    the next re-entry, so it has to go.

    Symbols in ``open_symbols`` are matched case-insensitively.
    Returns the list of deleted rows.
    """
    normalized = [s.upper() for s in open_symbols if s]
    rows = await db_conn.fetch(
        """
        DELETE FROM exit_requests
        WHERE symbol <> ALL($1::text[])
        RETURNING symbol, strategy, trim_percentage, updated;
        """,
        normalized,
    )
    return [dict(row) for row in rows]
