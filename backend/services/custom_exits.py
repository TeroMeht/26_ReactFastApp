"""
Custom price-target exit service — IB-only, no DB.

A custom exit is a real IB LIMIT order placed on the user's behalf at a
target price for a fraction of their open position. We tag every such
order with `orderRef = "CUSTOM_EXIT:<trim>"` so we can:
  - enumerate them straight from IB's open orders (no DB scan),
  - identify them on fill (so the fill listener can adjust the STP),
  - cancel by permId without needing any local bookkeeping.

When IB fills one, services.custom_exits.handle_custom_exit_fill mirrors
the strategy-based exit flow's STP behaviour (resize + move-to-BE on
partial, cancel on 100%).
"""

import logging
from decimal import Decimal
from typing import Dict, List, Optional

from services.orders import Order
from services.portfolio.ib_client import IbClient

logger = logging.getLogger(__name__)


CUSTOM_EXIT_PREFIX = "CUSTOM_EXIT"


# ----------------------------------------------------------------------
# orderRef helpers — single source of truth for the tag format
# ----------------------------------------------------------------------
def _build_order_ref(trim_percentage: float) -> str:
    """Encode trim into orderRef so the fill listener can recover it."""
    return f"{CUSTOM_EXIT_PREFIX}:{trim_percentage}"


def parse_order_ref(order_ref: Optional[str]) -> Optional[float]:
    """
    Return the trim_percentage encoded in a CUSTOM_EXIT orderRef, or None
    if this orderRef wasn't one of ours / is malformed.
    """
    if not order_ref or not order_ref.startswith(f"{CUSTOM_EXIT_PREFIX}:"):
        return None
    _, _, rest = order_ref.partition(":")
    try:
        return float(rest)
    except (TypeError, ValueError):
        return None


def is_custom_exit_ref(order_ref: Optional[str]) -> bool:
    return parse_order_ref(order_ref) is not None


# ----------------------------------------------------------------------
# Position-side helpers
# ----------------------------------------------------------------------
def _calc_trim_qty(position_size: int, trim_percentage: float) -> int:
    return int(round(abs(float(position_size)) * float(trim_percentage)))


def _exit_action(position_size: float) -> str:
    if position_size > 0:
        return "SELL"
    if position_size < 0:
        return "BUY"
    raise ValueError("Cannot place custom exit: position is 0")


# ----------------------------------------------------------------------
# Public surface
# ----------------------------------------------------------------------
async def place_custom_exit(
    client: IbClient,
    *,
    symbol: str,
    target_price: Decimal,
    trim_percentage: Decimal,
) -> Dict:
    """
    Validate the position, size the trim, place a tagged LIMIT order and
    return a flat dict describing it (the same shape list_custom_exits
    returns, so the frontend can use them interchangeably).
    """
    position = await client.get_position_by_symbol(symbol)
    if not position or not position.get("position"):
        raise ValueError(f"No open position for {symbol}; cannot arm custom exit.")

    pos_size = position["position"]
    pos_abs = abs(int(pos_size))
    contract_type = position.get("sectype") or "STK"
    action = _exit_action(pos_size)

    trim_f = float(trim_percentage)
    qty = _calc_trim_qty(pos_size, trim_f)
    if qty <= 0:
        raise ValueError(
            f"Computed trim quantity is 0 for {symbol} "
            f"(position={pos_size}, trim={trim_percentage})."
        )

    # Guard against over-trimming. Sum the quantities of every still-open
    # LMT order on the exit side (SELL for longs, BUY for shorts) — tagged
    # or external — and refuse if (existing + new) would exceed |position|.
    # Otherwise stacking e.g. a 50% then a 100% exit would flip the
    # position by 50% on the wrong side once both fill.
    existing_exit_qty = 0
    try:
        open_orders = await client.get_orders()
        symbol_u = symbol.upper()
        for o in open_orders:
            if (o.get("symbol") or "").upper() != symbol_u:
                continue
            if (o.get("ordertype") or "").upper() != "LMT":
                continue
            if (o.get("action") or "").upper() != action:
                continue
            existing_exit_qty += int(o.get("totalqty") or 0)
    except Exception:
        logger.exception(
            "place_custom_exit: failed to read open orders for over-trim check"
        )

    if existing_exit_qty + qty > pos_abs:
        remaining = max(0, pos_abs - existing_exit_qty)
        raise ValueError(
            f"Custom exit would over-trim {symbol}: "
            f"position={pos_abs}, already armed for {existing_exit_qty}, "
            f"requested {qty} (max remaining: {remaining}). "
            f"Cancel an existing exit or pick a smaller trim %."
        )

    avgcost = position.get("avgcost")
    if avgcost is not None:
        target_f = float(target_price)
        if action == "SELL" and target_f <= avgcost:
            logger.warning(
                "Custom SELL exit for %s priced %.4f at/under avg cost %.4f — "
                "this would realize a loss on fill.",
                symbol, target_f, avgcost,
            )
        if action == "BUY" and target_f >= avgcost:
            logger.warning(
                "Custom BUY exit for %s priced %.4f at/over avg cost %.4f — "
                "this would realize a loss on fill.",
                symbol, target_f, avgcost,
            )

    order = Order(
        symbol=symbol.upper(),
        action=action,
        position_size=qty,
        contract_type=contract_type,
        entry_price=float(target_price),  # place_limit_order maps entry_price -> lmtPrice
    )

    order_ref = _build_order_ref(trim_f)
    limit_order = await client.place_limit_order(order, order_ref=order_ref)
    if limit_order is None:
        raise RuntimeError(
            f"IB rejected the custom exit LIMIT order for {symbol}."
        )

    order_id = getattr(limit_order, "orderId", None)
    perm_id = getattr(limit_order, "permId", None) or None

    logger.info(
        "Armed custom exit | symbol=%s action=%s qty=%s target=%s trim=%s "
        "order_id=%s perm_id=%s",
        symbol, action, qty, target_price, trim_percentage, order_id, perm_id,
    )

    return {
        "symbol": symbol.upper(),
        "contract_type": contract_type,
        "order_id": int(order_id) if order_id else 0,
        "perm_id": int(perm_id) if perm_id else None,
        "target_price": float(target_price),
        "trim_percentage": trim_f,
        "action": action,
        "quantity": qty,
        "status": "armed",
    }


async def list_custom_exits(client: IbClient, symbol: str) -> List[Dict]:
    """
    Enumerate every open LIMIT order for a symbol so the manage page can
    show them under Custom Exits — regardless of whether we tagged them
    ourselves or they were placed externally (e.g. directly in IB TWS).

    For tagged orders we read the trim percentage straight off the
    orderRef. For untagged orders we derive it from quantity / |position|
    so the column still has a useful number (None when we can't size it).
    """
    orders = await client.get_orders()
    symbol_u = symbol.upper()

    # Pull the position once so we can derive trim for untagged orders.
    # Failure to read it (no position, IB hiccup) just leaves trim at None.
    position = None
    try:
        position = await client.get_position_by_symbol(symbol_u)
    except Exception:
        logger.exception("list_custom_exits: failed to read position for %s", symbol_u)

    pos_size = abs(float(position.get("position") or 0)) if position else 0.0

    rows: List[Dict] = []
    for o in orders:
        if (o.get("symbol") or "").upper() != symbol_u:
            continue
        if (o.get("ordertype") or "").upper() != "LMT":
            continue

        tagged_trim = parse_order_ref(o.get("orderref"))
        qty = int(o.get("totalqty") or 0)
        if tagged_trim is not None:
            trim_val: Optional[float] = tagged_trim
        elif pos_size > 0 and qty > 0:
            # Derive trim from the order's size as a fraction of the open
            # position. Clamp to (0, 1] — a LIMIT for more than the
            # position is shown as 100% rather than a misleading >100%.
            trim_val = min(1.0, qty / pos_size)
        else:
            trim_val = None

        rows.append({
            "symbol": symbol_u,
            "contract_type": "",  # not returned by IbClient.get_orders()
            "order_id": o.get("orderid") or 0,
            "perm_id": o.get("orderid"),
            "target_price": float(o.get("lmtprice") or 0.0),
            "trim_percentage": trim_val,
            "action": o.get("action") or "",
            "quantity": qty,
            "status": (o.get("status") or "armed").lower(),
        })
    return rows


async def cancel_custom_exit_by_perm_id(
    client: IbClient, perm_id: int
) -> Dict:
    """Cancel the IB LIMIT order behind a custom exit by its permId."""
    try:
        result = await client.cancel_order_by_id(int(perm_id))
        return {"status": "cancelled", "perm_id": perm_id, "ib_result": result}
    except Exception as e:
        logger.exception("Failed to cancel custom exit perm_id=%s", perm_id)
        return {"status": "error", "perm_id": perm_id, "message": str(e)}


# ----------------------------------------------------------------------
# Fill handler — invoked by main.py's OrderTracker bridge
# ----------------------------------------------------------------------
async def handle_custom_exit_fill(
    client: IbClient, symbol: str, trim_percentage: float, filled_qty: int
) -> None:
    """
    Mirror the strategy-based exit flow's STP behaviour:
      - trim < 1.0  -> resize STP to remaining + move to BE (avgcost)
      - trim >= 1.0 -> cancel the STP outright

    Pure-IB; no DB. Called only after we've confirmed the filled order's
    orderRef matches CUSTOM_EXIT.
    """
    position = await client.get_position_by_symbol(symbol)
    existing_stp_order = await client.get_stp_order_by_symbol(symbol)

    if trim_percentage >= 1.0:
        if existing_stp_order is None:
            logger.info(
                "No STP found to cancel after custom 100%% exit | symbol=%s",
                symbol,
            )
        else:
            await client.cancel_order_by_id(existing_stp_order["orderid"])
            logger.info(
                "Cancelled STP after custom 100%% exit | symbol=%s order_id=%s",
                symbol, existing_stp_order["orderid"],
            )
        return

    # Partial
    if existing_stp_order is None:
        logger.info(
            "No STP found to modify after custom partial exit | symbol=%s",
            symbol,
        )
        return

    if position and position.get("position") is not None:
        remaining_qty = abs(int(position["position"]))
    else:
        remaining_qty = max(0, filled_qty)

    stp_order_id = existing_stp_order["orderid"]
    await client.modify_stp_order_by_id(stp_order_id, remaining_qty)
    if position and position.get("avgcost") is not None:
        await client.move_stp_auxprice_to_avgcost(
            order_id=stp_order_id,
            new_auxprice=round(position["avgcost"], 2),
        )
    logger.info(
        "Resized STP after custom partial exit | symbol=%s remaining=%s "
        "order_id=%s",
        symbol, remaining_qty, stp_order_id,
    )
