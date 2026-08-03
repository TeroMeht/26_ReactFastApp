"""
Open-risk listing.

Read-only view of currently held positions plus the open risk implied by
each position's STP. Fetches positions, account summary, and the full
open-orders book once (concurrently) so the per-position loop stays
local — previously this called get_stp_order_by_symbol per position,
which re-fetched all open orders every time (O(N) round trips).

Exit strategies for every held symbol are fetched in ONE DB query up
front (fetch_strategies_grouped_by_symbols) rather than one query per
position — same O(N) → O(1) collapse.
"""

import asyncio
import logging
from typing import List

from services.portfolio.ib_client import IbClient, OpenOrder
from db.exits import fetch_strategies_grouped_by_symbols
from schemas.api_schemas import OpenPosition

logger = logging.getLogger(__name__)


def _index_stp_orders_by_symbol(orders: list[OpenOrder]) -> dict[str, OpenOrder]:
    """Build {SYMBOL: order} for every open STP / STP LMT order."""
    index: dict[str, OpenOrder] = {}
    for o in orders:
        sym = o.symbol
        otype = (o.ordertype or "").upper()
        if not sym or otype not in ("STP", "STP LMT"):
            continue
        # First STP per symbol wins, matching get_stp_order_by_symbol semantics.
        index.setdefault(sym.upper(), o)
    return index


async def process_openrisktable(client: IbClient, db_conn) -> List[OpenPosition]:
    """
    Build the open-risk table. One IB call each for positions, account
    summary, and open orders (concurrent), plus one DB query for all
    exit strategies keyed by held symbol.
    """
    try:
        positions, account_summary, all_orders = await asyncio.gather(
            client.get_positions(),
            client.get_account_summary(),
            client.get_orders(),
        )
    except Exception:
        logger.exception("Failed to fetch portfolio snapshot from IB")
        return []

    if not positions:
        return []

    netliq = account_summary.net_liquidation
    stp_by_symbol = _index_stp_orders_by_symbol(all_orders or [])

    # One-shot DB fetch of armed strategies for every held symbol. Empty
    # list for symbols with no armed strategies (matches the old per-symbol
    # fetch that returned an empty result).
    held_symbols = [p.symbol for p in positions if p.symbol]
    strategies_by_symbol = await fetch_strategies_grouped_by_symbols(
        db_conn, held_symbols
    )

    portfolio_positions: List[OpenPosition] = []

    for pos in positions:
        symbol = pos.symbol
        try:
            contract_type = pos.sectype
            position = float(pos.position)
            avgcost = float(pos.avgcost)

            size = round(abs(position * avgcost), 2)
            allocation = (
                round((size / netliq) * 100, 2)
                if netliq > 0
                else None
            )

            symbol_u = (symbol or "").upper()
            stop_order = stp_by_symbol.get(symbol_u)
            exit_strategies = strategies_by_symbol.get(symbol_u, [])

            if stop_order and stop_order.auxprice is not None:
                aux_price = float(stop_order.auxprice)
                open_risk = round(abs(position * (aux_price - avgcost)), 2)
            else:
                aux_price = 0.0
                open_risk = 999_999_999  # no stop = unbounded risk

            portfolio_positions.append(
                OpenPosition(
                    exit_strategies=exit_strategies,
                    symbol=symbol,
                    contract_type=contract_type,
                    allocation=allocation,
                    size=size,
                    avgcost=avgcost,
                    auxprice=aux_price,
                    position=position,
                    openrisk=open_risk,
                )
            )

        except Exception:
            logger.exception("Error processing %s", symbol)
            continue

    logger.info(
        "Open-risk table built: %d positions, %d with STP",
        len(portfolio_positions),
        sum(1 for p in portfolio_positions if p.auxprice),
    )

    return portfolio_positions
