"""
Auto Assist router.

Exposes:
- POST /api/auto-assist/start    -> start a per-symbol live session
- POST /api/auto-assist/stop     -> stop the per-symbol live session
- GET  /api/auto-assist/stream   -> SSE stream of ticks / bars / levels / signal
                                    (follows the alarms /stream pattern)
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from dependencies import get_ib
from schemas.api_schemas import AutoAssistStartRequest
from services.auto_assist import get_session, start_session, stop_session

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/auto-assist",
    tags=["Auto Assist"],
)


@router.post("/start")
async def start(payload: AutoAssistStartRequest, ib=Depends(get_ib)):
    try:
        session = await start_session(ib, payload.symbol)
        return {"status": "started", "symbol": session.symbol}
    except Exception as e:
        logger.exception("Failed to start auto-assist session")
        raise HTTPException(status_code=500, detail=f"Failed to start: {e}")


@router.post("/stop")
async def stop(payload: AutoAssistStartRequest):
    ok = stop_session(payload.symbol)
    return {
        "status": "stopped" if ok else "not_running",
        "symbol": payload.symbol.upper(),
    }


@router.get("/stream")
async def stream(request: Request, symbol: str = Query(..., min_length=1)):
    session = get_session(symbol)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"No running auto-assist session for {symbol.upper()}",
        )

    queue = session.add_subscriber()

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=10.0)
                    yield {"event": "message", "data": json.dumps(msg, default=str)}
                except asyncio.TimeoutError:
                    # heartbeat (keeps the SSE connection alive through proxies)
                    yield {"event": "ping", "data": "keep-alive"}
        finally:
            session.remove_subscriber(queue)

    return EventSourceResponse(event_generator())
