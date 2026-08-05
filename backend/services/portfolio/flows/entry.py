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

import logging
from datetime import datetime, time, timedelta

import pytz

from typing import Optional

from services.orders import (
    Order,
    OrderBuilder,
    build_order,
    calculate_entry_price,
    calculate_position_size,
)
from services.portfolio.ib_client import IbClient
from services.portfolio.pending_approvals_hub import (
    PendingApproval,
    PendingApprovalsHub,
)
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

from core.risk_manager_config import risk_settings
from core.config import settings
from schemas.api_schemas import EntryRequest, EntryRequestResponse

logger = logging.getLogger(__name__)




def check_block_window(now: datetime) -> tuple[bool, str]:
    risk = risk_settings
    first_entry = time(risk.FIRST_ENTRY_HOUR, risk.FIRST_ENTRY_MINUTE)
    if now.time() < first_entry:
        msg = (
            f"Entry blocked before {first_entry.strftime('%H:%M')} "
            f"(current time: {now.strftime('%H:%M')})."
        )
        logger.info(msg)
        return False, msg
    return True, ""


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




async def _place_and_respond(
    client: IbClient,
    order: Order,
    *,
    success_message: str,
) -> EntryRequestResponse:
    """
    Place a pre-built bracket order and map the IB result onto the
    standard EntryRequestResponse shape.

    Both the manual path in ``process_entry_request`` and the
    post-approval call in ``place_approved_entry`` end here so the
    success/failure translation lives in one place.
    """
    parent, stop = await client.place_bracket_order(order)

    if not parent or not stop:
        msg = f"Bracket order placement failed for {order.symbol}"
        logger.error(msg)
        return EntryRequestResponse(
            allowed=False, message=msg, symbol=order.symbol
        )

    return EntryRequestResponse(
        allowed=True,
        message=success_message,
        symbol=order.symbol,
        parentOrderId=parent.orderId,
        stopOrderId=stop.orderId,
    )


async def place_approved_entry(
    client: IbClient,
    approval: PendingApproval,
) -> EntryRequestResponse:

    symbol = approval.symbol

    try:

        bid_ask = await client.get_bid_ask_price(symbol)
        fresh_entry_price = calculate_entry_price(bid_ask, approval.stop_price)

        position_size = calculate_position_size(
            entry_price=fresh_entry_price,
            stop_price=approval.stop_price,
            risk=risk_settings.RISK,
        )
        order = build_order(OrderBuilder(
            symbol=symbol,
            entry_price=fresh_entry_price,
            stop_price=approval.stop_price,
            position_size=position_size,
            contract_type=approval.contract_type,
        ))
        return await _place_and_respond(
            client, order, success_message="Entry ok (approved)"
        )
    except ValueError as e:
        # Same split as process_entry_request: pricing/sizing rejects
        # are business logic, not crashes -- e.g. the fresh IB quote at
        # Accept-time may have drifted so the stop now sits inside the
        # spread. Clean reject, no traceback.
        logger.info(
            "Approved entry for %s rejected by pricing/sizing: %s", symbol, e
        )
        return EntryRequestResponse(
            allowed=False,
            message=str(e),
            symbol=symbol,
        )
    except Exception as e:
        logger.exception(
            f"Error placing approved automatic entry for {symbol}"
        )
        return EntryRequestResponse(
            allowed=False,
            message=str(e),
            symbol=symbol,
        )




async def process_entry_request(
    client: IbClient,
    payload: EntryRequest,
    approvals_hub: Optional[PendingApprovalsHub] = None,
) -> EntryRequestResponse:

    symbol = payload.symbol
    stop_price = payload.stop_price
    request_type = payload.request_type


    TIMEZONE = pytz.timezone(settings.TIMEZONE)
    current_time = datetime.now(TIMEZONE)

    logger.info(
        f"=== ENTRY REQUEST START === Symbol: {symbol}, "
        f"Requested Stop: {stop_price}, request_type: {request_type}"
    )

    try:
        snapshot = await build_today_snapshot(client)


        ok, message = check_daily_loss(snapshot)
        if not ok:
            enforce_daily_loss_circuit_breaker(client)
            return EntryRequestResponse(allowed=False, message=message, symbol=symbol)

        for ok, message in (
            check_block_window(current_time),
            check_total_attempts(snapshot),
            check_attempts(snapshot, symbol),
            check_frequency(snapshot, symbol, current_time),
        ):
            if not ok:
                return EntryRequestResponse(allowed=False, message=message, symbol=symbol)

        # Escalating consecutive-loss lockout runs first -- it's the more
        # restrictive of the two and they share the loss_cooldown response
        # shape, so the frontend banner handles either tier transparently.
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

        logger.info(f"Entry allowed for {symbol}")

        # ---- Automatic path -----------------------------------------------

        if request_type == "automatic":
            if approvals_hub is None:
                msg = (
                    f"Automatic entry for {symbol} rejected: approvals hub "
                    "unavailable."
                )
                logger.error(msg)
                return EntryRequestResponse(
                    allowed=False, message=msg, symbol=symbol
                )

            # Sizing without build_order: call calculate_position_size
            # directly so the popup can show the user the size that
            # goes with the previewed entry/stop. If the risk-per-share
            # exceeds RISK the ValueError bubbles to the outer handler
            # and becomes a clean allowed=False reject.
            preview_size = calculate_position_size(
                entry_price=payload.entry_price,
                stop_price=stop_price,
                risk=risk_settings.RISK,
            )
            pending = await approvals_hub.add_pending(
                symbol=symbol,
                contract_type=payload.contract_type,
                entry_price=float(payload.entry_price),
                stop_price=float(stop_price),
                position_size=preview_size,
            )
            return EntryRequestResponse(
                allowed=True,
                message=(
                    f"Awaiting user approval (id={pending.approval_id})."
                ),
                symbol=symbol,
            )

        # ---- Manual path ---------------------------
        # The manual "Send" button needs a real limit right now, so this
        # is where the one and only IB bid/ask fetch of the manual flow
        # happens. build_order is a pure packager -- we compute size
        # here and hand it in explicitly.
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
        return await _place_and_respond(
            client, order, success_message="Entry ok"
        )

    except ValueError as e:
        # Business-logic rejects raised by pricing / sizing helpers
        # (e.g. size rounds to 0, stop inside the spread, entry == stop).
        # These are not programming errors, so no traceback -- just log
        # the reason at INFO and hand the caller a clean reject.
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
