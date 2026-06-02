from my_logging.logger import setup_logging
# Set up logging and get a logger
logger = setup_logging(__name__)
logger.info("Application backend starting")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from core.config import settings
from ib_async import IB
import uvicorn
import asyncpg
from db.exits import clear_exit_requests, create_exit_requests_table

# Import routers
from routers import (
    tickers, script, alarms, livestream, portfolio,
    pending_orders, exits, scanner, live_scanner,
)
from services.live_scanner import LiveScannerManager
from services.portfolio.order_tracker import OrderTracker


# Global IBKR object
ib = IB()

# Global order tracker — bound to ib_async events in lifespan
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
            logger.info("exit_requests table ensured")
            await clear_exit_requests(conn)
            logger.info("exit_requests table cleared on startup")

        # Store shared services
        app.state.ib = ib
        app.state.db_pool = db_pool
        app.state.order_tracker = order_tracker

        # Bind order tracker to ib_async events and seed from existing open
        # orders. Done after the IB connection is up so events flow cleanly.
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

    except Exception:
        logger.exception("Startup failed")
        raise

    # --- APP RUNS HERE ---
    yield

    # --- SHUTDOWN ---
    try:
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

app.include_router(tickers.router)
app.include_router(script.router)
app.include_router(alarms.router)
app.include_router(livestream.router)
app.include_router(portfolio.router)
app.include_router(pending_orders.router)
app.include_router(exits.router)
app.include_router(scanner.router)
app.include_router(live_scanner.router)


if __name__ == "__main__":
    uvicorn.run("main:app")
