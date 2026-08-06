"""OrderTracker wiring.

Attaches the DB pool, IB event handlers, and the exit-fill bridge, then
seeds from existing open orders. Must run AFTER connect_ib and
init_database (needs both app.state.ib and app.state.db_pool).
"""
import logging

from fastapi import FastAPI

from services.portfolio.order_tracker import OrderTracker
from services.portfolio.ib_client import IbClient
from services.portfolio.flows.exit import (
    handle_exit_fill,
    notify_manual_exit_fill_if_relevant,
)

logger = logging.getLogger(__name__)


# Module-level singleton so a single tracker instance carries state across
# the app. Requests reach it via dependencies.get_order_tracker.
order_tracker = OrderTracker()


async def wire_order_tracker(app: FastAPI) -> None:
    ib = app.state.ib
    db_pool = app.state.db_pool

    # Attach the pool first so seed/bind writes are persisted.
    order_tracker.set_db_pool(db_pool)
    order_tracker.add_fill_handler(lambda snap: _sync_stp_on_fill(ib, snap))
    order_tracker.bind_events(ib)
    await order_tracker.seed(ib)

    app.state.order_tracker = order_tracker


async def _sync_stp_on_fill(ib, snap: dict) -> None:
    """Fill bridge. Runs on every filled order (entries, adds, exits,
    external TWS trades, and the STP itself). Delegates to
    handle_exit_fill, which keeps the protective STP in sync with the
    current position — resize to remaining, or cancel when flat.
    """
    symbol = snap.get("symbol")
    if not symbol:
        return
    # Telegram-notify manual-exit fills before STP sync so a slow STP
    # query never delays the user seeing the fill. Automatic exits are
    # notified at placement time (see process_automatic_exit) since MKTs
    # fill too fast to reliably register between placement and fill.
    try:
        notify_manual_exit_fill_if_relevant(snap)
    except Exception:
        logger.exception(
            "manual-exit notification failed for perm_id=%s",
            snap.get("perm_id"),
        )
    try:
        client = IbClient(ib, tracker=order_tracker)
        await handle_exit_fill(client, symbol=symbol)
    except Exception:
        logger.exception(
            "STP sync failed for symbol=%s perm_id=%s",
            symbol, snap.get("perm_id"),
        )
