from fastapi import APIRouter, Depends, HTTPException
from typing import List
from services.alarms import  AlarmResponse, CreateAlarmRequest, get_alarms,put_alarm_to_db
from dependencies import get_db_conn

router = APIRouter(
    prefix="/api",
    tags=["Alarms"]
)

@router.get("/alarms", response_model=List[AlarmResponse])
async def read_alarms(db_conn=Depends(get_db_conn)):

    try:
        return await get_alarms(db_conn)
    except Exception as e:
        # Generic error handling, do not import asyncpg here
        raise HTTPException(status_code=500, detail=f"Failed to fetch alarms: {str(e)}")



@router.post("/alarms", response_model=AlarmResponse)
async def create_alarm(request: CreateAlarmRequest, db_conn=Depends(get_db_conn)):

    try:
        return await put_alarm_to_db(db_conn,request)
    except Exception as e:
        # Generic error handling, db-specific exceptions are handled inside repository/service
        raise HTTPException(status_code=500, detail=f"Failed to create alarm: {str(e)}")
