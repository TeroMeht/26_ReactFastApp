from typing import Optional
from ib_async import IB
import asyncpg

# Global instances
_ib_instance: Optional[IB] = None
_db_pool: Optional[asyncpg.pool.Pool] = None

# --- IBKR dependency ---
def get_ib() -> IB:
    if _ib_instance is None:
        raise RuntimeError("IB instance not initialized")
    return _ib_instance

def set_ib_instance(ib: IB):
    global _ib_instance
    _ib_instance = ib

# --- PostgreSQL async dependencies ---
async def get_db_conn() -> asyncpg.Connection:
    """Acquire a connection from the pool."""
    if _db_pool is None:
        raise RuntimeError("Database pool not initialized")
    return await _db_pool.acquire()

async def release_db_conn(conn: asyncpg.Connection):
    """Release a connection back to the pool."""
    if _db_pool is None:
        raise RuntimeError("Database pool not initialized")
    await _db_pool.release(conn)

async def set_database_pool(pool: asyncpg.pool.Pool):
    global _db_pool
    _db_pool = pool

# --- Combined setup ---
async def setup_dependencies(ib: IB, db_pool: asyncpg.pool.Pool):
    set_ib_instance(ib)
    await set_database_pool(db_pool)
