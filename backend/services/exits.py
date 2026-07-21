from db.exits import (
    fetch_exits,
    fetch_exits_by_symbol,
    fetch_exit_by_symbol_and_strategy,
    update_exit_request,
    delete_exit_request,
    delete_orphan_exit_requests,
)
from typing import List, Dict
from schemas.api_schemas import ExitRequestResponse
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
        (p.get("symbol") or "").upper() for p in positions if p.get("symbol")
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
