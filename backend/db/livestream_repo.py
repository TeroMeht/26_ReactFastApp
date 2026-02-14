from typing import List, Dict
import asyncpg

class LiveStreamRepository:
    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn

    async def fetch_tables(self, prefix: str) -> List[str]:

        # Use ILIKE for case-insensitive matching
        search_pattern = f"%{prefix}%"
        rows = await self.conn.fetch(
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

    async def fetch_last_row(self, table_name: str) -> Dict:
        """Fetch the latest row from a specific table ordered by Date + Time descending."""
        row = await self.conn.fetchrow(
            f"""
            SELECT *
            FROM "{table_name}"
            ORDER BY "Date" DESC, "Time" DESC
            LIMIT 1;
            """
        )
        return dict(row) if row else None
