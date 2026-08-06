"""
Custom (user-defined) price-target exits.

IB-only — no DB. State of every custom exit comes from IB's open-orders
API, filtered by the EXIT orderRef tag we attach at placement.
"""

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from dependencies import get_ib, get_order_tracker
from services.portfolio.ib_client import IbClient
from services.portfolio.order_tracker import OrderTracker
from services.portfolio.flows.exit import process_manual_exit
from services.exits import list_manual_exits, cancel_manual_exit_by_perm_id
from schemas.api_schemas import CreateManualExitRequest, ManualExitResponse

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/exits/custom",
    tags=["Custom exits"],
)


@router.get("/{symbol}", response_model=List[ManualExitResponse])
async def get_custom_exits(
    symbol: str,
    ib=Depends(get_ib),
    tracker: OrderTracker = Depends(get_order_tracker),
):
    try:
        client = IbClient(ib, tracker=tracker)
        return await list_manual_exits(client, symbol)
    except Exception as e:
        logger.exception("Failed to list custom exits for %s", symbol)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", response_model=ManualExitResponse)
async def create_custom_exit(
    payload: CreateManualExitRequest,
    ib=Depends(get_ib),
    tracker: OrderTracker = Depends(get_order_tracker),
):
    try:
        client = IbClient(ib, tracker=tracker)
        return await process_manual_exit(
            client,
            symbol=payload.symbol,
            target_price=payload.target_price,
            trim_percentage=payload.trim_percentage,
        )
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
        result = await cancel_manual_exit_by_perm_id(client, perm_id)
        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result.get("message"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to cancel custom exit perm_id=%s", perm_id)
        raise HTTPException(status_code=500, detail=str(e))
