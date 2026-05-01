import asyncio
from blinker import signal
from ib_async import IB,Stock,LimitOrder, StopOrder,MarketOrder,CFD
import pytz
import logging
from services.orders import Order, build_order, calculate_position_size
from db.exits import (
    fetch_exits_by_symbol,
    update_exit_request,
    delete_exit_request,
    delete_exit_requests_by_symbol,
)
import datetime
from datetime import datetime, timedelta
from core.config import settings
from schemas.api_schemas import AddRequest, EntryRequest,EntryRequestResponse,AddRequestResponse,ExitRequest, ExitRequestResponseIB, ModifyOrderRequest, ModifyOrderByIdRequest, OpenPosition
from typing import Optional,List
import math
import subprocess
from datetime import date
from collections import defaultdict

logger = logging.getLogger(__name__)


# Per-symbol asyncio locks. Two alarms hitting the same symbol concurrently
# (e.g., vwap_exit and endofday_exit firing in the same tick) would otherwise
# race the position read / market-order placement / STP modify and could
# over-exit. The lock serializes them.
#
# Module-level dict is fine because asyncio is single-threaded; setdefault is
# atomic relative to other coroutines that don't await between the lookup
# and the assignment.
_symbol_exit_locks: dict[str, asyncio.Lock] = {}


def _get_symbol_lock(symbol: str) -> asyncio.Lock:
    return _symbol_exit_locks.setdefault(symbol.upper(), asyncio.Lock())



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
                    "avgcost": round(p.avgCost, 2)
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
        Uses event-driven reqExecutions to ensure all data is populated before processing.
        Converts execution time to Helsinki timezone and returns a list of dicts.
        """
        try:
            helsinki_tz = pytz.timezone("Europe/Helsinki")

            # Request fresh executions and wait for IB to signal completion
            # reqExecutions triggers execDetailsEnd event when all data is delivered
            trades = await asyncio.wait_for(
                self.ib.reqExecutionsAsync(),
                timeout=10.0
            )

            executed = []

            for fill in trades:
                # reqExecutionsAsync returns Fill objects directly
                if not fill.execution:
                    continue

                time_utc = fill.execution.time
                time_helsinki = time_utc.astimezone(helsinki_tz)

                # Resolve commission — may be None if report hasn't arrived yet
                commission = None
                if fill.commissionReport:
                    commission = fill.commissionReport.commission

                executed.append({
                    "tradeid":    fill.execution.permId,
                    "symbol":     fill.contract.symbol    if fill.contract   else None,
                    "sectype":    fill.contract.secType   if fill.contract   else None,
                    "action":     fill.execution.side,
                    "quantity":   fill.execution.shares,
                    "price":      fill.execution.price,
                    "time":       time_helsinki.isoformat(),
                    "exchange":   fill.execution.exchange,
                    "commission": commission,
                })

            logging.debug(f"Fetched executed trades: {executed}")
            return executed

        except asyncio.TimeoutError:
            logging.error("Timeout waiting for executions from IB (>10s)")
            return []
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

            # Normalize NaN or invalid values to 0
            if bid is None or (isinstance(bid, float) and math.isnan(bid)):
                bid = 0

            if ask is None or (isinstance(ask, float) and math.isnan(ask)):
                ask = 0

            logger.info(f"Fetched bid/ask for {symbol}: bid={bid}, ask={ask}")
            # Cancel subscription (important)
            self.ib.cancelMktData(contract)

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
                    and o["ordertype"].upper() == "STP" or o["ordertype"].upper() == "STP LMT"
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

    async def get_trades_by_symbol(self, symbol: str) -> dict | None:
        try:
            trades = await self.get_trades()
            if not trades:
                logging.debug(f"No executed trades found at all for {symbol}")
                return None

            symbol_trades = [
                t for t in trades
                if t.get("symbol") and t["symbol"].upper() == symbol.upper()
            ]

            if not symbol_trades:
                logging.debug(f"No executed trades found for {symbol}")
                return None

            def parse_time(trade):
                trade_time = trade.get("time")
                if isinstance(trade_time, str):
                    return datetime.fromisoformat(trade_time)
                return trade_time

            latest_trade = max(symbol_trades, key=parse_time)
            logging.debug(f"Latest executed trade for {symbol}: {latest_trade}")
            return latest_trade

        except Exception as e:
            logging.error(f"Error fetching latest executed trade for {symbol}: {e}")
            return None


    async def get_realized_pnl_today(self) -> dict:
        """
        Calculate total realized PnL for today across all symbols.
        """
        try:


            trades = await self.get_trades()
            today = date.today()

            today_trades = [
                t for t in trades
                if t["time"] and date.fromisoformat(t["time"][:10]) == today
            ]

            if not today_trades:
                return {"realized_pnl": 0.0, "total_commission": 0.0, "net_pnl": 0.0, "fills": 0}

            fills_by_symbol = defaultdict(list)
            for fill in today_trades:
                fills_by_symbol[fill["symbol"]].append(fill)

            total_realized = 0.0
            total_commission = 0.0
          #  log_lines = ["", "─" * 60, f"  PnL BREAKDOWN — {today}", "─" * 60]

            for symbol, fills in fills_by_symbol.items():
                fills.sort(key=lambda x: x["time"])
                buy_queue: list[tuple[float, float]] = []
                symbol_realized = 0.0
                symbol_commission = 0.0
                # log_lines.append(f"\n  {symbol}")
                # log_lines.append(f"  {'Time':<10} {'Action':<6} {'Qty':>6} {'Price':>8}  {'Matched':>6} {'Fill PnL':>10}")
                # log_lines.append(f"  {'─'*10} {'─'*6} {'─'*6} {'─'*8}  {'─'*6} {'─'*10}")

                for fill in fills:
                    qty        = float(fill["quantity"]   or 0)
                    price      = float(fill["price"]      or 0)
                    commission = float(fill["commission"] or 0)
                    action     = (fill["action"] or "").upper()
                    time_str   = fill["time"][11:16]  # HH:MM

                    symbol_commission += commission

                    if action in ("BUY", "BOT"):
                        buy_queue.append((qty, price))
                        # log_lines.append(
                        #     f"  {time_str:<10} {'BOT':<6} {qty:>6.0f} {price:>8.4f}  {'—':>6} {'—':>10}"
                        # )

                    elif action in ("SELL", "SLD"):
                        remaining = qty
                        fill_pnl = 0.0

                        while remaining > 0 and buy_queue:
                            buy_qty, buy_price = buy_queue[0]
                            matched = min(remaining, buy_qty)
                            leg_pnl = matched * (price - buy_price)
                            fill_pnl += leg_pnl
                            symbol_realized += leg_pnl
                            remaining -= matched
                            if matched == buy_qty:
                                buy_queue.pop(0)
                            else:
                                buy_queue[0] = (buy_qty - matched, buy_price)

                        # log_lines.append(
                        #     f"  {time_str:<10} {'SLD':<6} {qty:>6.0f} {price:>8.4f}  "
                        #     f"{qty - remaining:>6.0f} {fill_pnl:>+10.4f}"
                        # )

                symbol_net = symbol_realized - symbol_commission
                total_realized += symbol_realized
                total_commission += symbol_commission

                # log_lines.append(f"  {'─'*52}")
                # log_lines.append(f"  {'Gross PnL:':>36} {symbol_realized:>+10.4f}")
                # log_lines.append(f"  {'Commission:':>36} {-symbol_commission:>+10.4f}")
                # log_lines.append(f"  {'Net PnL:':>36} {symbol_net:>+10.4f}")
                if buy_queue:
                    open_qty = sum(q for q, _ in buy_queue)
            #         log_lines.append(f"  {'Open lots (unrealized):':>36} {open_qty:>10.0f} shares")

            # log_lines += [
            #     "",
            #     "─" * 60,
            #     f"  {'TOTAL GROSS PnL:':>36} {total_realized:>+10.4f}",
            #     f"  {'TOTAL COMMISSION:':>36} {-total_commission:>+10.4f}",
            #     f"  {'TOTAL NET PnL:':>36} {(total_realized - total_commission):>+10.4f}",
            #     f"  {'FILLS:':>36} {len(today_trades):>10}",
            #     "─" * 60,
            # ]

           # logging.info("\n".join(log_lines))

            return {
                "realized_pnl":     round(total_realized, 4),
                "total_commission": round(total_commission, 4),
                "net_pnl":          round(total_realized - total_commission, 4),
                "fills":            len(today_trades),
            }

        except Exception as e:
            logging.error(f"Error calculating today's realized PnL: {e}")
            return {"realized_pnl": 0.0, "total_commission": 0.0, "net_pnl": 0.0, "fills": 0}

    async def get_trades_with_pnl(self) -> list[dict]:
        """
        Returns a list of completed trades (round-trips) with PnL, sorted by time.
        Each dict represents one closed trade (BOT + matched SLD).
        """
        try:
            trades = await self.get_trades()
            today = date.today()

            today_trades = [
                t for t in trades
                if t["time"] and date.fromisoformat(t["time"][:10]) == today
            ]

            if not today_trades:
                return []

            fills_by_symbol = defaultdict(list)
            for fill in today_trades:
                fills_by_symbol[fill["symbol"]].append(fill)

            completed_trades = []

            for symbol, fills in fills_by_symbol.items():
                fills.sort(key=lambda x: x["time"])
                buy_queue: list[tuple[float, float, str]] = []  # (qty, price, time)

                for fill in fills:
                    qty        = float(fill["quantity"]   or 0)
                    price      = float(fill["price"]      or 0)
                    commission = float(fill["commission"] or 0)
                    action     = (fill["action"] or "").upper()
                    time_str   = fill["time"]

                    if action in ("BUY", "BOT"):
                        buy_queue.append((qty, price, time_str, commission))

                    elif action in ("SELL", "SLD"):
                        remaining = qty
                        sell_commission = commission

                        while remaining > 0 and buy_queue:
                            buy_qty, buy_price, buy_time, buy_commission = buy_queue[0]
                            matched = min(remaining, buy_qty)
                            gross_pnl = matched * (price - buy_price)

                            # Prorate commission based on matched qty
                            prorated_buy_commission = buy_commission * (matched / buy_qty)
                            prorated_sell_commission = sell_commission * (matched / qty)
                            total_commission = prorated_buy_commission + prorated_sell_commission
                            net_pnl = gross_pnl - total_commission

                            completed_trades.append({
                                "symbol":           symbol,
                                "entry_time":       buy_time,
                                "exit_time":        time_str,
                                "entry_price":      buy_price,
                                "exit_price":       price,
                                "quantity":         matched,
                                "gross_pnl":        round(gross_pnl, 4),
                                "commission":       round(total_commission, 4),
                                "net_pnl":          round(net_pnl, 4),
                                "is_loss":          net_pnl < 0,
                            })

                            remaining -= matched
                            if matched == buy_qty:
                                buy_queue.pop(0)
                            else:
                                buy_queue[0] = (buy_qty - matched, buy_price, buy_time, buy_commission - prorated_buy_commission)

            # Sort all completed trades by exit time
            completed_trades.sort(key=lambda x: x["exit_time"])
            return completed_trades

        except Exception as e:
            logging.error(f"Error calculating trade-by-trade PnL: {e}")
            return []

# Actions towards IB client: placing orders, modifying orders, and validation logic for entries and adds.
    async def place_bracket_order(self, order: Order):

        try:
            if order.contract_type == 'CFD':
                contract = CFD(symbol=order.symbol,
                            exchange="SMART",
                            currency="USD")
                
            elif order.contract_type == 'stock' or 'STK':
                contract = Stock(symbol=order.symbol,
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
                outsideRth=True,
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
            if order.contract_type == 'CFD':
                contract = CFD(symbol=order.symbol,
                            exchange="SMART",
                            currency="USD")
                
            elif order.contract_type == 'stock' or 'STK':
                contract = Stock(symbol=order.symbol,
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
                outsideRth=True,
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
    async def place_market_order(self, order: Order):
        """
        Place a market order asynchronously. Returns the ib_async Trade
        object on success (so callers can wait on its order status), or None
        on error.
        """
        try:
            if order.contract_type == 'CFD':
                contract = CFD(symbol=order.symbol,
                            exchange="SMART",
                            currency="USD")

            elif order.contract_type == 'stock' or 'STK':
                contract = Stock(symbol=order.symbol,
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

            trade = self.ib.placeOrder(contract, market_order)
            logger.info(f"Market order submitted for {order.symbol}: "
                        f"orderId={market_order.orderId}, "
                        f"action={order.action}, quantity={order.position_size}")

            return trade

        except Exception as e:
            logger.error(f"Error in place_market_order for {order.symbol}: {e}")
            return None

    async def _wait_for_order_done(self, trade, timeout: float = 15.0) -> str:
        """
        Poll an ib_async Trade until it reaches a terminal status.

        Returns the final status string. Treat 'Filled' as success and any of
        {'Cancelled', 'ApiCancelled', 'Inactive', 'Rejected', 'timeout'} as
        failure. Caller decides what to do (e.g., re-insert the exit_request
        row so the user can retry).
        """
        DONE_STATES = {"Filled", "Cancelled", "ApiCancelled", "Inactive", "Rejected"}
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        last_status = None

        while True:
            status = (
                trade.orderStatus.status
                if trade is not None and trade.orderStatus is not None
                else None
            )
            if status != last_status:
                logger.info(
                    "Order status update | orderId=%s status=%s filled=%s remaining=%s",
                    getattr(trade.order, "orderId", None) if trade and trade.order else None,
                    status,
                    getattr(trade.orderStatus, "filled", None) if trade and trade.orderStatus else None,
                    getattr(trade.orderStatus, "remaining", None) if trade and trade.orderStatus else None,
                )
                last_status = status

            if status in DONE_STATES:
                return status

            if loop.time() >= deadline:
                logger.warning(
                    "Order ack timed out after %.1fs | last_status=%s",
                    timeout, status,
                )
                return "timeout"

            await asyncio.sleep(0.1)




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


# Portfolio level risk limit monitoring

    async def check_daily_loss_limit(self) -> tuple[bool, str]:
        """
        Check if daily loss limit has been exceeded.
        If exceeded, logs the breakdown, force closes TWS, and shuts down the program.
        Returns (allowed: bool, message: str).
        """
        daily_pnl = await self.get_realized_pnl_today()
        net_pnl = daily_pnl["net_pnl"]
        limit = -settings.MAX_DAILY_LOSS

        if net_pnl < limit:
            message = (
                f"Daily loss limit exceeded (net PnL: {net_pnl:.2f}, limit: {limit:.2f}). "
                f"TWS has been shut down. No new entries allowed today."
            )
            logger.warning(
                f"Daily loss limit exceeded — net PnL: {net_pnl:.4f}, limit: {limit:.4f}. "
                f"Forcing TWS shutdown."
            )

            # 1. Disconnect IB API cleanly first
            try:
                self.ib.disconnect()
                logger.warning("IB API disconnected.")
            except Exception as e:
                logger.error(f"Failed to disconnect IB API: {e}")

            # 2. Force kill TWS process
            try:
                subprocess.call(["taskkill", "/F", "/IM", "tws.exe"])
                logger.warning("TWS process killed.")
            except Exception as e:
                logger.error(f"Failed to kill TWS process: {e}")

            return False, message

        logger.info(f"Daily loss check passed — net PnL: {net_pnl:.4f}, limit: {limit:.4f}")
        return True, ""

 
# helper function 
    def _calculate_entry_price(self,bid_ask, stop_price, offset=0.02):

        if bid_ask["ask"] > stop_price:
            return round(bid_ask["ask"] + offset, 2)
        elif bid_ask["bid"] < stop_price:
            return round(bid_ask["bid"] - offset, 2)


    # Checks if user is trying to add to losing position. Won't allow that. 
    async def is_add_allowed(self, position: dict) -> dict:
        symbol = position.get("symbol")
        try:
            avg_cost = position.get("avgcost")
            position_size = position.get("position")
            price_data = await self.get_bid_ask_price(symbol)
            ask = price_data["ask"]
            bid = price_data["bid"]

            if position_size > 0:
                allowed = ask > avg_cost
                message = "OK to add to this long position" if allowed else "Cannot add to losing long position"
            elif position_size < 0:
                allowed = bid < avg_cost
                message = "OK to add to this short position" if allowed else "Cannot add to losing short position"
            else:
                allowed = False
                message = "No existing position to add to"

            return {
                "allowed": allowed,
                "symbol": symbol,
                "message": message,
                "price_data": price_data,
            }

        except Exception as e:
            logging.error(f"Error validating add for {symbol}: {e}")
            return {
                "allowed": False,
                "symbol": symbol,
                "message": str(e),
                "price_data": None,
            }
        
    @staticmethod
    def _count_entries_from_fills(fills: list[dict]) -> int:
        """
        Walk a chronologically-sorted list of IB fills for a single symbol and
        count "entries". An entry is a fill that transitions the symbol's net
        position from flat (zero) to non-zero. Adds, stop fills and manual
        exits don't count.
        """
        entries = 0
        net_position = 0

        for fill in fills:
            action = (fill.get("action") or "").upper()
            qty = int(float(fill.get("quantity") or 0))

            if action in ("BOT", "BUY"):
                signed = qty
            elif action in ("SLD", "SELL"):
                signed = -qty
            else:
                continue

            # Position transitioned from flat to non-flat -> new entry
            if net_position == 0 and signed != 0:
                entries += 1

            net_position += signed

        return entries

    async def count_entry_attempts_today_all(self) -> dict[str, int]:
        """
        Return a {symbol: entry_count} dict for every symbol that had at least
        one entry attempt today. An "entry attempt" is a fill that opens a
        position from flat (see _count_entries_from_fills).

        Source of truth: IB executions for today (derived live each call,
        survives backend restarts).
        """
        try:
            trades = await self.get_trades()
            today = date.today()

            today_trades = [
                t for t in trades
                if t.get("symbol")
                and t.get("time")
                and date.fromisoformat(t["time"][:10]) == today
            ]

            if not today_trades:
                return {}

            fills_by_symbol: dict[str, list[dict]] = defaultdict(list)
            for fill in today_trades:
                fills_by_symbol[fill["symbol"].upper()].append(fill)

            result: dict[str, int] = {}
            for symbol, fills in fills_by_symbol.items():
                fills.sort(key=lambda x: x["time"])
                entries = self._count_entries_from_fills(fills)
                if entries > 0:
                    result[symbol] = entries

            return result

        except Exception as e:
            logger.error(f"Error counting entry attempts (all symbols): {e}")
            return {}

    async def count_entry_attempts_today(self, symbol: str) -> int:
        """
        Count entry attempts today for a single symbol. Convenience wrapper
        around count_entry_attempts_today_all so both the validation hot path
        and the UI stats endpoint share the same semantics.
        """
        counts = await self.count_entry_attempts_today_all()
        return counts.get(symbol.upper(), 0)

    async def is_entry_allowed(self, latest_trade: dict | None, symbol: str) -> tuple[bool, str]:
        threshold_minutes = settings.MAX_ENTRY_FREQUENCY_MINUTES
        max_attempts = settings.MAX_ATTEMPTS_PER_SYMBOL_PER_DAY

        START_HOUR = settings.BLOCK_START_HOUR
        START_MINUTE = settings.BLOCK_START_MINUTE
        END_HOUR = settings.BLOCK_END_HOUR
        END_MINUTE = settings.BLOCK_END_MINUTE

        helsinki_tz = pytz.timezone("Europe/Helsinki")
        now = datetime.now(helsinki_tz)

        try:

            # --- Check 0: Max entry attempts per symbol per day ---
            attempts_today = await self.count_entry_attempts_today(symbol)
            if attempts_today >= max_attempts:
                message = (
                    f"Max entry attempts reached for {symbol} today "
                    f"({attempts_today}/{max_attempts}). No more entries allowed today."
                )
                logger.info(message)
                return False, message

            # --- Check 3: Blocked time window (16:30–17:00 Helsinki time) ---
            block_start = now.replace(hour=START_HOUR, minute=START_MINUTE).time()
            block_end = now.replace(hour=END_HOUR, minute=END_MINUTE).time()

            if block_start <= now.time() <= block_end:
                message = f"Entry blocked during {block_start}–{block_end} window (current time: {now.strftime('%H:%M')})."
                logging.info(message)
                return False, message


            # --- Check 1: Loss cooldown ---
            trades = await self.get_trades_with_pnl()
            last_loss = next((t for t in reversed(trades) if t["is_loss"]), None)

            if last_loss:
                loss_exit_time = last_loss["exit_time"]
                if isinstance(loss_exit_time, str):
                    loss_exit_time = datetime.fromisoformat(loss_exit_time)
                if loss_exit_time.tzinfo is None:
                    loss_exit_time = helsinki_tz.localize(loss_exit_time)

                elapsed_since_loss = now - loss_exit_time

                if elapsed_since_loss <= timedelta(minutes=threshold_minutes):
                    elapsed_str = str(elapsed_since_loss).split(".")[0]
                    message = f"Loss cooldown active. Last loss was {elapsed_str} ago (PnL: {last_loss['net_pnl']})."
                    logging.info(message)
                    return False, message

            # --- Check 2: Entry frequency ---
            if not latest_trade:
                logging.info("No executions found. Entry allowed.")
                return True,  ""

            trade_time = latest_trade["time"]
            if isinstance(trade_time, str):
                trade_time = datetime.fromisoformat(trade_time)

            elapsed = now - trade_time
            elapsed_str = str(elapsed).split(".")[0]

            if elapsed > timedelta(minutes=threshold_minutes):
                logging.info(f"Last execution was {elapsed}. Entry allowed.")
                return True,  ""

            message = f"Too soon to re-enter. Last execution was {elapsed_str} ago."
            logging.info(message)
            return False,  message
        

        except Exception as e:
            logging.exception("Error in is_entry_allowed")
            return False, "Internal error in entry validation."
    
    async def process_entry_request(self, payload: EntryRequest)-> EntryRequestResponse:
        """
        Process an entry request:
        - Check if daily loss limit has been exceeded
        - Check if a new entry is allowed based on past executed trades
        - Check if break is needed because of loss
        - Fetch current ask price
        - Calculate position size
        - Build order with correct size and price
        - Place bracket order
        """
        symbol = payload.symbol
        stop_price = payload.stop_price

        try:
            # --- Check daily loss limit ---
            allowed, message = await self.check_daily_loss_limit()
            if not allowed:
                return EntryRequestResponse(allowed=allowed, message=message, symbol=symbol)

            # --- Fetch executed trades only for this symbol ---
            executed_trades = await self.get_trades_by_symbol(symbol)

            # --- Check if entry is allowed ---
            allowed, message = await self.is_entry_allowed(executed_trades, symbol)

            if not allowed:
                return EntryRequestResponse(
                    allowed=False,
                    message=message,
                    symbol=symbol,
                )

            # --- Entry is allowed ---
            logger.info(f"Entry allowed for {symbol}")

            # --- Step 1: Get current ask price ---
            bid_ask = await self.get_bid_ask_price(symbol)

            entry_price = self._calculate_entry_price(bid_ask, stop_price)


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
                "position_size": position_size,
                "contract_type": payload.contract_type
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

        

            # Get existing aux price
            stp_order_aux_price = existing_stp_order.get("auxprice")
            stp_order_id = existing_stp_order.get("orderid")

            # Get existing position size
            existing_position = position.get("position")
            logger.info(f"Existing STP order aux price for {symbol}: {stp_order_aux_price}, orderId: {stp_order_id}")


            bid_ask = validation.get("price_data")

            add_price = self._calculate_entry_price(bid_ask, stp_order_aux_price)


                # --- Step 3: Recalculate position size ---
            total_size = calculate_position_size(
                    entry_price=add_price,
                    stop_price=stp_order_aux_price,
                    risk=total_risk  # Use the configured risk value from settings
                )
            if existing_position < 0:
                new_qty = total_size + existing_position
            elif existing_position > 0:
                new_qty = total_size - existing_position # Tämän verran pitää lisätä

            # jos on shorttipositiossa niin new_qty on total_size -- existing eli 
            
            
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
                "entry_price": add_price,
                "stop_price": stp_order_aux_price,
                "position_size": new_qty,
                "contract_type":payload.contract_type
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

    async def process_exit_request(self, payload: ExitRequest) -> ExitRequestResponseIB:

        symbol = payload.symbol  # already uppercased + validated by ExitRequest schema
        alarm = payload.alarm    # already validated against EXIT_TRIGGERS by schema
        logger.info(
            "Received exit request | symbol=%s alarm=%s time=%s",
            symbol, alarm, payload.time,
        )

        lock = _get_symbol_lock(symbol)

        async with lock:
            # ---- 1. CLAIM THE ROW (delete-first idempotency) -------------
            claimed = await delete_exit_request(self.db_conn, symbol, alarm)
            if not claimed:
                logger.info(
                    "No armed exit_request row matched | symbol=%s alarm=%s "
                    "(either never armed or duplicate alarm already consumed)",
                    symbol, alarm,
                )
                return ExitRequestResponseIB(
                    symbol=symbol,
                    message=(
                        f"No exit_request armed for {symbol} with "
                        f"strategy '{alarm}'."
                    ),
                )

            logger.info(
                "Claimed exit_request row | symbol=%s strategy=%s "
                "trim_percentage=%s updated=%s",
                claimed["symbol"], claimed["strategy"],
                claimed["trim_percentage"], claimed["updated"],
            )

            # Resolve trim_percentage from the claimed row.
            raw_trim = claimed.get("trim_percentage")
            try:
                trim_percentage = float(raw_trim) if raw_trim is not None else 1.0
            except (TypeError, ValueError):
                trim_percentage = 1.0
            if trim_percentage <= 0 or trim_percentage > 1:
                logger.warning(
                    "Invalid trim_percentage=%s on claimed row, defaulting to 1.0 | symbol=%s",
                    raw_trim, symbol,
                )
                trim_percentage = 1.0
            is_partial = trim_percentage < 1.0

            # Helper: re-insert the claimed row when we couldn't act on it.
            async def _restore_claim(reason: str) -> None:
                try:
                    await update_exit_request(
                        self.db_conn,
                        symbol=symbol,
                        strategy=alarm,
                        trim_percentage=trim_percentage,
                    )
                    logger.info(
                        "Re-inserted claimed exit_request row after %s | "
                        "symbol=%s strategy=%s",
                        reason, symbol, alarm,
                    )
                except Exception:
                    logger.exception(
                        "Failed to re-insert claimed exit_request row | "
                        "symbol=%s strategy=%s reason=%s",
                        symbol, alarm, reason,
                    )

            try:
                # ---- 2. POSITION CHECK -----------------------------------
                position = await self.get_position_by_symbol(symbol)
                logger.info(
                    "Fetched position | symbol=%s position=%s", symbol, position
                )

                if not position or float(position.get("position", 0) or 0) == 0:
                    # Position is gone or flat. The claimed row is stale, and
                    # so are any siblings for this symbol — wipe them so they
                    # can't re-fire on a re-entry.
                    deleted = await delete_exit_requests_by_symbol(
                        self.db_conn, symbol
                    )
                    logger.warning(
                        "No open position; cleared %d stale exit_request row(s) | symbol=%s",
                        len(deleted), symbol,
                    )
                    return ExitRequestResponseIB(
                        symbol=symbol,
                        message=(
                            f"No open position for {symbol}; cleared "
                            f"{len(deleted) + 1} stale exit_request row(s)."
                        ),
                    )

                shares = position["position"]
                if shares > 0:
                    action = "SELL"
                elif shares < 0:
                    action = "BUY"
                else:
                    # Defensive — already covered above, but keep the guard.
                    deleted = await delete_exit_requests_by_symbol(
                        self.db_conn, symbol
                    )
                    return ExitRequestResponseIB(
                        symbol=symbol,
                        message=(
                            f"Position size is zero for {symbol}; cleared "
                            f"{len(deleted) + 1} stale exit_request row(s)."
                        ),
                    )

                total_abs = abs(int(round(float(shares))))
                exit_qty = int(round(total_abs * trim_percentage))
                if is_partial and exit_qty <= 0:
                    exit_qty = 1
                if exit_qty > total_abs:
                    exit_qty = total_abs
                remaining_qty = total_abs - exit_qty
                logger.info(
                    "Exit qty | symbol=%s total=%s exit_qty=%s remaining=%s",
                    symbol, total_abs, exit_qty, remaining_qty,
                )

                # ---- 3. SHORT-CIRCUIT IF MKT ALREADY IN FLIGHT ------------
                existing_mkt_order = await self.get_mkt_order_by_symbol(symbol)
                if existing_mkt_order:
                    logger.info(
                        "Market order already in flight; restoring claim | "
                        "symbol=%s order=%s",
                        symbol, existing_mkt_order,
                    )
                    await _restore_claim("market order already in flight")
                    return ExitRequestResponseIB(
                        symbol=symbol,
                        message=f"Market order already exists for {symbol}.",
                    )

                # ---- 4. PLACE MARKET ORDER & WAIT FOR ACK -----------------
                order = Order(
                    symbol=symbol,
                    contract_type=position["sectype"],
                    action=action,
                    position_size=exit_qty,
                )

                trade = await self.place_market_order(order)
                if trade is None:
                    await _restore_claim("place_market_order returned None")
                    return ExitRequestResponseIB(
                        symbol=symbol,
                        message=(
                            f"IB error placing market order for {symbol}; "
                            f"row restored so you can retry."
                        ),
                    )

                final_status = await self._wait_for_order_done(trade, timeout=15.0)
                if final_status != "Filled":
                    # Order didn't fill (rejected, cancelled, timed out).
                    # Don't touch the STP. Restore the claim so the user
                    # can retry rather than re-arming manually.
                    await _restore_claim(f"order final status={final_status}")
                    return ExitRequestResponseIB(
                        symbol=symbol,
                        message=(
                            f"Market order for {symbol} did not fill "
                            f"(status={final_status}); row restored."
                        ),
                        order_id=getattr(trade.order, "orderId", None) if trade.order else None,
                    )

                filled_order_id = getattr(trade.order, "orderId", None) if trade.order else None
                logger.info(
                    "Market order filled | symbol=%s order_id=%s qty=%s",
                    symbol, filled_order_id, exit_qty,
                )

                # Re-fetch the STP fresh AFTER the fill so we modify against
                # current state, not a snapshot from before the fill.
                existing_stp_order = await self.get_stp_order_by_symbol(symbol)
                logger.info(
                    "Post-fill STP snapshot | symbol=%s stp=%s",
                    symbol, existing_stp_order,
                )

                # ---- 5. ADJUST STP --------------------------------------
                if is_partial:
                    stop_moved_to = None
                    if (
                        existing_stp_order
                        and 'orderid' in existing_stp_order
                        and remaining_qty > 0
                    ):
                        stp_order_id = existing_stp_order['orderid']
                        await self.modify_stp_order_by_id(stp_order_id, remaining_qty)

                        avgcost = position.get("avgcost")
                        if avgcost is not None:
                            new_stop = round(float(avgcost), 2)
                            await self.move_stp_auxprice_to_avgcost(
                                order_id=stp_order_id,
                                new_auxprice=new_stop,
                            )
                            stop_moved_to = new_stop
                        else:
                            logger.warning(
                                "avgcost missing; skipping stop move | symbol=%s",
                                symbol,
                            )
                    elif (
                        existing_stp_order
                        and 'orderid' in existing_stp_order
                        and remaining_qty == 0
                    ):
                        # Rounding edge case: partial flag set but math
                        # produced a full exit. Cancel the STP.
                        await self.cancel_order_by_id(existing_stp_order['orderid'])
                    else:
                        logger.info(
                            "No STP found to modify on partial exit | symbol=%s",
                            symbol,
                        )

                    msg = (
                        f"Partial exit ({alarm}): {action} {exit_qty}/{total_abs} "
                        f"shares of {symbol} at {trim_percentage*100:.0f}%. "
                        f"Remaining: {remaining_qty}."
                    )
                    if stop_moved_to is not None:
                        msg += f" STP resized and moved to avg cost {stop_moved_to}."
                    else:
                        msg += " STP resized."

                    return ExitRequestResponseIB(
                        symbol=symbol,
                        message=msg,
                        order_id=filled_order_id,
                    )

                # Full exit: cancel STP and wipe any sibling rows so they
                # don't fire against a re-entered position later.
                if existing_stp_order and 'orderid' in existing_stp_order:
                    await self.cancel_order_by_id(existing_stp_order['orderid'])
                else:
                    logger.info("No STP found to cancel | symbol=%s", symbol)

                siblings = await delete_exit_requests_by_symbol(
                    self.db_conn, symbol
                )
                logger.info(
                    "Full exit cleanup | symbol=%s sibling_rows_deleted=%s",
                    symbol, len(siblings),
                )

                return ExitRequestResponseIB(
                    symbol=symbol,
                    message=(
                        f"Full exit ({alarm}): {action} {exit_qty} shares of "
                        f"{symbol}. Cleared {len(siblings) + 1} exit_request row(s)."
                    ),
                    order_id=filled_order_id,
                )

            except Exception:
                # Anything unexpected mid-flight: log loudly and re-insert
                # so we don't silently lose the user's arming.
                logger.exception(
                    "Unhandled exception during exit handling | symbol=%s alarm=%s",
                    symbol, alarm,
                )
                await _restore_claim("unhandled exception")
                raise



    async def process_openrisktable(self) -> List[OpenPosition]:

        # Fetch everything concurrently (faster)
        positions, account_summary = await asyncio.gather(
            self.get_positions(),
            self.get_account_summary()
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
                stop_order = await self.get_stp_order_by_symbol(symbol)

                # Collect the names of every strategy currently armed for
                # this symbol. Empty list means nothing armed.
                exit_rows = await fetch_exits_by_symbol(self.db_conn, symbol)
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
                        openrisk=open_risk
                    )
                )

            except Exception as e:
                logger.error("Error processing %s: %s", pos.get("symbol"), e)
                continue
        logger.info(portfolio_positions)

        return portfolio_positions



