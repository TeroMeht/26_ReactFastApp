from fastapi import APIRouter, Depends, HTTPException, Request
from typing import List
from services.livestream import *
from schemas.api_schemas import CandleRow
from dependencies import get_db_conn
from helpers.events import LivestreamSSEEvent
from sse_starlette.sse import EventSourceResponse
import asyncio
import asyncpg

router = APIRouter(
    prefix="/api/livestream",
    tags=["Livestream data"]
)


@router.get("/latest", response_model=List[CandleRow])
async def get_latest(db_conn=Depends(get_db_conn)):
    """
    One-shot snapshot used as the seed for the SSE stream. The frontend
    calls this once on page load and then opens /api/livestream/stream
    to receive incremental row updates instead of polling.
    """
    try:
        return await fetch_latest_from_db(db_conn)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch latest rows: {str(e)}")


@router.post("/emit")
async def new_livestream_event(event: CandleRow):
    """
    Webhook target for the streamer (22_WatchlistStreamer). The streamer
    POSTs a single CandleRow whenever a symbol's row updates and that row
    is fanned out to every connected SSE client via /stream. Mirrors the
    /api/alarms/emit pattern.
    """
    LivestreamSSEEvent.add_event(event)
    return {"message": "Event added", "count": LivestreamSSEEvent.count()}


@router.get("/stream")
async def stream_livestream(request: Request):
    """
    SSE stream of CandleRow updates. The frontend listens for `message`
    events; each event payload is one row from the streamer. The frontend
    is expected to merge incoming rows by Symbol (last-write-wins) into
    the table it seeded from /latest.
    """

    async def event_generator():
        while True:
            if await request.is_disconnected():
                break

            row = LivestreamSSEEvent.get_event()

            if row is not None:
                yield {
                    "event": "message",
                    "data": row.model_dump_json(),
                }
            else:
                # Heartbeat so reverse proxies don't drop the connection.
                yield {
                    "event": "ping",
                    "data": "keep-alive",
                }

            await asyncio.sleep(1)

    return EventSourceResponse(event_generator())


@router.get("/pricedata", response_model=List[CandleRow])
async def read_pricedata(symbol: str, db_conn=Depends(get_db_conn)):

    try:
        result = await fetch_pricedata_from_db(db_conn, symbol)

        if not result:
            print(f"No candle data found for symbol: {symbol}")
        return result  # empty list if no data

    except asyncpg.exceptions.UndefinedTableError:
        # Table does not exist - treat as "no data"
        print(f"Table for symbol {symbol} does not exist")
        return []

    except Exception as e:
        # Other unexpected errors
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch price data for {symbol}: {str(e)}"
        )
