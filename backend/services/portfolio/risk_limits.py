import logging
import subprocess

from services.portfolio.ib_client import IbClient
from services.portfolio.trades_snapshot import TradesSnapshot, build_today_snapshot
from core.config import settings


logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Pure check
# ----------------------------------------------------------------------
def check_daily_loss(snapshot: TradesSnapshot) -> tuple[bool, str]:
    """
    Pure validation: is today's net realized PnL above the configured
    loss limit? No side effects. Use this from the entry flow; the kill
    switch is invoked separately via enforce_daily_loss_circuit_breaker.
    """
    net_pnl = snapshot.realized_pnl.get("net_pnl", 0.0)
    limit = -settings.MAX_DAILY_LOSS

    if net_pnl < limit:
        message = (
            f"Daily loss limit exceeded (net PnL: {net_pnl:.2f}, limit: {limit:.2f}). "
            f"No new entries allowed today."
        )
        logger.warning(
            f"Daily loss limit exceeded — net PnL: {net_pnl:.4f}, limit: {limit:.4f}."
        )
        return False, message

    logger.info(f"Daily loss check passed — net PnL: {net_pnl:.4f}, limit: {limit:.4f}")
    return True, ""


# ----------------------------------------------------------------------
# Side effect: tear down TWS when the limit is breached
# ----------------------------------------------------------------------
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


# ----------------------------------------------------------------------
# Back-compat wrapper
# ----------------------------------------------------------------------
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
