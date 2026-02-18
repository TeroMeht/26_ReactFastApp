

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
import dependencies
import asyncpg


# Import routers
from routers import tickers, script, alarms, livestream, portfolio, pending_orders, exits


# Global IBKR object
ib = IB()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_pool = None

    try:
        # --- IBKR startup ---
        await ib.connectAsync(
            settings.IB_HOST,
            settings.IB_PORT,
            clientId=settings.IB_CLIENT_ID
        )

        # --- Setup PostgreSQL pool ---
        db_pool = await asyncpg.create_pool(dsn=settings.DATABASE_URL)
        await dependencies.setup_dependencies(ib, db_pool)


        yield  # app runs here

    except Exception as e:
        logger.exception("Error during startup: %s", e)
        raise

    finally:
        # --- IBKR shutdown ---
        if db_pool:
            await db_pool.close()
            logger.info("PostgreSQL pool closed")
        ib.disconnect()
        logger.info("IBKR disconnected")

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

if __name__ == "__main__":

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)