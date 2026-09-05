from typing import AsyncGenerator
from fastapi import Request
from ib_async import IB
import asyncpg

from services.portfolio.order_tracker import OrderTracker
from services.portfolio.openrisk_hub import OpenRiskHub


# --- IBKR dependency ---
def get_ib(request: Request) -> IB:
    ib: IB = request.app.state.ib
    return ib


# --- Order tracker dependency ---
def get_order_tracker(request: Request) -> OrderTracker:
    tracker: OrderTracker = request.app.state.order_tracker
    return tracker


# --- Open-risk hub dependency ---
def get_openrisk_hub(request: Request) -> OpenRiskHub:
    hub: OpenRiskHub = request.app.state.openrisk_hub
    return hub


# --- Database dependency ---
async def get_db_conn(request: Request) -> AsyncGenerator[asyncpg.Connection, None]:
    pool: asyncpg.Pool = request.app.state.db_pool

    async with pool.acquire() as conn:
        yield conn