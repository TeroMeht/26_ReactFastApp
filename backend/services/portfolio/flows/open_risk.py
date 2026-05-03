import asyncio
import logging
from typing import List
from services.portfolio.ib_client import IbClient
from db.exits import fetch_exits_by_symbol
from schemas.api_schemas import (
    OpenPosition,
)

logger = logging.getLogger(__name__)



async def process_openrisktable(client: IbClient, db_conn) -> List[OpenPosition]:
    # Fetch everything concurrently (faster)
    positions, account_summary = await asyncio.gather(
        client.get_positions(),
        client.get_account_summary(),
    )

    if not positions:
        return []

    netliq = float(account_summary.get("NetLiquidation", 0))

    portfolio_positions: List[OpenPosition] = []

    for pos in positions:
        try:
            symbol = pos.get("symbol")
            contract_type = pos.get("sectype")
            position = float(pos.get("position", 0))
            avgcost = float(pos.get("avgcost", 0))

            size = round(abs(position * avgcost), 2)
            allocation = (
                round((size / netliq) * 100, 2)
                if netliq > 0
                else None
            )

            # Find STOP order for this symbol
            stop_order = await client.get_stp_order_by_symbol(symbol)

            # Collect the names of every strategy currently armed for
            # this symbol. Empty list means nothing armed.
            exit_rows = await fetch_exits_by_symbol(db_conn, symbol)
            exit_strategies = [r["strategy"] for r in exit_rows]

            if stop_order and stop_order.get("auxprice") is not None:
                aux_price = float(stop_order.get("auxprice"))
                open_risk = round(abs(position * (aux_price - avgcost)), 2)
            else:
                aux_price = 0.0
                open_risk = 999_999_999  # no stop = unlimited risk

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

        except Exception as e:
            logger.error("Error processing %s: %s", pos.get("symbol"), e)
            continue
    logger.info(portfolio_positions)

    return portfolio_positions
