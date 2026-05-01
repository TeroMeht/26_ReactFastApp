from db.exits import (
    fetch_exits,
    fetch_exits_by_symbol,
    fetch_exit_by_symbol_and_strategy,
    update_exit_request,
    delete_exit_request,
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
