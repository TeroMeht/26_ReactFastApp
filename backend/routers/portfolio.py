from fastapi import APIRouter, Depends, HTTPException
from services.portfolio import PortfolioService
from dependencies import get_ib

from schemas.api_schemas import AddRequest, EntryRequest, ModifyOrderRequest, ModifyOrderByIdRequest

router = APIRouter(
    prefix="/api/portfolio",
    tags=["portfolio"]
)

@router.get("/positions")
async def get_positions(ib=Depends(get_ib)):
    try:
        service = PortfolioService(ib)
        return await service.get_positions()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/orders")
async def get_orders(ib=Depends(get_ib)):
    try:
        service = PortfolioService(ib)
        return await service.get_orders()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/account-summary")
async def get_account_summary(ib=Depends(get_ib)):
    try:
        service = PortfolioService(ib)
        return await service.get_account_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trades")
async def get_trades(ib=Depends(get_ib)):
    try:
        service = PortfolioService(ib)
        return await service.get_trades()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    

@router.get("/price/{symbol}")
async def get_bid_ask_price(symbol: str, ib = Depends(get_ib)):
    """
    Fetch latest bid/ask price snapshot for a symbol.
    Example: /api/portfolio/price/AAPL
    """
    try:
        service = PortfolioService(ib)
        data = await service.get_bid_ask_price(symbol)

        if data is None:
            raise HTTPException(
                status_code=404,
                detail=f"No bid/ask data available for {symbol}"
            )

        return data

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.post("/entry-request")
async def entry_request(payload: EntryRequest, ib=Depends(get_ib)):
    service = PortfolioService(ib)
    parent, stop, allowed, msg = await service.process_entry_request(payload)
    return {
        "allowed": allowed,
        "message": msg,
        "symbol": payload.symbol,
        "parentOrderId": parent.orderId if parent else None,
        "stopOrderId": stop.orderId if stop else None,
    }


@router.post("/add-request")
async def add_request(payload: AddRequest, ib=Depends(get_ib)):
    """
    Process an add request:
    - Only symbol and risk are needed as input
    - Calculates position size, builds and places new order, modifies STP
    - Returns details of the new order and STP modification
    """
    service = PortfolioService(ib)

    # Call the service method
    result = await service.process_add_request(payload)

    # Return response directly
    return {
        "allowed": result.get("allowed", False),
        "message": result.get("message"),
        "symbol": payload.symbol,
        "new_order": result.get("new_order"),           # Order as dict
        "place_result": result.get("place_result"),     # Limit order placement result
        "modified_stp_qty": result.get("modified_stp_qty")  # Updated STP qty
    }




# Temporary endpoint to fetch open STP order for a symbol (used for testing modify flow)

@router.get("/stp-order/{symbol}")
async def get_stp_order(symbol: str, ib=Depends(get_ib)):
    """
    Fetch the first open STP (Stop) order for the given symbol.
    """
    ib_service = PortfolioService(ib)

    result = await ib_service.get_stp_order_by_symbol(symbol)

    if result.get("status") == "not_found":
        raise HTTPException(
            status_code=404,
            detail=f"No open STP order found for {symbol}"
        )
    elif result.get("status") == "error":
        raise HTTPException(
            status_code=500,
            detail=result.get("message")
        )

    return result

@router.post("/modify-order-by-id")
async def modify_order(request: ModifyOrderByIdRequest, ib=Depends(get_ib)):
    """
    Modify the quantity of an open IB order using its orderId.
    """
    ib_service = PortfolioService(ib)

    result = await ib_service.modify_stp_order_by_id(
        order_id=request.order_id,
        new_qty=request.new_quantity
    )

    if result.get("status") == "not_found":
        raise HTTPException(
            status_code=404,
            detail=f"No open order found with orderId {request.order_id}"
        )
    elif result.get("status") == "error":
        raise HTTPException(
            status_code=500,
            detail=result.get("message")
        )

    return result