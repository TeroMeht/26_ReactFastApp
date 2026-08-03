"""
Strategy-based exit flow.

Companion to services.portfolio.flows.exit_manual, which handles the
POST-fill side of the same lifecycle. This module handles the PRE-fill
side: a strategy alarm arrives → look up its armed exit_request row →
place a tagged MKT order → disarm the strategy (delete the DB row).
The STP adjustment fires off the fill via exit_manual.handle_exit_fill.

Public surface:
    process_exit_request  - orchestrator, called by the router
"""

import logging

from services.orders import Order
from services.portfolio.ib_client import IbClient, Position
from services.portfolio.flows.exit_manual import build_exit_ref
from db.exits import (
    fetch_exits_by_symbol,
    delete_exit_request,
    delete_exit_requests_by_symbol,
)
from schemas.api_schemas import (
    ExitRequest,
    ExitRequestResponseIB,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------
def _exit_action(position: Position) -> str:
    """SELL for a long, BUY for a short. Raises on zero position."""
    if position.position > 0:
        return "SELL"
    if position.position < 0:
        return "BUY"
    raise ValueError("Cannot place exit: position is zero")


def _exit_qty(position: Position, trim_percentage: float) -> int:
    """Round(|position| * trim) — how many contracts the exit should touch."""
    return int(round(abs(position.position) * float(trim_percentage)))


# ----------------------------------------------------------------------
# Orchestrator
# ----------------------------------------------------------------------
async def process_exit_request(
    client: IbClient, db_conn, payload: ExitRequest,
) -> ExitRequestResponseIB:
    """
    Process a strategy-triggered exit. Top-to-bottom checklist:
      1. Bail if an exit MKT is already out for this symbol.
      2. Bail if there's no position to exit.
      3. Bail if no exit_request rows exist for this symbol.
      4. Bail if no armed strategy matches the incoming alarm.
      5. Place a tagged MKT for the matched trim, then disarm:
           - trim >= 1.0 -> full exit, delete every row for this symbol
             so leftover strategies don't fire on a re-entered position
           - trim <  1.0 -> partial exit, delete only the fired row

    STP adjustment (cancel on full, resize on partial) is handled off
    the fill event in exit_manual.handle_exit_fill.
    """
    symbol = payload.symbol   # uppercased + validated by ExitRequest schema
    alarm = payload.alarm     # validated against EXIT_TRIGGERS by schema

    logger.info(
        "Received exit request | symbol=%s alarm=%s time=%s",
        symbol, alarm, payload.time,
    )

    try:
        existing_mkt = await client.get_mkt_order_by_symbol(symbol)
        if existing_mkt:
            msg = "MKT order for this exit already exists"
            logger.info("%s | symbol=%s", msg, symbol)
            return ExitRequestResponseIB(symbol=symbol, message=msg)

        position = await client.get_position_by_symbol(symbol)
        if not position:
            msg = "No position to exit"
            logger.info("%s | symbol=%s", msg, symbol)
            return ExitRequestResponseIB(symbol=symbol, message=msg)

        exits_for_symbol = await fetch_exits_by_symbol(db_conn, symbol)
        if not exits_for_symbol:
            msg = "No active exit request for this symbol"
            logger.info("%s | symbol=%s", msg, symbol)
            return ExitRequestResponseIB(symbol=symbol, message=msg)

        matched = next(
            (r for r in exits_for_symbol if r["strategy"] == alarm),
            None,
        )
        if not matched:
            msg = "No matching exit strategy for alarm"
            logger.warning("%s | symbol=%s alarm=%s", msg, symbol, alarm)
            return ExitRequestResponseIB(symbol=symbol, message=msg)

        trim = float(matched["trim_percentage"])
        if not 0.0 < trim <= 1.0:
            raise ValueError(f"Unexpected trim_percentage: {trim}")

        # Place the tagged MKT. Fill bridge picks up the tag and adjusts STP.
        action = _exit_action(position)
        qty = _exit_qty(position, trim)
        order = Order(
            symbol=position.symbol,
            action=action,
            position_size=qty,
            contract_type=position.sectype,
        )
        await client.place_market_order(
            order, order_ref=build_exit_ref(trim),
        )
        logger.info(
            "Exit MKT placed | symbol=%s action=%s qty=%s trim=%s",
            symbol, action, qty, trim,
        )

        # Disarm — full exit clears every strategy for the symbol so
        # leftover rows don't fire on a re-entered position.
        if trim >= 1.0:
            await delete_exit_requests_by_symbol(db_conn, symbol)
        else:
            await delete_exit_request(db_conn, symbol, matched["strategy"])

        return ExitRequestResponseIB(
            symbol=symbol,
            message="Exit MKT placed; STP will be adjusted on fill",
        )

    except Exception:
        # Any unexpected failure: log loudly, return a shaped error so the
        # caller doesn't see None. Same pattern as the entry/add flows.
        logger.exception(
            "Unhandled exception during exit handling | symbol=%s alarm=%s",
            symbol, alarm,
        )
        return ExitRequestResponseIB(
            symbol=symbol,
            message="Unhandled error during exit handling",
        )
