"""OpenRiskHub wiring.

Instantiates the hub (once, module-level) and wires it to every event that
should trigger a rebuild:
  - IB execDetailsEvent  → a fill landed
  - IB openOrderEvent    → an order was placed / modified
  - IB accountValueEvent → NetLiquidation moved (allocation column drifts)
  - OrderTracker fill handler → belt-and-braces for the fill path (goes
    through the same debounced notify)

Exit-request arm / disarm is triggered separately from the exits router
after the DB mutation commits.

Must run AFTER connect_ib, init_database, and wire_order_tracker (needs
app.state.ib, app.state.db_pool, app.state.order_tracker).
"""
import asyncio
import logging

from fastapi import FastAPI

from services.portfolio.openrisk_hub import OpenRiskHub

logger = logging.getLogger(__name__)


async def wire_openrisk_hub(app: FastAPI) -> None:
    ib = app.state.ib
    db_pool = app.state.db_pool
    tracker = app.state.order_tracker

    hub = OpenRiskHub(ib=ib, db_pool=db_pool)
    hub.bind_loop(asyncio.get_running_loop())

    # ib_async fires these events synchronously from the socket callback.
    # notify() is safe from sync context — it just schedules a debounced
    # rebuild on the bound loop.
    ib.execDetailsEvent += hub.notify
    ib.openOrderEvent += hub.notify
    ib.accountValueEvent += hub.notify

    # OrderTracker's fill handler is redundant with execDetailsEvent but
    # cheap (both feed the same debounce) and covers the case where the
    # execDetailsEvent binding somehow misses.
    tracker.add_fill_handler(lambda _snap: hub.notify())

    app.state.openrisk_hub = hub
    logger.info("OpenRiskHub wired to IB events + OrderTracker")
