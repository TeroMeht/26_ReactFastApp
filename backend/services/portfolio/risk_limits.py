"""
Risk-limit checks and lockout monitoring.

Two families live here:
  * Daily-loss guard + circuit breaker (check_daily_loss,
    enforce_daily_loss_circuit_breaker).
  * Loss-cooldown lockouts -- the escalating consecutive-loss lockout and
    the post-loss cooldown that gate re-entry. Both are read by
    process_entry_request AND surfaced to the UI via /lockout-status.

The /lockout-status view is built here rather than in flows/entry.py
because it's a total-lockout monitoring concern, not an entry flow --
it just happens to reuse the same pure guards.
"""

import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytz

from core.config import settings
from core.risk_manager_config import risk_settings
from services.portfolio import lockout_cache
from services.portfolio.ib_client import IbClient
from services.portfolio.trades.trades_snapshot import TradesSnapshot, build_today_snapshot


logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Daily loss
# ----------------------------------------------------------------------
def check_daily_loss(snapshot: TradesSnapshot) -> tuple[bool, str]:
    """
    Pure validation: is today's realized PnL above the configured loss
    limit? No side effects. Use this from the entry flow; the kill switch
    is invoked separately via enforce_daily_loss_circuit_breaker.
    """
    pnl = snapshot.realized_pnl
    limit = -risk_settings.MAX_DAILY_LOSS

    if pnl < limit:
        message = (
            f"Daily loss limit exceeded (PnL: {pnl:.2f}, limit: {limit:.2f}). "
            f"No new entries allowed today."
        )
        logger.warning(
            f"Daily loss limit exceeded — PnL: {pnl:.4f}, limit: {limit:.4f}."
        )
        return False, message

    logger.info(f"Daily loss check passed — PnL: {pnl:.4f}, limit: {limit:.4f}")
    return True, ""


def enforce_daily_loss_circuit_breaker(client: IbClient) -> None:
    """
    Disconnect IB and kill the TWS process. Called only when
    check_daily_loss returns False on a real entry attempt.
    """
    try:
        client.ib.disconnect()
        logger.warning("IB API disconnected.")
    except Exception as e:
        logger.error(f"Failed to disconnect IB API: {e}")

    try:
        subprocess.call(["taskkill", "/F", "/IM", "tws.exe"])
        logger.warning("TWS process killed.")
    except Exception as e:
        logger.error(f"Failed to kill TWS process: {e}")


async def check_daily_loss_limit(client: IbClient) -> tuple[bool, str]:
    """
    Legacy entry-point preserved for any external callers. Builds its own
    snapshot, runs the pure check, and on breach also fires the circuit
    breaker — matching the previous behavior.
    """
    snapshot = await build_today_snapshot(client)
    allowed, message = check_daily_loss(snapshot)
    if not allowed:
        enforce_daily_loss_circuit_breaker(client)
    return allowed, message


# ----------------------------------------------------------------------
# Loss-cooldown lockouts
#
# Consumed by process_entry_request AND by build_lockout_status below.
# Both return the shared (ok, msg, cooldown_until) shape so callers can
# treat the two guards uniformly.
# ----------------------------------------------------------------------
def check_loss_cooldown(snapshot: TradesSnapshot, now: datetime):
    last_loss = snapshot.last_loss()
    if not last_loss:
        return True, "", None
    exit_time = last_loss.get("exit_time")
    if exit_time is None:
        return True, "", None
    threshold = timedelta(minutes=risk_settings.MAX_ENTRY_FREQUENCY_MINUTES)
    cooldown_until = exit_time + threshold
    elapsed = now - exit_time
    if elapsed <= threshold:
        elapsed_str = str(elapsed).split(".")[0]
        msg = (
            f"Loss cooldown active. Last loss was {elapsed_str} ago "
            f"(PnL: {last_loss.get('pnl')})."
        )
        logger.info(msg)
        return False, msg, cooldown_until
    return True, "", None


_TIER1_CACHE_KEY = "consecutive_losses:tier1_floating"
_TIER2_CACHE_KEY = "consecutive_losses:tier2_floating"


def check_consecutive_losses(snapshot: TradesSnapshot, now: datetime):
    """
    Escalating lockout based on the current losing streak:
      - tier 2 (>= CONSECUTIVE_LOSS_TIER2_COUNT): locked for
        CONSECUTIVE_LOSS_TIER2_MINUTES from the last loss's exit_time.
      - tier 1 (>= CONSECUTIVE_LOSS_TIER1_COUNT): locked for
        CONSECUTIVE_LOSS_TIER1_MINUTES from the last loss's exit_time.
    Returns the same (ok, msg, cooldown_until) shape as check_loss_cooldown
    so the existing EntryRequestResponse(reason="loss_cooldown", ...)
    surface is reused and the frontend banner picks it up unchanged.

    Refresh safety: both cooldowns are normally anchored to a real loss
    fill's exit_time, which is stable across requests. When no fill is
    available to anchor on (test overrides; future code paths) we cache
    the first cooldown_until we compute so subsequent polls don't slide
    the timer forward. The cache is cleared once the streak breaks or
    the window elapses.
    """
    streak = snapshot.consecutive_losses()
    risk = risk_settings
    tier1 = risk.CONSECUTIVE_LOSS_TIER1_COUNT
    tier2 = risk.CONSECUTIVE_LOSS_TIER2_COUNT

    if streak < tier1:
        # No streak -- drop any stale fallback anchors so the next streak
        # starts fresh instead of inheriting yesterday's expired window.
        lockout_cache.clear(_TIER1_CACHE_KEY)
        lockout_cache.clear(_TIER2_CACHE_KEY)
        return True, "", None

    last_loss = snapshot.last_loss()
    exit_time = last_loss.get("exit_time")
    # Tier 2: N minutes from last loss exit_time.
    if streak >= tier2:
        threshold = timedelta(minutes=risk.CONSECUTIVE_LOSS_TIER2_MINUTES)
        if exit_time is not None:
            cooldown_until = exit_time + threshold
            lockout_cache.clear(_TIER2_CACHE_KEY)
        else:
            candidate = now + threshold
            cooldown_until = lockout_cache.remember(_TIER2_CACHE_KEY, candidate)

        if now >= cooldown_until:
            lockout_cache.clear(_TIER2_CACHE_KEY)
            return True, "", None

        remaining = cooldown_until - now
        remaining_str = str(remaining).split(".")[0]
        msg = (
            f"Consecutive-loss lockout (tier 2): {streak} losses in a row. "
            f"No new entries for {remaining_str} more."
        )
        logger.warning(msg)
        return False, msg, cooldown_until

    # Tier 1: N minutes from last loss exit_time.
    threshold = timedelta(minutes=risk.CONSECUTIVE_LOSS_TIER1_MINUTES)
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


# ----------------------------------------------------------------------
# Lockout status view (for /lockout-status endpoint)
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class LockoutStatus:
    """
    Read-only lockout view for the UI banner. `cooldown_until` is
    ISO-8601 (with tz) rather than a datetime because the wire schema
    (LockoutStatusResponse.cooldown_until) is typed as str; keeping the
    dataclass shape identical simplifies the router boundary (asdict ->
    Pydantic) and preserves the frontend contract.
    """
    locked: bool
    reason: str | None
    message: str
    cooldown_until: str | None
    streak: int


def compute_lockout_state(snapshot: TradesSnapshot, current_time: datetime) -> LockoutStatus:
    """
    Pure lockout view over a snapshot at a point in time. No IB round trip.
    Mirrors the order in which process_entry_request runs the two guards
    (consecutive first) so the banner shows the same decision an entry
    attempt would receive right now.
    """
    streak = snapshot.consecutive_losses()

    for cd_ok, cd_msg, cd_until in (
        check_consecutive_losses(snapshot, current_time),
        check_loss_cooldown(snapshot, current_time),
    ):
        if not cd_ok:
            return LockoutStatus(
                locked=True,
                reason="loss_cooldown",
                message=cd_msg,
                cooldown_until=cd_until.isoformat() if cd_until else None,
                streak=streak,
            )

    return LockoutStatus(
        locked=False,
        reason=None,
        message="",
        cooldown_until=None,
        streak=streak,
    )


async def build_lockout_status(client: IbClient) -> LockoutStatus:
    """
    Async wrapper: fetch today's snapshot and derive the lockout view.
    Used by the /lockout-status router endpoint.
    """
    snapshot = await build_today_snapshot(client)
    now = datetime.now(pytz.timezone(settings.TIMEZONE))
    return compute_lockout_state(snapshot, now)
