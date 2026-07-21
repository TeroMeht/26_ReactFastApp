from fastapi import APIRouter, Depends, HTTPException
from typing import List
from services.exits import (
    get_exits,
    get_exits_by_symbol,
    update_exit_request,
    delete_exit_requests,
    reconcile_exit_requests_with_positions,
)
from services.portfolio.ib_client import IbClient

from dependencies import get_db_conn, get_ib
from schemas.api_schemas import UpdateExitRequest, ExitRequestResponse

router = APIRouter(
    prefix="/api",
    tags=["Exit requests"]
)


# GET all exits across all symbols
@router.get("/exits", response_model=List[ExitRequestResponse])
async def read_exits(db_conn=Depends(get_db_conn)):
    try:
        return await get_exits(db_conn)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch requested exits: {str(e)}",
        )


# GET all exits for a single symbol
@router.get(
    "/exits/{symbol}",
    response_model=List[ExitRequestResponse],
)
async def read_exits_for_symbol(symbol: str, db_conn=Depends(get_db_conn)):
    try:
        return await get_exits_by_symbol(db_conn, symbol)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch exits for {symbol}: {str(e)}",
        )


@router.post("/exits", response_model=dict)
async def update_exit(request: UpdateExitRequest, db_conn=Depends(get_db_conn)):

    try:
        # Upsert by (symbol, strategy). No 'requested' flag — every row in
        # the table is implicitly armed; users disarm by deleting the row.
        result = await update_exit_request(
            db_conn,
            symbol=request.symbol,
            strategy=request.strategy,
            trim_percentage=float(request.trim_percentage),
        )
        return result

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update requested exits: {str(e)}",
        )


@router.post("/exits/reconcile", response_model=dict)
async def reconcile_exits(
    ib=Depends(get_ib),
    db_conn=Depends(get_db_conn),
):
    """
    Drop any armed exit_requests whose symbol is no longer held in the IB
    portfolio. Meant to be called when the DB drifts out of sync — e.g.
    a position was closed outside the normal exit flow and left an armed
    row behind.
    """
    try:
        client = IbClient(ib)
        return await reconcile_exit_requests_with_positions(client, db_conn)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to reconcile exit requests: {str(e)}",
        )


# DELETE a single (symbol, strategy) exit request row.
@router.delete("/exits/{symbol}/{strategy}", response_model=dict)
async def delete_exit(
    symbol: str, strategy: str, db_conn=Depends(get_db_conn)
):
    try:
        result = await delete_exit_requests(db_conn, symbol, strategy)
        if result["status"] == "not_found":
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No exit request for symbol='{symbol.upper()}' "
                    f"strategy='{strategy}'."
                ),
            )
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete exit request: {str(e)}",
        )
