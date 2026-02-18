import asyncio
from ib_async import IB,Stock,LimitOrder, StopOrder,MarketOrder
import pytz
import logging
from services.orders import Order, build_order, calculate_position_size
from db.exits import fetch_exit_by_symbol,delete_exit_request
import datetime
from datetime import datetime, timedelta
from core.config import settings
from schemas.api_schemas import AddRequest, EntryRequest,EntryRequestResponse,AddRequestResponse,ExitRequest, ExitRequestResponseIB, ModifyOrderRequest, ModifyOrderByIdRequest
from dataclasses import dataclass, asdict,field
from typing import Optional,List


logger = logging.getLogger(__name__)

@dataclass
class PortfolioPosition:
    Symbol: str
    Allocation: Optional[float]
    Size: float
    AvgCost: float
    AuxPrice: Optional[float] = field(default=0.0)
    Position: float = 0.0
    OpenRisk: Optional[float] = field(default=0.0)




class PortfolioService:
    def __init__(self, ib: IB,db_conn):
        self.ib = ib
        self.db_conn = db_conn


    def create_order(self, payload: dict) -> Order:
        """
        Build and validate an Order from request payload.
        """
        return build_order(payload)


# Basic functions to fetch positions, orders, account summary, and trades from IB asynchronously.
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
                    "account": p.account,
                    "symbol": p.contract.symbol if p.contract else None,
                    "sectype": p.contract.secType if p.contract else None,
                    "currency": p.contract.currency if p.contract else None,
                    "position": p.position,
                    "avgcost": p.avgCost
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
            await asyncio.sleep(0.5)  # small delay to ensure data is populated

            orders = [
                {
                    "orderid": t.order.permId if t.order else None,
                    "symbol": t.contract.symbol if t.contract else None,
                    "action": t.order.action if t.order else None,
                    "ordertype": t.order.orderType if t.order else None,
                    "totalqty": t.order.totalQuantity if t.order else None,
                    "lmtprice": getattr(t.order, "lmtPrice", None) if t.order else None,
                    "auxprice": getattr(t.order, "auxPrice", None) if t.order else None,
                    "status": t.orderStatus.status if t.orderStatus else None,
                    "filled": t.orderStatus.filled if t.orderStatus else None,
                    "remaining": t.orderStatus.remaining if t.orderStatus else None
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
                        "tradeid": t.order.permId if t.order else None,
                        "symbol": t.contract.symbol if t.contract else None,
                        "sectype": t.contract.secType if t.contract else None,
                        "action": fill.execution.side if fill.execution else None,
                        "quantity": fill.execution.shares if fill.execution else None,
                        "price": fill.execution.price if fill.execution else None,
                        "time": time_helsinki.isoformat(),
                        "exchange": fill.execution.exchange if fill.execution else None,
                        "commission": (
                            fill.commissionReport.commission
                            if fill.commissionReport else None
                        ),
                    })

            logging.debug(f"Fetched executed trades: {executed}")
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
            await asyncio.sleep(1)

            bid = ticker.bid
            ask = ticker.ask

            # For testing
            # bid = 100
            # ask = 185

            logger.info(f"Fetched bid/ask for {symbol}: bid={bid}, ask={ask}")
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


# Helpers filtering functions and order placement logic
    async def get_stp_order_by_symbol(self, symbol: str) -> dict | None:
        """
        Return the first open STP (Stop) order for the given symbol.
        Returns None if not found.
        """
        try:
            orders = await self.get_orders()

            return next(
                (
                    o for o in orders
                    if o["symbol"]
                    and o["symbol"].upper() == symbol.upper()
                    and o["ordertype"]
                    and o["ordertype"].upper() == "STP"
                ),
                None
            )

        except Exception as e:
            logging.error(f"Error fetching STP order for {symbol}: {e}")
            return None
        
    async def get_mkt_order_by_symbol(self, symbol: str) -> dict | None:
        """
        Return the first open MKT (Market) order for the given symbol.
        Returns None if not found.
        """
        try:
            orders = await self.get_orders()

            return next(
                (
                    o for o in orders
                    if o["symbol"]
                    and o["symbol"].upper() == symbol.upper()
                    and o["ordertype"]
                    and o["ordertype"].upper() == "MKT"
                ),
                None
            )

        except Exception as e:
            logging.error(f"Error fetching MKT order for {symbol}: {e}")
            return None

    async def get_position_by_symbol(self, symbol: str) -> dict | None:
        """
        Return the non-zero position dict for the given symbol.
        Returns None if not found.
        """
        try:
            positions = await self.get_positions()

            return next(
                (
                    p for p in positions
                    if p["symbol"]
                    and p["symbol"].upper() == symbol.upper()
                ),
                None
            )

        except Exception as e:
            logging.error(f"Error fetching position for {symbol}: {e}")
            return None

    async def get_trades_by_symbol(self, symbol: str) -> list[dict]:
        """
        Fetch executed trades for a specific symbol.
        Reuses get_trades() to avoid extra IB calls.
        """
        try:
            # 1️⃣ Fetch all executed trades
            trades = await self.get_trades()
            if not trades:
                logging.info(f"No executed trades found at all for {symbol}")
                return []

            # 2️⃣ Filter trades by symbol (case-insensitive)
            symbol_trades = [
                t for t in trades
                if t.get("symbol") and t["symbol"].upper() == symbol.upper()
            ]

            # --- Find the latest trade ---
            def parse_time(trade):
                trade_time = trade.get("time")
                if isinstance(trade_time, str):
                    return datetime.fromisoformat(trade_time)
                return trade_time

            latest_trade = max(symbol_trades, key=parse_time)

            logging.info(f"Latest executed trade for {symbol}: {latest_trade}")
            return latest_trade

        except Exception as e:
            logging.error(f"Error fetching latest executed trade for {symbol}: {e}")
            return None



# Actions towards IB client: placing orders, modifying orders, and validation logic for entries and adds.
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

            logger.info(f"Bracket orders submitted for {order.symbol}: "
                f"parent={parent.orderId}, stoploss={stoploss.orderId}, "
                f"action={order.action}, quantity={order.position_size}, "
                f"entry={order.entry_price}, stop={order.stop_price}")
            

            return parent, stoploss

        except Exception as e:
            logging.error(f"Error in place_bracket_order for {order.symbol}: {e}")
            return None, None

    async def place_limit_order(self, order: Order):
        """
        Place a simple limit order asynchronously.
        """
        try:
            contract = Stock(
                symbol=order.symbol,
                exchange="SMART",
                currency="USD"
            )

            # Properly await qualification
            await self.ib.qualifyContractsAsync(contract)

            limit_order = LimitOrder(
                action=order.action,
                totalQuantity=order.position_size,
                lmtPrice=order.entry_price,
                orderId=self.ib.client.getReqId(),
                transmit=True,
            )

            self.ib.placeOrder(contract, limit_order)
            logger.info(f"Limit order submitted for {order.symbol}: "
                        f"orderId={limit_order.orderId}, "
                        f"action={order.action}, quantity={order.position_size}, "
                        f"price={order.entry_price}")

            return limit_order

        except Exception as e:
            logger.error(f"Error in place_limit_order for {order.symbol}: {e}")
            return None

# This is for closing position
    async def place_market_order(self, order:Order):
        """
        Place a market order asynchronously.
        """
        try:
            contract = Stock(
                symbol=order.symbol,
                exchange="SMART",
                currency="USD"
            )

            await self.ib.qualifyContractsAsync(contract)

            market_order = MarketOrder(
                action=order.action,
                totalQuantity=order.position_size,
                outsideRth=True,
                transmit=True,
            )

            self.ib.placeOrder(contract, market_order)
            logger.info(f"Market order submitted for {order.symbol}: "
                        f"orderId={market_order.orderId}, "
                        f"action={order.action}, quantity={order.position_size}")

            return market_order

        except Exception as e:
            logger.error(f"Error in place_market_order for {order.symbol}: {e}")
            return None




    async def modify_stp_order_by_id(self, order_id: int, new_qty: float) -> dict:
        """
        Modify the quantity of an open IB order using its permId.
        """
        try:
            # 1️⃣ Fetch all open orders
            open_orders = await self.ib.reqAllOpenOrdersAsync()
            await asyncio.sleep(0.5)

            # 2️⃣ Find order matching permId
            target_trade = next(
                (
                    t for t in open_orders
                    if t.order and t.order.permId == order_id
                ),
                None
            )

            if not target_trade:
                logging.warning(f"No open order found with permId {order_id}")
                return {"status": "not_found", "order_id": order_id}

            order = target_trade.order
            contract = target_trade.contract

            if not order or not contract:
                logging.error(f"Order or contract missing for permId {order_id}")
                return {
                    "status": "error",
                    "message": "Order or contract not found",
                    "order_id": order_id
                }

            # 3️⃣ Modify quantity
            order.totalQuantity = new_qty

            # 4️⃣ Qualify contract (required by IB)
            await self.ib.qualifyContractsAsync(contract)

            # 5️⃣ Place order again (same orderId updates existing order)
            self.ib.placeOrder(contract, order)

            await asyncio.sleep(0.5)  # small delay to ensure modification is processed
            logging.info(f"Modified order {order_id} → new quantity {new_qty}",
                         {"order_id": order_id, "symbol": contract.symbol, "new_qty": new_qty}
            )

            return {
                "status": "success",
                "order_id": order_id,
                "symbol": contract.symbol,
                "new_quantity": new_qty
            }

        except Exception as e:
            logging.error(f"Error modifying order {order_id}: {e}")
            return {
                "status": "error",
                "message": str(e),
                "order_id": order_id
            }

    async def move_stp_auxprice_to_avgcost(self, order_id: int, new_auxprice: float) -> dict:
        """
        Modify the auxPrice (stop price) of an open STP order to the given avg_cost.
        Uses permId to locate the order.
        """
        try:
            # 1️⃣ Fetch all open orders
            open_orders = await self.ib.reqAllOpenOrdersAsync()
            await asyncio.sleep(0.5)

            # 2️⃣ Find order matching permId
            target_trade = next(
                (
                    t for t in open_orders
                    if t.order and t.order.permId == order_id
                ),
                None
            )

            if not target_trade:
                logging.warning(f"No open order found with permId {order_id}")
                return {"status": "not_found", "order_id": order_id}

            order = target_trade.order
            contract = target_trade.contract


            # 4️⃣ Modify auxPrice (stop price)
            order.auxPrice = float(new_auxprice)

            # 5️⃣ Qualify contract (required by IB)
            await self.ib.qualifyContractsAsync(contract)

            # 6️⃣ Place order again (same orderId updates existing order)
            self.ib.placeOrder(contract, order)

            await asyncio.sleep(1)

            logging.info(
                f"Moved STP order {order_id} stop to new price {new_auxprice}",
                extra={
                    "order_id": order_id,
                    "symbol": contract.symbol,
                    "new_stop": new_auxprice
                }
            )

            return {
                "status": "success",
                "order_id": order_id,
                "symbol": contract.symbol,
                "new_stop_price": new_auxprice
            }

        except Exception as e:
            logging.error(f"Error modifying STP order {order_id}: {e}")
            return {
                "status": "error",
                "message": str(e),
                "order_id": order_id
            }
   
    async def move_stp_order_by_symbol(self, symbol: str):
        """
        Move the stop loss order for a given symbol to breakeven (avg cost).
        """
        try:
            # 1️⃣ Get existing STP order
            stp_order = await self.get_stp_order_by_symbol(symbol)

            # 2️⃣ Get current position (for avg cost)
            position = await self.get_position_by_symbol(symbol)

            order_id = stp_order.get("orderid")
            avgcost = position.get("avgcost")
            avgcost = round(avgcost,2)

            # 3️⃣ Move stop to breakeven
            result= await self.move_stp_auxprice_to_avgcost(
                order_id=order_id,
                new_auxprice=avgcost
            )
                # 3️⃣ If successful, return detailed response
            if result.get("status") == "success":
                return {
                    "status": "success",
                    "message": f"STP order for {symbol} moved to breakeven at price {avgcost}",
                    "symbol": symbol,
                    "order_id": order_id,
                    "new_stop_price": avgcost,
                }

            return result  # propagate error from modify function

        except Exception as e:
            logging.error(f"Error in move_stp_order_by_id for {symbol}: {e}")
            return {
                "status": "error",
                "message": str(e)
            }
        
    async def cancel_order_by_id(self, order_id: int) -> bool:
        """
        Cancel an open order by order ID.
        """
        try:
            open_trades = await self.ib.reqOpenOrdersAsync()
            print(open_trades)
            target = next((t for t in open_trades if t.order.permId == order_id), None)

            if not target:
                logger.warning(f"No open order found with orderId={order_id}")
                return False

            self.ib.cancelOrder(target.order)
            logger.info(f"Cancel request sent for orderId={order_id}")
            return True

        except Exception as e:
            logger.error(f"Error cancelling order {order_id}: {e}")
            return False


# Checks if user is trying to add to losing position. Won't allow that. 
    async def is_add_allowed(self, position: dict) -> dict:
        """
        Check if adding to a position is allowed.
        Returns allowed=True if current ask > avg cost, otherwise allowed=False.
        """
        try:
            symbol = position.get("symbol")
            avg_cost = position.get("avgcost")
            current_qty = position.get("position")

            # 2️⃣ Get current ask price
            market_data = await self.get_bid_ask_price(symbol)

            ask = market_data["ask"]

            # 3️⃣ Validate: allow if ask > avg cost
            if ask > avg_cost:
                return {
                    "allowed": True,
                    "symbol": symbol,
                    "message": f"Current ask ({ask}) is above avg cost ({avg_cost})",
                    "current_position": current_qty,
                    "avg_cost": avg_cost,
                    "ask": ask
                }
            else:
                return {
                    "allowed": False,
                    "symbol": symbol,
                    "message": f"Current ask ({ask}) is not above avg cost ({avg_cost})",
                    "current_position": current_qty,
                    "avg_cost": avg_cost,
                    "ask": ask
                }

        except Exception as e:
            logging.error(f"Error validating add for {symbol}: {e}")
            return {
                "allowed": False,
                "symbol": symbol,
                "message": str(e)
            }
        
    async def is_entry_allowed(self, latest_trade: dict | None) -> bool:
        """
        Returns True if a new entry is allowed based on the latest executed trade,
        otherwise False. Times are checked in Helsinki timezone.
        """
        threshold_minutes = settings.MAX_ENTRY_FREQUENCY_MINUTES
        helsinki_tz = pytz.timezone("Europe/Helsinki")
        now = datetime.now(helsinki_tz)

        try:
            if not latest_trade:
                logging.info("No executions found. Entry allowed.")
                return True

            trade_time = latest_trade["time"]

            # Convert string to datetime if needed
            if isinstance(trade_time, str):
                trade_time = datetime.fromisoformat(trade_time)

            elapsed = now - trade_time

            if elapsed <= timedelta(minutes=threshold_minutes):
                logging.info(f"Last execution was {elapsed}. Entry not allowed.")
                return False

            logging.info(f"Last execution was {elapsed}. Entry allowed.")
            return True

        except Exception as e:
            logging.exception("Error in is_entry_allowed")
            return False

    async def process_entry_request(self, payload: EntryRequest)-> EntryRequestResponse:
        """
        Process an entry request:
        - Check if a new entry is allowed based on past executed trades
        - Fetch current ask price
        - Calculate position size
        - Build order with correct size and price
        - Place bracket order
        """
        symbol = payload.symbol
        stop_price = payload.stop_price

        try:
            # --- Fetch executed trades only for this symbol ---
            executed_trades = await self.get_trades_by_symbol(symbol)

            # --- Check if entry is allowed ---
            validation = await self.is_entry_allowed(executed_trades)

            if validation == False:
                return EntryRequestResponse(
                    allowed=False,
                    message="Less than threshold limit passed from last execution",
                    symbol=symbol,
                )

            # --- Entry is allowed ---
            logger.info(f"Entry allowed for {symbol}")

            # --- Step 1: Get current ask price ---
            bid_ask = await self.get_bid_ask_price(symbol)
            entry_price = bid_ask["ask"]

            # --- Step 2: Calculate position size ---
            position_size = calculate_position_size(
                entry_price=entry_price,
                stop_price=stop_price,
                risk=settings.RISK  # Use configured risk
            )

            logger.info(f"Calculated position size: {position_size} for {symbol} at entry {entry_price}")

            # --- Step 3: Build order dataclass with correct size and price ---
            order_data = {
                "symbol": symbol,
                "entry_price": entry_price,
                "stop_price": stop_price,
                "position_size": position_size
            }
            order = self.create_order(order_data)

            # --- Step 4: Place bracket order ---
            parent, stop = await self.place_bracket_order(order)

            return EntryRequestResponse(
                allowed=True,
                message="Entry ok",
                symbol=symbol,
                parentOrderId=parent.orderId if parent else None,
                stopOrderId=stop.orderId if stop else None,
            )

        except Exception as e:
            logger.exception(f"Error processing entry request for {symbol}")
            return EntryRequestResponse(
                allowed=False,
                message=str(e),
                symbol=symbol,
            )

    async def process_add_request(self, payload: AddRequest)-> AddRequestResponse:
        """
        Process an add request:
        - Check if adding to the current position is allowed
        - If allowed, create a new order
        - Modify existing STP order for the symbol
        """
        # 1 Extract fields directly from AddRequest model
        symbol = payload.symbol
        total_risk = payload.total_risk

        try:
            
            # 2 Get existing position quantity
            position = await self.get_position_by_symbol(symbol)
            # Get existing stp order
            existing_stp_order = await self.get_stp_order_by_symbol(symbol)

            # 1 Check if adding is allowed to that position
            validation = await self.is_add_allowed(position)

            if not validation.get("allowed"):
                logger.info(f"Add not allowed for {symbol}: {validation.get('message')}")
                return AddRequestResponse(
                    allowed=False,
                    message=validation.get("message"),
                    symbol=symbol,
                )

        

            logger.info(f"Existing STP order for {symbol}: {existing_stp_order}")

            # Get existing aux price
            stp_order_aux_price = existing_stp_order.get("auxprice")
            stp_order_id = existing_stp_order.get("orderid")
            logger.info(f"Existing STP order aux price for {symbol}: {stp_order_aux_price}, orderId: {stp_order_id}")

            # Get existing position size
            existing_position = position.get("position")

            # Get current price
            bid_ask = await self.get_bid_ask_price(symbol)
            ask = bid_ask["ask"]
            # add orderin pitää olla kokonais- olemassa oleva
            # stop modify orderin pitää olla kokonais

                # --- Step 3: Recalculate position size ---
            total_size = calculate_position_size(
                    entry_price=ask,
                    stop_price=stp_order_aux_price,
                    risk=total_risk  # Use the configured risk value from settings
                )

            new_qty = total_size - existing_position # Tämän verran pitää lisätä
            
            if existing_position > total_size:
                return AddRequestResponse(
                        allowed=False,
                        message="Wanted position size is already in portfolio",
                        symbol=symbol,
                    )
            
            modified_stp_qty = total_size # Tähän uusi kokonaismäärä


            logger.info(f"Calculated new total position size: {total_size}, existing position: {existing_position}, new quantity to add: {new_qty}")
            # --- 2 Build the order dict ---
            order_data = {
                "symbol": symbol,
                "entry_price": ask,
                "stop_price": stp_order_aux_price,
                "position_size": new_qty
            }

            # --- 3 Create new Order dataclass ---
            new_order = self.create_order(order_data)
            place_result = await self.place_limit_order(new_order)
            modify_result = await self.modify_stp_order_by_id(stp_order_id, modified_stp_qty)


            return AddRequestResponse(
                allowed=True,
                message="New order placed and STP modified successfully",
                symbol=symbol,
                new_order=new_order,
                place_result=place_result,
                modified_stp_qty=modify_result.get("new_quantity"),
            )

        except Exception as e:
            logger.exception(f"Error processing add request for {symbol}")
            return AddRequestResponse(
                allowed=False,
                message=str(e),
                symbol=symbol,
            )



    async def process_openrisktable(self) -> List[PortfolioPosition]:
        """
        Build risk objects for each portfolio position:
        - OpenRisk (based on stop)
        - NetLiquidity% exposure
        - Size (absolute position value)
        """

        # Fetch everything concurrently (faster)
        positions, orders, account_summary = await asyncio.gather(
            self.get_positions(),
            self.get_orders(),
            self.get_account_summary()
        )

        if not positions:
            return []

        netliq = float(account_summary.get("NetLiquidation", 0))

        portfolio_positions: List[PortfolioPosition] = []

        for pos in positions:
            try:
                symbol = pos.get("symbol")
                position = float(pos.get("position", 0))
                avgcost = float(pos.get("avgcost", 0))

                if not symbol or position == 0:
                    continue

                size = round(abs(position * avgcost), 2)
                allocation = (
                    round((size / netliq) * 100, 2)
                    if netliq > 0
                    else None
                )

                # Find STOP order for this symbol
                stop_order = next(
                    (
                        o for o in orders
                        if o.get("symbol") == symbol
                        and o.get("ordertype") == "STP"
                    ),
                    None
                )

                if stop_order and stop_order.get("auxprice") is not None:
                    aux_price = float(stop_order.get("auxprice"))
                    open_risk = round(abs(position * (aux_price - avgcost)), 2)
                else:
                    aux_price = 0.0
                    open_risk = 999_999_999  # no stop = unlimited risk

                portfolio_positions.append(
                    PortfolioPosition(
                        Symbol=symbol,
                        Allocation=allocation,
                        Size=size,
                        AvgCost=avgcost,
                        AuxPrice=aux_price,
                        Position=position,
                        OpenRisk=open_risk
                    )
                )

            except Exception as e:
                logger.error("Error processing %s: %s", pos.get("symbol"), e)
                continue
        logger.info("Built open risk table for %d positions", len(portfolio_positions))

        return portfolio_positions



    async def process_exit_request(self, payload: ExitRequest) -> ExitRequestResponseIB:
        logger.info("Processing exit request | symbol=%s alarm=%s", payload.symbol, payload.alarm)

        alarm = payload.alarm

        # Check exit requested
        exit_request = await fetch_exit_by_symbol(self.db_conn, payload.symbol)
        logger.info("Fetched exit_request from DB | symbol=%s result=%s", payload.symbol, exit_request)

        if alarm == "euforia_exit" and exit_request and exit_request["exitrequested"]:
            logger.info("Valid euforia_exit detected and exitrequested=True | symbol=%s", payload.symbol)

            # Check position
            position = await self.get_position_by_symbol(payload.symbol)
            logger.info("Fetched position | symbol=%s position=%s", payload.symbol, position)

            if not position:
                logger.warning("No open position found | symbol=%s", payload.symbol)
                return ExitRequestResponseIB(
                    symbol=payload.symbol,
                    message=f"No open position found for {payload.symbol}."
                )

            shares = position["position"]
            logger.info("Position shares=%s | symbol=%s", shares, payload.symbol)

            # Decide direction
            if shares > 0:
                action = "SELL"
            elif shares < 0:
                action = "BUY"
            else:

                return ExitRequestResponseIB(
                    symbol=payload.symbol,
                    message=f"Position size is zero for {payload.symbol}."
                )

            # Check for existing market order
            existing_mkt_order = await self.get_mkt_order_by_symbol(payload.symbol)
            if existing_mkt_order:
                logger.info(
                    "Market order already exists | symbol=%s order=%s",
                    payload.symbol,
                    existing_mkt_order
                )

                return ExitRequestResponseIB(
                    symbol=payload.symbol,
                    message=f"Market order already exists for {payload.symbol}."
                    # If available:
                    # order_id=existing_mkt_order.order.orderId
                )

            # Place market order
            order = Order(
                symbol=payload.symbol,
                action=action,
                position_size=abs(int(shares))
            )


            await self.place_market_order(order)
            await delete_exit_request(self.db_conn,payload.symbol)

            return ExitRequestResponseIB(
                symbol=payload.symbol,
                message=f"Market order placed to {action} {abs(shares)} shares of {payload.symbol}."
            )

        elif not exit_request:
            return ExitRequestResponseIB(
                symbol=payload.symbol,
                message=f"No exit request record found for {payload.symbol}."
            )

        elif not exit_request["exitrequested"]:
            logger.info("Exit request exists but exitrequested=False | symbol=%s", payload.symbol)
            return ExitRequestResponseIB(
                symbol=payload.symbol,
                message=f"Exit not requested for {payload.symbol}."
            )

        else:
            return ExitRequestResponseIB(
                symbol=payload.symbol,
                message=f"Unhandled exit request state: {payload.alarm}"
            )
