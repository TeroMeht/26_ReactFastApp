from db.exits import (
    fetch_exits,
    fetch_exits_by_symbol,
    fetch_exit_by_symbol_and_strategy,
    update_exit_request,
    delete_exit_request,
    delete_orphan_exit_requests,
)
from decimal import Decimal
from typing import List, Dict, Optional
from schemas.api_schemas import ExitRequestResponse, ManualExitResponse
from services.portfolio.ib_client import IbClient
import logging

logger = logging.getLogger(__name__)


async def get_exits(db_conn) -> List[ExitRequestResponse]:
    exit_requests = await fetch_exits(db_conn)

    logger.info(f"Fetched {len(exit_requests)} exit requests from database.")
    return [ExitRequestResponse(**exit) for exit in exit_requests]


async def get_exits_by_symbol(db_conn, symbol: str) -> List[ExitRequestResponse]:
    """
    Return every exit_request row for a single symbol. May be empty.
    """
    rows = await fetch_exits_by_symbol(db_conn, symbol)
    return [ExitRequestResponse(**row) for row in rows]


async def get_exit_by_symbol_and_strategy(
    db_conn, symbol: str, strategy: str
) -> ExitRequestResponse | None:
    row = await fetch_exit_by_symbol_and_strategy(db_conn, symbol, strategy)
    if not row:
        logger.info(
            "No exit request found for symbol=%s strategy=%s", symbol, strategy
        )
        return None
    return ExitRequestResponse(**row)


async def update_exit_requests(
    db_conn,
    symbol: str,
    strategy: str,
    trim_percentage: float = 1.0,
) -> Dict:
    exit_row = await update_exit_request(
        db_conn,
        symbol,
        strategy=strategy,
        trim_percentage=trim_percentage,
    )
    return {
        "status": "success",
        **exit_row,
    }


async def delete_exit_requests(db_conn, symbol: str, strategy: str) -> Dict:
    """
    Delete a single (symbol, strategy) row.
    """
    deleted_row = await delete_exit_request(db_conn, symbol, strategy)

    if not deleted_row:
        logger.warning(
            "Attempted to delete symbol='%s' strategy='%s' but no row was found.",
            symbol, strategy,
        )
        return {
            "status": "not_found",
            "symbol": symbol.upper(),
            "strategy": strategy,
        }

    logger.info(
        "Deleted exit request | symbol=%s strategy=%s", symbol, strategy
    )
    return {
        "status": "deleted",
        **deleted_row,
    }


async def list_manual_exits(client: IbClient, symbol: str) -> List[ManualExitResponse]:
    """
    Enumerate every open LIMIT order for a symbol so the manage page can
    show them under Manual Exits — regardless of whether we placed them
    ourselves or they were placed externally (e.g. directly in IB TWS).

    Trim % is derived from the order's quantity as a fraction of the
    open position (None when the position can't be read or is zero).
    """
    orders = await client.get_orders()
    symbol_u = symbol.upper()

    # Pull the position once so we can derive trim per order.
    # Failure to read it (no position, IB hiccup) just leaves trim at None.
    position = None
    try:
        position = await client.get_position_by_symbol(symbol_u)
    except Exception:
        logger.exception("list_manual_exits: failed to read position for %s", symbol_u)

    pos_size = abs(float(position.position)) if position else 0.0

    rows: List[ManualExitResponse] = []
    for o in orders:
        if (o.symbol or "").upper() != symbol_u:
            continue
        if (o.ordertype or "").upper() != "LMT":
            continue

        qty = int(o.totalqty or 0)
        if pos_size > 0 and qty > 0:
            # Clamp to (0, 1] — a LIMIT for more than the position is
            # shown as 100% rather than a misleading >100%.
            trim_val: Optional[Decimal] = Decimal(str(min(1.0, qty / pos_size)))
        else:
            trim_val = None

        rows.append(ManualExitResponse(
            symbol=symbol_u,
            contract_type="",  # not returned by IbClient.get_orders()
            order_id=o.orderid or 0,
            perm_id=o.orderid,
            target_price=Decimal(str(o.lmtprice or 0.0)),
            trim_percentage=trim_val,
            action=o.action or "",
            quantity=qty,
            status=(o.status or "armed").lower(),
        ))
    return rows


async def cancel_manual_exit_by_perm_id(client: IbClient, perm_id: int) -> Dict:
    """Cancel the IB LIMIT order behind a manual exit by its permId."""
    try:
        result = await client.cancel_order_by_id(int(perm_id))
        return {"status": "cancelled", "perm_id": perm_id, "ib_result": result}
    except Exception as e:
        logger.exception("Failed to cancel manual exit perm_id=%s", perm_id)
        return {"status": "error", "perm_id": perm_id, "message": str(e)}


async def reconcile_exit_requests_with_positions(client, db_conn) -> Dict:
    """
    Drop every armed exit_request whose symbol is not currently held in IB.

    Rationale: exit requests are per-position triggers. If a position was
    closed outside the normal exit flow (manual IB cancel, external kill,
    stop-loss fill without our fill-hook running, etc.) the row is stale
    and would re-fire on the next entry for that ticker. This reconciler
    reads the current open positions from IB and truncates every DB row
    whose symbol isn't in that set.

    Returns:
        {
            "status": "success",
            "open_symbols": [...],   # symbols kept
            "deleted": [...],        # rows removed
            "deleted_count": int,
        }
    """
    positions = await client.get_positions()
    open_symbols = [
        (p.symbol or "").upper() for p in positions if p.symbol
    ]
    deleted = await delete_orphan_exit_requests(db_conn, open_symbols)
    logger.info(
        "Reconciled exit_requests | open_positions=%d orphan_rows_deleted=%d",
        len(open_symbols), len(deleted),
    )
    return {
        "status": "success",
        "open_symbols": open_symbols,
        "deleted": deleted,
        "deleted_count": len(deleted),
    }
