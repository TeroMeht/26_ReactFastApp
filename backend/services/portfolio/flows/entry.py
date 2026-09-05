"""
Entry flow.

One IB executions fetch per request (via TradesSnapshot), pure guards over
the snapshot, then the actual order placement. Public surface:
    process_entry_request  - the orchestrator
    check_* (local)        - block-window / attempts / frequency guards

The loss-cooldown lockouts (check_consecutive_losses, check_loss_cooldown)
and the /lockout-status view live in services.portfolio.risk_limits --
they're total-lockout monitoring, not entry-flow-specific.
"""

import asyncio
import logging
import time as _time
from datetime import datetime, time, timedelta

import pytz

from typing import Optional

from services.orders import (
    BidAsk,
    Order,
    OrderBuilder,
    build_order,
    calculate_entry_price,
    calculate_position_size,
)

from services.portfolio.ib_client import IbClient
from services.portfolio.risk_limits import (
    check_consecutive_losses,
    check_daily_loss,
    check_loss_cooldown,
    enforce_daily_loss_circuit_breaker,
)
from services.portfolio.trades.trades_snapshot import (
    TradesSnapshot,
    build_today_snapshot,
)
from db.entry_log import insert_entry_event

from core.risk_manager_config import risk_settings
from core.config import settings
from schemas.api_schemas import EntryRequest, EntryRequestResponse

logger = logging.getLogger(__name__)







def check_attempts(snapshot: TradesSnapshot, symbol: str) -> tuple[bool, str]:
    attempts = snapshot.attempts_for(symbol)
    max_attempts = risk_settings.MAX_ATTEMPTS_PER_SYMBOL_PER_DAY
    if attempts >= max_attempts:
        msg = (
            f"Max entry attempts reached for {symbol} today "
            f"({attempts}/{max_attempts}). No more entries allowed today."
        )
        logger.info(msg)
        return False, msg
    return True, ""


def check_total_attempts(snapshot: TradesSnapshot) -> tuple[bool, str]:
    total = snapshot.total_attempts()
    max_total = risk_settings.MAX_TOTAL_ENTRIES_PER_DAY
    if total >= max_total:
        msg = (
            f"Max total entries reached for today ({total}/{max_total}). "
            f"No more entries allowed today."
        )
        logger.info(msg)
        return False, msg
    return True, ""


def check_weekly_attempts(snapshot: TradesSnapshot) -> tuple[bool, str]:
    total = snapshot.weekly_entries
    max_total = risk_settings.MAX_TOTAL_ENTRIES_PER_WEEK
    if total >= max_total:
        msg = (f"Max weekly entries reached ({total}/{max_total}). "
               f"No more entries allowed this week.")
        logger.info(msg)
        return False, msg
    return True, ""


def check_frequency(snapshot: TradesSnapshot, symbol: str, current_time: datetime) -> tuple[bool, str]:
    latest = snapshot.latest_fill_for_symbol(symbol)
    if not latest:
        logger.info("No executions found. Entry allowed.")
        return True, ""
    trade_time = latest.time
    if trade_time is None:
        return True, ""
    elapsed = current_time - trade_time
    threshold = timedelta(minutes=risk_settings.MAX_ENTRY_FREQUENCY_MINUTES)
    if elapsed > threshold:
        logger.info(f"Last execution was {elapsed}. Entry allowed.")
        return True, ""
    elapsed_str = str(elapsed).split(".")[0]
    msg = f"Too soon to re-enter. Last execution was {elapsed_str} ago."
    logger.info(msg)
    return False, msg




def check_all_guards(
    snapshot: TradesSnapshot,
    current_time: datetime,
    symbol: str,
) -> EntryRequestResponse:

    ok, message = check_daily_loss(snapshot)
    if not ok:
        return EntryRequestResponse(
            allowed=False, message=message, symbol=symbol,
            reason="daily_loss",
        )

    for ok, message in (
        check_total_attempts(snapshot),
        check_weekly_attempts(snapshot),
        check_attempts(snapshot, symbol),
        check_frequency(snapshot, symbol, current_time),
    ):
        if not ok:
            return EntryRequestResponse(allowed=False, message=message, symbol=symbol)

    for cd_ok, cd_msg, cd_until in (
        check_consecutive_losses(snapshot, current_time),
        check_loss_cooldown(snapshot, current_time),
    ):
        if not cd_ok:
            return EntryRequestResponse(
                allowed=False,
                message=cd_msg,
                symbol=symbol,
                reason="loss_cooldown",
                cooldown_until=cd_until.isoformat() if cd_until else None,
            )

    return EntryRequestResponse(allowed=True, message="Entry allowed", symbol=symbol)


async def process_entry_order(
    client: IbClient,
    payload: EntryRequest,
    db_conn=None,
) -> EntryRequestResponse:

    symbol = payload.symbol
    stop_price = payload.stop_price



    bid_ask = await client.get_bid_ask_price(symbol)



    entry_price = calculate_entry_price(bid_ask, stop_price)
    position_size = calculate_position_size(
        entry_price=entry_price,
        stop_price=stop_price,
        risk=risk_settings.RISK,
    )
    order = build_order(OrderBuilder(
        symbol=symbol,
        entry_price=entry_price,
        stop_price=stop_price,
        position_size=position_size,
        contract_type=payload.contract_type,
    ))

    response = await _place_and_respond(
        client, order, success_message="Entry ok", db_conn=db_conn
    )

    return response


async def _place_and_respond(
    client: IbClient,
    order: Order,
    *,
    success_message: str,
    db_conn=None,
) -> EntryRequestResponse:
    """
    Place a pre-built bracket order and map the IB result onto the
    standard EntryRequestResponse shape.
    """
    parent, stop = await client.place_bracket_order(order)

    if not parent or not stop:
        msg = f"Bracket order placement failed for {order.symbol}"
        logger.error(msg)
        return EntryRequestResponse(
            allowed=False, message=msg, symbol=order.symbol
        )

    if db_conn is not None:
        try:
            await insert_entry_event(db_conn, order.symbol)
        except Exception:
            # The order is already live at IB -- a logging-DB write
            # failure must never fail the order placement response.
            logger.exception(
                "Failed to record entry_log row for %s", order.symbol
            )

    return EntryRequestResponse(
        allowed=True,
        message=success_message,
        symbol=order.symbol,
        parentOrderId=parent.orderId,
        stopOrderId=stop.orderId,
    )

# Main entry flow orchestrator -- the public surface of this module.
async def process_entry_request(client: IbClient, payload: EntryRequest, db_conn=None) -> EntryRequestResponse:

    symbol = payload.symbol
    stop_price = payload.stop_price

    TIMEZONE = pytz.timezone(settings.TIMEZONE)
    current_time = datetime.now(TIMEZONE)

    logger.info(
        f"=== ENTRY REQUEST START === Symbol: {symbol}, "
        f"Requested Stop: {stop_price}"
    )

    try:

        snapshot= await build_today_snapshot(client, db_conn=db_conn)
        validation = check_all_guards(snapshot, current_time, symbol)
        if not validation.allowed:
            if validation.reason == "daily_loss":
                enforce_daily_loss_circuit_breaker(client)
            return validation

        logger.info(f"Entry allowed for {symbol}")

        # Single entry flow: price the order and place the bracket.
        return await process_entry_order(
            client,
            payload,
            db_conn=db_conn,
        )

    except ValueError as e:

        logger.info(
            "Entry request for %s rejected by pricing/sizing: %s", symbol, e
        )
        return EntryRequestResponse(
            allowed=False,
            message=str(e),
            symbol=symbol,
        )
    except Exception as e:
        logger.exception(f"Error processing entry request for {symbol}")
        return EntryRequestResponse(
            allowed=False,
            message=str(e),
            symbol=symbol,
        )
