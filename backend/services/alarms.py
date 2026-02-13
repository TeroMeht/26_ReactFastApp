from db.alarms_repo import AlarmRepository
from typing import List, Dict
from pydantic import BaseModel
from datetime import date, time
import logging

logger = logging.getLogger(__name__)


class AlarmResponse(BaseModel):
    Id: int
    Symbol: str
    Time: time
    Alarm: str
    Date: date

class CreateAlarmRequest(BaseModel):
    Symbol: str
    Time: time
    Alarm: str
    Date: date

class AlarmService:
    def __init__(self, db_connection):
        # db_connection is an asyncpg connection, no unpacking needed
        self.repository = AlarmRepository(db_connection)

    async def get_alarms(self) -> List[AlarmResponse]:
        alarms = await self.repository.fetch_alarms()
        logger.info(f"Fetched {len(alarms)} alarms from database.")
        return [AlarmResponse(**alarm) for alarm in alarms]
    
    async def create_alarm(self, request_model: BaseModel) -> Dict:
        # Convert Pydantic model to dict
        alarm_data = request_model.dict()
        logger.info(f"Creating alarm with data: {alarm_data}")
        return await self.repository.insert_alarm(alarm_data)