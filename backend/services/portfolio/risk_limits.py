import logging
import subprocess
from services.portfolio.ib_client import IbClient
from core.config import settings


logger = logging.getLogger(__name__)





# ----------------------------------------------------------------------
# Risk / validation
# ----------------------------------------------------------------------
async def check_daily_loss_limit(client: IbClient) -> tuple[bool, str]:
    """
    Check if daily loss limit has been exceeded.
    If exceeded, logs the breakdown, force closes TWS, and shuts down the program.
    Returns (allowed: bool, message: str).
    """
    daily_pnl = await client.get_realized_pnl_today()
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
            client.ib.disconnect()
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