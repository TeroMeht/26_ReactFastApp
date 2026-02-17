from db.exits import fetch_exits,update_exit_request
from typing import List,Dict
from schemas.api_schemas import ExitRequestResponse
import logging

logger = logging.getLogger(__name__)




async def get_exits(db_conn)-> List[ExitRequestResponse]:
    exit_requests = await fetch_exits(db_conn)

    logger.info(f"Fetched {len(exit_requests)} exit requests from database.")
    return [ExitRequestResponse(**exit) for exit in exit_requests]



async def update_exit_requests(db_conn, symbol: str, requested: bool)-> Dict:

    exit_row = await update_exit_request(db_conn,symbol, requested)
    return {
        "status": "success",
        **exit_row
    }
