from db.exits import fetch_exits,fetch_exit_by_symbol,update_exit_request,delete_exit_request
from typing import List,Dict
from schemas.api_schemas import ExitRequestResponse
import logging

logger = logging.getLogger(__name__)




async def get_exits(db_conn)-> List[ExitRequestResponse]:
    exit_requests = await fetch_exits(db_conn)

    logger.info(f"Fetched {len(exit_requests)} exit requests from database.")
    return [ExitRequestResponse(**exit) for exit in exit_requests]


async def get_exit_by_symbol(db_conn, symbol: str) -> ExitRequestResponse | None:
    exit_request = await fetch_exit_by_symbol(db_conn, symbol)

    if not exit_request:
        logger.info(f"No exit request found for symbol '{symbol}'.")
        return None

    return ExitRequestResponse(**exit_request)


async def update_exit_requests(db_conn, symbol: str, requested: bool)-> Dict:

    exit_row = await update_exit_request(db_conn,symbol, requested)
    return {
        "status": "success",
        **exit_row
    }


async def delete_exit_requests(db_conn, symbol: str) -> Dict:
    deleted_row = await delete_exit_request(db_conn, symbol)

    if not deleted_row:
        logger.warning(f"Attempted to delete symbol '{symbol}' but it was not found.")
        return {"status": "not_found", "symbol": symbol.upper()}

    logger.info(f"Deleted exit request for symbol '{symbol}'.")
    return {
        "status": "deleted",
        **deleted_row
    }