import logging

from datetime import datetime, date, timedelta

import pytz
from collections import defaultdict
from services.orders import Order, build_order, calculate_position_size,calculate_entry_price
from services.portfolio.ib_client import IbClient
from services.portfolio.risk_limits import check_daily_loss_limit

from core.config import settings
from schemas.api_schemas import (
    EntryRequest,
    EntryRequestResponse
)

logger = logging.getLogger(__name__)



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

        if net_position == 0 and signed != 0:
            entries += 1

        net_position += signed

    return entries

async def count_entry_attempts_today_all(client: IbClient) -> dict[str, int]:

    try:
        trades = await client.get_trades()
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
            entries = _count_entries_from_fills(fills)
            if entries > 0:
                result[symbol] = entries

        return result

    except Exception as e:
        logger.error(f"Error counting entry attempts (all symbols): {e}")
        return {}

async def count_entry_attempts_today(client: IbClient, symbol: str) -> int:
    counts = await count_entry_attempts_today_all(client)
    return counts.get(symbol.upper(), 0)



async def is_entry_allowed(client: IbClient,latest_trade: dict | None, symbol: str) -> tuple[bool, str]:
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
        attempts_today = await count_entry_attempts_today(client, symbol)
        if attempts_today >= max_attempts:
            message = (
                f"Max entry attempts reached for {symbol} today "
                f"({attempts_today}/{max_attempts}). No more entries allowed today."
            )
            logger.info(message)
            return False, message

        # --- Check 3: Blocked time window ---
        block_start = now.replace(hour=START_HOUR, minute=START_MINUTE).time()
        block_end = now.replace(hour=END_HOUR, minute=END_MINUTE).time()

        if block_start <= now.time() <= block_end:
            message = f"Entry blocked during {block_start}–{block_end} window (current time: {now.strftime('%H:%M')})."
            logger.info(message)
            return False, message

        # --- Check 1: Loss cooldown ---
        trades = await client.get_trades_with_pnl()
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
                logger.info(message)
                return False, message

        # --- Check 2: Entry frequency ---
        if not latest_trade:
            logger.info("No executions found. Entry allowed.")
            return True, ""

        trade_time = latest_trade["time"]
        if isinstance(trade_time, str):
            trade_time = datetime.fromisoformat(trade_time)

        elapsed = now - trade_time
        elapsed_str = str(elapsed).split(".")[0]

        if elapsed > timedelta(minutes=threshold_minutes):
            logger.info(f"Last execution was {elapsed}. Entry allowed.")
            return True, ""

        message = f"Too soon to re-enter. Last execution was {elapsed_str} ago."
        logger.info(message)
        return False, message

    except Exception:
        logger.exception("Error in is_entry_allowed")
        return False, "Internal error in entry validation."


# ----------------------------------------------------------------------
# Workflows
# ----------------------------------------------------------------------
async def process_entry_request(client: IbClient, payload: EntryRequest) -> EntryRequestResponse:
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
        allowed, message = await check_daily_loss_limit(client)
        if not allowed:
            return EntryRequestResponse(allowed=allowed, message=message, symbol=symbol)

        # --- Fetch executed trades only for this symbol ---
        executed_trades = await client.get_trades_by_symbol(symbol)

        # --- Check if entry is allowed ---
        allowed, message = await is_entry_allowed(client, executed_trades, symbol)

        if not allowed:
            return EntryRequestResponse(
                allowed=False,
                message=message,
                symbol=symbol,
            )

        # --- Entry is allowed ---
        logger.info(f"Entry allowed for {symbol}")

        # --- Step 1: Get current ask price ---
        bid_ask = await client.get_bid_ask_price(symbol)

        entry_price = calculate_entry_price(bid_ask, stop_price)

        # --- Step 2: Calculate position size ---
        position_size = calculate_position_size(
            entry_price=entry_price,
            stop_price=stop_price,
            risk=settings.RISK,
        )

        logger.info(f"Calculated position size: {position_size} for {symbol} at entry {entry_price}")

        # --- Step 3: Build order dataclass with correct size and price ---
        order_data = {
            "symbol":        symbol,
            "entry_price":   entry_price,
            "stop_price":    stop_price,
            "position_size": position_size,
            "contract_type": payload.contract_type,
        }
        order = build_order(order_data)

        # --- Step 4: Place bracket order ---
        parent, stop = await client.place_bracket_order(order)

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
