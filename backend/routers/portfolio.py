from fastapi import APIRouter, Depends, HTTPException
from services.portfolio import PortfolioService
from dependencies import get_ib

from schemas.APIschemas import EntryRequest, ModifyOrderRequest

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
    parent, stop, allowed, msg = await service.process_entry_request(payload.dict())
    return {
        "allowed": allowed,
        "message": msg,
        "symbol": payload.symbol,
        "parentOrderId": parent.orderId if parent else None,
        "stopOrderId": stop.orderId if stop else None,
    }


@router.post("/add-request")
async def add_request(payload: EntryRequest, ib=Depends(get_ib)):
    service = PortfolioService(ib)

    parent, stop, allowed, msg = await service.process_entry_request(payload.dict())
    return {
        "allowed": allowed,
        "message": msg,
        "symbol": payload.symbol,
        "parentOrderId": parent.orderId if parent else None,
        "stopOrderId": stop.orderId if stop else None,
    }

@router.post("/modify-order")
async def modify_order(request: ModifyOrderRequest, ib=Depends(get_ib)):
    """
    Modify the quantity of the first open IB order matching the given symbol.
    """
    ib_service = PortfolioService(ib)

    result = await ib_service.modify_order_quantity_by_symbol(
        symbol=request.symbol,
        new_qty=request.new_quantity
    )

    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=f"No open order found for {request.symbol}")
    elif result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("message"))

    return result