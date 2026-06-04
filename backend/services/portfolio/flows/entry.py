"""
Entry flow.

One IB executions fetch per request (via TradesSnapshot), pure guards over
the snapshot, then the actual order placement. Public surface preserved:
    process_entry_request          - the orchestrator
    count_entry_attempts_today_all - used by routers/portfolio.py
"""

import logging
from datetime import datetime, time, timedelta

import pytz

from services.orders import build_order, calculate_position_size, calculate_entry_price
from services.portfolio.ib_client import IbClient
from services.portfolio.risk_limits import (
    check_daily_loss,
    enforce_daily_loss_circuit_breaker,
)
from services.portfolio.trades_snapshot import (
    TradesSnapshot,
    build_today_snapshot,
)
from db.exits import update_exit_request

from core.config import settings
from schemas.api_schemas import EntryRequest, EntryRequestResponse

logger = logging.getLogger(__name__)

HELSINKI = pytz.timezone("Europe/Helsinki")


# ----------------------------------------------------------------------
# Public helper used by routers/portfolio.py
# ----------------------------------------------------------------------
async def count_entry_attempts_today_all(client: IbClient) -> dict[str, int]:
    """
    Per-symbol entry-attempt counts for today. Thin wrapper around the
    snapshot so the existing router can keep calling this name.
    """
    try:
        snapshot = await build_today_snapshot(client)
        return dict(snapshot.entry_counts)
    except Exception as e:
        logger.error(f"Error counting entry attempts (all symbols): {e}")
        return {}


# ----------------------------------------------------------------------
# Guards — pure functions over a snapshot + clock + settings
# ----------------------------------------------------------------------
def _parse_helsinki(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = HELSINKI.localize(dt)
    return dt


def _in_block_window(now_t: time, start: time, end: time) -> bool:
    """Window is inclusive on both ends. Supports overnight windows."""
    if start <= end:
        return start <= now_t <= end
    # crosses midnight, e.g. 22:00–06:00
    return now_t >= start or now_t <= end


def check_block_window(now: datetime) -> tuple[bool, str]:
    start = time(settings.BLOCK_START_HOUR, settings.BLOCK_START_MINUTE)
    end = time(settings.BLOCK_END_HOUR, settings.BLOCK_END_MINUTE)
    if _in_block_window(now.time(), start, end):
        msg = (
            f"Entry blocked during {start.strftime('%H:%M')}–{end.strftime('%H:%M')} "
            f"window (current time: {now.strftime('%H:%M')})."
        )
        logger.info(msg)
        return False, msg
    return True, ""


def check_attempts(snapshot: TradesSnapshot, symbol: str) -> tuple[bool, str]:
    attempts = snapshot.attempts_for(symbol)
    max_attempts = settings.MAX_ATTEMPTS_PER_SYMBOL_PER_DAY
    if attempts >= max_attempts:
        msg = (
            f"Max entry attempts reached for {symbol} today "
            f"({attempts}/{max_attempts}). No more entries allowed today."
        )
        logger.info(msg)
        return False, msg
    return True, ""


def check_total_attempts(snapshot: TradesSnapshot) -> tuple[bool, str]:
    """
    Daily cap across all tickers. Independent of per-symbol limits — once
    the total number of entries today reaches MAX_TOTAL_ENTRIES_PER_DAY no
    further entries are allowed regardless of which symbol is requested.
    """
    total = snapshot.total_attempts()
    max_total = settings.MAX_TOTAL_ENTRIES_PER_DAY
    if total >= max_total:
        msg = (
            f"Max total entries reached for today ({total}/{max_total}). "
            f"No more entries allowed today."
        )
        logger.info(msg)
        return False, msg
    return True, ""


def check_loss_cooldown(
    snapshot: TradesSnapshot, now: datetime
) -> tuple[bool, str, datetime | None]:
    """
    Returns (ok, message, cooldown_until).

    `cooldown_until` is set only when the cooldown is active so the UI can
    display a persistent banner with a countdown until entries become
    allowed again.
    """
    last_loss = snapshot.last_loss()
    if not last_loss:
        return True, "", None

    exit_time = _parse_helsinki(last_loss.get("exit_time"))
    if exit_time is None:
        return True, "", None

    threshold = timedelta(minutes=settings.MAX_ENTRY_FREQUENCY_MINUTES)
    cooldown_until = exit_time + threshold
    elapsed = now - exit_time
    if elapsed <= threshold:
        elapsed_str = str(elapsed).split(".")[0]
        msg = (
            f"Loss cooldown active. Last loss was {elapsed_str} ago "
            f"(PnL: {last_loss.get('net_pnl')})."
        )
        logger.info(msg)
        return False, msg, cooldown_until
    return True, "", None


def check_frequency(
    snapshot: TradesSnapshot, symbol: str, now: datetime
) -> tuple[bool, str]:
    latest = snapshot.latest_fill_for_symbol(symbol)
    if not latest:
        logger.info("No executions found. Entry allowed.")
        return True, ""

    trade_time = _parse_helsinki(latest.get("time"))
    if trade_time is None:
        return True, ""

    elapsed = now - trade_time
    threshold = timedelta(minutes=settings.MAX_ENTRY_FREQUENCY_MINUTES)
    if elapsed > threshold:
        logger.info(f"Last execution was {elapsed}. Entry allowed.")
        return True, ""

    elapsed_str = str(elapsed).split(".")[0]
    msg = f"Too soon to re-enter. Last execution was {elapsed_str} ago."
    logger.info(msg)
    return False, msg


# ----------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------
async def process_entry_request(
    client: IbClient,
    db_conn,
    payload: EntryRequest,
) -> EntryRequestResponse:
    """
    Validate guards (one IB executions fetch), fetch the quote, size the
    position, build the order, and place a bracket. On success, persist the
    user-defined exit_strategies into exit_requests so the streamer's exit
    alarms have somewhere to match against.

    The schema enforces ``len(payload.exit_strategies) >= 1`` — there is no
    "no exits" code path here.
    """
    symbol = payload.symbol
    stop_price = payload.stop_price

    try:
        snapshot = await build_today_snapshot(client)
        now = datetime.now(HELSINKI)

        # Daily loss is a kill-switch: fire the circuit breaker on breach.
        ok, message = check_daily_loss(snapshot)
        if not ok:
            enforce_daily_loss_circuit_breaker(client)
            return EntryRequestResponse(allowed=False, message=message, symbol=symbol)

        # Cheap, pure guards in cheapest-to-most-relevant order.
        for ok, message in (
            check_block_window(now),
            check_total_attempts(snapshot),
            check_attempts(snapshot, symbol),
            check_frequency(snapshot, symbol, now),
        ):
            if not ok:
                return EntryRequestResponse(allowed=False, message=message, symbol=symbol)

        # Loss cooldown is handled separately so we can surface the exact
        # expiry time to the UI (it shows a persistent banner with a
        # countdown until entries become allowed again).
        cd_ok, cd_msg, cd_until = check_loss_cooldown(snapshot, now)
        if not cd_ok:
            return EntryRequestResponse(
                allowed=False,
                message=cd_msg,
                symbol=symbol,
                reason="loss_cooldown",
                cooldown_until=cd_until.isoformat() if cd_until else None,
            )

        logger.info(f"Entry allowed for {symbol}")

        # Quote → size → order → bracket
        bid_ask = await client.get_bid_ask_price(symbol)
        entry_price = calculate_entry_price(bid_ask, stop_price)
        position_size = calculate_position_size(
            entry_price=entry_price,
            stop_price=stop_price,
            risk=settings.RISK,
        )
        logger.info(
            f"Calculated position size: {position_size} for {symbol} at entry {entry_price}"
        )

        order = build_order({
            "symbol":        symbol,
            "entry_price":   entry_price,
            "stop_price":    stop_price,
            "position_size": position_size,
            "contract_type": payload.contract_type,
        })

        parent, stop = await client.place_bracket_order(order)

        # place_bracket_order returns (None, None) on failure. Don't claim
        # success in that case — surface a clear rejection to the caller.
        if not parent or not stop:
            msg = f"Bracket order placement failed for {symbol}"
            logger.error(msg)
            return EntryRequestResponse(
                allowed=False,
                message=msg,
                symbol=symbol,
            )

        # Arm the user-defined exits now that the bracket is live. Insert
        # after order placement so a failed order doesn't leave stranded
        # exit_request rows. Per-strategy errors are logged but do not roll
        # back the entry — the position is open either way.
        for spec in payload.exit_strategies:
            try:
                await update_exit_request(
                    db_conn,
                    symbol,
                    strategy=spec.strategy,
                    trim_percentage=float(spec.trim_percentage),
                )
            except Exception:
                logger.exception(
                    "Failed to persist exit_request | symbol=%s strategy=%s",
                    symbol, spec.strategy,
                )

        return EntryRequestResponse(
            allowed=True,
            message="Entry ok",
            symbol=symbol,
            parentOrderId=parent.orderId,
            stopOrderId=stop.orderId,
        )

    except Exception as e:
        logger.exception(f"Error processing entry request for {symbol}")
        return EntryRequestResponse(
            allowed=False,
            message=str(e),
            symbol=symbol,
        )
