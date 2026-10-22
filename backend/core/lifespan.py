import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from core.config import settings
from data_sources.ib._client import IBSource, connect, disconnect
from data_sources.ib._config import IBSourceConfig
from core.startup.database import init_database, ensure_schema, close_database
from core.startup.order_tracker_setup import create_order_tracker, wire_order_tracker
from core.startup.ib_client_setup import create_ib_client
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
        # IB connection is owned by the shared data_sources package.
        # app.state.ib_source -> IBSource (for data_sources fetchers)
        # app.state.ib        -> raw IB() used by IbClient / get_ib
        # Host/port come from the shared IBSourceConfig; clientId is
        # per-project, so it stays in 26's settings.
        ib_config = IBSourceConfig()
        app.state.ib_source = IBSource(
            host      = ib_config.IB_HOST,
            port      = ib_config.IB_PORT,
            client_id = settings.IB_CLIENT_ID,
        )
        app.state.ib = await connect(app.state.ib_source)
        await init_database(app)
        await ensure_schema(app)
        create_order_tracker(app)       # tracker + ledger, no IB events yet
        create_ib_client(app)           # the ONE shared IbClient
        await wire_order_tracker(app)   # reconciler, ledger seed, IB events
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
        disconnect(app.state.ib_source)
    except Exception:
        logger.exception("Error during shutdown")


