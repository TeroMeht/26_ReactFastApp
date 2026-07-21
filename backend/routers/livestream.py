from fastapi import APIRouter, Depends, HTTPException
from typing import List
from services.livestream import *
from schemas.api_schemas import CandleRow
from dependencies import get_db_conn
import asyncpg
import logging

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/livestream",
    tags=["Livestream data"]
)


@router.get("/latest", response_model=List[CandleRow])
async def get_latest(db_conn=Depends(get_db_conn)):
    """
    Current snapshot of the latest row for every symbol. The frontend
    polls this endpoint on an interval (see RelatrTable.tsx) instead of
    subscribing to a push stream.
    """
    try:
        return await fetch_latest_from_db(db_conn)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch latest rows: {str(e)}")


@router.get("/pricedata", response_model=List[CandleRow])
async def read_pricedata(symbol: str, db_conn=Depends(get_db_conn)):

    try:
        result = await fetch_pricedata_from_db(db_conn, symbol)

        if not result:
            print(f"No candle data found for symbol: {symbol}")
        return result

    except asyncpg.exceptions.UndefinedTableError:
        print(f"Table for symbol {symbol} does not exist")
        return []

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch price data for {symbol}: {str(e)}"
        )
