"""Postgres pool + schema bootstrap.

Two responsibilities, deliberately split into two functions:
  - init_database creates the asyncpg pool and publishes it to
    app.state.db_pool. It does not touch schema.
  - ensure_schema runs the idempotent CREATE TABLE IF NOT EXISTS
    calls against that pool.

The split keeps pool construction (a networking/config concern) apart
from schema management (a domain concern), and lets tests or migration
tools reuse just the pieces they need.

close_database drains the pool on shutdown.
"""
import logging

import asyncpg
from fastapi import FastAPI

from core.config import settings
from db.exits import create_exit_requests_table
from db.watchlist import create_watchlist_tables
from db.order_log import create_order_log_table
from db.daily_summary import create_daily_summary_tables

logger = logging.getLogger(__name__)


async def init_database(app: FastAPI) -> None:
    """Create the asyncpg pool and publish it to app.state.db_pool.

    Does not touch schema — call ensure_schema(app) next.
    """
    logger.info("PostgreSQL pool initializing")
    pool = await asyncpg.create_pool(dsn=settings.DATABASE_URL)
    app.state.db_pool = pool


async def ensure_schema(app: FastAPI) -> None:
    """Idempotent CREATE TABLE IF NOT EXISTS ... for every table the app
    owns. Safe to run on every boot; the create_* helpers are no-ops
    when the schema is already in place.

    Depends on app.state.db_pool being set — must run after init_database.
    """
    pool = app.state.db_pool
    async with pool.acquire() as conn:
        await create_exit_requests_table(conn)
        await create_watchlist_tables(conn)
        await create_order_log_table(conn)
        await create_daily_summary_tables(conn)


async def close_database(app: FastAPI) -> None:
    pool = getattr(app.state, "db_pool", None)
    if pool is None:
        return
    await pool.close()
    logger.info("PostgreSQL pool closed")
