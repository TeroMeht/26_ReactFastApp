from fastapi import APIRouter, Depends, HTTPException, Request
import asyncio
from typing import List
from services.livestream import LiveStreamService, LatestRow
from schemas.api_schemas import CandleRow
from dependencies import get_db_conn
from helpers.events import SSEEvent
from sse_starlette.sse import EventSourceResponse

router = APIRouter(
    prefix="/api/livestream",
    tags=["Livestream data"]
)


@router.get("/latest", response_model=List[LatestRow])
async def get_latest(db_conn=Depends(get_db_conn)):
    service = LiveStreamService(db_conn)
    try:
        return await service.fetch_latest_from_db()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch latest rows: {str(e)}")

# URL to receive event
@router.post("/emit")
async def new_event(event: CandleRow):
    SSEEvent.add_event(event)
    return {"message:": "Event added", "count": SSEEvent.count()}



# URL to stream event
@router.get("/stream")
async def stream_events(req:Request):
    async def stream_generator():
        while True:
            if await req.is_disconnected():
                print("SSE Disconnected")
                break
            sse_event = SSEEvent.get_event()
            if sse_event:
                yield "data: {}".format(sse_event.model_dump_json())
                await asyncio.sleep(1)
    return EventSourceResponse(stream_generator())