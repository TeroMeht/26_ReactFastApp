from typing import List, Dict
import asyncpg
from decimal import Decimal



async def fetch_tables(db_conn:asyncpg.Connection, prefix: str) -> List[str]:

    # Use ILIKE for case-insensitive matching
    search_pattern = f"%{prefix}%"
    rows = await db_conn.fetch(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
            AND table_type = 'BASE TABLE'
            AND table_name ILIKE $1;
        """,
        search_pattern
    )
    return [row['table_name'] for row in rows]


async def fetch_last_row(db_conn: asyncpg.Connection, table_name: str) -> Dict:
    row = await db_conn.fetchrow(
        f"""
        SELECT *
        FROM "{table_name}"
        ORDER BY "Date" DESC, "Time" DESC
        LIMIT 1;
        """
    )
    if not row:
        return None

    row_dict = dict(row)

    # Convert NaN to None (only if DB can return NaN)
    for k, v in row_dict.items():
        if isinstance(v, Decimal) and v.is_nan():
            row_dict[k] = None

    return row_dict


async def fetch_pricedata_by_symbol(db_conn: asyncpg.Connection, table_name: str, symbol: str) -> List[Dict]:
    """
    Fetch all rows from a specific table filtered by symbol, ordered by Date + Time ascending.
    """
    rows = await db_conn.fetch(
        f"""
        SELECT *
        FROM "{table_name}"
        WHERE "Symbol" = $1
        ORDER BY "Date" ASC, "Time" ASC;
        """,
        symbol
    )
    return [dict(row) for row in rows]