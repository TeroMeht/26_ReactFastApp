"""
Open-risk listing.

Read-only view of currently held positions plus the open risk implied by
each position's STP. Fetches positions, account summary, and the full
open-orders book once (concurrently) so the per-position loop stays
local — previously this called get_stp_order_by_symbol per position,
which re-fetched all open orders every time (O(N) round trips).
"""

import asyncio
import logging
from typing import List

from services.portfolio.ib_client import IbClient
from db.exits import fetch_exits_by_symbol
from schemas.api_schemas import OpenPosition

logger = logging.getLogger(__name__)


def _index_stp_orders_by_symbol(orders: list[dict]) -> dict[str, dict]:
    """Build {SYMBOL: order} for every open STP / STP LMT order."""
    index: dict[str, dict] = {}
    for o in orders:
        sym = o.get("symbol")
        otype = (o.get("ordertype") or "").upper()
        if not sym or otype not in ("STP", "STP LMT"):
            continue
        # First STP per symbol wins, matching get_stp_order_by_symbol semantics.
        index.setdefault(sym.upper(), o)
    return index


async def process_openrisktable(client: IbClient, db_conn) -> List[OpenPosition]:
    """
    Build the open-risk table. One IB call each for positions, account
    summary, and open orders, then a per-position DB call for armed exit
    strategies.
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

    netliq = float(account_summary.get("NetLiquidation", 0))
    stp_by_symbol = _index_stp_orders_by_symbol(all_orders or [])

    portfolio_positions: List[OpenPosition] = []

    for pos in positions:
        symbol = pos.get("symbol")
        try:
            contract_type = pos.get("sectype")
            position = float(pos.get("position", 0))
            avgcost = float(pos.get("avgcost", 0))

            size = round(abs(position * avgcost), 2)
            allocation = (
                round((size / netliq) * 100, 2)
                if netliq > 0
                else None
            )

            stop_order = stp_by_symbol.get((symbol or "").upper())

            exit_rows = await fetch_exits_by_symbol(db_conn, symbol)
            exit_strategies = [r["strategy"] for r in exit_rows]

            if stop_order and stop_order.get("auxprice") is not None:
                aux_price = float(stop_order.get("auxprice"))
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
