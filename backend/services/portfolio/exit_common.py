"""
Shared exit-flow plumbing.

Both the strategy-based exit flow (services.portfolio.flows.exit) and the
user-initiated custom exit flow (services.custom_exits) follow the same
post-fill pattern:

    1. Place an exit order (MKT for strategy, LMT for custom), tagged
       with orderRef = "EXIT:<trim_percentage>".
    2. When IB reports the order as Filled, OrderTracker's fill bridge
       (main.py) calls `handle_exit_fill` here.
    3. `handle_exit_fill` adjusts the protective STP:
         - trim >= 1.0 -> cancel the STP outright
         - trim <  1.0 -> resize the STP to the remaining position

No move-to-breakeven, no DB lookup; the tag itself encodes the trim.
"""

import logging
from typing import Optional

from services.portfolio.ib_client import IbClient

logger = logging.getLogger(__name__)


EXIT_PREFIX = "EXIT"


# ----------------------------------------------------------------------
# orderRef helpers — single source of truth for the tag format
# ----------------------------------------------------------------------
def build_exit_ref(trim_percentage: float) -> str:
    """Encode trim into orderRef so the fill listener can recover it."""
    return f"{EXIT_PREFIX}:{trim_percentage}"


def parse_exit_ref(order_ref: Optional[str]) -> Optional[float]:
    """
    Return the trim_percentage encoded in an EXIT orderRef, or None if
    this orderRef wasn't one of ours / is malformed.
    """
    if not order_ref or not order_ref.startswith(f"{EXIT_PREFIX}:"):
        return None
    _, _, rest = order_ref.partition(":")
    try:
        return float(rest)
    except (TypeError, ValueError):
        return None


def is_exit_ref(order_ref: Optional[str]) -> bool:
    return parse_exit_ref(order_ref) is not None


# ----------------------------------------------------------------------
# Fill handler — invoked by main.py's OrderTracker bridge
# ----------------------------------------------------------------------
async def handle_exit_fill(
    client: IbClient, symbol: str, trim_percentage: float
) -> None:
    """
    Adjust the protective STP after any tagged exit fills.

      - trim >= 1.0 -> cancel the STP outright
      - trim <  1.0 -> resize the STP to the remaining position

    Pure-IB; no DB. Called only after OrderTracker confirms the filled
    order's orderRef matches our EXIT tag.
    """
    existing_stp_order = await client.get_stp_order_by_symbol(symbol)

    if trim_percentage >= 1.0:
        if existing_stp_order is None:
            logger.info(
                "No STP found to cancel after full exit | symbol=%s",
                symbol,
            )
        else:
            await client.cancel_order_by_id(existing_stp_order["orderid"])
            logger.info(
                "Cancelled STP after full exit | symbol=%s order_id=%s",
                symbol, existing_stp_order["orderid"],
            )
        return

    # Partial
    if existing_stp_order is None:
        logger.info(
            "No STP found to modify after partial exit | symbol=%s",
            symbol,
        )
        return

    position = await client.get_position_by_symbol(symbol)
    if not position or position.get("position") is None:
        logger.info(
            "No position found after partial exit; leaving STP as-is | "
            "symbol=%s",
            symbol,
        )
        return

    remaining_qty = abs(int(position["position"]))
    if remaining_qty <= 0:
        logger.info(
            "Position is zero after partial exit; cancelling STP | "
            "symbol=%s",
            symbol,
        )
        await client.cancel_order_by_id(existing_stp_order["orderid"])
        return

    stp_order_id = existing_stp_order["orderid"]
    await client.modify_stp_order_by_id(stp_order_id, remaining_qty)
    logger.info(
        "Resized STP after partial exit | symbol=%s remaining=%s "
        "order_id=%s",
        symbol, remaining_qty, stp_order_id,
    )
