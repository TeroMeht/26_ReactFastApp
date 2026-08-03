"""
Order log persistence.

The OrderTracker keeps a transient in-memory event log for SSE and current-
session use. This module mirrors every event to PostgreSQL so the /order-log
page can show the full history across application restarts.

Schema is created with CREATE TABLE IF NOT EXISTS and is NEVER truncated on
startup — the order log is intended as a permanent audit trail.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

import asyncpg


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

async def create_order_log_table(db_conn: asyncpg.Connection) -> None:
    """
    Idempotent table + index creation. Called once at startup. Existing
    rows are preserved across restarts so the order log accumulates a
    permanent audit history.
    """
    await db_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS order_log (
            id              BIGSERIAL PRIMARY KEY,
            ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            perm_id         BIGINT NOT NULL DEFAULT 0,
            order_id        BIGINT NOT NULL DEFAULT 0,
            symbol          TEXT,
            action          TEXT,
            order_type      TEXT,
            total_qty       DOUBLE PRECISION DEFAULT 0,
            lmt_price       DOUBLE PRECISION,
            aux_price       DOUBLE PRECISION,
            status          TEXT,
            filled          DOUBLE PRECISION DEFAULT 0,
            remaining       DOUBLE PRECISION DEFAULT 0,
            avg_fill_price  DOUBLE PRECISION DEFAULT 0,
            last_error      TEXT,
            last_error_code INTEGER
        );
        """
    )
    await db_conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_order_log_ts
            ON order_log (ts DESC);
        """
    )
    await db_conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_order_log_perm
            ON order_log (perm_id);
        """
    )


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def _ts_to_datetime(ts) -> datetime:
    """Accept unix-epoch float/int or datetime; return a tz-aware datetime."""
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(tz=timezone.utc)


async def insert_order_log_event(
    db_conn: asyncpg.Connection, entry: Dict
) -> None:
    """
    Persist one event row. Accepts the same dict shape OrderTracker builds
    for its in-memory log (unix-epoch float `ts`).

    Dedup lives at the caller: OrderTracker._log_event guards on its
    in-memory `_last_logged_status` before scheduling the write, so we
    never see two consecutive rows with identical status for the same
    order under normal operation. Doing a defensive SELECT here would
    double the DB round trips per event (a real cost under active
    trading) to catch a couple of edge cases (startup re-seed, repeated
    error callbacks) that at worst leave a few duplicate rows in an
    audit table. That trade isn't worth it.
    """
    await db_conn.execute(
        """
        INSERT INTO order_log (
            ts, perm_id, order_id, symbol, action, order_type,
            total_qty, lmt_price, aux_price, status,
            filled, remaining, avg_fill_price,
            last_error, last_error_code
        )
        VALUES (
            $1, $2, $3, $4, $5, $6,
            $7, $8, $9, $10,
            $11, $12, $13,
            $14, $15
        );
        """,
        _ts_to_datetime(entry.get("ts")),
        int(entry.get("perm_id") or 0),
        int(entry.get("order_id") or 0),
        entry.get("symbol"),
        entry.get("action"),
        entry.get("order_type"),
        float(entry.get("total_qty") or 0),
        entry.get("lmt_price"),
        entry.get("aux_price"),
        entry.get("status"),
        float(entry.get("filled") or 0),
        float(entry.get("remaining") or 0),
        float(entry.get("avg_fill_price") or 0),
        entry.get("last_error"),
        entry.get("last_error_code"),
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

async def fetch_order_log(
    db_conn: asyncpg.Connection,
    limit: int = 2000,
    symbol: Optional[str] = None,
) -> List[Dict]:
    """
    Return persisted order-log events, newest first. `ts` is converted to a
    unix-epoch float so the response matches the existing OrderLogEntry
    schema and the frontend doesn't need to change.
    """
    if symbol:
        rows = await db_conn.fetch(
            """
            SELECT ts, perm_id, order_id, symbol, action, order_type,
                   total_qty, lmt_price, aux_price, status,
                   filled, remaining, avg_fill_price,
                   last_error, last_error_code
            FROM order_log
            WHERE symbol = $1
            ORDER BY ts DESC, id DESC
            LIMIT $2;
            """,
            symbol.upper(),
            limit,
        )
    else:
        rows = await db_conn.fetch(
            """
            SELECT ts, perm_id, order_id, symbol, action, order_type,
                   total_qty, lmt_price, aux_price, status,
                   filled, remaining, avg_fill_price,
                   last_error, last_error_code
            FROM order_log
            ORDER BY ts DESC, id DESC
            LIMIT $1;
            """,
            limit,
        )

    out: List[Dict] = []
    for row in rows:
        d = dict(row)
        ts_val = d.get("ts")
        if isinstance(ts_val, datetime):
            d["ts"] = ts_val.timestamp()
        out.append(d)
    return out

