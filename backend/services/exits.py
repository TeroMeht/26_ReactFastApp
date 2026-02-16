from fastapi import HTTPException
from db.exits_repo import ExitRepository

class ExitService:
    """
    Business logic layer for exits_requests.
    """

    def __init__(self, conn):
        self.repo = ExitRepository(conn)
        self._initialized = False

    async def initialize(self):
        if not self._initialized:
            await self.repo.ensure_table_exists()
            self._initialized = True

    async def get_all_exits(self):
        await self.initialize()
        return await self.repo.fetch_exits()


    async def update_exit_request(self, symbol: str, requested: bool):
        await self.initialize()
        exit_row = await self.repo.upsert_exit_request(symbol, requested)
        return {
            "status": "success",
            **exit_row
        }
