from typing import List, Dict
from db.livestream_repo import LiveStreamRepository
from schemas.APIschemas import LatestRow
import logging

logger = logging.getLogger(__name__)


    
class LiveStreamService:
    def __init__(self, db_connection):
        self.repo = LiveStreamRepository(db_connection)
        self.latest_rows: Dict[str, LatestRow] = {}  # in-memory cache

    # ---------------- DB Fetch ----------------
    async def fetch_latest_from_db(self, prefix: str = "livestream") -> list[Dict]:
        if not self.repo:
            raise RuntimeError("Database connection not provided")
        tables = await self.repo.fetch_tables(prefix)
        latest_rows = []
        for table in tables:
            row = await self.repo.fetch_last_row(table)
            if row:
                row["TableName"] = table
                latest_rows.append(row)
        return latest_rows

    # ---------------- In-Memory Cache ----------------
    def update_row(self, row: LatestRow):
        self.latest_rows[row.TableName] = row

    def get_latest_row(self, table_name: str) -> LatestRow | None:
        return self.latest_rows.get(table_name)

    def get_all_latest_rows(self) -> list[LatestRow]:
        return list(self.latest_rows.values())