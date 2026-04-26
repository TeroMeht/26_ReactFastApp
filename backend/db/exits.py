from typing import List, Dict
import asyncpg
from datetime import datetime
from decimal import Decimal


async def create_exit_requests_table(db_conn: asyncpg.Connection) -> None:
    """
    Create the exit_requests table if it doesn't already exist.
    """
    await db_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS exit_requests (
            symbol TEXT PRIMARY KEY,
            exitrequested BOOLEAN NOT NULL DEFAULT FALSE,
            trim_percentage NUMERIC(5, 4) NOT NULL DEFAULT 1.0,
            updated TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
        );
        """
    )





async def fetch_exits(db_conn: asyncpg.Connection) -> List[Dict]:
    rows = await db_conn.fetch(
        f"""
        SELECT symbol, exitrequested, trim_percentage, updated
        FROM exit_requests
        ORDER BY Symbol ASC
        """
    )
    return [dict(row) for row in rows]


async def fetch_exit_by_symbol(db_conn: asyncpg.Connection, symbol: str) -> Dict | None:
    row = await db_conn.fetchrow(
        """
        SELECT symbol, exitrequested, trim_percentage, updated
        FROM exit_requests
        WHERE symbol = $1
        """,
        symbol.upper()
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
    requested: bool,
    trim_percentage: float = 1.0,
) -> Dict:
    """
    Insert new row if not exists, else update Exitrequested, trim_percentage and updated timestamp.
    """
    now = datetime.utcnow()
    row = await db_conn.fetchrow(
        f"""
        INSERT INTO exit_requests (Symbol, Exitrequested, trim_percentage, updated)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (Symbol) DO UPDATE
        SET Exitrequested = EXCLUDED.Exitrequested,
            trim_percentage = EXCLUDED.trim_percentage,
            updated = EXCLUDED.updated
        RETURNING Symbol, Exitrequested, trim_percentage, updated;
        """,
        symbol.upper(),
        requested,
        Decimal(str(trim_percentage)),
        now,
    )
    return dict(row)


async def delete_exit_request(db_conn: asyncpg.Connection, symbol: str) -> Dict | None:
    """
    Delete row by symbol. Returns deleted row or None if not found.
    """
    row = await db_conn.fetchrow(
        """
        DELETE FROM exit_requests
        WHERE symbol = $1
        RETURNING symbol, exitrequested, trim_percentage, updated;
        """,
        symbol.upper()
    )
    return dict(row) if row else None
