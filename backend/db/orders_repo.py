from typing import List, Dict, Optional
import asyncpg

class AutoOrderRepository:
    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn

    async def fetch_active_auto_orders(self) -> List[Dict]:
        """
        Retrieve all active open orders from the 'orders' table.
        """
        rows = await self.conn.fetch(
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
            ORDER BY "Time" ASC;
            """
        )

        return [dict(row) for row in rows]

    async def update_auto_order_status(self,order_id: int,new_status: str) -> int:
        """
        Update order status.
        Returns number of rows updated.
        """
        result = await self.conn.execute(
            """
            UPDATE orders
            SET "Status" = $1
            WHERE "Id" = $2;
            """,
            new_status,
            order_id
        )

        # asyncpg returns string like: "UPDATE 1"
        rows_updated = int(result.split(" ")[1])
        return rows_updated
