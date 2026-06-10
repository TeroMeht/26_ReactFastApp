from my_logging.logger import setup_logging
# Set up logging and get a logger
logger = setup_logging(__name__)
logger.info("Application backend starting")

import asyncio
import psutil
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from core.config import settings
from ib_async import IB
import uvicorn
import asyncpg
from db.exits import clear_exit_requests,create_exit_requests_table
from db.watchlist import create_watchlist_tables
from db.order_log import create_order_log_table
from helpers.events import StreamerStatusStore

# Import routers
from routers import (
    watchlist, script, alarms, livestream, portfolio,
    pending_orders, exits, scanner, live_scanner, custom_exits,
)
from services.live_scanner import LiveScannerManager
from services.portfolio.order_tracker import OrderTracker
from services.portfolio.ib_client import IbClient
from services.custom_exits import handle_custom_exit_fill, parse_order_ref


# Global IBKR object
ib = IB()

# Global order tracker - bound to ib_async events in lifespan
order_tracker = OrderTracker()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_pool = None

    try:
        # --- IBKR startup ---
        logger.info(
            "Connecting to IBKR | host=%s port=%s clientId=%s",
            settings.IB_HOST, settings.IB_PORT, settings.IB_CLIENT_ID,
        )

        await ib.connectAsync(
            settings.IB_HOST,
            settings.IB_PORT,
            clientId=settings.IB_CLIENT_ID,
        )
        logger.info("Creating DB pool")

        db_pool = await asyncpg.create_pool(dsn=settings.DATABASE_URL)

        # ENSURE TABLE EXISTS, THEN CLEAN ON STARTUP
        async with db_pool.acquire() as conn:
            await create_exit_requests_table(conn)
            await create_watchlist_tables(conn)
            await create_order_log_table(conn)

        # Store shared services
        app.state.ib = ib
        app.state.db_pool = db_pool
        app.state.order_tracker = order_tracker

        # Bind order tracker to ib_async events and seed from existing open
        # orders. Done after the IB connection is up so events flow cleanly.
        # Attach the pool first so seed/bind writes are persisted.
        order_tracker.set_db_pool(db_pool)

        # Custom-exit fill bridge. When IB reports a fill, look at its
        # orderRef — if it carries the CUSTOM_EXIT tag we placed, run the
        # same STP-adjustment logic the strategy-based exit flow does
        # (resize on partial, cancel on 100%). No DB lookup; the tag
        # itself encodes the trim percentage.
        async def _custom_exit_on_fill(snap: dict) -> None:
            trim = parse_order_ref(snap.get("order_ref"))
            if trim is None:
                return  # not a custom-exit order
            symbol = snap.get("symbol")
            if not symbol:
                return
            try:
                client = IbClient(ib, tracker=order_tracker)
                await handle_custom_exit_fill(
                    client,
                    symbol=symbol,
                    trim_percentage=trim,
                    filled_qty=int(snap.get("filled") or snap.get("total_qty") or 0),
                )
            except Exception:
                logger.exception(
                    "custom-exit fill handler failed for symbol=%s perm_id=%s",
                    symbol, snap.get("perm_id"),
                )

        order_tracker.add_fill_handler(
            lambda snap: _custom_exit_on_fill(snap)
        )

        order_tracker.bind_events(ib)
        await order_tracker.seed(ib)

        # Spin up the live streaming scanner manager (gap up/down via IB
        # ScannerSubscription). Failures here are non-fatal -- the rest of
        # the API stays up; the Live Scanner page just shows disconnected.
        try:
            live_mgr = LiveScannerManager(ib)
            await live_mgr.start()
            app.state.live_scanner_manager = live_mgr
            logger.info("LiveScannerManager started")
        except Exception:
            logger.exception("LiveScannerManager failed to start (non-fatal)")
            app.state.live_scanner_manager = None

        # Streamer-status watchdog. No network heartbeat — the streamer
        # POSTs its PID once on /start, and this task asks the OS whether
        # that PID is still alive every 5s. Cheap local syscall; detects
        # hard kills (e.g. user closing the cmd window) without any
        # cooperation from the streamer.
        async def _status_watchdog():
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

        app.state.status_watchdog = asyncio.create_task(_status_watchdog())
        logger.info("StreamerStatus watchdog started")

    except Exception:
        logger.exception("Startup failed")
        raise

    # --- APP RUNS HERE ---
    yield

    # --- SHUTDOWN ---
    try:
        watchdog = getattr(app.state, "status_watchdog", None)
        if watchdog is not None:
            watchdog.cancel()
            try:
                await watchdog
            except (asyncio.CancelledError, Exception):
                pass
            logger.info("StreamerStatus watchdog stopped")

        live_mgr = getattr(app.state, "live_scanner_manager", None)
        if live_mgr is not None:
            try:
                await live_mgr.stop()
                logger.info("LiveScannerManager stopped")
            except Exception:
                logger.exception("Error stopping LiveScannerManager")

        await app.state.db_pool.close()
        logger.info("PostgreSQL pool closed")

        if ib.isConnected():
            ib.disconnect()
            logger.info("IBKR disconnected")

    except Exception:
        logger.exception("Error during shutdown")


# --- App instance ---
app = FastAPI(
    title="TradeApp",
    description="API to manage trades",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(watchlist.router)
app.include_router(script.router)
app.include_router(alarms.router)
app.include_router(livestream.router)
app.include_router(portfolio.router)
app.include_router(pending_orders.router)
# custom_exits MUST be registered before exits: both share the /api/exits
# prefix, and exits.py declares DELETE /api/exits/{symbol}/{strategy}, which
# otherwise greedily matches DELETE /api/exits/custom/{id} (interpreting
# "custom" as the symbol).
app.include_router(custom_exits.router)
app.include_router(exits.router)
app.include_router(scanner.router)
app.include_router(live_scanner.router)


if __name__ == "__main__":
    uvicorn.run("main:app")
