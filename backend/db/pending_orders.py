from typing import List, Dict, Optional
import asyncpg



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
