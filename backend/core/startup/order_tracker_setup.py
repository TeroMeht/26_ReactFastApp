"""OrderTracker wiring.

Attaches the DB pool, the position ledger, IB event handlers, and the
per-fill fanout for manual-exit Telegram notifications; then seeds the
ledger from IB's current position snapshot and pulls existing open
orders into the tracker.

STP quantity is owned by ``stp_reconciler.reconcile_stp``, subscribed
to ``PositionLedger``. Fill events feed the ledger from
``OrderTracker._on_exec``; the ledger emits ``PositionChanged``; the
reconciler brings the STP into agreement. No other code path modifies
STP quantity in response to a fill.

Must run AFTER connect_ib and init_database (needs both app.state.ib
and app.state.db_pool).
"""
import logging

from fastapi import FastAPI

from services.portfolio.order_tracker import OrderTracker
from services.portfolio.ib_client import IbClient
from services.portfolio.position_ledger import (
    PositionLedger,
    PositionChanged,
)
from services.portfolio.stp_reconciler import reconcile_stp
from services.portfolio.flows.exit import notify_manual_exit_fill_if_relevant

logger = logging.getLogger(__name__)


# Module-level singletons so a single tracker + ledger instance carry
# state across the app. Requests reach the tracker via
# dependencies.get_order_tracker.
order_tracker = OrderTracker()
position_ledger = PositionLedger()


async def wire_order_tracker(app: FastAPI) -> None:
    ib = app.state.ib
    db_pool = app.state.db_pool

    # Attach the pool first so seed/bind writes are persisted.
    order_tracker.set_db_pool(db_pool)
    # Attach the ledger BEFORE binding IB events so the very first fill
    # after startup finds the ledger in place.
    order_tracker.set_position_ledger(position_ledger)

    # Subscribe the STP reconciler to ledger events. It is the sole
    # writer of STP quantity in response to a fill.
    async def _on_position_changed(event: PositionChanged) -> None:
        # A fresh IbClient per event keeps the reconciler self-contained
        # (no long-lived client state to manage) and is cheap — IbClient
        # is a stateless wrapper over the shared ib_async connection.
        client = IbClient(ib, tracker=order_tracker)
        await reconcile_stp(event, client)

    position_ledger.subscribe(_on_position_changed)

    # Manual-exit Telegram notification is orthogonal to STP handling
    # and still hangs off the tracker's Filled-status fanout.
    order_tracker.add_fill_handler(_notify_manual_exit_fill)

    # Seed the ledger from IB's authoritative position snapshot BEFORE
    # binding IB events. This ordering matters: if bind_events came
    # first, a fill delivered before seed_symbol ran would be applied
    # against a zero starting position and then wiped out when
    # seed_symbol overlays it. Seeding first, then binding, guarantees
    # every fill from the listener onwards is a delta on top of the
    # correct baseline.
    try:
        client = IbClient(ib, tracker=order_tracker)
        positions = await client.get_positions()
        for pos in positions or []:
            if pos.symbol and pos.position is not None:
                position_ledger.seed_symbol(pos.symbol, int(pos.position))
        logger.info(
            "PositionLedger seeded with %d symbol(s): %s",
            len(positions or []),
            {s: q for s, q in position_ledger.snapshot().items() if q},
        )
    except Exception:
        logger.exception("PositionLedger seed failed (non-fatal)")

    order_tracker.bind_events(ib)
    await order_tracker.seed(ib)

    app.state.order_tracker = order_tracker
    app.state.position_ledger = position_ledger


def _notify_manual_exit_fill(snap: dict) -> None:
    """Fire manual-exit Telegram on Filled fanout. Safe on every fill;
    no-op unless the permId matches a pending manual exit."""
    try:
        notify_manual_exit_fill_if_relevant(snap)
    except Exception:
        logger.exception(
            "manual-exit notification failed for perm_id=%s",
            snap.get("perm_id"),
        )
