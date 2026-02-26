from fastapi import APIRouter, Depends, HTTPException,Request
from typing import List
from services.alarms import  AlarmResponse, get_alarms
from dependencies import get_db_conn
from helpers.events import SSEEvent
from sse_starlette.sse import EventSourceResponse
import asyncio



router = APIRouter(
    prefix="/api/alarms",
    tags=["Alarms"]
)

@router.get("/alarms", response_model=List[AlarmResponse])
async def read_alarms(db_conn=Depends(get_db_conn)):

    try:
        return await get_alarms(db_conn)
    except Exception as e:
        # Generic error handling, do not import asyncpg here
        raise HTTPException(status_code=500, detail=f"Failed to fetch alarms: {str(e)}")

# URL to receive event
@router.post("/emit")
async def new_event(event: AlarmResponse):
    SSEEvent.add_event(event)
    return {"message:": "Event added", "count": SSEEvent.count()}


@router.get("/stream")
async def stream_events(request: Request):

    async def event_generator():
        while True:
            if await request.is_disconnected():
                print("SSE Disconnected")
                break

            sse_event = SSEEvent.get_event()

            if sse_event:
                yield {
                    "event": "message",
                    "data": sse_event.model_dump_json(),
                }
            else:
                # 🔥 HEARTBEAT (VERY IMPORTANT)
                yield {
                    "event": "ping",
                    "data": "keep-alive",
                }

            await asyncio.sleep(1)

    return EventSourceResponse(event_generator())