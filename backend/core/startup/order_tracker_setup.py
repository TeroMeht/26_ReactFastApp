"""OrderTracker wiring.

Attaches the DB pool, IB event handlers, and the exit-fill bridge, then
seeds from existing open orders. Must run AFTER connect_ib and
init_database (needs both app.state.ib and app.state.db_pool).
"""
import logging

from fastapi import FastAPI

from services.portfolio.order_tracker import OrderTracker
from services.portfolio.ib_client import IbClient
from services.portfolio.flows.exit_manual import handle_exit_fill, parse_exit_ref

logger = logging.getLogger(__name__)


# Module-level singleton so a single tracker instance carries state across
# the app. Requests reach it via dependencies.get_order_tracker.
order_tracker = OrderTracker()


async def wire_order_tracker(app: FastAPI) -> None:
    ib = app.state.ib
    db_pool = app.state.db_pool

    # Attach the pool first so seed/bind writes are persisted.
    order_tracker.set_db_pool(db_pool)
    order_tracker.add_fill_handler(lambda snap: _exit_on_fill(ib, snap))
    order_tracker.bind_events(ib)
    await order_tracker.seed(ib)

    app.state.order_tracker = order_tracker


async def _exit_on_fill(ib, snap: dict) -> None:
    """Exit fill bridge. When IB reports a fill, look at its orderRef —
    if it carries the EXIT tag (from either the strategy-based exit flow
    or a user-placed custom exit), run the shared STP adjustment logic
    (resize on partial, cancel on 100%). No DB lookup; the tag itself
    encodes the trim percentage.
    """
    trim = parse_exit_ref(snap.get("order_ref"))
    if trim is None:
        return  # not one of our exits
    symbol = snap.get("symbol")
    if not symbol:
        return
    try:
        client = IbClient(ib, tracker=order_tracker)
        await handle_exit_fill(
            client,
            symbol=symbol,
            trim_percentage=trim,
        )
    except Exception:
        logger.exception(
            "exit fill handler failed for symbol=%s perm_id=%s",
            symbol, snap.get("perm_id"),
        )
