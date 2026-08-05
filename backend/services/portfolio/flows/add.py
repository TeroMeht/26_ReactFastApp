"""
Add flow.

Pyramid into an existing winning position. Orchestrator fetches position
+ open STP + quote once, then walks the guard checklist inline.

Public surface preserved:
    process_add_request - the orchestrator
"""

import logging
from datetime import datetime, timedelta

import pytz

from services.orders import (
    BidAsk,
    OrderBuilder,
    build_order,
    calculate_position_size,
    calculate_entry_price,
)
from services.portfolio.ib_client import IbClient, OpenOrder, Position
from services.portfolio.risk_limits import (
    check_daily_loss,
    enforce_daily_loss_circuit_breaker,
)
from services.portfolio.trades.trades_snapshot import (
    TradesSnapshot,
    build_today_snapshot,
)

from core.risk_manager_config import risk_settings
from core.config import settings
from schemas.api_schemas import AddRequest, AddRequestResponse

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Guards — pure functions
# ----------------------------------------------------------------------
def check_not_losing(position: Position, bid_ask: BidAsk) -> tuple[bool, str]:
    """Refuse to add to a losing position."""
    pos_size = position.position
    avg_cost = position.avgcost
    bid = bid_ask.bid
    ask = bid_ask.ask

    if pos_size > 0:
        if ask > avg_cost:
            return True, ""
        return False, "Cannot add to losing long position."
    if pos_size < 0:
        if bid < avg_cost:
            return True, ""
        return False, "Cannot add to losing short position."
    return False, "No existing position to add to."


def check_not_at_target_size(position: Position, total_size: int) -> tuple[bool, str]:
    """Refuse to place a 0- or negative-quantity add for both longs and shorts."""
    current = abs(position.position)
    if current >= total_size:
        msg = "Wanted position size is already in portfolio"
        logger.info(msg)
        return False, msg
    return True, ""


def check_add_cooldown(
    snapshot: TradesSnapshot, symbol: str, now: datetime
) -> tuple[bool, str, datetime | None]:
    """Block adds within MAX_ADD_FREQUENCY_MINUTES of the position being
    opened. Separate window from MAX_ENTRY_FREQUENCY_MINUTES so adds can
    be paced differently than fresh entries. Returns (ok, msg,
    cooldown_until). If the position was opened before today (no open
    fill in today's snapshot), the cooldown does not apply."""
    opened_at = snapshot.position_opened_at(symbol)
    if opened_at is None:
        return True, "", None
    threshold = timedelta(minutes=risk_settings.MAX_ADD_FREQUENCY_MINUTES)
    cooldown_until = opened_at + threshold
    elapsed = now - opened_at
    if elapsed <= threshold:
        elapsed_str = str(elapsed).split(".")[0]
        msg = (
            f"Add cooldown active for {symbol}. Position opened "
            f"{elapsed_str} ago."
        )
        logger.info(msg)
        return False, msg, cooldown_until
    return True, "", None


# ----------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------
async def process_add_request(
    client: IbClient, payload: AddRequest
) -> AddRequestResponse:
    """Validate guards over a one-shot AddContext, size the add against
    current open risk, place a limit order, and resize the STP to the
    new total. Public contract unchanged."""
    symbol = payload.symbol
    total_risk = payload.total_risk

    TIMEZONE = pytz.timezone(settings.TIMEZONE)
    current_time = datetime.now(TIMEZONE)

    logger.info(
        f"=== ADD REQUEST START === Symbol: {symbol}, Requested Risk: {total_risk}"
    )

    try:
        snapshot = await build_today_snapshot(client)

        # Fail-fast circuit breaker: kills the whole session on breach.
        ok, message = check_daily_loss(snapshot)
        if not ok:
            enforce_daily_loss_circuit_breaker(client)
            return AddRequestResponse(allowed=False, message=message, symbol=symbol)

        # Cheap snapshot-only guards first, before we spend network on IB.
        cd_ok, cd_msg, cd_until = check_add_cooldown(snapshot, symbol, current_time)
        if not cd_ok:
            return AddRequestResponse(
                allowed=False,
                message=cd_msg,
                symbol=symbol,
                reason="add_cooldown",
                cooldown_until=cd_until.isoformat() if cd_until else None,
            )

        # Fetch position, STP order, and quote in sequence (fail-fast).
        position = await client.get_position_by_symbol(symbol)
        if not position or not position.position:
            msg = f"No existing position for {symbol} to add to."
            logger.info(msg)
            return AddRequestResponse(allowed=False, message=msg, symbol=symbol)

        stp_order = await client.get_stp_order_by_symbol(symbol)
        if not stp_order:
            msg = f"No open STP order for {symbol}; cannot determine stop price."
            logger.info(msg)
            return AddRequestResponse(allowed=False, message=msg, symbol=symbol)

        # get_bid_ask_price now guarantees a valid dict or raises
        # ValueError; the outer handler in this flow surfaces that as
        # a clean AddRequestResponse.allowed=False.
        bid_ask = await client.get_bid_ask_price(symbol)

        # Pure guards over the fetched data.
        ok, message = check_not_losing(position, bid_ask)
        if not ok:
            return AddRequestResponse(allowed=False, message=message, symbol=symbol)

        # Sizing — subtract risk already tied up in the existing position so
        # `total_risk` specifies desired total exposure, not incremental.
        stp_aux_price = stp_order.auxprice
        existing_position = position.position
        add_price = calculate_entry_price(bid_ask, stp_aux_price)
        current_open_risk = round(
            abs(existing_position * (stp_aux_price - position.avgcost)), 2
        )
        risk_to_add = total_risk - current_open_risk
        new_qty = calculate_position_size(
            entry_price=add_price,
            stop_price=stp_aux_price,
            risk=risk_to_add,
        )
        total_size = abs(existing_position) + new_qty

        logger.info(
            f"Sizing add {symbol}: entry={add_price} stop={stp_aux_price} "
            f"total_risk={total_risk} current_open_risk={current_open_risk} "
            f"risk_to_add={risk_to_add} adding={new_qty} target={total_size}"
        )

        # Guard that needs the computed size.
        ok, message = check_not_at_target_size(position, total_size)
        if not ok:
            return AddRequestResponse(allowed=False, message=message, symbol=symbol)

        # Place the add order and resize the existing STP to cover the new total.
        new_order = build_order(OrderBuilder(
            symbol=symbol,
            entry_price=add_price,
            stop_price=stp_aux_price,
            position_size=new_qty,
            contract_type=payload.contract_type,
        ))
        place_result = await client.place_limit_order(new_order)
        modify_result = await client.modify_stp_order_by_id(
            stp_order.orderid, total_size
        )

        return AddRequestResponse(
            allowed=True,
            message="New order placed and STP modified successfully",
            symbol=symbol,
            new_order=new_order,
            place_result=place_result,
            modified_stp_qty=modify_result.get("new_quantity"),
        )

    except Exception as e:
        logger.exception(f"Error processing add request for {symbol}")
        return AddRequestResponse(
            allowed=False,
            message=str(e),
            symbol=symbol,
        )
