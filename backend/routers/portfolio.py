from fastapi import APIRouter, Depends, HTTPException
from typing import List

from services.portfolio.ib_client import IbClient
from services.portfolio.flows.entry import process_entry_request,count_entry_attempts_today_all
from services.portfolio.flows.add import process_add_request
from services.portfolio.flows.exit import process_exit_request
from services.portfolio.flows.open_risk import process_openrisktable


from dependencies import get_ib, get_db_conn
from core.config import settings

from schemas.api_schemas import (
    AddRequest,
    EntryRequestResponse,
    EntryRequest,
    ExitRequest,
    ExitRequestResponseIB,
    OpenPosition,
    AddRequestResponse,
    EntryAttemptsRow,
    EntryAttemptsResponse,
)

router = APIRouter(
    prefix="/api/portfolio",
    tags=["Portfolio"],
)


# ----------------------------------------------------------------------
# Read endpoints — thin pass-throughs to IbClient
# ----------------------------------------------------------------------
@router.get("/positions")
async def get_positions(ib=Depends(get_ib)):
    try:
        client = IbClient(ib)
        return await client.get_positions()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/orders")
async def get_orders(ib=Depends(get_ib)):
    try:
        client = IbClient(ib)
        return await client.get_orders()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/account-summary")
async def get_account_summary(ib=Depends(get_ib)):
    try:
        client = IbClient(ib)
        return await client.get_account_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trades")
async def get_trades(ib=Depends(get_ib)):
    try:
        client = IbClient(ib)
        return await client.get_trades()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pnl")
async def get_pnl(ib=Depends(get_ib)):
    try:
        client = IbClient(ib)
        return await client.get_trades_with_pnl()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




@router.get("/price/{symbol}")
async def get_bid_ask_price(symbol: str, ib=Depends(get_ib)):
    try:
        client = IbClient(ib)
        return await client.get_bid_ask_price(symbol)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ----------------------------------------------------------------------
# Workflow endpoints — call the function-style handlers in services.portfolio
# ----------------------------------------------------------------------
@router.post("/entry-request", response_model=EntryRequestResponse)
async def entry_request(payload: EntryRequest, ib=Depends(get_ib)):
    client = IbClient(ib)
    return await process_entry_request(client, payload)


@router.post("/add-request", response_model=AddRequestResponse)
async def add_request(payload: AddRequest, ib=Depends(get_ib)):
    client = IbClient(ib)
    return await process_add_request(client, payload)


@router.post("/exit-request", response_model=ExitRequestResponseIB)
async def exit_request(payload: ExitRequest, ib=Depends(get_ib), db_conn=Depends(get_db_conn)):
    client = IbClient(ib)
    return await process_exit_request(client, db_conn, payload)


@router.post("/move-stop-be")
async def move_stop_by_symbol(symbol: str, ib=Depends(get_ib)):
    client = IbClient(ib)
    return await client.move_stp_order_by_symbol(symbol)


@router.post("/cancel-order/{order_id}")
async def cancel_order(order_id: int, ib=Depends(get_ib)):
    try:
        client = IbClient(ib)
        cancelled = await client.cancel_order_by_id(order_id)

        if not cancelled:
            raise HTTPException(
                status_code=404,
                detail=f"No open order found with orderId={order_id}",
            )

        return {"status": "cancelled", "order_id": order_id}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to cancel order: {str(e)}")


@router.get("/entry-attempts", response_model=EntryAttemptsResponse)
async def get_entry_attempts(ib=Depends(get_ib)):
    """
    Per-symbol entry-attempt stats for today plus the daily total. Only
    symbols with at least one entry attempt today are returned (ordered
    alphabetically). Used by the Risk Levels UI to surface how close each
    symbol is to MAX_ATTEMPTS_PER_SYMBOL_PER_DAY and how close the day is
    to MAX_TOTAL_ENTRIES_PER_DAY.
    """
    try:
        client = IbClient(ib)
        counts = await count_entry_attempts_today_all(client)
        max_attempts = settings.MAX_ATTEMPTS_PER_SYMBOL_PER_DAY
        max_total = settings.MAX_TOTAL_ENTRIES_PER_DAY

        rows = [
            EntryAttemptsRow(
                symbol=symbol,
                attempts=count,
                max_attempts=max_attempts,
                remaining=max(0, max_attempts - count),
            )
            for symbol, count in sorted(counts.items())
        ]

        total_attempts = sum(counts.values())

        return EntryAttemptsResponse(
            rows=rows,
            total_attempts=total_attempts,
            max_total=max_total,
            total_remaining=max(0, max_total - total_attempts),
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch entry attempts: {str(e)}",
        )


@router.get("/open-risk-table", response_model=List[OpenPosition])
async def get_open_risk_table(ib=Depends(get_ib), db_conn=Depends(get_db_conn)):
    """
    Fetch the current open risk table for all portfolio positions.
    """
    try:
        client = IbClient(ib)
        return await process_openrisktable(client, db_conn)

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch open risk table: {str(e)}",
        )
