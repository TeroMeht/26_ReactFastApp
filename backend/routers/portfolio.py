import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from typing import List
from services.portfolio.ib_client import IbClient, OrderNotFoundError
from services.portfolio.order_tracker import OrderTracker
from services.portfolio.flows.entry import process_entry_request, place_approved_entry
from services.portfolio.trades.trade_log import build_trade_log
from services.portfolio.entry_attempts import build_entry_attempts
from services.portfolio.risk_limits import build_lockout_status
from services.portfolio.flows.add import process_add_request
from services.portfolio.flows.exit import process_automatic_exit
from services.portfolio.flows.open_risk import process_openrisktable
from services.portfolio.openrisk_hub import OpenRiskHub
from services.portfolio.pending_approvals_hub import PendingApprovalsHub
from db.order_log import fetch_order_log


from dependencies import (
    get_ib,
    get_db_conn,
    get_order_tracker,
    get_openrisk_hub,
    get_pending_approvals_hub,
)

from schemas.api_schemas import (
    AddRequest,
    EntryRequestResponse,
    EntryRequest,
    ExitRequest,
    ExitRequestResponseIB,
    OpenPosition,
    AddRequestResponse,
    EntryAttemptsResponse,
    LiveOrder,
    CancelOrderResult,
    OrderLogEntry,
    TradeLogResponse,
    LockoutStatusResponse,
    ApprovalDecisionRequest,
)
from dataclasses import asdict


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/portfolio",
    tags=["Portfolio"],
)


# ----------------------------------------------------------------------
# Read endpoints - thin pass-throughs to IbClient
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
        summary = await client.get_account_summary()
        # Preserve the historical wire shape: `{tag: value}` flat dict, not
        # the wrapping AccountSummary dataclass. Consumers of the endpoint
        # still expect to key by IB tag name.
        return summary.tags
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trades")
async def get_trades(ib=Depends(get_ib)):
    try:
        client = IbClient(ib)
        return await client.get_trades()
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
# Workflow endpoints - call the function-style handlers in services.portfolio

@router.get("/lockout-status", response_model=LockoutStatusResponse)
async def get_lockout_status(ib=Depends(get_ib)):
    # Same aligned handler shape as /trade-log and /entry-attempts:
    # thin call into the service layer, asdict + Pydantic wrap.
    try:
        client = IbClient(ib)
        return LockoutStatusResponse(**asdict(await build_lockout_status(client)))
    except Exception as e:
        logger.exception("lockout-status failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/entry-request", response_model=EntryRequestResponse)
async def entry_request(
    payload: EntryRequest,
    ib=Depends(get_ib),
    tracker: OrderTracker = Depends(get_order_tracker),
    approvals_hub: PendingApprovalsHub = Depends(get_pending_approvals_hub),
):
    """
    Unified entry endpoint.

    - request_type="manual"    -> unchanged historical behaviour: guards
                                  run and, if they pass, the bracket
                                  order is placed immediately.
    - request_type="automatic" -> same guards; on pass, the priced order
                                  is parked in ``approvals_hub`` and
                                  broadcast on /entry-request/pending/stream.
                                  The user's Accept click on the FE modal
                                  hits /entry-request/approve, which does
                                  the actual place_bracket_order.
    Response shape is identical in both cases (parentOrderId/stopOrderId
    are simply None on the automatic path until approval).
    """
    client = IbClient(ib, tracker=tracker)
    return await process_entry_request(client, payload, approvals_hub=approvals_hub)


@router.get("/entry-request/pending/stream")
async def stream_pending_approvals(
    approvals_hub: PendingApprovalsHub = Depends(get_pending_approvals_hub),
):
    """
    Server-Sent Events stream of pending automatic-entry approvals.

    A single global consumer (the FE ApprovalDialog mounted in the root
    layout) subscribes once and pops a confirmation modal for every
    pending row. On connect we send the current snapshot; subsequent
    add/remove events fire as automatic requests are parked and the user
    accepts/declines them.

    Event shapes:
      data: {"type": "snapshot", "pending": [PendingApproval, ...]}
      data: {"type": "add",       "pending": PendingApproval}
      data: {"type": "remove",    "approval_id": "..."}
      data: {"type": "ping"}                                       (every 15s)
    """
    q = approvals_hub.subscribe()

    async def event_gen():
        try:
            yield "data: " + json.dumps(approvals_hub.snapshot_now()) + "\n\n"

            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield "data: " + json.dumps(msg) + "\n\n"
                except asyncio.TimeoutError:
                    yield "data: " + json.dumps({"type": "ping"}) + "\n\n"
        except asyncio.CancelledError:
            logger.debug("Pending-approvals SSE client disconnected")
            raise
        finally:
            approvals_hub.unsubscribe(q)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/entry-request/approve", response_model=EntryRequestResponse)
async def approve_entry_request(
    payload: ApprovalDecisionRequest,
    ib=Depends(get_ib),
    tracker: OrderTracker = Depends(get_order_tracker),
    approvals_hub: PendingApprovalsHub = Depends(get_pending_approvals_hub),
):
    """
    Deliver the user's Accept/Decline for a parked automatic entry.

    Accept  -> pop the row, place the bracket order, return the standard
               EntryRequestResponse (allowed=True with parent/stop order
               IDs on success).
    Decline -> pop the row, return allowed=False with a "declined" note.

    A missing approval_id (already handled, expired, backend restarted)
    returns 404 so the FE can clear its local state.
    """
    approval = await approvals_hub.pop_pending(payload.approval_id)
    if approval is None:
        raise HTTPException(
            status_code=404,
            detail=f"No pending approval with id={payload.approval_id}",
        )

    if payload.decision == "decline":
        logger.info(
            "User declined automatic entry %s for %s",
            approval.approval_id,
            approval.symbol,
        )
        return EntryRequestResponse(
            allowed=False,
            message="Automatic entry declined by user.",
            symbol=approval.symbol,
        )

    client = IbClient(ib, tracker=tracker)
    return await place_approved_entry(client, approval)


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
    return await process_automatic_exit(client, db_conn, payload)


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
        return CancelOrderResult(**result)
    except OrderNotFoundError as e:
        # Domain error from the service layer; translate to 404.
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("cancel-order failed")
        raise HTTPException(status_code=500, detail=str(e))


# ----------------------------------------------------------------------
# Live order status - snapshot, SSE stream, bulk cancel
# ----------------------------------------------------------------------
@router.get("/order-status", response_model=List[LiveOrder])
async def get_order_status(tracker: OrderTracker = Depends(get_order_tracker)):
    """Current snapshot of every order the tracker knows about."""
    try:
        return [LiveOrder(**row) for row in tracker.snapshot()]
    except Exception as e:
        logger.exception("order-status failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trade-log", response_model=TradeLogResponse)
async def get_trade_log(ib=Depends(get_ib)):

    try:
        client = IbClient(ib)
        # asdict() recurses into TradeLogEntry rows so Pydantic can
        # construct the response model from the resulting nested dict.
        return TradeLogResponse(**asdict(await build_trade_log(client)))
    except Exception as e:
        logger.exception("trade-log failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/order-log", response_model=List[OrderLogEntry])
async def get_order_log(
    limit: int = 2000,
    symbol: str | None = None,
    db_conn=Depends(get_db_conn),
):
    try:
        rows = await fetch_order_log(db_conn, limit=limit, symbol=symbol)
        return [OrderLogEntry(**row) for row in rows]
    except Exception as e:
        logger.exception("order-log failed")
        raise HTTPException(status_code=500, detail=str(e))


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
    symbols with at least one attempt today are returned (ordered
    alphabetically). Used by the Trade Manager UI to surface how close each
    symbol is to MAX_ATTEMPTS_PER_SYMBOL_PER_DAY and how close the day is
    to MAX_TOTAL_ENTRIES_PER_DAY.
    """
    try:
        client = IbClient(ib)
        return EntryAttemptsResponse(**asdict(await build_entry_attempts(client)))
    except Exception as e:
        logger.exception("entry-attempts failed")
        raise HTTPException(status_code=500, detail=str(e))


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


@router.get("/open-risk-table/stream")
async def stream_open_risk_table(
    hub: OpenRiskHub = Depends(get_openrisk_hub),
):
    """
    Server-Sent Events stream of the open-risk table. On connect we send
    the current snapshot, then push a new snapshot whenever anything that
    could change the table happens (fills, order updates, NetLiq shifts,
    exit-request arm/disarm). See services.portfolio.openrisk_hub for the
    trigger wiring and debounce logic.

    Event shapes:
      data: {"type": "snapshot", "rows": [OpenPosition, ...]}
      data: {"type": "ping"}                                      (every 15s)
    """
    q = hub.subscribe()

    async def event_gen():
        try:
            # Initial snapshot so the client paints immediately without
            # waiting for the next event.
            initial = await hub.snapshot_now()
            yield "data: " + json.dumps(initial, default=str) + "\n\n"

            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield "data: " + json.dumps(msg, default=str) + "\n\n"
                except asyncio.TimeoutError:
                    # Keepalive so proxies don't drop the connection.
                    yield "data: " + json.dumps({"type": "ping"}) + "\n\n"
        except asyncio.CancelledError:
            logger.debug("Open-risk SSE client disconnected")
            raise
        finally:
            hub.unsubscribe(q)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
