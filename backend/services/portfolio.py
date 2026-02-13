import asyncio
from ib_async import IB,Stock,LimitOrder, StopOrder
import pytz
import logging
from services.orders import Order, build_order, calculate_position_size
import datetime
from datetime import datetime, timedelta
from core.config import settings, Settings
settings = Settings()



logger = logging.getLogger(__name__)


class PortfolioService:
    def __init__(self, ib: IB):
        self.ib = ib


    def create_order(self, payload: dict) -> Order:
        """
        Build and validate an Order from request payload.
        """
        return build_order(payload)



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
            trades = self.ib.trades()
            await asyncio.sleep(1)  # small delay to ensure all data is populated

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

            logging.info(f"Fetched executed trades: {executed}")
            return executed

        except Exception as e:
            logging.error(f"Error fetching executed trades: {e}")
            return []

    async def get_bid_ask_price(self, symbol: str) -> dict | None:
        try:
            contract = Stock(symbol=symbol, exchange="SMART", currency="USD")

            # Async qualify
            await self.ib.qualifyContractsAsync(contract)

            # Request market data (subscription)
            ticker = self.ib.reqMktData(contract, "", False, False)

            # Wait briefly for first tick
            await asyncio.sleep(0.5)

            bid = ticker.bid
            ask = ticker.ask

            # Cancel subscription (important)
            self.ib.cancelMktData(contract)

            if bid is None and ask is None:
                logging.warning(f"No bid/ask data available for {symbol}")
                return None

            return {
                "symbol": symbol,
                "bid": bid,
                "ask": ask
            }

        except Exception as e:
            logging.error(f"Error fetching bid/ask for {symbol}: {e}")
            return None

    async def place_bracket_order(self, order: Order):
        """
        Asynchronous bracket order placement (parent + stoploss).
        No threads used.
        """
        try:
            contract = Stock(
                symbol=order.symbol,
                exchange="SMART",
                currency="USD"
            )

            # Properly await qualification
            await self.ib.qualifyContractsAsync(contract)

            reverse_action = "SELL" if order.action.upper() == "BUY" else "BUY"

            parent = LimitOrder(
                action=order.action,
                totalQuantity=order.position_size,
                lmtPrice=order.entry_price,
                orderId=self.ib.client.getReqId(),
                transmit=False,  # IMPORTANT for bracket logic
            )

            stoploss = StopOrder(
                action=reverse_action,
                totalQuantity=order.position_size,
                stopPrice=order.stop_price,
                orderId=self.ib.client.getReqId(),
                parentId=parent.orderId,
                transmit=True,  # Last order transmits entire bracket
                outsideRth=True,
            )

            # 1️⃣ Place parent
            self.ib.placeOrder(contract, parent)

            await asyncio.sleep(0.5)  # small delay to ensure parent is processed

            # 2️⃣ Place stop (transmit=True sends both)
            self.ib.placeOrder(contract, stoploss)

            logger.info(
                f"Bracket orders submitted for {order.symbol}: "
                f"parent={parent.orderId}, stoploss={stoploss.orderId}"
            )

            return parent, stoploss

        except Exception as e:
            logging.error(f"Error in place_bracket_order for {order.symbol}: {e}")
            return None, None





    async def process_entry_request(self, payload: dict):
            """
            Process entry request:
            - Validate payload
            - Check if entry is allowed based on last executions
            - Place bracket order if allowed
            """
            try:
                order = self.create_order(payload)

                # --- Fetch executed trades ---
                executed_trades = await self.get_trades()

                # --- Check if entry is allowed ---
                allowed, message = self.is_entry_allowed(executed_trades, order.symbol)

                if not allowed:
                    logger.info(message)
                    return None, None, allowed,message

                # --- Step 2: Update order entry price dynamically ---
                bid_ask = await self.get_bid_ask_price(order.symbol)

                if not bid_ask or bid_ask.get("ask") is None:
                    raise ValueError(f"Could not fetch latest ask price for {order.symbol}")

                order.entry_price = bid_ask["ask"]

                # --- Step 3: Recalculate position size ---
                order.position_size = calculate_position_size(
                    entry_price=order.entry_price,
                    stop_price=order.stop_price,
                    risk=settings.RISK  # Use the configured risk value from settings
                )

                logger.debug(f"Updated order: symbol={order.symbol}, entry={order.entry_price}, size={order.position_size}")

                # --- Place bracket order ---
                parent, stop = await self.place_bracket_order(order)
                return parent, stop, allowed,message

            except Exception as e:
                logger.error(f"Error processing entry request: {e}")
                return None, None, None,str(e)





    def is_entry_allowed(self, executed_trades: list[dict], symbol: str):
        """
        Returns (allowed: bool, message: str)

        Entry is allowed when:
        - No previous executions for this symbol, OR
        - The latest execution is older than max_entry_freq_minutes
        """
        try:
            threshold_minutes = settings.MAX_ENTRY_FREQUENCY_MINUTES


            if not executed_trades:
                message = f"No executions found. Entry allowed for {symbol}."
                logger.info(message)
                return True, message

            # Filter by symbol
            symbol_trades = [t for t in executed_trades if t.get("Symbol") == symbol]

            if not symbol_trades:
                message = f"No previous executions for {symbol}. Entry allowed."
                logger.info(message)
                return True, message

            # Latest execution time
            latest_trade = max(symbol_trades, key=lambda t: t["Time"])
            last_trade_time = latest_trade["Time"]
            if isinstance(last_trade_time, str):
                last_trade_time = datetime.fromisoformat(last_trade_time)

            # Current Helsinki time
            helsinki_now = datetime.now(pytz.timezone("Europe/Helsinki"))

            elapsed = helsinki_now - last_trade_time
            minutes = int(elapsed.total_seconds() // 60)
            seconds = int(elapsed.total_seconds() % 60)

            if elapsed <= timedelta(minutes=threshold_minutes):
                message = (
                    f"Last execution was: "
                    f"{minutes}m {seconds}s ago (limit: {threshold_minutes} minutes)"
                )
                logger.info(message)
                return False, message

            message = (
                f"Entry allowed for {symbol}: last execution was "
                f"{minutes}m {seconds}s ago (limit: {threshold_minutes} minutes)"
            )
            logger.info(message)
            return True, message

        except Exception as e:
            message = f"Error in is_entry_allowed: {e}"
            logger.error(message)
            return False, message