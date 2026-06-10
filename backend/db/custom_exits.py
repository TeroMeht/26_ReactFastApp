"""
Custom (user-defined) price-target exit orders.

A custom exit is a real IB LIMIT order placed on the user's behalf at a
target price for a fraction of their open position. When IB fills it,
the OrderTracker's fill hook calls into services.custom_exits to resize
the symbol's STP (or cancel it on a 100% trim) — same end state the
strategy-based exit flow produces.

Multiple custom exits per symbol are allowed (the user might want to
ladder out: 25% at 740, 50% at 750, etc.), so the PK is a synthetic id
rather than (symbol, strategy) like exit_requests.
"""

from typing import List, Dict, Optional
from datetime import datetime
from decimal import Decimal

import asyncpg


async def create_custom_exit_orders_table(db_conn: asyncpg.Connection) -> None:
    """
    Recreate the custom_exit_orders table on startup. We DROP + CREATE for
    the same reason exit_requests does — the app treats the table as
    ephemeral working state; persistent IB orders are what survive
    restarts anyway, and we reconcile back to them via order_id.
    """
    await db_conn.execute("DROP TABLE IF EXISTS custom_exit_orders;")
    await db_conn.execute(
        """
        CREATE TABLE custom_exit_orders (
            id SERIAL PRIMARY KEY,
            symbol TEXT NOT NULL,
            contract_type TEXT NOT NULL,
            order_id BIGINT NOT NULL,
            perm_id BIGINT,
            target_price NUMERIC(12, 4) NOT NULL,
            trim_percentage NUMERIC(5, 4) NOT NULL,
            action TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'armed',
            created TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
            updated TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
        );
        CREATE INDEX custom_exit_orders_symbol_idx
            ON custom_exit_orders(symbol);
        CREATE INDEX custom_exit_orders_order_id_idx
            ON custom_exit_orders(order_id);
        """
    )


async def insert_custom_exit(
    db_conn: asyncpg.Connection,
    *,
    symbol: str,
    contract_type: str,
    order_id: int,
    perm_id: Optional[int],
    target_price: float,
    trim_percentage: float,
    action: str,
    quantity: int,
) -> Dict:
    now = datetime.utcnow()
    row = await db_conn.fetchrow(
        """
        INSERT INTO custom_exit_orders (
            symbol, contract_type, order_id, perm_id,
            target_price, trim_percentage,
            action, quantity, status, created, updated
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'armed', $9, $9)
        RETURNING id, symbol, contract_type, order_id, perm_id,
                  target_price, trim_percentage, action, quantity,
                  status, created, updated;
        """,
        symbol.upper(),
        contract_type,
        order_id,
        perm_id,
        Decimal(str(target_price)),
        Decimal(str(trim_percentage)),
        action,
        quantity,
        now,
    )
    return dict(row)


async def fetch_custom_exits_by_symbol(
    db_conn: asyncpg.Connection, symbol: str
) -> List[Dict]:
    rows = await db_conn.fetch(
        """
        SELECT id, symbol, contract_type, order_id, perm_id,
               target_price, trim_percentage, action, quantity,
               status, created, updated
        FROM custom_exit_orders
        WHERE symbol = $1
        ORDER BY created ASC
        """,
        symbol.upper(),
    )
    return [dict(r) for r in rows]


async def fetch_custom_exit_by_id(
    db_conn: asyncpg.Connection, id_: int
) -> Optional[Dict]:
    row = await db_conn.fetchrow(
        """
        SELECT id, symbol, contract_type, order_id, perm_id,
               target_price, trim_percentage, action, quantity,
               status, created, updated
        FROM custom_exit_orders
        WHERE id = $1
        """,
        id_,
    )
    return dict(row) if row else None


async def fetch_armed_custom_exit_by_order_id(
    db_conn: asyncpg.Connection, order_id: int
) -> Optional[Dict]:
    """
    Used by the fill listener: given an IB orderId, find a still-armed
    custom exit row that owns it. Returns None if the order isn't ours
    or has already been processed.
    """
    row = await db_conn.fetchrow(
        """
        SELECT id, symbol, contract_type, order_id, perm_id,
               target_price, trim_percentage, action, quantity,
               status, created, updated
        FROM custom_exit_orders
        WHERE order_id = $1 AND status = 'armed'
        """,
        order_id,
    )
    return dict(row) if row else None


async def update_custom_exit_perm_id(
    db_conn: asyncpg.Connection, id_: int, perm_id: int
) -> None:
    await db_conn.execute(
        """
        UPDATE custom_exit_orders
        SET perm_id = $2, updated = (NOW() AT TIME ZONE 'UTC')
        WHERE id = $1
        """,
        id_,
        perm_id,
    )


async def mark_custom_exit_status(
    db_conn: asyncpg.Connection, id_: int, status: str
) -> Optional[Dict]:
    """
    Move a row to a new status ('filled', 'cancelled'). Idempotent —
    returns the updated row or None if id is gone.
    """
    row = await db_conn.fetchrow(
        """
        UPDATE custom_exit_orders
        SET status = $2, updated = (NOW() AT TIME ZONE 'UTC')
        WHERE id = $1
        RETURNING id, symbol, contract_type, order_id, perm_id,
                  target_price, trim_percentage, action, quantity,
                  status, created, updated;
        """,
        id_,
        status,
    )
    return dict(row) if row else None


async def delete_custom_exit_by_id(
    db_conn: asyncpg.Connection, id_: int
) -> Optional[Dict]:
    row = await db_conn.fetchrow(
        """
        DELETE FROM custom_exit_orders
        WHERE id = $1
        RETURNING id, symbol, contract_type, order_id, perm_id,
                  target_price, trim_percentage, action, quantity,
                  status, created, updated;
        """,
        id_,
    )
    return dict(row) if row else None
