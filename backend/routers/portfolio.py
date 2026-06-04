import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from typing import List

from services.portfolio.ib_client import IbClient
from services.portfolio.order_tracker import OrderTracker
from services.portfolio.flows.entry import process_entry_request,count_entry_attempts_today_all
from services.portfolio.flows.add import process_add_request
from services.portfolio.flows.exit import process_exit_request
from services.portfolio.flows.open_risk import process_openrisktable


from dependencies import get_ib, get_db_conn, get_order_tracker
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
    LiveOrder,
    CancelOrderResult,
    OrderLogEntry,
)


logger = logging.getLogger(__name__)

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
async def entry_request(
    payload: EntryRequest,
    ib=Depends(get_ib),
    db_conn=Depends(get_db_conn),
    tracker: OrderTracker = Depends(get_order_tracker),
):
    client = IbClient(ib, tracker=tracker)
    return await process_entry_request(client, db_conn, payload)


@router.post("/add-request", response_model=AddRequestResponse)
async def add_request(
    payload: AddRequest,
    ib=Depends(get_ib),
    tracker: OrderTracker = Depends(get_order_tracker),
):
    client = IbClient(ib, tracker=tracker)
    return await process_add_request(client, payload)


@router.post("/exit-request", response_model=ExitRequestResponseIB)
async def exit_request(
    payload: ExitRequest,
    ib=Depends(get_ib),
    db_conn=Depends(get_db_conn),
    tracker: OrderTracker = Depends(get_order_tracker),
):
    client = IbClient(ib, tracker=tracker)
    return await process_exit_request(client, db_conn, payload)


@router.post("/move-stop-be")
async def move_stop_by_symbol(
    symbol: str,
    ib=Depends(get_ib),
    tracker: OrderTracker = Depends(get_order_tracker),
):
    client = IbClient(ib, tracker=tracker)
    return await client.move_stp_order_by_symbol(symbol)


@router.post("/cancel-order/{order_id}", response_model=CancelOrderResult)
async def cancel_order(
    order_id: int,
    ib=Depends(get_ib),
    tracker: OrderTracker = Depends(get_order_tracker),
):
    """
    Cancel an open IB order by permId and *wait* for IB to acknowledge a
    terminal state. The response tells the caller whether the order was
    actually cancelled, or whether it filled before the cancel landed.
    """
    try:
        client = IbClient(ib, tracker=tracker)
        result = await client.cancel_order_by_id(order_id)

        if result.get("status") == "not_found":
            raise HTTPException(
                status_code=404,
                detail=f"No open order found with permId={order_id}",
            )

        return CancelOrderResult(**result)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to cancel order: {str(e)}")


# ----------------------------------------------------------------------
# Live order status — snapshot, SSE stream, bulk cancel
# ----------------------------------------------------------------------
@router.get("/order-status", response_model=List[LiveOrder])
async def get_order_status(tracker: OrderTracker = Depends(get_order_tracker)):
    """Current snapshot of every order the tracker knows about."""
    return [LiveOrder(**row) for row in tracker.snapshot()]


@router.get("/order-log", response_model=List[OrderLogEntry])
async def get_order_log(tracker: OrderTracker = Depends(get_order_tracker)):
    """
    Chronological audit log of every status transition and error attached
    to any order since the backend started. Newest events first.
    """
    return [OrderLogEntry(**row) for row in tracker.event_log()]


@router.get("/order-status/stream")
async def stream_order_status(tracker: OrderTracker = Depends(get_order_tracker)):
    """
    Server-Sent Events stream. On connect we send the current snapshot,
    then push one event per orderStatus / openOrder / error update.

    Event shapes:
      data: {"type": "snapshot", "orders": [...]}
      data: {"type": "update",   "order":  {...}}
      data: {"type": "ping"}                          (every 15s keepalive)
    """
    q = tracker.subscribe()

    async def event_gen():
        try:
            # Initial snapshot so the client paints immediately.
            yield "data: " + json.dumps({
                "type": "snapshot",
                "orders": tracker.snapshot(),
            }) + "\n\n"

            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield "data: " + json.dumps(msg) + "\n\n"
                except asyncio.TimeoutError:
                    # Keepalive so proxies don't drop the connection.
                    yield "data: " + json.dumps({"type": "ping"}) + "\n\n"
        except asyncio.CancelledError:
            logger.debug("SSE client disconnected")
            raise
        finally:
            tracker.unsubscribe(q)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/cancel-all-unfilled", response_model=List[CancelOrderResult])
async def cancel_all_unfilled(
    ib=Depends(get_ib),
    tracker: OrderTracker = Depends(get_order_tracker),
):
    """
    Cancel every currently-open order that has zero fills. Useful as a
    panic button when you want to flatten pending entries quickly.
    """
    try:
        client = IbClient(ib, tracker=tracker)
        results = await client.cancel_all_unfilled()
        return [CancelOrderResult(**r) for r in results]
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to cancel unfilled orders: {str(e)}",
        )


@router.get("/entry-attempts", response_model=EntryAttemptsResponse)
async def get_entry_attempts(ib=Depends(get_ib)):
    """
    Per-symbol entry-attempt stats for today plus the daily total. Only
    symbols with at least one entry attempt today are returned (ordered
    alphabetically). Used by the Trade Manager UI to surface how close each
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
