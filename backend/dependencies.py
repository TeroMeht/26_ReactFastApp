from typing import AsyncGenerator
from fastapi import Request
from ib_async import IB
import asyncpg


# --- IBKR dependency ---
def get_ib(request: Request) -> IB:
    ib: IB = request.app.state.ib
    return ib


# --- Database dependency ---
async def get_db_conn(request: Request) -> AsyncGenerator[asyncpg.Connection, None]:
    pool: asyncpg.Pool = request.app.state.db_pool

    async with pool.acquire() as conn:
        yield conn