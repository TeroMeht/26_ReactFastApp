from db.alarms_repo import AlarmRepository
from typing import List, Dict
from schemas.api_schemas import AlarmResponse,CreateAlarmRequest

import logging

logger = logging.getLogger(__name__)




class AlarmService:
    def __init__(self, db_connection):
        # db_connection is an asyncpg connection, no unpacking needed
        self.repository = AlarmRepository(db_connection)

    async def get_alarms(self) -> List[AlarmResponse]:
        alarms = await self.repository.fetch_alarms()
        logger.info(f"Fetched {len(alarms)} alarms from database.")
        return [AlarmResponse(**alarm) for alarm in alarms]
    
    async def create_alarm(self, request_model: CreateAlarmRequest) -> Dict:
        # Convert Pydantic model to dict
        alarm_data = request_model.dict()
        logger.info(f"Creating alarm with data: {alarm_data}")
        return await self.repository.insert_alarm(alarm_data)