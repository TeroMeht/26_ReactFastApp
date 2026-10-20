import logging
from zoneinfo import ZoneInfo

from fastapi import FastAPI

from core.config import settings
from services.portfolio.ib_client import IbClient

logger = logging.getLogger(__name__)


def create_ib_client(app: FastAPI) -> None:
    app.state.ib_client = IbClient(
        app.state.ib,
        tracker=app.state.order_tracker,
        tz=ZoneInfo(settings.TIMEZONE),
    )
    logger.info("Shared IbClient created")
