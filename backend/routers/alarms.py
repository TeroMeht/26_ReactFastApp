from fastapi import APIRouter, Depends, HTTPException
from typing import List
from services.alarms import AlarmService, AlarmResponse, CreateAlarmRequest
from dependencies import get_db_conn, release_db_conn

router = APIRouter(
    prefix="/api/alarms",
    tags=["alarms"]
)

@router.get("/", response_model=List[AlarmResponse])
async def read_alarms(db_conn=Depends(get_db_conn)):

    service = AlarmService(db_conn)

    try:
        return await service.get_alarms()
    except Exception as e:
        # Generic error handling, do not import asyncpg here
        raise HTTPException(status_code=500, detail=f"Failed to fetch alarms: {str(e)}")
    finally:
        await release_db_conn(db_conn)


@router.post("/", response_model=AlarmResponse)
async def create_alarm(request: CreateAlarmRequest, db_conn=Depends(get_db_conn)):

    service = AlarmService(db_conn)
    
    try:
        return await service.create_alarm(request)
    except Exception as e:
        # Generic error handling, db-specific exceptions are handled inside repository/service
        raise HTTPException(status_code=500, detail=f"Failed to create alarm: {str(e)}")
    finally:
        await release_db_conn(db_conn)