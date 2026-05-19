"""
Live Scanner HTTP surface.

Two endpoints:
  GET /api/live-scanner/status   -> connection + counters JSON
  GET /api/live-scanner/stream   -> SSE stream of LiveScannerUpdate

The manager singleton lives on app.state.live_scanner_manager and is
started/stopped from main.py's lifespan handler.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from services.live_scanner import LiveScannerManager

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/live-scanner",
    tags=["Live Scanner"],
)


def _get_manager(request: Request) -> LiveScannerManager:
    mgr: LiveScannerManager | None = getattr(
        request.app.state, "live_scanner_manager", None
    )
    if mgr is None:
        raise HTTPException(
            status_code=503,
            detail="Live scanner manager not initialized.",
        )
    return mgr


@router.get("/status")
async def status(request: Request) -> dict:
    return _get_manager(request).status()


@router.get("/stream")
async def stream(request: Request):
    """Server-Sent Events stream of LiveScannerUpdate.

    Each event payload is a JSON LiveScannerUpdate. The client should
    keep two pieces of state — gap-up rows and gap-down rows — and
    replace whichever side comes in.
    """
    mgr = _get_manager(request)
    queue = await mgr.hub.add()

    async def event_generator():
        try:
            # Bootstrap: emit current snapshots immediately so the
            # client doesn't have to wait for the next IB push.
            for snap in mgr.current_snapshot():
                yield {"event": "update", "data": snap.model_dump_json()}

            while True:
                if await request.is_disconnected():
                    logger.info("LiveScanner SSE: client disconnect detected")
                    break
                try:
                    update = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield {
                        "event": "update",
                        "data": update.model_dump_json(),
                    }
                except asyncio.TimeoutError:
                    # Heartbeat — keeps proxies/browsers from killing the
                    # connection during quiet periods.
                    yield {"event": "ping", "data": "keep-alive"}
        finally:
            await mgr.hub.remove(queue)

    return EventSourceResponse(event_generator())
