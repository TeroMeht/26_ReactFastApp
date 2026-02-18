from typing import List, Dict
import asyncpg
from datetime import datetime


async def fetch_exits(db_conn:asyncpg.Connection) -> List[Dict]:
    rows = await db_conn.fetch(
        f"""
        SELECT symbol, exitrequested, updated
        FROM exit_requests
        ORDER BY Symbol ASC
        """
    )
    return [dict(row) for row in rows]


async def fetch_exit_by_symbol(db_conn: asyncpg.Connection, symbol: str) -> Dict | None:
    row = await db_conn.fetchrow(
        """
        SELECT symbol, exitrequested, updated
        FROM exit_requests
        WHERE symbol = $1
        """,
        symbol.upper()
    )
    return dict(row) if row else None





async def update_exit_request(db_conn:asyncpg.Connection, symbol: str, requested: bool) -> Dict:
    """
    Insert new row if not exists, else update Exitrequested and updated timestamp.
    """
    now = datetime.utcnow()
    row = await db_conn.fetchrow(
        f"""
        INSERT INTO exit_requests (Symbol, Exitrequested, updated)
        VALUES ($1, $2, $3)
        ON CONFLICT (Symbol) DO UPDATE
        SET Exitrequested = EXCLUDED.Exitrequested,
            updated = EXCLUDED.updated
        RETURNING Symbol, Exitrequested, updated;
        """,
        symbol.upper(),
        requested,
        now
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
        RETURNING symbol, exitrequested, updated;
        """,
        symbol.upper()
    )
    return dict(row) if row else None