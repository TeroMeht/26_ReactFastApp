

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
from db.exits import clear_exit_requests,create_exit_requests_table
from services.fills import FillsTracker

# Import routers
from routers import tickers, script, alarms, livestream, portfolio, pending_orders, exits,scanner, auto_assist


# Global IBKR object
ib = IB()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_pool = None

    try:
        # --- IBKR startup ---
        logger.info("Connecting to IBKR | host=%s port=%s clientId=%s",
                    settings.IB_HOST, settings.IB_PORT, settings.IB_CLIENT_ID)

        await ib.connectAsync(
            settings.IB_HOST,
            settings.IB_PORT,
            clientId=settings.IB_CLIENT_ID
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

        # Hook the fills tracker into IB events at startup so orderStatus /
        # execDetails / commissionReport updates are captured from the very
        # first order onward (not lazily on the first HTTP hit, which would
        # miss any cancels or fills that happened before the UI loaded).
        # reqAutoOpenOrders(True) makes IB stream status updates for orders
        # placed / cancelled from other clients (e.g. TWS) to this session.
        try:
            tracker = FillsTracker.get(ib)
            tracker.enable_auto_open_orders()
            logger.info("FillsTracker initialised at startup")
        except Exception:
            logger.exception("Failed to initialise FillsTracker at startup")

    except Exception:
        logger.exception("Startup failed")
        raise

    # --- APP RUNS HERE ---
    yield

    # --- SHUTDOWN ---
    try:
        await app.state.db_pool.close()
        logger.info("PostgreSQL pool closed")

        if ib.isConnected():
            ib.disconnect()
            logger.info("IBKR disconnected")

    except Exception:
        logger.exception("Error during shutdown")

# --- App instance ---
app = FastAPI(
    title="Trade Review App",
    description="API to manage trade data and show charts",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,  # <--- set lifespan instead of on_event
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
app.include_router(auto_assist.router)


if __name__ == "__main__":

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)