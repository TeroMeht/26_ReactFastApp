"""Persistent log of entry placements. One row per successful bracket-order
entry placed via place_bracket_order. Used by check_weekly_attempts and
the entry-attempts view."""
import asyncpg
from datetime import datetime
from typing import Optional


async def create_entry_log_table(db_conn: asyncpg.Connection) -> None:
    await db_conn.execute("""
        CREATE TABLE IF NOT EXISTS entry_log (
            id      BIGSERIAL PRIMARY KEY,
            ts      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            symbol  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_entry_log_ts ON entry_log (ts);
    """)


async def insert_entry_event(db_conn: asyncpg.Connection, symbol: str) -> None:
    await db_conn.execute("INSERT INTO entry_log (symbol) VALUES ($1)", symbol)


async def count_entries_since(db_conn: asyncpg.Connection, since: datetime) -> int:
    row = await db_conn.fetchval(
        "SELECT COUNT(*) FROM entry_log WHERE ts >= $1", since
    )
    return int(row or 0)
