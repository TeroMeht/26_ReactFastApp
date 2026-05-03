import asyncio
import logging

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


# ----------------------------------------------------------------------
# Per-symbol locks (module-level state)
# ----------------------------------------------------------------------
# Two alarms hitting the same symbol concurrently (e.g., vwap_exit and
# endofday_exit firing in the same tick) would otherwise race the position
# read / market-order placement / STP modify and could over-exit. The lock
# serializes them.
#
# Module-level dict is fine because asyncio is single-threaded; setdefault
# is atomic relative to other coroutines that don't await between the
# lookup and the assignment.
_symbol_exit_locks: dict[str, asyncio.Lock] = {}






def _get_symbol_lock(symbol: str) -> asyncio.Lock:
    return _symbol_exit_locks.setdefault(symbol.upper(), asyncio.Lock())


# ----------------------------------------------------------------------
# Order lifecycle helper
# ----------------------------------------------------------------------
async def _wait_for_order_done(trade, timeout: float = 15.0) -> str:
    """
    Poll an ib_async Trade until it reaches a terminal status.

    Returns the final status string. Treat 'Filled' as success and any of
    {'Cancelled', 'ApiCancelled', 'Inactive', 'Rejected', 'timeout'} as
    failure. Caller decides what to do (e.g., re-insert the exit_request
    row so the user can retry).
    """
    DONE_STATES = {"Filled", "Cancelled", "ApiCancelled", "Inactive", "Rejected"}
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    last_status = None

    while True:
        status = (
            trade.orderStatus.status
            if trade is not None and trade.orderStatus is not None
            else None
        )
        if status != last_status:
            logger.info(
                "Order status update | orderId=%s status=%s filled=%s remaining=%s",
                getattr(trade.order, "orderId", None) if trade and trade.order else None,
                status,
                getattr(trade.orderStatus, "filled", None) if trade and trade.orderStatus else None,
                getattr(trade.orderStatus, "remaining", None) if trade and trade.orderStatus else None,
            )
            last_status = status

        if status in DONE_STATES:
            return status

        if loop.time() >= deadline:
            logger.warning(
                "Order ack timed out after %.1fs | last_status=%s",
                timeout, status,
            )
            return "timeout"

        await asyncio.sleep(0.1)







async def process_exit_request(client: IbClient,db_conn,payload: ExitRequest) -> ExitRequestResponseIB:


    symbol = payload.symbol  # already uppercased + validated by ExitRequest schema
    alarm = payload.alarm    # already validated against EXIT_TRIGGERS by schema
    logger.info(
        "Received exit request | symbol=%s alarm=%s time=%s",
        symbol, alarm, payload.time,
    )

    lock = _get_symbol_lock(symbol)

    async with lock:
        # ---- 1. CLAIM THE ROW (delete-first idempotency) -------------
        claimed = await delete_exit_request(db_conn, symbol, alarm)
        if not claimed:
            logger.info(
                "No armed exit_request row matched | symbol=%s alarm=%s "
                "(either never armed or duplicate alarm already consumed)",
                symbol, alarm,
            )
            return ExitRequestResponseIB(
                symbol=symbol,
                message=(
                    f"No exit_request armed for {symbol} with "
                    f"strategy '{alarm}'."
                ),
            )

        # Resolve trim_percentage from the claimed row.
        trim_percentage = claimed.get("trim_percentage")
        is_partial = trim_percentage < 1.0

        # Helper: re-insert the claimed row when we couldn't act on it.
        async def _restore_claim(reason: str) -> None:
            try:
                await update_exit_request(
                    db_conn,
                    symbol=symbol,
                    strategy=alarm,
                    trim_percentage=trim_percentage,
                )
                logger.info(
                    "Re-inserted claimed exit_request row after %s | "
                    "symbol=%s strategy=%s",
                    reason, symbol, alarm,
                )
            except Exception:
                logger.exception(
                    "Failed to re-insert claimed exit_request row | "
                    "symbol=%s strategy=%s reason=%s",
                    symbol, alarm, reason,
                )

        try:
            # ---- 2. POSITION CHECK -----------------------------------
            position = await client.get_position_by_symbol(symbol)
            logger.info(
                "Fetched position | symbol=%s position=%s", symbol, position
            )

            if not position or float(position.get("position", 0) or 0) == 0:
                # Position is gone or flat. The claimed row is stale, and
                # so are any siblings for this symbol — wipe them so they
                # can't re-fire on a re-entry.
                deleted = await delete_exit_requests_by_symbol(db_conn, symbol)
                logger.warning(
                    "No open position; cleared %d stale exit_request row(s) | symbol=%s",
                    len(deleted), symbol,
                )
                return ExitRequestResponseIB(
                    symbol=symbol,
                    message=(
                        f"No open position for {symbol}; cleared "
                        f"{len(deleted) + 1} stale exit_request row(s)."
                    ),
                )

            shares = position["position"]
            avgcost = position["avgcost"]
            if shares > 0:
                action = "SELL"
            elif shares < 0:
                action = "BUY"
            else:
                # Defensive — already covered above, but keep the guard.
                deleted = await delete_exit_requests_by_symbol(db_conn, symbol)
                return ExitRequestResponseIB(
                    symbol=symbol,
                    message=(
                        f"Position size is zero for {symbol}; cleared "
                        f"{len(deleted) + 1} stale exit_request row(s)."
                    ),
                )

            total_abs = abs(int(round(float(shares))))
            exit_qty = int(round(total_abs * trim_percentage))
            if is_partial and exit_qty <= 0:
                exit_qty = 1
            if exit_qty > total_abs:
                exit_qty = total_abs
            remaining_qty = total_abs - exit_qty
            logger.info(
                "Exit qty | symbol=%s total=%s exit_qty=%s remaining=%s",
                symbol, total_abs, exit_qty, remaining_qty,
            )

            # ---- 3. SHORT-CIRCUIT IF MKT ALREADY IN FLIGHT ------------
            existing_mkt_order = await client.get_mkt_order_by_symbol(symbol)
            avgcost = position.get("avgcost")

            if existing_mkt_order:
                logger.info(
                    "Market order already in flight; restoring claim | "
                    "symbol=%s order=%s",
                    symbol, existing_mkt_order,
                )
                await _restore_claim("market order already in flight")
                return ExitRequestResponseIB(
                    symbol=symbol,
                    message=f"Market order already exists for {symbol}.",
                )

            # ---- 4. PLACE MARKET ORDER & WAIT FOR ACK -----------------
            order = Order(
                symbol=symbol,
                contract_type=position["sectype"],
                action=action,
                position_size=exit_qty,
            )

            trade = await client.place_market_order(order)
            if trade is None:
                await _restore_claim("place_market_order returned None")
                return ExitRequestResponseIB(
                    symbol=symbol,
                    message=(
                        f"IB error placing market order for {symbol}; "
                        f"row restored so you can retry."
                    ),
                )

            final_status = await _wait_for_order_done(trade, timeout=15.0)
            if final_status != "Filled":
                # Order didn't fill (rejected, cancelled, timed out).
                # Don't touch the STP. Restore the claim so the user
                # can retry rather than re-arming manually.
                await _restore_claim(f"order final status={final_status}")
                return ExitRequestResponseIB(
                    symbol=symbol,
                    message=(
                        f"Market order for {symbol} did not fill "
                        f"(status={final_status}); row restored."
                    ),
                    order_id=getattr(trade.order, "orderId", None) if trade.order else None,
                )

            filled_order_id = getattr(trade.order, "orderId", None) if trade.order else None
            logger.info(
                "Market order filled | symbol=%s order_id=%s qty=%s",
                symbol, filled_order_id, exit_qty,
            )

            # Re-fetch the STP fresh AFTER the fill so we modify against
            # current state, not a snapshot from before the fill.
            existing_stp_order = await client.get_stp_order_by_symbol(symbol)
            logger.info(
                "Post-fill STP snapshot | symbol=%s stp=%s",
                symbol, existing_stp_order,
            )


            # ---- 5. ADJUST STP --------------------------------------
            if is_partial:

                if existing_stp_order is None:
                    logger.info("No STP found to modify on partial exit | symbol=%s", symbol)
                else:
                    # Resize the STP, then move it to breakeven if we know avgcost.
                    await client.modify_stp_order_by_id(existing_stp_order, remaining_qty)

                    avgcost = position.get("avgcost")
                    if avgcost is None:
                        logger.warning("avgcost missing; skipping stop move | symbol=%s", symbol)
                    else:
                        await client.move_stp_auxprice_to_avgcost(
                            order_id=existing_stp_order,
                            new_auxprice=round(float(avgcost), 2),
                        )

                return ExitRequestResponseIB(
                    symbol=symbol,
                    message=(
                        f"Partial exit ({alarm}): {action} {exit_qty}/{total_abs} "
                        f"shares of {symbol} at {trim_percentage*100:.0f}%. "
                        f"Remaining: {remaining_qty}."
                    ),
                    order_id=filled_order_id,
                )

            # Full exit
            if existing_stp_order is None:
                logger.info("No STP found to cancel | symbol=%s", symbol)
            else:
                await client.cancel_order_by_id(existing_stp_order)

            siblings = await delete_exit_requests_by_symbol(db_conn, symbol)

            return ExitRequestResponseIB(
                symbol=symbol,
                message=(
                    f"Full exit ({alarm}): {action} {exit_qty} shares of "
                    f"{symbol}. Cleared {len(siblings) + 1} exit_request row(s)."
                ),
                order_id=filled_order_id,
            )
        
        
        except Exception:
            # Anything unexpected mid-flight: log loudly and re-insert
            # so we don't silently lose the user's arming.
            logger.exception(
                "Unhandled exception during exit handling | symbol=%s alarm=%s",
                symbol, alarm,
            )
            await _restore_claim("unhandled exception")
            raise


