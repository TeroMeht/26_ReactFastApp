import logging
from typing import List, Dict
from services.orders import Order
from services.portfolio.ib_client import IbClient
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


# helper functions
def _decide_exit_mtk_order_action(position) -> str:

    if position["position"] > 0:
        action = "SELL"
    elif position["position"] < 0:
        action = "BUY"
    else:
        # If the position is exactly 0, we raise an error
        raise ValueError("Invalid position value: Position cannot be zero for an exit order.")

    return action

def _calculate_exit_mkt_order_size(position, trim_percentage) -> int:

    current_position_size = abs(position["position"])
    exit_qty = int(round(float(current_position_size) * float(trim_percentage)))  # paljonko pitää myydä/ostaa

    return exit_qty

def _find_matching_exit(exits_for_this_symbol: List[Dict], alarm: str) -> Dict:

    matched_exit = None

    for exit_request in exits_for_this_symbol:
        if exit_request["strategy"] == alarm:  # Matching strategy to alarm
            matched_exit = exit_request
            break

    if matched_exit:
        # We found a matching strategy and alarm
        logger.info("Found matching exit strategy: %s", matched_exit)

        # Prepare the result with relevant information
        result = {
            "matched_exit": matched_exit
        }

        return result
    else:
        # No matching strategy found for the given alarm
        logger.warning("No matching exit strategy found for alarm: %s", alarm)
        return None


# handlers to deal with ib client and db together, called by the router
async def _handle_partial_exit(client, position, matched_exit) -> ExitRequestResponseIB:
    exit_qty            = _calculate_exit_mkt_order_size(position, matched_exit["trim_percentage"])
    action              = _decide_exit_mtk_order_action(position)
    remaining_qty       = abs(position["position"]) - exit_qty


    # ---- 4. Create order
    order = Order(
        symbol=position["symbol"],
        action=action,
        position_size=exit_qty,
        contract_type=position["sectype"],
    )

    await client.place_market_order(order)

    existing_stp_order = await client.get_stp_order_by_symbol(position["symbol"])

    if existing_stp_order is None:
        logger.info("No STP found to modify on partial exit")
    else:
        # Resize the STP, then move it to breakeven if we know avgcost.
        # IbClient methods expect an integer permId, not the full order dict.
        stp_order_id = existing_stp_order["orderid"]
        await client.modify_stp_order_by_id(stp_order_id, remaining_qty)
        await client.move_stp_auxprice_to_avgcost(
            order_id=stp_order_id,
            new_auxprice=round(position.get("avgcost"), 2),
        )
    return ExitRequestResponseIB(
        symbol=position["symbol"],
        message="Partial exit and stop adjustments done")

async def _handle_full_exit(client, position) -> ExitRequestResponseIB:

    action              = _decide_exit_mtk_order_action(position)

    # ---- 4. Create order
    order = Order(
        symbol=position["symbol"],
        action=action,
        position_size=abs(position["position"]),
        contract_type=position["sectype"],
    )

    await client.place_market_order(order)

    existing_stp_order = await client.get_stp_order_by_symbol(position["symbol"])

    if existing_stp_order is None:
        logger.info("No STP found to cancel it full exit")
    else:
        # cancel_order_by_id expects an integer permId, not the full order dict.
        await client.cancel_order_by_id(existing_stp_order["orderid"])

    return ExitRequestResponseIB(
        symbol=position["symbol"],
        message="Full exit done and stp order cancelled")


# wrapper
async def _dispatch_exit(client, db_conn, position, matched_exit) -> ExitRequestResponseIB:
    """
    Pick partial vs full exit based on trim_percentage, execute it, and
    clean up the corresponding exit_request row(s) so the strategy disarms.

    - trim < 1.0  -> partial exit, delete only this (symbol, strategy) row
    - trim == 1.0 -> full exit, delete every exit_request row for the symbol
                     so leftover strategies don't fire on a re-entered position
    """
    trim = matched_exit["trim_percentage"]
    symbol = position["symbol"]

    if trim < 1.0:
        response = await _handle_partial_exit(client, position, matched_exit)
        await delete_exit_request(db_conn, symbol, matched_exit["strategy"])
    elif trim == 1.0:
        response = await _handle_full_exit(client, position)
        await delete_exit_requests_by_symbol(db_conn, symbol)
    else:
        raise ValueError(f"Unexpected trim_percentage: {trim}")

    return response


# main flow
async def process_exit_request(client: IbClient, db_conn, payload: ExitRequest) -> ExitRequestResponseIB:


    symbol = payload.symbol  # already uppercased + validated by ExitRequest schema
    alarm = payload.alarm    # already validated against EXIT_TRIGGERS by schema

    logger.info("Received exit request | symbol = %s alarm = %s time = %s", symbol, alarm, payload.time)

    try:
        existing_mkt_order = await client.get_mkt_order_by_symbol(symbol)  # jos mkt order tälle symbolille on jo niin ei tarvi mennä pidemmälle
        if existing_mkt_order:
            logger.info("Market order for this exit exists already")
            return ExitRequestResponseIB(symbol=symbol, message="Market order for this exit exists already")

        position = await client.get_position_by_symbol(symbol)
        if not position:  # Jos ei ole positiota
            logger.info("No position found for symbol: %s", symbol)
            return ExitRequestResponseIB(symbol=symbol, message="No position found")

        exits_for_this_symbol = await fetch_exits_by_symbol(db_conn, symbol)  # katso exit requestit onko sille
        if not exits_for_this_symbol:  # ei exit requestiä positiolle
            logger.info("No active exit request for symbol: %s", symbol)
            return ExitRequestResponseIB(symbol=symbol, message="No active exit request for this symbol")

        matching_exit_row = _find_matching_exit(exits_for_this_symbol, alarm)
        if not matching_exit_row:
            logger.warning("No exit strategy found for alarm: %s", alarm)
            return ExitRequestResponseIB(symbol=symbol, message="There is no matching exit request")

        return await _dispatch_exit(client, db_conn, position, matching_exit_row["matched_exit"])

    except Exception:
        # Anything unexpected mid-flight: log loudly and return an error response
        # so the caller doesn't get None back.
        logger.exception(
            "Unhandled exception during exit handling | symbol=%s alarm=%s",
            symbol, alarm,
        )
        return ExitRequestResponseIB(symbol=symbol, message="Unhandled error during exit handling")
