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
from services.portfolio import lockout_cache

from core.config import settings
from schemas.api_schemas import EntryRequest, EntryRequestResponse

logger = logging.getLogger(__name__)

HELSINKI = pytz.timezone("Europe/Helsinki")


async def count_entry_attempts_today_all(client: IbClient) -> dict[str, int]:
    """Per-symbol entry-attempt counts for today."""
    try:
        snapshot = await build_today_snapshot(client)
        return dict(snapshot.entry_counts)
    except Exception as e:
        logger.error(f"Error counting entry attempts (all symbols): {e}")
        return {}


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
    if start <= end:
        return start <= now_t <= end
    return now_t >= start or now_t <= end


def check_block_window(now: datetime) -> tuple[bool, str]:
    start = time(settings.BLOCK_START_HOUR, settings.BLOCK_START_MINUTE)
    end = time(settings.BLOCK_END_HOUR, settings.BLOCK_END_MINUTE)
    if _in_block_window(now.time(), start, end):
        msg = (
            f"Entry blocked during {start.strftime('%H:%M')}-{end.strftime('%H:%M')} "
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


def check_loss_cooldown(snapshot: TradesSnapshot, now: datetime):
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


_TIER1_CACHE_KEY = "consecutive_losses:tier1_floating"


def check_consecutive_losses(snapshot: TradesSnapshot, now: datetime):
    """
    Escalating lockout based on the current losing streak:
      - tier 2 (>= CONSECUTIVE_LOSS_TIER2_COUNT): locked until end of
        trading day (midnight Helsinki).
      - tier 1 (>= CONSECUTIVE_LOSS_TIER1_COUNT): locked for
        CONSECUTIVE_LOSS_TIER1_MINUTES from the last loss's exit_time.
    Returns the same (ok, msg, cooldown_until) shape as check_loss_cooldown
    so the existing EntryRequestResponse(reason="loss_cooldown", ...)
    surface is reused and the frontend banner picks it up unchanged.

    Refresh safety: the tier-1 cooldown is normally anchored to a real
    loss fill's exit_time, which is stable across requests. When no fill
    is available to anchor on (test overrides; future code paths) we
    cache the first cooldown_until we compute so subsequent polls don't
    slide the timer forward. The cache is cleared once the streak breaks
    or the window elapses.
    """
    streak = snapshot.consecutive_losses()
    tier1 = settings.CONSECUTIVE_LOSS_TIER1_COUNT
    tier2 = settings.CONSECUTIVE_LOSS_TIER2_COUNT

    if streak < tier1:
        # No streak -- drop any stale fallback anchor so the next streak
        # starts fresh instead of inheriting yesterday's expired window.
        lockout_cache.clear(_TIER1_CACHE_KEY)
        return True, "", None

    last_loss = snapshot.last_loss()
    exit_time = _parse_helsinki(last_loss.get("exit_time")) if last_loss else None

    # Tier 2: rest of trading day. Use end of today in Helsinki. Already
    # stable across refreshes (anchored to today's date, not to "now").
    if streak >= tier2:
        end_of_day = HELSINKI.localize(
            datetime.combine(now.date(), time(23, 59, 59))
        )
        msg = (
            f"Consecutive-loss lockout (tier 2): {streak} losses in a row. "
            f"No new entries for the rest of the day."
        )
        logger.warning(msg)
        return False, msg, end_of_day

    # Tier 1: N minutes from last loss exit_time.
    threshold = timedelta(minutes=settings.CONSECUTIVE_LOSS_TIER1_MINUTES)
    if exit_time is not None:
        # Stable anchor -- exit_time is the same across every refresh.
        cooldown_until = exit_time + threshold
        # If we had a fallback cache from before the fill materialized,
        # clear it -- the real anchor takes over from here.
        lockout_cache.clear(_TIER1_CACHE_KEY)
    else:
        # No fill-derived anchor -- cache the first cooldown_until we
        # compute. Subsequent calls return the same value, so refreshing
        # the page cannot reset the timer.
        candidate = now + threshold
        cooldown_until = lockout_cache.remember(_TIER1_CACHE_KEY, candidate)

    if now >= cooldown_until:
        # Window elapsed -- drop the cache and allow entries again.
        lockout_cache.clear(_TIER1_CACHE_KEY)
        return True, "", None

    remaining = cooldown_until - now
    remaining_str = str(remaining).split(".")[0]
    msg = (
        f"Consecutive-loss lockout (tier 1): {streak} losses in a row. "
        f"No new entries for {remaining_str} more."
    )
    logger.warning(msg)
    return False, msg, cooldown_until


def compute_lockout_state(snapshot: TradesSnapshot, now: datetime) -> dict:
    """
    Pure read-only status of the loss-cooldown lockouts. Used by the
    /portfolio/lockout-status endpoint so the UI can show a countdown
    *before* the user attempts an entry. Mirrors the order in which
    process_entry_request runs the checks (consecutive first), so the
    decision the UI shows matches what an entry attempt would receive.
    """
    streak = snapshot.consecutive_losses()

    for cd_ok, cd_msg, cd_until in (
        check_consecutive_losses(snapshot, now),
        check_loss_cooldown(snapshot, now),
    ):
        if not cd_ok:
            return {
                "locked": True,
                "reason": "loss_cooldown",
                "message": cd_msg,
                "cooldown_until": cd_until.isoformat() if cd_until else None,
                "streak": streak,
            }
    return {
        "locked": False,
        "reason": None,
        "message": "",
        "cooldown_until": None,
        "streak": streak,
    }


def check_frequency(snapshot: TradesSnapshot, symbol: str, now: datetime) -> tuple[bool, str]:
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


async def process_entry_request(
    client: IbClient,
    payload: EntryRequest,
) -> EntryRequestResponse:
    """
    Validate guards and place a bracket order. No exit arming happens
    here -- exits are managed separately on the trade-manager page.
    """
    symbol = payload.symbol
    stop_price = payload.stop_price

    try:
        snapshot = await build_today_snapshot(client)
        now = datetime.now(HELSINKI)

        ok, message = check_daily_loss(snapshot)
        if not ok:
            enforce_daily_loss_circuit_breaker(client)
            return EntryRequestResponse(allowed=False, message=message, symbol=symbol)

        for ok, message in (
            check_block_window(now),
            check_total_attempts(snapshot),
            check_attempts(snapshot, symbol),
            check_frequency(snapshot, symbol, now),
        ):
            if not ok:
                return EntryRequestResponse(allowed=False, message=message, symbol=symbol)

        # Escalating consecutive-loss lockout runs first -- it's the more
        # restrictive of the two and they share the loss_cooldown response
        # shape, so the frontend banner handles either tier transparently.
        for cd_ok, cd_msg, cd_until in (
            check_consecutive_losses(snapshot, now),
            check_loss_cooldown(snapshot, now),
        ):
            if not cd_ok:
                return EntryRequestResponse(
                    allowed=False,
                    message=cd_msg,
                    symbol=symbol,
                    reason="loss_cooldown",
                    cooldown_until=cd_until.isoformat() if cd_until else None,
                )

        logger.info(f"Entry allowed for {symbol}")

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

        if not parent or not stop:
            msg = f"Bracket order placement failed for {symbol}"
            logger.error(msg)
            return EntryRequestResponse(allowed=False, message=msg, symbol=symbol)

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
