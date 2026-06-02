"""
Watchlist endpoints — replaces the old file-based /api/tickers router.

Endpoints
---------
GET    /api/watchlist                  list all rows (symbol + bound strategies)
POST   /api/watchlist                  add (or replace strategies of) a ticker
PUT    /api/watchlist/{symbol}         replace the strategy set for a ticker
DELETE /api/watchlist/{symbol}         remove a ticker and its bindings
GET    /api/strategies                 list available entry strategy names

The 22_WatchlistStreamer reads the resulting tables at startup; users restart
the streamer to pick up changes (per the agreed refresh model).
"""
from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from dependencies import get_db_conn
from schemas.api_schemas import (
    StrategiesResponse,
    WatchlistCreateRequest,
    WatchlistRow,
    WatchlistStrategiesRequest,
)
from services import watchlist as watchlist_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api",
    tags=["Watchlist"],
)


# ---------------------------------------------------------------------------
# Strategy list (for the UI's multi-select)
# ---------------------------------------------------------------------------

@router.get("/strategies", response_model=StrategiesResponse)
async def list_strategies():
    """Names of entry strategies the user can bind to a ticker."""
    return StrategiesResponse(
        strategies=watchlist_service.get_available_entry_strategies()
    )


# ---------------------------------------------------------------------------
# Watchlist CRUD
# ---------------------------------------------------------------------------

@router.get("/watchlist", response_model=List[WatchlistRow])
async def read_watchlist(db_conn=Depends(get_db_conn)):
    try:
        return await watchlist_service.list_watchlist(db_conn)
    except Exception as e:
        logger.exception("Failed to list watchlist")
        raise HTTPException(status_code=500, detail=f"Failed to list watchlist: {e}")


@router.post("/watchlist", response_model=WatchlistRow, status_code=201)
async def add_watchlist_entry(
    payload: WatchlistCreateRequest,
    db_conn=Depends(get_db_conn),
):
    """
    Add a brand-new ticker. Returns 409 if the symbol is already in the
    watchlist — use PUT /api/watchlist/{symbol} to update its strategies.
    Pydantic validates strategy names against ENTRY_STRATEGY_NAMES; unknown
    names get rejected with a 422 before reaching this handler.
    """
    try:
        result = await watchlist_service.add_watchlist_entry(
            db_conn, payload.symbol, payload.strategies
        )
        if result is None:
            raise HTTPException(
                status_code=409,
                detail=f"Symbol '{payload.symbol}' is already in the watchlist.",
            )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to add watchlist entry")
        raise HTTPException(status_code=500, detail=f"Failed to add watchlist entry: {e}")


@router.put("/watchlist/{symbol}", response_model=WatchlistRow)
async def replace_strategies(
    symbol: str,
    payload: WatchlistStrategiesRequest,
    db_conn=Depends(get_db_conn),
):
    """Replace the strategy set bound to an existing ticker."""
    try:
        result = await watchlist_service.update_watchlist_strategies(
            db_conn, symbol, payload.strategies
        )
        if result is None:
            raise HTTPException(
                status_code=404,
                detail=f"Symbol '{symbol.upper()}' not in watchlist.",
            )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to update strategies for %s", symbol)
        raise HTTPException(status_code=500, detail=f"Failed to update strategies: {e}")


@router.delete("/watchlist/{symbol}", response_model=WatchlistRow)
async def remove_watchlist_entry(symbol: str, db_conn=Depends(get_db_conn)):
    try:
        result = await watchlist_service.delete_watchlist_entry(db_conn, symbol)
        if result is None:
            raise HTTPException(
                status_code=404,
                detail=f"Symbol '{symbol.upper()}' not in watchlist.",
            )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to delete watchlist entry %s", symbol)
        raise HTTPException(status_code=500, detail=f"Failed to delete watchlist entry: {e}")
