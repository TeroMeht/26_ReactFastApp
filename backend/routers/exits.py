from fastapi import APIRouter, Depends, HTTPException
from typing import List
from services.exits import get_exits,update_exit_request,delete_exit_requests

from dependencies import get_db_conn
from schemas.api_schemas import UpdateExitRequest,ExitRequestResponse

router = APIRouter(
    prefix="/api",
    tags=["Exit requests"]
)


# GET all exits
@router.get("/exits", response_model=List[ExitRequestResponse])
async def read_exits(db_conn=Depends(get_db_conn)):
    try:
        return await get_exits(db_conn)
    except Exception as e:
        # Generic error handling, do not import asyncpg here
        raise HTTPException(status_code=500, detail=f"Failed to fetch requested exits: {str(e)}")
    
    

@router.post("/exits", response_model=dict)
async def update_exit(request: UpdateExitRequest, db_conn=Depends(get_db_conn)):

    try:
        # Call the new service method (auto-create table + upsert)
        result = await update_exit_request(
            db_conn,
            symbol=request.symbol,
            requested=request.requested
        )
        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update requested exits: {str(e)}")
    

# DELETE exit by symbol
@router.delete("/{symbol}", response_model=dict)
async def delete_exit(symbol: str, db_conn=Depends(get_db_conn)):
    try:
        result = await delete_exit_requests(db_conn, symbol)
        if result["status"] == "not_found":
            raise HTTPException(status_code=404, detail=f"Symbol '{symbol.upper()}' not found.")
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete exit request: {str(e)}")