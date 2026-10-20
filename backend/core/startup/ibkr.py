"""IBKR connection lifecycle.

Creates the process-wide IB() instance and publishes it as app.state.ib.
app.state is the only way to reach it:
  - dependencies.get_ib_client -> anything touching orders/positions
  - dependencies.get_ib        -> raw connection, market data only
"""
import logging

from fastapi import FastAPI
from ib_async import IB

from core.config import settings

logger = logging.getLogger(__name__)


async def connect_ib(app: FastAPI) -> None:
    ib = IB()
    logger.info(
        "Connecting to IBKR | host= %s port= %s clientId= %s",
        settings.IB_HOST, settings.IB_PORT, settings.IB_CLIENT_ID,
    )
    await ib.connectAsync(
        settings.IB_HOST,
        settings.IB_PORT,
        clientId=settings.IB_CLIENT_ID,
    )
    app.state.ib = ib


def disconnect_ib(app: FastAPI) -> None:
    ib = getattr(app.state, "ib", None)
    if ib is not None and ib.isConnected():
        ib.disconnect()
        logger.info("IBKR disconnected")
