"""HTTP surface for runtime-derived toggles (see `services.runtime_settings`).

Read-only: the values are computed from wall-clock time on each request,
so there is no setter. The UI uses this to display the current mode
above the pending-orders table.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from services.runtime_settings import get_extended_hours_stop_enabled


router = APIRouter(
    prefix="/api/runtime-settings",
    tags=["Runtime settings"],
)


class ExtendedHoursStopState(BaseModel):
    enabled: bool


@router.get("/extended-hours-stop", response_model=ExtendedHoursStopState)
async def read_extended_hours_stop() -> ExtendedHoursStopState:
    return ExtendedHoursStopState(enabled=get_extended_hours_stop_enabled())
