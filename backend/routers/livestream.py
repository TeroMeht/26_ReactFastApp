from fastapi import APIRouter, Depends, HTTPException
from typing import List
from services.livestream import LiveStreamService, LatestRow
from dependencies import get_db_conn, release_db_conn

router = APIRouter(
    prefix="/api/livestream",
    tags=["livestream"]
)


@router.get("/latest", response_model=List[LatestRow])
async def get_latest(db_conn=Depends(get_db_conn)):
    service = LiveStreamService(db_conn)
    try:
        return await service.fetch_latest_from_db()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch latest rows: {str(e)}")
    finally:
        await release_db_conn(db_conn)



@router.put("/latest", response_model=LatestRow)
async def receive_latest(row: LatestRow):
    """
    Receive the latest row from external software and update in-memory cache.
    """
    try:
        service = LiveStreamService()
        service.update_row(row)
        return row
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update latest row: {str(e)}")