"""FastAPI application factory.

Kept dumb on purpose: constructs the app, mounts middleware, and includes
routers. All startup/shutdown logic lives in core.lifespan and
core.startup.*; adding a new subsystem should not require changes here.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings
from core.lifespan import lifespan

from routers import (
    watchlist, script, alarms, livestream, portfolio,
    pending_orders, exits, scanner, live_scanner, custom_exits,
    daily_summary,data_streamer
)


def create_app() -> FastAPI:
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
    app.include_router(daily_summary.router)
    app.include_router(live_scanner.router)
    app.include_router(data_streamer.router)
    
    return app
