import asyncio
import logging
from typing import List, Dict
from services.orders import Order
from services.portfolio.ib_client import IbClient
from db.exits import (
    fetch_exits_by_symbol,
    update_exit_request,
    delete_exit_request,
    delete_exit_requests_by_symbol,
)
from core.config import settings
from schemas.api_schemas import (

    ExitRequest,
    ExitRequestResponseIB,

)

logger = logging.getLogger(__name__)



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
        exit_qty = int(round(float(current_position_size) * float(trim_percentage))) # paljonko pitää myydä/ostaa

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




async def handle_partial_exit(client, position,matched_exit) -> ExitRequestResponseIB:
    exit_qty            = _calculate_exit_mkt_order_size(position, matched_exit["trim_percentage"])
    action              = _decide_exit_mtk_order_action(position)
    remaining_qty       = position["position"]- exit_qty
            

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
        await client.modify_stp_order_by_id(existing_stp_order, remaining_qty)
        await client.move_stp_auxprice_to_avgcost(
            order_id=existing_stp_order,
            new_auxprice=round(position.get("avgcost"), 2),
        )
    return ExitRequestResponseIB(
        symbol=position["symbol"],
        message=(f"Partial exit and stop adjustments done"))

async def handle_full_exit(client, position)-> ExitRequestResponseIB:

    action              = _decide_exit_mtk_order_action(position)

    # ---- 4. Create order
    order = Order(
        symbol=position["symbol"],
        action=action,
        position_size=position["position"],
        contract_type=position["sectype"],
    )

    await client.place_market_order(order)

    existing_stp_order = await client.get_stp_order_by_symbol(position["symbol"])

    if existing_stp_order is None:
        logger.info("No STP found to cancel it full exit")
    else:
        await client.cancel_order_by_id(existing_stp_order)

    return ExitRequestResponseIB(
        symbol=position["symbol"],
        message=(f"Full exit done and stp order cancelled"))




async def process_exit_request(client: IbClient, db_conn,payload: ExitRequest) -> ExitRequestResponseIB:


    symbol = payload.symbol  # already uppercased + validated by ExitRequest schema
    alarm = payload.alarm    # already validated against EXIT_TRIGGERS by schema

    logger.info("Received exit request | symbol=%s alarm=%s time=%s",symbol, alarm, payload.time)

    try:
        position = await client.get_position_by_symbol(symbol)
        existing_mkt_order = await client.get_mkt_order_by_symbol(symbol) # jos mkt order tälle symbolille on jo niin ei tarvi mennä pidemmälle

        if existing_mkt_order:
            logger.info(f"Market order for this exit exists already")
            return ExitRequestResponseIB(symbol=symbol, message=(f"Market order for this exit exists already"))


        if position: # Jos on positio
            exits_for_this_symbol = await fetch_exits_by_symbol(db_conn,symbol) # katso exit requestit onko sille
            
            if exits_for_this_symbol: # jos positiolla on exit request

                matching_exit_row = _find_matching_exit(exits_for_this_symbol, alarm)

                if matching_exit_row:

                    matched_exit = matching_exit_row["matched_exit"]
                   
                    if matched_exit["trim_percentage"] < 1.0: # Jos ei olla tekemässä exittiä koko positiosta
                        ib_exit_response = await handle_partial_exit(client,position, matched_exit)
                        return(ib_exit_response)

                    elif matched_exit["trim_percentage"] == 1.0:
                        ib_exit_response = await handle_full_exit(client,position)
                        return(ib_exit_response)

                    # You can now use matched_exit, trim_percentage, and is_partial for further processing
                else:
                    logger.warning("No exit strategy found for alarm: %s", alarm)
                    return ExitRequestResponseIB(symbol=symbol, message=(f"There is no matchin exit request"))

                
        else: # Jos ei ole positiota
            logger.info("No position found for symbol: %s", symbol)
            return ExitRequestResponseIB(symbol=symbol, message=(f"No position found"))
        
        #TODO: Delete exit request puuttuu
        
    except Exception:
        # Anything unexpected mid-flight: log loudly and re-insert
        # so we don't silently lose the user's arming.
        logger.exception(
            "Unhandled exception during exit handling | symbol=%s alarm=%s",
            symbol, alarm,
        )



