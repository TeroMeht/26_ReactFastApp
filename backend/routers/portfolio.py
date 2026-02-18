from fastapi import APIRouter, Depends, HTTPException
from services.portfolio import PortfolioService
from dependencies import get_ib,get_db_conn
from typing import Optional,List

from schemas.api_schemas import AddRequest, EntryRequestResponse, EntryRequest, ExitRequest, ExitRequestResponseIB,ModifyOrderRequest, ModifyOrderByIdRequest,PortfolioPositionModel, AddRequestResponse

router = APIRouter(
    prefix="/api/portfolio",
    tags=["Portfolio"]
)

@router.get("/positions")
async def get_positions(ib=Depends(get_ib),db_conn=Depends(get_db_conn)):
    try:
        service = PortfolioService(ib,db_conn)
        return await service.get_positions()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/orders")
async def get_orders(ib=Depends(get_ib),db_conn=Depends(get_db_conn)):
    try:
        service = PortfolioService(ib,db_conn)
        return await service.get_orders()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/account-summary")
async def get_account_summary(ib=Depends(get_ib),db_conn=Depends(get_db_conn)):
    try:
        service = PortfolioService(ib,db_conn)
        return await service.get_account_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trades")
async def get_trades(ib=Depends(get_ib),db_conn=Depends(get_db_conn)):
    try:
        service = PortfolioService(ib,db_conn)
        return await service.get_trades()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    

@router.get("/price/{symbol}")
async def get_bid_ask_price(symbol: str, ib = Depends(get_ib),db_conn=Depends(get_db_conn)):
    """
    Fetch latest bid/ask price snapshot for a symbol.
    Example: /api/portfolio/price/AAPL
    """
    try:
        service = PortfolioService(ib,db_conn)
        data = await service.get_bid_ask_price(symbol)

        return data

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




@router.post("/entry-request", response_model=EntryRequestResponse)
async def entry_request(payload: EntryRequest,ib=Depends(get_ib),db_conn=Depends(get_db_conn)):
    service = PortfolioService(ib,db_conn)
    return await service.process_entry_request(payload)


@router.post("/add-request", response_model=AddRequestResponse)
async def add_request(payload: AddRequest,ib=Depends(get_ib),db_conn=Depends(get_db_conn)):
    service = PortfolioService(ib,db_conn)
    return await service.process_add_request(payload)


@router.post("/exit-request", response_model=ExitRequestResponseIB)
async def exit_request(payload: ExitRequest, ib=Depends(get_ib),db_conn=Depends(get_db_conn)):
    service = PortfolioService(ib,db_conn)
    return await service.process_exit_request(payload)


@router.post("/move-stop-be")
async def move_stop_by_symbol(symbol: str, ib=Depends(get_ib),db_conn=Depends(get_db_conn)):
    service = PortfolioService(ib,db_conn)
    if not symbol:
        raise HTTPException(status_code=400, detail="Symbol is required in the request body")
    return await service.move_stp_order_by_symbol(symbol)

 





@router.post("/cancel-order/{order_id}")
async def cancel_order(order_id: int, ib=Depends(get_ib),db_conn=Depends(get_db_conn)):
    try:
        service = PortfolioService(ib,db_conn)
        cancelled = await service.cancel_order_by_id(order_id)

        if not cancelled:
            raise HTTPException(status_code=404, detail=f"No open order found with orderId={order_id}")

        return {"status": "cancelled", "order_id": order_id}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to cancel order: {str(e)}")


@router.get("/open-risk-table", response_model=List[PortfolioPositionModel])
async def get_open_risk_table(ib=Depends(get_ib),db_conn=Depends(get_db_conn)):
    """
    Fetch the current open risk table for all portfolio positions.
    """
    try:
        ib_service = PortfolioService(ib,db_conn)
        return await ib_service.process_openrisktable()

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch open risk table: {str(e)}"
        )



# Temporary endpoint to fetch open STP order for a symbol (used for testing modify flow)

# @router.get("/stp-order/{symbol}")
# async def get_stp_order(symbol: str, ib=Depends(get_ib),db_conn=Depends(get_db_conn)):
#     """
#     Fetch the first open STP (Stop) order for the given symbol.
#     """
#     ib_service = PortfolioService(ib,db_conn)

#     return await ib_service.get_stp_order_by_symbol(symbol)


# @router.post("/modify-order-by-id")
# async def modify_order(request: ModifyOrderByIdRequest, ib=Depends(get_ib),db_conn=Depends(get_db_conn)):
#     """
#     Modify the quantity of an open IB order using its orderId.
#     """
#     ib_service = PortfolioService(ib,db_conn)
#     return await ib_service.modify_stp_order_by_id(
#         order_id=request.order_id,
#         new_qty=request.new_quantity
#     )