import asyncio
import logging
from attr import ib
from ib_async import IB
import pytz


class PortfolioService:
    def __init__(self, ib: IB):
        self.ib = ib

    async def get_positions(self) -> list[dict]:
        """
        Fetch all positions asynchronously and return as a list of dicts.
        Only includes non-zero positions.
        """
        try:
            positions = await self.ib.reqPositionsAsync()  # async fetch positions
            await asyncio.sleep(0.1)  # ensure data is populated

            result = [
                {
                    "Account": p.account,
                    "Symbol": p.contract.symbol if p.contract else None,
                    "SecType": p.contract.secType if p.contract else None,
                    "Currency": p.contract.currency if p.contract else None,
                    "Position": p.position,
                    "AvgCost": p.avgCost
                }
                for p in positions
                if p.position != 0
            ]

            logging.info(f"Fetched positions: {result}")
            return result

        except Exception as e:
            logging.error(f"Error fetching positions: {e}")
            return []

    async def get_orders(self) -> list[dict]:
        """
        Fetch all open orders asynchronously and return as a list of dicts.
        """
        try:
            trades = await self.ib.reqAllOpenOrdersAsync()  # async fetch trades
            await asyncio.sleep(0.1)  # small delay to ensure data is populated

            orders = [
                {
                    "OrderId": t.order.permId if t.order else None,
                    "Symbol": t.contract.symbol if t.contract else None,
                    "Action": t.order.action if t.order else None,
                    "OrderType": t.order.orderType if t.order else None,
                    "TotalQty": t.order.totalQuantity if t.order else None,
                    "LmtPrice": getattr(t.order, "lmtPrice", None) if t.order else None,
                    "AuxPrice": getattr(t.order, "auxPrice", None) if t.order else None,
                    "Status": t.orderStatus.status if t.orderStatus else None,
                    "Filled": t.orderStatus.filled if t.orderStatus else None,
                    "Remaining": t.orderStatus.remaining if t.orderStatus else None
                }
                for t in trades
            ]

            logging.info(f"Fetched orders: {orders}")
            return orders

        except Exception as e:
            logging.error(f"Error fetching orders: {e}")
            return []

    async def get_account_summary(self) -> dict:
        """
        Fetch account summary asynchronously and return as a dict.
        """
        try:
            summary = await self.ib.accountSummaryAsync()
            return {item.tag: item.value for item in summary}
        except Exception as e:
            logging.error(f"Error fetching account summary: {e}")
            return {}

    async def get_trades(self) -> list[dict]:
        """
        Fetch all executed trades (completed fills) asynchronously from IB.
        Converts execution time to Helsinki timezone and returns a list of dicts.
        """
        try:
            helsinki_tz = pytz.timezone("Europe/Helsinki")

            # Async fetch all trades
            trades = await self.ib.trades()
            await asyncio.sleep(0.1)  # small delay to ensure all data is populated

            executed = []

            for t in trades:
                if not t.fills:
                    continue

                for fill in t.fills:
                    if not fill.execution:
                        continue

                    # Convert IB timestamp (UTC) → Helsinki
                    time_utc = fill.execution.time  # datetime in UTC
                    time_helsinki = time_utc.astimezone(helsinki_tz)

                    executed.append({
                        "TradeId": t.order.permId if t.order else None,
                        "Symbol": t.contract.symbol if t.contract else None,
                        "SecType": t.contract.secType if t.contract else None,
                        "Action": fill.execution.side if fill.execution else None,
                        "Quantity": fill.execution.shares if fill.execution else None,
                        "Price": fill.execution.price if fill.execution else None,
                        "Time": time_helsinki.isoformat(),
                        "Exchange": fill.execution.exchange if fill.execution else None,
                        "Commission": (
                            fill.commissionReport.commission
                            if fill.commissionReport else None
                        ),
                    })

            logging.info(f"Fetched executed trades: {len(executed)}")
            return executed

        except Exception as e:
            logging.error(f"Error fetching executed trades: {e}")
            return []
        
