from typing import List, Dict, Optional
import asyncpg


async def create_orders_table(db_conn: asyncpg.Connection) -> None:
    """Create the 'orders' table used for auto (DB-driven) pending orders.

    Idempotent: safe to run on every boot. Columns match the SELECT in
    fetch_active_auto_orders (id, symbol, time, stop, date, status).
    """
    await db_conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id      BIGSERIAL PRIMARY KEY,
            symbol  TEXT        NOT NULL,
            time    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            stop    NUMERIC,
            date    DATE        NOT NULL DEFAULT CURRENT_DATE,
            status  TEXT        NOT NULL DEFAULT 'active'
        );
        CREATE INDEX IF NOT EXISTS idx_orders_date_status
            ON orders (date, status);
    """)


async def fetch_active_auto_orders(db_conn:asyncpg.Connection) -> List[Dict]:
    """
    Retrieve all active open orders from the 'orders' table.
    """
    rows = await db_conn.fetch(
        """
        SELECT
            "id",
            "symbol",
            "time",
            "stop",
            "date",
            "status"
        FROM orders
        WHERE "status" = 'active'
          AND "date" = CURRENT_DATE
        ORDER BY "time" ASC;
        """
    )

    return [dict(row) for row in rows]



async def delete_auto_order(db_conn: asyncpg.Connection, order_id: int) -> Optional[Dict[str, str]]:

    row = await db_conn.fetchrow(
        """
        DELETE FROM orders
        WHERE "id" = $1
        RETURNING "id", "status", "symbol";
        """,
        order_id
    )

    if row:
        return {
            "order_id": row["id"],
            "status": "deleted",
            "symbol": row["symbol"]
        }

    return None
