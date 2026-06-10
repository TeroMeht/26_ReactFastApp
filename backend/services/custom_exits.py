"""
Custom price-target exit service — IB-only, no DB.

A custom exit is a real IB LIMIT order placed on the user's behalf at a
target price for a fraction of their open position. We tag every such
order with `orderRef = "EXIT:<trim>"` (see exit_common.build_exit_ref)
so we can:
  - enumerate them straight from IB's open orders (no DB scan),
  - identify them on fill (the OrderTracker fill bridge runs
    exit_common.handle_exit_fill, shared with strategy-based exits),
  - cancel by permId without needing any local bookkeeping.
"""

import logging
from decimal import Decimal
from typing import Dict, List, Optional

from services.orders import Order
from services.portfolio.ib_client import IbClient
from services.portfolio.exit_common import build_exit_ref, parse_exit_ref

logger = logging.getLogger(__name__)


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

    order = Order(
        symbol=symbol.upper(),
        action=action,
        position_size=qty,
        contract_type=contract_type,
        entry_price=float(target_price),  # place_limit_order maps entry_price -> lmtPrice
    )

    order_ref = build_exit_ref(trim_f)
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

        tagged_trim = parse_exit_ref(o.get("orderref"))
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


# Fill-time STP adjustment lives in services.portfolio.exit_common; the
# OrderTracker fill bridge in main.py routes EXIT-tagged fills to it.
