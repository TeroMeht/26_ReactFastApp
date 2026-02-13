# routers/portfolio_router.py
from fastapi import APIRouter, Depends, HTTPException
from services.portfolio import PortfolioService
from dependencies import get_ib

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
    

