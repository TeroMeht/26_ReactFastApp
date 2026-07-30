"""Streamer PID watchdog.

No network heartbeat — the streamer POSTs its PID once on /start, and
this task asks the OS every 5s whether that PID is still alive. Cheap
local syscall; detects hard kills (e.g. user closing the cmd window)
without any cooperation from the streamer.
"""
import asyncio
import logging

import psutil
from fastapi import FastAPI

from helpers.events import StreamerStatusStore

logger = logging.getLogger(__name__)


def start_streamer_watchdog(app: FastAPI) -> None:
    app.state.status_watchdog = asyncio.create_task(_run_watchdog())
    logger.info("StreamerStatus watchdog started")


async def stop_streamer_watchdog(app: FastAPI) -> None:
    watchdog = getattr(app.state, "status_watchdog", None)
    if watchdog is None:
        return
    watchdog.cancel()
    try:
        await watchdog
    except (asyncio.CancelledError, Exception):
        pass
    logger.info("StreamerStatus watchdog stopped")


async def _run_watchdog() -> None:
    try:
        while True:
            await asyncio.sleep(5)
            snap = StreamerStatusStore.current()
            if snap["status"] != "running":
                continue
            pid = snap.get("pid")
            if pid is None or not psutil.pid_exists(pid):
                logger.info(
                    "Streamer PID %s no longer alive — marking offline",
                    pid,
                )
                StreamerStatusStore.mark_offline()
    except asyncio.CancelledError:
        pass
