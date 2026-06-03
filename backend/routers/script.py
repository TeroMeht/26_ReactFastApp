from fastapi import APIRouter, HTTPException, Request
from services.script import ScriptService
from helpers.events import StreamerStatusStore
from sse_starlette.sse import EventSourceResponse
import asyncio

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


@router.post("/streamer-status/heartbeat")
def streamer_status_heartbeat():
    """
    Heartbeat ping from the streamer (22_WatchlistStreamer). The streamer
    should POST this on a fixed cadence (default 2s); the backend uses the
    last-seen timestamp + StreamerStatusStore.HEARTBEAT_THRESHOLD_SEC to
    derive a running/offline status and pushes any transition into the
    SSE queue.
    """
    return StreamerStatusStore.record_heartbeat()


@router.get("/streamer-status/stream")
async def stream_streamer_status(request: Request):
    """
    SSE stream of status transitions. We send the current snapshot on
    connect so a fresh client paints the dot immediately, then push one
    event per state change. A `ping` event every second keeps the
    connection alive across reverse proxies.
    """

    initial_sent = False

    async def event_generator():
        nonlocal initial_sent
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

            evt = StreamerStatusStore.get_event()
            if evt is not None:
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

    return EventSourceResponse(event_generator())


# Tiny json helper to keep the SSE generator above readable. json.dumps would
# also do; importing at module scope keeps the hot loop branch-free.
def __dumps(obj) -> str:
    import json
    return json.dumps(obj)
