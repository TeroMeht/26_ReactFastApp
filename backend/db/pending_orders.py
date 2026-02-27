from typing import List, Dict, Optional
import asyncpg



async def fetch_active_auto_orders(db_conn:asyncpg.Connection) -> List[Dict]:
    """
    Retrieve all active open orders from the 'orders' table.
    """
    rows = await db_conn.fetch(
        """
        SELECT
            "Id",
            "Symbol",
            "Time",
            "Stop",
            "Date",
            "Status"
        FROM orders
        WHERE "Status" = 'active'
          AND "Date" = CURRENT_DATE
        ORDER BY "Time" ASC;
        """
    )

    return [dict(row) for row in rows]



async def delete_auto_order(db_conn: asyncpg.Connection, order_id: int) -> Optional[Dict[str, str]]:

    row = await db_conn.fetchrow(
        """
        DELETE FROM orders
        WHERE "Id" = $1
        RETURNING "Id", "Status", "Symbol";
        """,
        order_id
    )

    if row:
        return {
            "order_id": row["Id"],
            "status": "deleted",
            "symbol": row["Symbol"]
        }

    return None
