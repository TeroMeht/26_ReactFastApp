import asyncio
import logging

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import pytz
from ib_async import IB, Stock, CFD, LimitOrder, StopOrder, MarketOrder
from core.config import settings
from services.orders import BidAsk, Order
from services.portfolio.order_tracker import OrderTracker, TERMINAL_STATUSES

logger = logging.getLogger(__name__)


async def _await_event(event_source, condition_fn, timeout: float) -> bool:
    """
    Bridge an ib_async / eventkit event to async/await.

    Registers a handler on `event_source`, resolves the moment the handler
    sees a payload that `condition_fn` accepts, or times out. The handler
    is always detached in the finally clause so subscriptions don't leak.

    Returns True if the condition was met, False on timeout. Never raises.
    """
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()

    def handler(*args, **kwargs):
        try:
            if condition_fn(*args, **kwargs) and not fut.done():
                fut.set_result(True)
        except Exception:
            pass

    event_source += handler
    try:
        await asyncio.wait_for(fut, timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False
    finally:
        event_source -= handler






@dataclass(frozen=True)
class Fill:
    tradeid: int
    symbol: str
    conid: int
    sectype: str
    action: str            # "BOT" | "SLD"
    quantity: float
    price: float
    time: datetime
    exchange: str


@dataclass(frozen=True)
class Position:
    account: str
    symbol: str
    sectype: str
    currency: str
    position: float        # signed: + long, - short
    avgcost: float


@dataclass(frozen=True)
class OpenOrder:
    orderid: int
    symbol: str
    action: str
    ordertype: str
    totalqty: float
    lmtprice: float
    auxprice: float
    orderref: str
    status: str
    filled: float
    remaining: float


@dataclass(frozen=True)
class AccountSummary:
    """
    Typed view over IB's accountSummary. Common numeric fields are
    surfaced as properties; the raw {tag: value} dict is kept for any
    tag the app hasn't grown a typed accessor for yet.
    """
    tags: dict[str, str]

    @staticmethod
    def _to_float(v) -> float:
        try:
            return float(v) if v not in (None, "") else 0.0
        except (TypeError, ValueError):
            return 0.0

    @property
    def net_liquidation(self) -> float:
        return self._to_float(self.tags.get("NetLiquidation"))

    @property
    def buying_power(self) -> float:
        return self._to_float(self.tags.get("BuyingPower"))

    @property
    def total_cash_value(self) -> float:
        return self._to_float(self.tags.get("TotalCashValue"))

    @property
    def available_funds(self) -> float:
        return self._to_float(self.tags.get("AvailableFunds"))

    def get(self, tag: str, default=None) -> str | None:
        return self.tags.get(tag, default)



class OrderNotFoundError(Exception):
    """
    Raised when a cancel targets an order that isn't in IB's open-orders
    list and isn't in a known terminal state on the tracker. Lets the
    router translate to HTTP 404 without inspecting a status string, and
    lets internal callers (flows.exit, cancel_all_unfilled) treat the
    race case explicitly.
    """
    def __init__(self, order_id: int, message: str | None = None):
        self.order_id = order_id
        super().__init__(message or f"No open order found with permId={order_id}")


def _build_contract(symbol: str, contract_type: str):

    if contract_type == "CFD":
        return CFD(symbol=symbol, exchange="SMART", currency="USD")
    if contract_type in ("stock", "STK"):
        return Stock(symbol=symbol, exchange="SMART", currency="USD")
    raise ValueError(f"Unsupported contract_type: {contract_type!r}")


class IbClient:

    def __init__(self, ib: IB, tracker: Optional[OrderTracker] = None):
        self.ib = ib
        self.tracker = tracker

    def _register(self, trade) -> None:
        if self.tracker is not None and trade is not None:
            try:
                self.tracker.register_trade(trade)
            except Exception:
                logger.exception("Failed to register trade with tracker")

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    async def get_positions(self) -> list[Position]:
        """Fetch all non-zero positions."""
        try:
            positions = await self.ib.reqPositionsAsync()

            result = [
                Position(
                    account=p.account,
                    symbol=p.contract.symbol,
                    sectype=p.contract.secType,
                    currency=p.contract.currency,
                    position=p.position,
                    avgcost=round(p.avgCost, 2),
                )
                for p in positions
                if p.position != 0
            ]

            logger.debug(f"Fetched positions: {result}")
            return result

        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            return []

    async def get_orders(self) -> list[OpenOrder]:
        """Fetch all open orders."""
        try:
            trades = await self.ib.reqAllOpenOrdersAsync()

            orders = [
                OpenOrder(
                    orderid=t.order.permId,
                    symbol=t.contract.symbol,
                    action=t.order.action,
                    ordertype=t.order.orderType,
                    totalqty=t.order.totalQuantity,
                    lmtprice=t.order.lmtPrice,
                    auxprice=t.order.auxPrice,
                    orderref=t.order.orderRef,
                    status=t.orderStatus.status,
                    filled=t.orderStatus.filled,
                    remaining=t.orderStatus.remaining,
                )
                for t in trades
            ]

            logger.debug(f"Fetched orders: {orders}")
            return orders

        except Exception as e:
            logger.error(f"Error fetching orders: {e}")
            return []

    async def get_account_summary(self) -> AccountSummary:
        """Fetch account summary."""
        try:
            summary = await self.ib.accountSummaryAsync()
            return AccountSummary(tags={item.tag: item.value for item in summary})
        except Exception as e:
            logger.error(f"Error fetching account summary: {e}")
            return AccountSummary(tags={})

    async def get_trades(self) -> list[Fill]:

        TIMEZONE = pytz.timezone(settings.TIMEZONE)

        try:
            trades = await asyncio.wait_for(
                self.ib.reqExecutionsAsync(),
                timeout=10.0
            )
            executed: list[Fill] = []

            for fill in trades:
                # reqExecutionsAsync returns Fill objects directly.
                if not fill.execution:
                    continue

                time_helsinki = fill.execution.time.astimezone(TIMEZONE)
                executed.append(Fill(
                    tradeid=fill.execution.permId,
                    symbol=fill.contract.symbol,
                    conid=fill.contract.conId,
                    sectype=fill.contract.secType,
                    action=fill.execution.side,
                    quantity=fill.execution.shares,
                    price=fill.execution.price,
                    time=time_helsinki,
                    exchange=fill.execution.exchange,

                ))

            for t in executed:
                logging.info(
                    "Trade: %s %s %.0f @ %.2f at %s",
                    t.symbol, t.action, t.quantity, t.price, t.time,
                )
            return executed

        except asyncio.TimeoutError:
            logging.error("Timeout waiting for executions from IB (>10s)")
            return []
        except Exception as e:
            logging.error(f"Error fetching executed trades: {e}")
            return []

    async def get_bid_ask_price(self, symbol: str) -> BidAsk:

        contract = Stock(symbol=symbol, exchange="SMART", currency="USD")
        await self.ib.qualifyContractsAsync(contract)

        ticker = self.ib.reqMktData(contract, "", False, False)
        try:
            matched = await _await_event(
                ticker.updateEvent,
                lambda t: (
                    t.bid is not None and t.ask is not None
                    and t.bid > 0 and t.ask > 0
                ),
                timeout=2.0,
            )
        finally:
            self.ib.cancelMktData(contract)

        bid, ask = ticker.bid, ticker.ask
        if bid is None or ask is None or not (bid > 0 and ask > 0):
            raise ValueError(
                f"No usable bid/ask for {symbol}: bid={bid} ask={ask}"
                + ("" if matched else " (no live quote within 2s)")
            )


        logger.info(f"Quote for {symbol}: bid={bid} ask={ask})")
        
        return BidAsk(symbol=symbol, bid=bid, ask=ask)

# Helpers filtering functions and order placement logic
    async def get_stp_order_by_symbol(self, symbol: str) -> OpenOrder | None:
        """
        Return the first open STP (Stop) order for the given symbol.
        Returns None if not found.
        """
        try:
            orders = await self.get_orders()
            wanted = symbol.upper()
            return next(
                (
                    o for o in orders
                    if o.symbol and o.symbol.upper() == wanted
                    and o.ordertype and o.ordertype.upper() in ("STP", "STP LMT")
                ),
                None,
            )
        except Exception as e:
            logger.error(f"Error fetching STP order for {symbol}: {e}")
            return None

    async def get_mkt_order_by_symbol(self, symbol: str) -> OpenOrder | None:
        """
        Return the first open MKT (Market) order for the given symbol.
        Returns None if not found.
        """
        try:
            orders = await self.get_orders()
            wanted = symbol.upper()
            return next(
                (
                    o for o in orders
                    if o.symbol and o.symbol.upper() == wanted
                    and o.ordertype and o.ordertype.upper() == "MKT"
                ),
                None,
            )
        except Exception as e:
            logger.error(f"Error fetching MKT order for {symbol}: {e}")
            return None

    async def get_position_by_symbol(self, symbol: str) -> Position | None:
        """
        Return the non-zero Position for the given symbol.
        Returns None if not found.
        """
        try:
            positions = await self.get_positions()
            wanted = symbol.upper()
            return next(
                (p for p in positions if p.symbol and p.symbol.upper() == wanted),
                None,
            )
        except Exception as e:
            logger.error(f"Error fetching position for {symbol}: {e}")
            return None



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
                outsideRth=False,
                tif="GTC"
                
            )

            stoploss = StopOrder(
                action=reverse_action,
                totalQuantity=order.position_size,
                stopPrice=order.stop_price,
                orderId=self.ib.client.getReqId(),
                parentId=parent.orderId,
                transmit=True,  # Last order transmits entire bracket
                outsideRth=False,
                tif="GTC"
            )

            # 1️⃣ Place parent (transmit=False -> held until child arrives).
            parent_trade = self.ib.placeOrder(contract, parent)
            self._register(parent_trade)

            # Wait for IB to acknowledge the parent (statusEvent fires on
            # first orderStatus callback) so the child — which references
            # parent.orderId via parentId — can't arrive first. Cap at 500ms;
            # on timeout we ship the child anyway and IB matches on parentId.
            await _await_event(
                parent_trade.statusEvent,
                lambda *a: True,
                timeout=0.5,
            )

            # 2️⃣ Place stop (transmit=True sends both).
            stop_trade = self.ib.placeOrder(contract, stoploss)
            self._register(stop_trade)

            logger.info(f"Bracket orders submitted for {order.symbol}: "
                f"parent={parent.orderId}, stoploss={stoploss.orderId}, "
                f"action={order.action}, quantity={order.position_size}, "
                f"entry={order.entry_price}, stop={order.stop_price}")


            return parent, stoploss

        except Exception as e:
            logging.error(f"Error in place_bracket_order for {order.symbol}: {e}")
            return None, None

    async def place_limit_order(self, order: Order):
        """Place a simple limit order asynchronously."""
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
                tif="GTC",
            )

            trade = self.ib.placeOrder(contract, limit_order)
            self._register(trade)
            logger.info(f"Limit order submitted for {order.symbol}: "
                        f"orderId={limit_order.orderId}, "
                        f"action={order.action}, quantity={order.position_size}, "
                        f"price={order.entry_price}")

            return limit_order

        except Exception as e:
            logger.error(f"Error in place_limit_order for {order.symbol}: {e}")
            return None

    async def place_market_order(self, order: Order):
        """Place a market order asynchronously."""
        try:
            contract = _build_contract(order.symbol,order.contract_type)

            await self.ib.qualifyContractsAsync(contract)

            market_order = MarketOrder(
                action=order.action,
                totalQuantity=order.position_size,
                outsideRth=True,
                transmit=True,
                tif="DAY",
            )

            trade = self.ib.placeOrder(contract, market_order)
            self._register(trade)
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

            #  Find order matching permId
            target_trade = next(
                (
                    t for t in open_orders
                    if t.order and t.order.permId == order_id
                ),
                None
            )

            if not target_trade:
                logger.warning(f"No open order found with permId {order_id}")
                return {"status": "not_found", "order_id": order_id}

            order = target_trade.order
            contract = target_trade.contract

            if not order or not contract:
                logger.error(f"Order or contract missing for permId {order_id}")
                return {
                    "status": "error",
                    "message": "Order or contract not found",
                    "order_id": order_id
                }

            # Modify quantity
            order.totalQuantity = new_qty

            # Qualify contract (required by IB)
            await self.ib.qualifyContractsAsync(contract)

            # Place order again (same orderId updates the existing order).
            # Wait for IB to fire the Trade's statusEvent as the ack that
            # the modification landed. 500ms cap — timing out just means we
            # return before the async ack, same behaviour as the old sleep.
            self.ib.placeOrder(contract, order)
            await _await_event(
                target_trade.statusEvent,
                lambda *a: True,
                timeout=0.5,
            )

            logger.info(
                f"Modified order {order_id} → new quantity {new_qty}",
                extra={"order_id": order_id, "symbol": contract.symbol, "new_qty": new_qty},
            )

            return {
                "status": "success",
                "order_id": order_id,
                "symbol": contract.symbol,
                "new_quantity": new_qty
            }

        except Exception as e:
            logger.error(f"Error modifying order {order_id}: {e}")
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

            # 2️⃣ Find order matching permId
            target_trade = next(
                (
                    t for t in open_orders
                    if t.order and t.order.permId == order_id
                ),
                None
            )

            if not target_trade:
                logger.warning(f"No open order found with permId {order_id}")
                return {"status": "not_found", "order_id": order_id}

            order = target_trade.order
            contract = target_trade.contract

            # Modify auxPrice (stop price)
            order.auxPrice = float(new_auxprice)

            # Qualify contract (required by IB)
            await self.ib.qualifyContractsAsync(contract)

            # Same orderId => modification. Wait for IB's Trade.statusEvent
            # ack (cap at 1s to match the old sleep budget); timing out just
            # returns before the async ack, same behaviour as before.
            self.ib.placeOrder(contract, order)
            await _await_event(
                target_trade.statusEvent,
                lambda *a: True,
                timeout=1.0,
            )

            logger.info(
                f"Moved STP order {order_id} stop to new price {new_auxprice}",
                extra={
                    "order_id": order_id,
                    "symbol": contract.symbol,
                    "new_stop": new_auxprice,
                },
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

            order_id = stp_order.orderid if stp_order else None
            avgcost = round(position.avgcost, 2) if position else 0.0

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
        
    async def cancel_order_by_id(self, order_id: int, timeout: float = 5.0) -> dict:
        """
        Cancel an open order by its permId and *await* the terminal state so
        the caller knows whether the cancel actually landed or whether the
        order filled before the cancel could take effect.

        Returns a dict shaped:
          {
              "status": "Cancelled" | "ApiCancelled" | "Filled" | "Inactive" |
                        "not_found" | "timeout" | "error",
              "order_id": <permId>,
              "symbol": str | None,
              "filled": float,
              "remaining": float,
              "message": str (only on error/timeout),
          }
        """
        try:
            # Fetch the live Trade. reqAllOpenOrdersAsync returns Trade
            # objects with a live orderStatus we can poll.
            open_trades = await self.ib.reqAllOpenOrdersAsync()
            target = next(
                (t for t in open_trades if t.order and t.order.permId == order_id),
                None,
            )

            if not target:
                # Maybe already terminal — check tracker before giving up.
                if self.tracker is not None:
                    state = self.tracker.state(order_id)
                    if state and (state.get("status") in TERMINAL_STATUSES):
                        logger.info(
                            f"Order {order_id} already terminal ({state.get('status')})"
                        )
                        return {
                            "status": state.get("status"),
                            "order_id": order_id,
                            "symbol": state.get("symbol"),
                            "filled": state.get("filled", 0),
                            "remaining": state.get("remaining", 0),
                        }
                logger.warning(f"No open order found with permId={order_id}")
                # Signal via exception rather than a status string so
                # callers don't have to inspect the returned dict. The
                # router translates this to HTTP 404; internal callers
                # (flows.exit, cancel_all_unfilled) catch it explicitly.
                raise OrderNotFoundError(order_id)

            symbol = target.contract.symbol if target.contract else None

            # If already filled in the brief window between fetch and here.
            current = target.orderStatus.status if target.orderStatus else None
            if current in TERMINAL_STATUSES:
                logger.info(
                    f"Order {order_id} ({symbol}) already terminal: {current}"
                )
                return {
                    "status": current,
                    "order_id": order_id,
                    "symbol": symbol,
                    "filled": float(target.orderStatus.filled or 0),
                    "remaining": float(target.orderStatus.remaining or 0),
                }

            # Fire the cancel and wait for IB to acknowledge a terminal status.
            self.ib.cancelOrder(target.order)
            logger.info(f"Cancel request sent for permId={order_id} ({symbol})")

            deadline = asyncio.get_event_loop().time() + timeout
            poll_interval = 0.1
            while asyncio.get_event_loop().time() < deadline:
                status = target.orderStatus.status if target.orderStatus else None
                if status in TERMINAL_STATUSES:
                    return {
                        "status": status,
                        "order_id": order_id,
                        "symbol": symbol,
                        "filled": float(target.orderStatus.filled or 0),
                        "remaining": float(target.orderStatus.remaining or 0),
                    }
                await asyncio.sleep(poll_interval)

            logger.warning(
                f"Cancel timeout for permId={order_id} after {timeout}s; "
                f"last status={target.orderStatus.status if target.orderStatus else 'unknown'}"
            )
            return {
                "status": "timeout",
                "order_id": order_id,
                "symbol": symbol,
                "filled": float(target.orderStatus.filled or 0) if target.orderStatus else 0,
                "remaining": float(target.orderStatus.remaining or 0) if target.orderStatus else 0,
                "message": f"Cancel did not complete within {timeout}s",
            }

        except OrderNotFoundError:
            # Pass through -- callers translate this to 404 / silent skip
            # depending on context. Would otherwise get swallowed below.
            raise
        except Exception as e:
            logger.error(f"Error cancelling order {order_id}: {e}")
            return {
                "status": "error",
                "order_id": order_id,
                "symbol": None,
                "filled": 0,
                "remaining": 0,
                "message": str(e),
            }

    async def cancel_all_unfilled(self, timeout_each: float = 5.0) -> list[dict]:
        """
        Cancel every open order that is still unfilled (filled == 0 and
        status is non-terminal). Returns one result dict per order in the
        same shape as cancel_order_by_id.
        """
        results: list[dict] = []
        try:
            open_trades = await self.ib.reqAllOpenOrdersAsync()
            for t in open_trades or []:
                if not t.order or not t.orderStatus:
                    continue
                status = t.orderStatus.status
                filled = float(t.orderStatus.filled or 0)
                if status in TERMINAL_STATUSES or filled > 0:
                    continue
                try:
                    res = await self.cancel_order_by_id(
                        t.order.permId, timeout=timeout_each
                    )
                except OrderNotFoundError:
                    # Order became terminal between reqAllOpenOrdersAsync
                    # and the cancel call -- rare race, effectively already
                    # done. Skip; don't fail the whole batch.
                    logger.info(
                        "Order %s vanished between fetch and cancel; skipping",
                        t.order.permId,
                    )
                    continue
                results.append(res)
            logger.info(
                f"cancel_all_unfilled processed {len(results)} unfilled orders"
            )
            return results
        except Exception as e:
            logger.exception(f"cancel_all_unfilled failed: {e}")
            return results
