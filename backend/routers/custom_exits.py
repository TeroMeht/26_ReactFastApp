"""
Custom (user-defined) price-target exits.

IB-only — no DB. State of every custom exit comes from IB's open-orders
API, filtered by the CUSTOM_EXIT orderRef tag we attach at placement.
"""

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from dependencies import get_ib, get_order_tracker
from services.portfolio.ib_client import IbClient
from services.portfolio.order_tracker import OrderTracker
from services.custom_exits import (
    place_custom_exit,
    list_custom_exits,
    cancel_custom_exit_by_perm_id,
)
from schemas.api_schemas import CreateCustomExitRequest, CustomExitResponse

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/exits/custom",
    tags=["Custom exits"],
)


@router.get("/{symbol}", response_model=List[CustomExitResponse])
async def get_custom_exits(
    symbol: str,
    ib=Depends(get_ib),
    tracker: OrderTracker = Depends(get_order_tracker),
):
    try:
        client = IbClient(ib, tracker=tracker)
        rows = await list_custom_exits(client, symbol)
        return [CustomExitResponse(**r) for r in rows]
    except Exception as e:
        logger.exception("Failed to list custom exits for %s", symbol)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", response_model=CustomExitResponse)
async def create_custom_exit(
    payload: CreateCustomExitRequest,
    ib=Depends(get_ib),
    tracker: OrderTracker = Depends(get_order_tracker),
):
    try:
        client = IbClient(ib, tracker=tracker)
        row = await place_custom_exit(
            client,
            symbol=payload.symbol,
            target_price=payload.target_price,
            trim_percentage=payload.trim_percentage,
        )
        return CustomExitResponse(**row)
    except ValueError as e:
        # Pre-trade validation failure (no position, qty 0, …)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Failed to place custom exit")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{perm_id}", response_model=dict)
async def cancel_custom_exit_endpoint(
    perm_id: int,
    ib=Depends(get_ib),
    tracker: OrderTracker = Depends(get_order_tracker),
):
    """
    Cancel a custom exit by its IB permId. The frontend reads permId off
    the rows returned by GET /api/exits/custom/{symbol}.
    """
    try:
        client = IbClient(ib, tracker=tracker)
        result = await cancel_custom_exit_by_perm_id(client, perm_id)
        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result.get("message"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to cancel custom exit perm_id=%s", perm_id)
        raise HTTPException(status_code=500, detail=str(e))
