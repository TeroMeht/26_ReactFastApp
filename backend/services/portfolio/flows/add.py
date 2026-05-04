"""
Add flow.

Pyramid into an existing winning position. Fetches position + open STP +
quote once, runs pure guards over that context, then sizes a limit order
to bring total exposure to the requested risk and resizes the STP. Public
surface preserved:
    process_add_request - the orchestrator
"""

import logging
from dataclasses import dataclass

from services.orders import (
    build_order,
    calculate_position_size,
    calculate_entry_price,
)
from services.portfolio.ib_client import IbClient
from schemas.api_schemas import AddRequest, AddRequestResponse

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Context
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class AddContext:
    """Everything the add flow needs from IB, fetched once."""
    position: dict | None
    stp_order: dict | None
    bid_ask: dict | None


async def _build_add_context(client: IbClient, symbol: str) -> AddContext:
    position = await client.get_position_by_symbol(symbol)
    stp_order = await client.get_stp_order_by_symbol(symbol)
    bid_ask = await client.get_bid_ask_price(symbol)
    return AddContext(position=position, stp_order=stp_order, bid_ask=bid_ask)


# ----------------------------------------------------------------------
# Guards — pure functions over an AddContext
# ----------------------------------------------------------------------
def check_has_position(ctx: AddContext, symbol: str) -> tuple[bool, str]:
    if not ctx.position or not ctx.position.get("position"):
        msg = f"No existing position for {symbol} to add to."
        logger.info(msg)
        return False, msg
    return True, ""


def check_has_stp_order(ctx: AddContext, symbol: str) -> tuple[bool, str]:
    if not ctx.stp_order:
        msg = f"No open STP order for {symbol}; cannot determine stop price."
        logger.info(msg)
        return False, msg
    return True, ""


def check_has_quote(ctx: AddContext, symbol: str) -> tuple[bool, str]:
    if not ctx.bid_ask:
        msg = f"No bid/ask quote available for {symbol}."
        logger.info(msg)
        return False, msg
    return True, ""


def check_not_losing(ctx: AddContext) -> tuple[bool, str]:
    """Refuse to add to a losing position."""
    pos_size = ctx.position.get("position")
    avg_cost = ctx.position.get("avgcost")
    bid = ctx.bid_ask.get("bid")
    ask = ctx.bid_ask.get("ask")

    if pos_size > 0:
        if ask > avg_cost:
            return True, ""
        return False, "Cannot add to losing long position."
    if pos_size < 0:
        if bid < avg_cost:
            return True, ""
        return False, "Cannot add to losing short position."
    return False, "No existing position to add to."


def check_not_at_target_size(
    ctx: AddContext, total_size: int
) -> tuple[bool, str]:
    """Refuse to place a 0- or negative-quantity add for both longs and shorts."""
    current = abs(ctx.position.get("position", 0))
    if current >= total_size:
        msg = "Wanted position size is already in portfolio"
        logger.info(msg)
        return False, msg
    return True, ""


# ----------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------
async def process_add_request(client: IbClient, payload: AddRequest) -> AddRequestResponse:
    """
    Validate guards (one fetch each for position/STP/quote), size the
    add, place a limit order, and resize the STP to the new total.
    Public contract unchanged.
    """
    symbol = payload.symbol
    total_risk = payload.total_risk

    try:
        ctx = await _build_add_context(client, symbol)

        for ok, message in (
            check_has_position(ctx, symbol),
            check_has_stp_order(ctx, symbol),
            check_has_quote(ctx, symbol),
            check_not_losing(ctx),
        ):
            if not ok:
                logger.info(f"Add not allowed for {symbol}: {message}")
                return AddRequestResponse(
                    allowed=False, message=message, symbol=symbol
                )

        stp_aux_price = ctx.stp_order["auxprice"]
        stp_order_id = ctx.stp_order["orderid"]
        existing_position = ctx.position["position"]

        # Price → total target size at requested risk → quantity to add
        add_price = calculate_entry_price(ctx.bid_ask, stp_aux_price)
        total_size = calculate_position_size(
            entry_price=add_price,
            stop_price=stp_aux_price,
            risk=total_risk,
        )

        ok, message = check_not_at_target_size(ctx, total_size)
        if not ok:
            return AddRequestResponse(
                allowed=False, message=message, symbol=symbol
            )

        # target − current works for both long and short because total_size
        # is always positive and we compare against |existing_position|.
        new_qty = total_size - abs(existing_position)

        logger.info(
            f"Add {symbol}: target={total_size}, existing={existing_position}, "
            f"adding {new_qty} at {add_price} (stop {stp_aux_price})"
        )

        new_order = build_order({
            "symbol":        symbol,
            "entry_price":   add_price,
            "stop_price":    stp_aux_price,
            "position_size": new_qty,
            "contract_type": payload.contract_type,
        })

        place_result = await client.place_limit_order(new_order)
        modify_result = await client.modify_stp_order_by_id(stp_order_id, total_size)

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
