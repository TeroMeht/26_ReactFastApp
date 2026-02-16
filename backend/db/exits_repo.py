from typing import List, Dict
import asyncpg
from fastapi import HTTPException
from datetime import datetime

class ExitRepository:
    """
    Handles direct database access for exits_requests table.
    Automatically creates the table if it doesn't exist.
    """

    TABLE_NAME = "exits_requests"

    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn

    async def ensure_table_exists(self):
        """
        Create table if it doesn't exist, with updated timestamp.
        """
        await self.conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.TABLE_NAME} (
                Symbol TEXT PRIMARY KEY,
                Exitrequested BOOLEAN DEFAULT FALSE,
                updated TIMESTAMP DEFAULT NOW()
            );
            """
        )

    async def fetch_exits(self) -> List[Dict]:
        rows = await self.conn.fetch(
            f"""
            SELECT Symbol, Exitrequested, updated
            FROM {self.TABLE_NAME}
            ORDER BY Symbol ASC
            """
        )
        return [dict(row) for row in rows]

    async def upsert_exit_request(self, symbol: str, requested: bool) -> Dict:
        """
        Insert new row if not exists, else update Exitrequested and updated timestamp.
        """
        now = datetime.utcnow()
        row = await self.conn.fetchrow(
            f"""
            INSERT INTO {self.TABLE_NAME} (Symbol, Exitrequested, updated)
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
