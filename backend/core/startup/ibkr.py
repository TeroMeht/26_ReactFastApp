"""IBKR connection lifecycle.

Owns the process-wide IB() instance. Publishes it as app.state.ib so
request handlers can reach it via dependencies.get_ib.
"""
import logging

from fastapi import FastAPI
from ib_async import IB

from core.config import settings

logger = logging.getLogger(__name__)


# Module-level singleton. The ib_async IB() constructor is cheap and has
# no side effects; keeping it here (not inside connect_ib) mirrors the
# original layout so tests or ad-hoc scripts can import the same instance
# if needed.
ib = IB()


async def connect_ib(app: FastAPI) -> None:
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
    if ib.isConnected():
        ib.disconnect()
        logger.info("IBKR disconnected")
