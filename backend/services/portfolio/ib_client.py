import asyncio
import logging
import math
import datetime
from datetime import datetime,date
from collections import defaultdict
import pytz
from ib_async import IB, Stock, CFD, LimitOrder, StopOrder, MarketOrder

from services.orders import Order

logger = logging.getLogger(__name__)



def _build_contract(symbol: str, contract_type: str):

    if contract_type == "CFD":
        return CFD(symbol=symbol, exchange="SMART", currency="USD")
    if contract_type in ("stock", "STK"):
        return Stock(symbol=symbol, exchange="SMART", currency="USD")
    raise ValueError(f"Unsupported contract_type: {contract_type!r}")


class IbClient:

    def __init__(self, ib: IB):
        self.ib = ib

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
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

            logging.debug(f"Fetched positions: {result}")
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

            logging.debug(f"Fetched orders: {orders}")
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
                    and o["ordertype"].upper() in ("STP", "STP LMT")
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

    # ------------------------------------------------------------------
    # Writes — order placement
    # ------------------------------------------------------------------
# Actions towards IB client: placing orders, modifying orders, and validation logic for entries and adds.
    async def place_bracket_order(self, order: Order):

        try:
            contract = _build_contract(order.symbol,order.contract_type)

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
            contract = _build_contract(order.symbol,order.contract_type)

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

    async def place_market_order(self, order: Order):

        try:
            contract = _build_contract(order.symbol,order.contract_type)

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


    # ------------------------------------------------------------------
    # Writes — order modification / cancellation
    # ------------------------------------------------------------------
    async def modify_stp_order_by_id(self, order_id: int, new_qty: float) -> dict:
        """
        Modify the quantity of an open IB order using its permId.
        """
        try:
            #  Fetch all open orders
            open_orders = await self.ib.reqAllOpenOrdersAsync()
            await asyncio.sleep(0.5)

            #  Find order matching permId
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

            # Modify quantity
            order.totalQuantity = new_qty

            # Qualify contract (required by IB)
            await self.ib.qualifyContractsAsync(contract)

            # Place order again (same orderId updates existing order)
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
            #  Fetch all open orders
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


            # 4 Modify auxPrice (stop price)
            order.auxPrice = float(new_auxprice)

            # 5️ Qualify contract (required by IB)
            await self.ib.qualifyContractsAsync(contract)

            # 6️ Place order again (same orderId updates existing order)
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
            # 1️ Get existing STP order
            stp_order = await self.get_stp_order_by_symbol(symbol)

            # 2️ Get current position (for avg cost)
            position = await self.get_position_by_symbol(symbol)

            order_id = stp_order.get("orderid")
            avgcost = position.get("avgcost")
            avgcost = round(avgcost,2)

            # 3️ Move stop to breakeven
            result= await self.move_stp_auxprice_to_avgcost(
                order_id=order_id,
                new_auxprice=avgcost
            )
                # 3️ If successful, return detailed response
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
