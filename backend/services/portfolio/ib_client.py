import asyncio
import logging

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import pytz
from ib_async import IB, Stock, CFD, LimitOrder, StopOrder, MarketOrder,PriceCondition
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
    # Populated for conditional-LMT protective legs (pre-market stops):
    # the price at which the PriceCondition arms and submits the LMT.
    # None for native STP orders (their trigger sits in `auxprice`).
    trigger_price: Optional[float] = None
    has_price_condition: bool = False


# Sentinel orderRef marking a bracket's protective leg. Downstream code
# (get_stp_order_by_symbol, exit flow, open-risk, add flow) uses this to
# find "the stop" regardless of whether it's a native STP or a
# conditional LMT for the pre-market path.
PROTECTIVE_STP_REF = "PROTECTIVE_STP"


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
        # (symbol, contract_type) -> already-qualified Contract.
        # qualifyContractsAsync is a 50-200ms round-trip; once IB has
        # resolved conId/primaryExchange/etc for a symbol, that value is
        # stable for the session, so we cache it and skip subsequent
        # round-trips on the entry hot path.
        self._contract_cache: dict[tuple[str, str], object] = {}

    def _register(self, trade) -> None:
        if self.tracker is not None and trade is not None:
            try:
                self.tracker.register_trade(trade)
            except Exception:
                logger.exception("Failed to register trade with tracker")

    async def _qualified_contract(self, symbol: str, contract_type: str):
        """
        Return a qualified Contract for (symbol, contract_type), reusing
        a cached one when possible. First call for a given pair pays the
        IB qualification round-trip; subsequent calls are free.
        """
        key = (symbol.upper(), contract_type)
        cached = self._contract_cache.get(key)
        if cached is not None:
            return cached

        contract = _build_contract(symbol, contract_type)
        await self.ib.qualifyContractsAsync(contract)
        # Only cache if IB actually resolved it -- otherwise we'd cache
        # a broken object and every downstream conditional order would
        # attach a PriceCondition to conId=0.
        if getattr(contract, "conId", 0):
            self._contract_cache[key] = contract
        return contract

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

            orders = []
            for t in trades:
                # A protective conditional LMT carries its trigger on
                # order.conditions[0].price, not on auxPrice. Surface it as
                # trigger_price and mirror into auxprice so downstream code
                # (add flow, open-risk table) that reads auxprice keeps
                # working without a rewrite.
                trig: Optional[float] = None
                has_cond = False
                aux = t.order.auxPrice
                conds = getattr(t.order, "conditions", None) or []
                for c in conds:
                    if isinstance(c, PriceCondition):
                        trig = float(c.price)
                        has_cond = True
                        if not aux:
                            aux = trig
                        break

                orders.append(OpenOrder(
                    orderid=t.order.permId,
                    symbol=t.contract.symbol,
                    action=t.order.action,
                    ordertype=t.order.orderType,
                    totalqty=t.order.totalQuantity,
                    lmtprice=t.order.lmtPrice,
                    auxprice=aux,
                    orderref=t.order.orderRef,
                    status=t.orderStatus.status,
                    filled=t.orderStatus.filled,
                    remaining=t.orderStatus.remaining,
                    trigger_price=trig,
                    has_price_condition=has_cond,
                ))

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
        # Use the cached qualified contract when available -- first entry
        # for the session pays the ~50-200ms qualification round-trip;
        # subsequent entries on the same symbol skip it.
        contract = await self._qualified_contract(symbol, "STK")

        # Fast path: the scanner / watchlist streamer may already have a
        # live ticker for this symbol. If so, ib.ticker(contract) returns
        # it with the current cached bid/ask -- zero round-trip. Only
        # fall back to a fresh reqMktData subscription if there isn't
        # one, or the cached quote isn't populated yet.
        existing = self.ib.ticker(contract)
        if existing is not None and existing.bid and existing.ask \
                and existing.bid > 0 and existing.ask > 0:
            logger.debug(
                "Quote for %s (cached ticker): bid=%s ask=%s",
                symbol, existing.bid, existing.ask,
            )
            return BidAsk(symbol=symbol, bid=existing.bid, ask=existing.ask)

        # Slow path: one-shot subscription with a 2s ceiling.
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
        Return the first open protective (stop) order for the given symbol.
        Matches either a native STP / STP LMT or a conditional LMT tagged
        with orderRef=PROTECTIVE_STP (the pre-market path). Returns None
        if not found.
        """
        try:
            orders = await self.get_orders()
            wanted = symbol.upper()
            return next(
                (
                    o for o in orders
                    if o.symbol and o.symbol.upper() == wanted
                    and (
                        (o.ordertype and o.ordertype.upper() in ("STP", "STP LMT"))
                        or (o.orderref == PROTECTIVE_STP_REF and o.has_price_condition)
                    )
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

    @staticmethod
    def _reverse_action(action: str) -> str:
        """Flip a side: BUY <-> SELL. Used to derive the protective-leg action."""
        if action.upper() == "BUY":
            reverse = "SELL"
        else:
            reverse = "BUY"

        return reverse

    def _build_parent_order(self, order: Order) -> LimitOrder:
        return LimitOrder(
            action=order.action,
            totalQuantity=order.position_size,
            lmtPrice=order.entry_price,
            orderId=self.ib.client.getReqId(),
            transmit=False,
            outsideRth=True,
            tif="GTC",
        )

    def _build_native_stp_order(self, order: Order, parent_order_id: int, reverse_action: str) -> StopOrder:
        return StopOrder(
            action=reverse_action,
            totalQuantity=order.position_size,
            stopPrice=order.stop_price,
            orderId=self.ib.client.getReqId(),
            parentId=parent_order_id,
            transmit=True,
            outsideRth=True,
            tif="GTC",
        )

    @staticmethod
    def _stop_trigger_and_limit(stop_price: float, reverse_action: str) -> tuple[bool, float]:
        """
        Derive the PriceCondition direction and the protective LMT price
        from the stop price and the child's side.

          * SELL child (long stop): fire when price <= stop; LMT sits
            settings.STOP_LIMIT_OFFSET *below* the trigger.
          * BUY  child (short stop): fire when price >= stop; LMT sits
            settings.STOP_LIMIT_OFFSET *above* the trigger.

        Returns (is_more, lmt_price).
        """
        offset = float(settings.STOP_LIMIT_OFFSET)

        if reverse_action == "SELL":
            is_more = False
            lmt_price = round(stop_price - offset, 2)
        else:
            is_more = True
            lmt_price = round(stop_price + offset, 2)

        return is_more, lmt_price

    def _build_conditional_stp_order(self, contract, order: Order, parent_order_id: int, reverse_action: str) -> LimitOrder:

        if not getattr(contract, "conId", 0):
            raise ValueError(
                f"Cannot attach PriceCondition to {order.symbol}: "
                "contract.conId is not populated "
                "(qualifyContractsAsync did not resolve it)."
            )

        is_more, lmt_price = self._stop_trigger_and_limit(
            order.stop_price, reverse_action
        )

        cond = PriceCondition(
            conId=contract.conId,
            exch=getattr(contract, "primaryExchange", None) or "SMART",
            isMore=is_more,
            price=float(order.stop_price),
        )

        stoploss = LimitOrder(
            action=reverse_action,
            totalQuantity=order.position_size,
            lmtPrice=lmt_price,
            orderId=self.ib.client.getReqId(),
            parentId=parent_order_id,
            transmit=True,
            outsideRth=True,  # allow fill in extended hours once armed
            tif="GTC",
        )
        stoploss.conditions = [cond]
        # True = "conditions ignore the RTH restriction" -> evaluate on
        # extended-hours ticks too. (IB's field name reads literally.)
        stoploss.conditionsIgnoreRth = True
        stoploss.conditionsCancelOrder = False  # False = submit-on-trigger
        stoploss.orderRef = PROTECTIVE_STP_REF

        logger.info(
            "Built pre-market protective LMT for %s: "
            "trigger=%.2f lmt=%.2f isMore=%s conId=%s",
            order.symbol, order.stop_price, lmt_price,
            is_more, contract.conId,
        )
        return stoploss

    async def _submit_bracket_pair(self, contract, parent, stoploss, order: Order):
        """
        Ship a parent+child bracket to IB.

        Both orders go out back-to-back on the same socket in the same
        event-loop tick, in order: parent (transmit=False, held), then
        child (transmit=True, releases the bracket). No ack-wait between
        them -- parentId is a local orderId counter, the two messages
        arrive at IB in-order over TCP, and IB matches the child to the
        parent by ID without needing us to sequence the round-trip. This
        removes 50-500ms of latency vs. the previous statusEvent wait.
        Logs the submission summary. Returns (parent_trade, stop_trade).
        """
        parent_trade = self.ib.placeOrder(contract, parent)
        self._register(parent_trade)

        stop_trade = self.ib.placeOrder(contract, stoploss)
        self._register(stop_trade)

        logger.info(
            "Bracket orders submitted for %s: parent=%s, stoploss=%s, "
            "action=%s, quantity=%s, entry=%s, stop=%s",
            order.symbol, parent.orderId, stoploss.orderId,
            order.action, order.position_size,
            order.entry_price, order.stop_price,
        )
        return parent_trade, stop_trade


# Main bracket order orchestration: build the contract, parent, and protective leg, then submit both to IB.

    async def place_bracket_order(self, order: Order):
        """
        Orchestrate a bracket entry: contract -> parent LMT -> protective
        leg (native STP or conditional LMT depending on .env flag) ->
        submit both.
        """
        try:
            contract = await self._qualified_contract(order.symbol, order.contract_type)

            parent = self._build_parent_order(order)
            reverse_action = self._reverse_action(order.action)

            # Protective-leg shape is decided here, from .env:
            #   EXTENDED_HOURS_STOP_ENABLED=false -> native STP
            #   EXTENDED_HOURS_STOP_ENABLED=true  -> conditional LMT
            if settings.EXTENDED_HOURS_STOP_ENABLED:
                stoploss = self._build_conditional_stp_order(
                    contract, order, parent.orderId, reverse_action
                )
            else:
                stoploss = self._build_native_stp_order(
                    order, parent.orderId, reverse_action
                )

            await self._submit_bracket_pair(contract, parent, stoploss, order)

            return parent, stoploss

        except Exception as e:
            logging.error(f"Error in place_bracket_order for {order.symbol}: {e}")
            return None, None


        

    async def place_limit_order(self, order: Order):
        """Place a simple limit order asynchronously."""
        try:
            contract = await self._qualified_contract(order.symbol, order.contract_type)

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

            # Wait briefly for IB to acknowledge and populate permId. Without
            # this, callers (e.g. place_manual_exit) return a response with
            # perm_id=None, and any subsequent cancel-by-permId fails because
            # IB doesn't yet map the local orderId to the returned identifier.
            # IB typically assigns permId within ~100ms; cap the wait so a
            # stalled gateway can't hang the request.
            deadline = asyncio.get_event_loop().time() + 2.0
            while not getattr(limit_order, "permId", 0):
                if asyncio.get_event_loop().time() >= deadline:
                    logger.warning(
                        "permId not assigned within timeout for %s orderId=%s",
                        order.symbol, limit_order.orderId,
                    )
                    break
                await asyncio.sleep(0.05)

            logger.info(f"Limit order submitted for {order.symbol}: "
                        f"orderId={limit_order.orderId}, "
                        f"permId={getattr(limit_order, 'permId', 0)}, "
                        f"action={order.action}, quantity={order.position_size}, "
                        f"price={order.entry_price}")

            return limit_order

        except Exception as e:
            logger.error(f"Error in place_limit_order for {order.symbol}: {e}")
            return None

    async def place_market_order(self, order: Order):
        """Place a market order asynchronously."""
        try:
            contract = await self._qualified_contract(order.symbol, order.contract_type)

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

            # Where the trigger lives depends on the protective leg's shape.
            # Native STP: it's on order.auxPrice. Conditional LMT (pre-market
            # path): it's on order.conditions[0].price -- editing auxPrice on
            # such an order does nothing, so we mutate the condition
            # directly. Re-attach the whole list so ib_async ships the new
            # value to IB on the modification round-trip.
            price_cond = next(
                (c for c in (getattr(order, "conditions", None) or [])
                 if isinstance(c, PriceCondition)),
                None,
            )
            if price_cond is not None:
                new_cond = PriceCondition(
                    conId=price_cond.conId,
                    exch=price_cond.exch,
                    isMore=price_cond.isMore,
                    price=float(new_auxprice),
                )
                order.conditions = [new_cond]
                order.conditionsIgnoreRth = True
                order.conditionsCancelOrder = False
            else:
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
