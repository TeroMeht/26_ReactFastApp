from fastapi import APIRouter, Depends, HTTPException
from typing import List
from services.exits import ExitService

from dependencies import get_db_conn, release_db_conn
from schemas.api_schemas import UpdateExitRequest

router = APIRouter(
    prefix="/api/exits",
    tags=["exits"]
)

@router.post("/", response_model=dict)
async def update_exit(request: UpdateExitRequest, db=Depends(get_db_conn)):
    service = ExitService(db)  # Use ExitService here
    try:
        # Call the new service method (auto-create table + upsert)
        result = await service.update_exit_request(
            symbol=request.symbol,
            requested=request.requested
        )
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await release_db_conn(db)


# GET all exits
@router.get("/", response_model=List[dict])
async def get_all_exits(db=Depends(get_db_conn)):
    """
    Fetch all exit requests, including updated timestamps.
    """
    service = ExitService(db)
    try:
        return await service.get_all_exits()
    finally:
        await release_db_conn(db)