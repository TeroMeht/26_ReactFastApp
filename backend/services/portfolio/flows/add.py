import logging

from services.orders import Order, build_order, calculate_position_size,calculate_entry_price
from services.portfolio.ib_client import IbClient

from core.config import settings
from schemas.api_schemas import (
    AddRequest,
    AddRequestResponse
)

logger = logging.getLogger(__name__)



async def _is_add_allowed(client: IbClient, position: dict) -> dict:
    """Won't allow adding to a losing position."""
    symbol = position.get("symbol")
    try:
        avg_cost = position.get("avgcost")
        position_size = position.get("position")
        price_data = await client.get_bid_ask_price(symbol)
        ask = price_data["ask"]
        bid = price_data["bid"]

        if position_size > 0:
            allowed = ask > avg_cost
            message = "OK to add to this long position" if allowed else "Cannot add to losing long position"
        elif position_size < 0:
            allowed = bid < avg_cost
            message = "OK to add to this short position" if allowed else "Cannot add to losing short position"
        else:
            allowed = False
            message = "No existing position to add to"

        return {
            "allowed": allowed,
            "symbol": symbol,
            "message": message,
            "price_data": price_data,
        }

    except Exception as e:
        logger.error(f"Error validating add for {symbol}: {e}")
        return {
            "allowed": False,
            "symbol": symbol,
            "message": str(e),
            "price_data": None,
        }



async def process_add_request(client: IbClient, payload: AddRequest) -> AddRequestResponse:
    """
    Process an add request:
    - Check if adding to the current position is allowed
    - If allowed, create a new order
    - Modify existing STP order for the symbol
    """
    symbol = payload.symbol
    total_risk = payload.total_risk

    try:
        # Get existing position quantity
        position = await client.get_position_by_symbol(symbol)
        # Get existing stp order
        existing_stp_order = await client.get_stp_order_by_symbol(symbol)

        # Check if adding is allowed to that position
        validation = await _is_add_allowed(client, position)

        if not validation.get("allowed"):
            logger.info(f"Add not allowed for {symbol}: {validation.get('message')}")
            return AddRequestResponse(
                allowed=False,
                message=validation.get("message"),
                symbol=symbol,
            )

        # Get existing aux price
        stp_order_aux_price = existing_stp_order.get("auxprice")
        stp_order_id = existing_stp_order.get("orderid")

        # Get existing position size
        existing_position = position.get("position")
        logger.info(
            f"Existing STP order aux price for {symbol}: {stp_order_aux_price}, "
            f"orderId: {stp_order_id}"
        )

        bid_ask = validation.get("price_data")
        add_price = calculate_entry_price(bid_ask, stp_order_aux_price)

        # --- Step 3: Recalculate position size ---
        total_size = calculate_position_size(
            entry_price=add_price,
            stop_price=stp_order_aux_price,
            risk=total_risk,
        )
        if existing_position < 0:
            new_qty = total_size + existing_position
        elif existing_position > 0:
            new_qty = total_size - existing_position  # Tämän verran pitää lisätä

        if existing_position > total_size:
            return AddRequestResponse(
                allowed=False,
                message="Wanted position size is already in portfolio",
                symbol=symbol,
            )

        modified_stp_qty = total_size  # Tähän uusi kokonaismäärä

        logger.info(
            f"Calculated new total position size: {total_size}, "
            f"existing position: {existing_position}, new quantity to add: {new_qty}"
        )

        # --- 2 Build the order dict ---
        order_data = {
            "symbol":        symbol,
            "entry_price":   add_price,
            "stop_price":    stp_order_aux_price,
            "position_size": new_qty,
            "contract_type": payload.contract_type,
        }

        # --- 3 Create new Order dataclass ---
        new_order = build_order(order_data)
        place_result = await client.place_limit_order(new_order)
        modify_result = await client.modify_stp_order_by_id(stp_order_id, modified_stp_qty)

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

