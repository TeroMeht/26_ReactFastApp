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



async def update_auto_order_status(
    db_conn: asyncpg.Connection,
    order_id: int,
    new_status: str
) -> Optional[Dict[str, str]]:

    row = await db_conn.fetchrow(
        """
        UPDATE orders
        SET "Status" = $1
        WHERE "Id" = $2
        RETURNING "Id", "Status", "Symbol";
        """,
        new_status,
        order_id
    )

    if row:
        return {
            "order_id": row["Id"],
            "status": "deactivated",
            "symbol": row["Symbol"]
        }
    return None
