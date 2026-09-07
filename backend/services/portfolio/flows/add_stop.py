"""
Add-stop-order flow.

Places a stand-alone protective STP for an existing position that has
none. Sized to the full open position and priced from the caller's
stop_price. Shape (native STP vs conditional LMT tagged PROTECTIVE_STP)
follows the same extended-hours-stop runtime toggle used by the entry
bracket's protective leg.

Guards:
  - A non-zero position must exist for the symbol.
  - There must NOT already be an open protective stop for the symbol
    (avoid double-stops; the reconciler assumes a single writer).

Public surface:
    process_add_stop_request  - the orchestrator
"""

from __future__ import annotations

import logging

from services.portfolio.ib_client import IbClient
from schemas.api_schemas import AddStopOrderRequest, AddStopOrderResponse

logger = logging.getLogger(__name__)


async def process_add_stop_request(
    client: IbClient,
    payload: AddStopOrderRequest,
) -> AddStopOrderResponse:
    symbol = payload.symbol
    stop_price = float(payload.stop_price)

    logger.info(
        "=== ADD-STOP REQUEST START === Symbol: %s, Stop: %s",
        symbol, stop_price,
    )

    try:
        position = await client.get_position_by_symbol(symbol)
        if not position or not position.position:
            msg = f"No existing position for {symbol}; cannot add a stop."
            logger.info(msg)
            return AddStopOrderResponse(
                allowed=False, message=msg, symbol=symbol,
            )

        existing_stp = await client.get_stp_order_by_symbol(symbol)
        if existing_stp is not None:
            msg = (
                f"{symbol} already has a protective stop "
                f"(orderId={existing_stp.orderid}). Nothing to add."
            )
            logger.info(msg)
            return AddStopOrderResponse(
                allowed=False, message=msg, symbol=symbol,
            )

        pos_size = float(position.position)

        quantity = int(round(abs(pos_size)))
        # Reverse action of the position: SELL to close a long, BUY to close a short.
        action = "SELL" if pos_size > 0 else "BUY"
        # Contract type comes from the IB Position's secType (e.g. STK, CFD).
        # _build_contract accepts both "stock"/"STK" and "CFD"; normalize STK -> stock
        # to reuse the same code path the rest of the app uses.
        contract_type = (position.sectype or "STK").upper()
        if contract_type == "STK":
            contract_type = "stock"

        placed = await client.place_standalone_stp_order(
            symbol=symbol,
            contract_type=contract_type,
            stop_price=stop_price,
            quantity=quantity,
            action=action,
        )

        if not placed:
            msg = f"Failed to place stop order for {symbol}"
            logger.error(msg)
            return AddStopOrderResponse(
                allowed=False, message=msg, symbol=symbol,
            )

        return AddStopOrderResponse(
            allowed=True,
            message=f"Stop order placed for {symbol} at {stop_price}",
            symbol=symbol,
            order_id=getattr(placed, "orderId", None),
            stop_price=stop_price,
            quantity=quantity,
            action=action,
        )

    except Exception as e:
        logger.exception("Error processing add-stop request for %s", symbol)
        return AddStopOrderResponse(
            allowed=False, message=str(e), symbol=symbol,
        )
