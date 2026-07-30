"""LiveScannerManager lifecycle.

Spins up the streaming scanner manager (gap up/down via IB
ScannerSubscription). Non-fatal: if the scanner fails to start, the rest
of the API stays up and the Live Scanner page just shows disconnected.
"""
import logging

from fastapi import FastAPI

from services.live_scanner import LiveScannerManager

logger = logging.getLogger(__name__)


async def start_live_scanner(app: FastAPI) -> None:
    try:
        live_mgr = LiveScannerManager(app.state.ib)
        await live_mgr.start()
        app.state.live_scanner_manager = live_mgr
        logger.info("LiveScannerManager started")
    except Exception:
        logger.exception("LiveScannerManager failed to start (non-fatal)")
        app.state.live_scanner_manager = None


async def stop_live_scanner(app: FastAPI) -> None:
    live_mgr = getattr(app.state, "live_scanner_manager", None)
    if live_mgr is None:
        return
    try:
        await live_mgr.stop()
        logger.info("LiveScannerManager stopped")
    except Exception:
        logger.exception("Error stopping LiveScannerManager")
