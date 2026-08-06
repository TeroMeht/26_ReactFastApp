import logging
from decimal import Decimal

from services.orders import Order
from services.portfolio.ib_client import IbClient, OrderNotFoundError, Position
from db.exits import (
    fetch_exits_by_symbol,
    delete_exit_request,
    delete_exit_requests_by_symbol,
)
from schemas.api_schemas import (
    ExitRequest,
    ExitRequestResponseIB,
    ManualExitResponse,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Shared sizing helpers
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
    position_size = abs(position.position)
    trim = float(trim_percentage)
    raw_qty = position_size * trim
    return int(round(raw_qty))


# ======================================================================
# Automatic exit handling (strategy-driven MKT)
# ======================================================================
async def process_automatic_exit(
    client: IbClient, db_conn, payload: ExitRequest,
) -> ExitRequestResponseIB:
    """
    Process a strategy-triggered exit. Top-to-bottom checklist:
      1. Bail if an exit MKT is already out for this symbol.
      2. Bail if there's no position to exit.
      3. Bail if no exit_request rows exist for this symbol.
      4. Bail if no armed strategy matches the incoming alarm.
      5. Place a MKT for the matched trim, then disarm:
           - trim >= 1.0 -> full exit, delete every row for this symbol
             so leftover strategies don't fire on a re-entered position
           - trim <  1.0 -> partial exit, delete only the fired row

    STP adjustment (cancel on full, resize on partial) is handled off
    the fill event in `handle_exit_fill` below.
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

        # Place the MKT. Fill bridge will sync the STP to the resulting position.
        action = _exit_action(position)
        qty = _exit_qty(position, trim)
        order = Order(
            symbol=position.symbol,
            action=action,
            position_size=qty,
            contract_type=position.sectype,
        )
        await client.place_market_order(order)

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


# ======================================================================
# Manual exit handling (user-placed LMTs, pre-fill)
# ======================================================================
async def process_manual_exit(
    client: IbClient,
    *,
    symbol: str,
    target_price: Decimal,
    trim_percentage: Decimal,
) -> ManualExitResponse:
    """
    Validate the position, size the trim, place a LIMIT order and return
    a ManualExitResponse describing it (same shape as
    `services.exits.list_manual_exits`, so the frontend can use them
    interchangeably).
    """
    position = await client.get_position_by_symbol(symbol)
    if not position or not position.position:
        raise ValueError(f"No open position for {symbol}; cannot arm manual exit.")

    pos_size = position.position
    pos_abs = abs(int(pos_size))
    contract_type = position.sectype or "STK"
    action = _exit_action(position)
    qty = _exit_qty(position, trim_percentage)
    if qty <= 0:
        raise ValueError(
            f"Computed trim quantity is 0 for {symbol} "
            f"(position={pos_size}, trim={trim_percentage})."
        )

    # Guard against over-trimming. Sum the quantities of every still-open
    # LMT order on the exit side (SELL for longs, BUY for shorts) and
    # refuse if (existing + new) would exceed |position|. Otherwise
    # stacking e.g. a 50% then a 100% exit would flip the position by 50%
    # on the wrong side once both fill.
    existing_exit_qty = 0
    try:
        open_orders = await client.get_orders()
        symbol_u = symbol.upper()
        for o in open_orders:
            if (o.symbol or "").upper() != symbol_u:
                continue
            if (o.ordertype or "").upper() != "LMT":
                continue
            if (o.action or "").upper() != action:
                continue
            existing_exit_qty += int(o.totalqty or 0)
    except Exception:
        logger.exception(
            "process_manual_exit: failed to read open orders for over-trim check"
        )

    if existing_exit_qty + qty > pos_abs:
        remaining = max(0, pos_abs - existing_exit_qty)
        raise ValueError(
            f"Manual exit would over-trim {symbol}: "
            f"position={pos_abs}, already armed for {existing_exit_qty}, "
            f"requested {qty} (max remaining: {remaining}). "
            f"Cancel an existing exit or pick a smaller trim %."
        )

    order = Order(
        symbol=symbol.upper(),
        action=action,
        position_size=qty,
        contract_type=contract_type,
        entry_price=float(target_price),  # place_limit_order maps entry_price -> lmtPrice
    )

    limit_order = await client.place_limit_order(order)
    if limit_order is None:
        raise RuntimeError(
            f"IB rejected the manual exit LIMIT order for {symbol}."
        )

    order_id = getattr(limit_order, "orderId", None)
    perm_id = getattr(limit_order, "permId", None) or None

    logger.info(
        "Armed manual exit | symbol=%s action=%s qty=%s target=%s trim=%s "
        "order_id=%s perm_id=%s",
        symbol, action, qty, target_price, trim_percentage, order_id, perm_id,
    )

    return ManualExitResponse(
        symbol=symbol.upper(),
        contract_type=contract_type,
        order_id=int(order_id) if order_id else 0,
        perm_id=int(perm_id) if perm_id else None,
        target_price=target_price,
        trim_percentage=trim_percentage,
        action=action,
        quantity=qty,
        status="armed",
    )


# ======================================================================
# Post-fill STP adjustment (runs on every fill, syncs STP to position)
# ======================================================================
async def handle_exit_fill(client: IbClient, symbol: str) -> None:
    """
Tätä kutsutaan säätämään stoppia kun markkinatoimeksianto täyttyy. Jos positio on nyt nolla, peruutetaan STP. 
Jos positio on edelleen auki, muutetaan STP:n määrää vastaamaan jäljellä olevaa positioita.
    """
    existing_stp_order = await client.get_stp_order_by_symbol(symbol)
    if existing_stp_order is None:
        logger.info("No STP to adjust after fill | symbol=%s", symbol)
        return

    position = await client.get_position_by_symbol(symbol)
    remaining_qty = (
        abs(int(position.position))
        if position and position.position is not None
        else 0
    )

    if remaining_qty <= 0:
        try:
            await client.cancel_order_by_id(existing_stp_order.orderid)
            logger.info(
                "Cancelled STP after position went flat | symbol=%s order_id=%s",
                symbol, existing_stp_order.orderid,
            )
        except OrderNotFoundError:
            # STP became terminal between our lookup and the cancel
            # (e.g. the fill we're reacting to WAS the STP). Harmless.
            logger.info(
                "STP already gone by cancel time | symbol=%s order_id=%s",
                symbol, existing_stp_order.orderid,
            )
        return

    stp_order_id = existing_stp_order.orderid
    await client.modify_stp_order_by_id(stp_order_id, remaining_qty)
    logger.info(
        "Resized STP to match position | symbol=%s remaining=%s order_id=%s",
        symbol, remaining_qty, stp_order_id,
    )
