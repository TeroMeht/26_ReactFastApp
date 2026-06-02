from typing import AsyncGenerator
from fastapi import Request
from ib_async import IB
import asyncpg

from services.portfolio.order_tracker import OrderTracker


# --- IBKR dependency ---
def get_ib(request: Request) -> IB:
    ib: IB = request.app.state.ib
    return ib


# --- Order tracker dependency ---
def get_order_tracker(request: Request) -> OrderTracker:
    tracker: OrderTracker = request.app.state.order_tracker
    return tracker


# --- Database dependency ---
async def get_db_conn(request: Request) -> AsyncGenerator[asyncpg.Connection, None]:
    pool: asyncpg.Pool = request.app.state.db_pool

    async with pool.acquire() as conn:
        yield conn