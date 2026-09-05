"""FastAPI lifespan orchestrator.

Reads top-to-bottom like a checklist. Each subsystem owns its own
startup + shutdown pair in core/startup/*; this file only sequences
them and handles the outer try/except so failures at any stage abort
startup cleanly.

Shutdown runs in reverse dependency order:
  - watchdog first (it holds a running task)
  - live scanner (needs IB alive to unsubscribe cleanly)
  - database (nothing else needs it after this point)
  - IB last (everything downstream of it is already stopped)
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from core.risk_manager_config import risk_settings
from core.startup.ibkr import connect_ib, disconnect_ib
from core.startup.database import init_database, ensure_schema, close_database
from core.startup.order_tracker_setup import wire_order_tracker
from core.startup.openrisk_hub_setup import wire_openrisk_hub
from core.startup.live_scanner import start_live_scanner, stop_live_scanner
from core.startup.streamer_watchdog import (
    start_streamer_watchdog,
    stop_streamer_watchdog,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await connect_ib(app)
        await init_database(app)
        await ensure_schema(app)
        await wire_order_tracker(app)
        await wire_openrisk_hub(app)
        await start_live_scanner(app)   # non-fatal on failure
        start_streamer_watchdog(app)
    except Exception:
        logger.exception("Startup failed")
        raise

    # --- APP RUNS HERE ---
    yield

    # --- SHUTDOWN --- (reverse dependency order)
    try:
        await stop_streamer_watchdog(app)
        await stop_live_scanner(app)
        await close_database(app)
        disconnect_ib(app)
    except Exception:
        logger.exception("Error during shutdown")


