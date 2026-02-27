from fastapi import APIRouter, Depends, HTTPException, Request
from typing import List
from services.livestream import *
from schemas.api_schemas import LatestRow, CandleRow
from dependencies import get_db_conn
import asyncpg

router = APIRouter(
    prefix="/api/livestream",
    tags=["Livestream data"]
)


@router.get("/latest", response_model=List[LatestRow])
async def get_latest(db_conn=Depends(get_db_conn)):
    try:
        return await fetch_latest_from_db(db_conn)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch latest rows: {str(e)}")



@router.get("/pricedata", response_model=List[CandleRow])
async def read_pricedata(symbol: str, db_conn=Depends(get_db_conn)):

    try:
        result = await fetch_pricedata_from_db(db_conn, symbol)
        print(result)
        if not result:
            print(f"No candle data found for symbol: {symbol}")
        return result  # empty list if no data

    except asyncpg.exceptions.UndefinedTableError:
        # Table does not exist → treat as "no data"
        print(f"Table for symbol {symbol} does not exist")
        return []

    except Exception as e:
        # Other unexpected errors
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch price data for {symbol}: {str(e)}"
        )
