from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from services.script import ScriptService
from helpers.events import StreamerStatusStore
from sse_starlette.sse import EventSourceResponse
import asyncio


class StreamerStartPayload(BaseModel):
    pid: Optional[int] = None

router = APIRouter(
    prefix="/api",
    tags=["Scripts"]
)


@router.post("/run-script")
def run_script():
    service = ScriptService()
    try:
        output = service.run_script()
        return {"output": output}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/streamer-status")
def streamer_status():
    """
    One-shot status probe used as the seed for the SSE stream. The
    response now reflects the heartbeat-driven state in
    StreamerStatusStore rather than a psutil scan, so the frontend can
    paint the dot immediately and switch over to /streamer-status/stream
    for ongoing updates.
    """
    return StreamerStatusStore.current()


@router.post("/streamer-status/start")
def streamer_status_start(payload: StreamerStartPayload):
    """
    Streamer launch signal. Stores the streamer's PID so the backend
    watchdog can detect a hard close (e.g. user closing the cmd window).
    Flips the dot green. Idempotent.
    """
    return StreamerStatusStore.mark_running(pid=payload.pid)


@router.post("/streamer-status/stop")
def streamer_status_stop():
    """
    Fast-path shutdown signal for clean exits. Not relied on — the
    backend watchdog also detects death via psutil.pid_exists.
    """
    return StreamerStatusStore.mark_offline()


@router.get("/streamer-status/stream")
async def stream_streamer_status(request: Request):
    """
    SSE stream of status transitions. We send the current snapshot on
    connect so a fresh client paints the dot immediately, then push one
    event per state change. Each connection has its own subscription
    queue so multiple clients (sidebar, watchlist panel, other tabs)
    all see every transition.
    """

    q = StreamerStatusStore.subscribe()
    initial_sent = False

    async def event_generator():
        nonlocal initial_sent
        try:
            while True:
                if await request.is_disconnected():
                    break

                if not initial_sent:
                    snapshot = StreamerStatusStore.current()
                    initial_sent = True
                    yield {
                        "event": "message",
                        "data": __dumps(snapshot),
                    }
                    await asyncio.sleep(1)
                    continue

                if q:
                    evt = q.popleft()
                    yield {
                        "event": "message",
                        "data": __dumps(evt),
                    }
                else:
                    yield {
                        "event": "ping",
                        "data": "keep-alive",
                    }

                await asyncio.sleep(1)
        finally:
            StreamerStatusStore.unsubscribe(q)

    return EventSourceResponse(event_generator())


# Tiny json helper to keep the SSE generator above readable.
def __dumps(obj) -> str:
    import json
    return json.dumps(obj)
