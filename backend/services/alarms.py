from db.alarms import fetch_alarms,insert_alarm
from typing import List,Dict
from schemas.api_schemas import AlarmResponse,CreateAlarmRequest
import logging

logger = logging.getLogger(__name__)




async def get_alarms(db_conn) -> List[AlarmResponse]:
    alarms = await fetch_alarms(db_conn)

    logger.info(f"Fetched {len(alarms)} alarms from database.")
    return [AlarmResponse(**alarm) for alarm in alarms]



async def put_alarm_to_db(db_conn, request_model: CreateAlarmRequest) -> Dict:
    # Convert Pydantic model to dict
    alarm_data = request_model.model_dump()  # Pydantic v2

    logger.info(f"Creating alarm with data: {alarm_data}")
    return await insert_alarm(db_conn,alarm_data)