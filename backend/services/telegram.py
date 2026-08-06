"""
Telegram notification helper.

Fire-and-forget messaging for automatic exit events (and any other flow
that wants to notify the user). Ported from 22_WatchlistStreamer's
alarms/send_telegram.py.

The helper never raises: transport errors, API errors, and missing
credentials all resolve to a logged warning + a result dict. Missing
credentials (either TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID unset in the
env file) short-circuits the call so the app runs fine without Telegram
configured.
"""

import logging
from datetime import datetime

import httpx
import pytz

from core.config import settings

logger = logging.getLogger(__name__)


# Cap outbound HTTP so a slow Telegram API can't stall the caller.
_TELEGRAM_TIMEOUT = 10.0




def now_hhmm_helsinki() -> str:
    """Current wall-clock time as HH:MM in Europe/Helsinki."""
    return datetime.now(pytz.timezone(settings.TIMEZONE)).strftime("%H:%M")


async def send_telegram_message(text: str) -> dict:
    """
    Send a Telegram message asynchronously. Returns the Telegram API
    response dict on success, or a {"ok": False, "error": "..."} dict on
    any failure. Never raises.
    """
    bot_token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID
    if not bot_token or not chat_id:
        logger.warning(
            "Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID "
            "missing in env); skipping message."
        )
        return {"ok": False, "error": "not configured"}

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }

    try:
        async with httpx.AsyncClient(timeout=_TELEGRAM_TIMEOUT) as client:
            response = await client.post(url, data=payload)
        result = response.json()
        if result.get("ok"):
            logger.info("Telegram message sent")
        else:
            logger.warning("Telegram API error: %s", result)
        return result
    except Exception as e:
        logger.exception("Failed to send Telegram message")
        return {"ok": False, "error": str(e)}
