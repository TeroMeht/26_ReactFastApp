import httpx
import logging
from datetime import datetime
from typing import List, Optional, Dict
import pytz
from db.pending_orders import *
from services.orders import calculate_position_size
from services.portfolio.ib_client import IbClient
from services.portfolio.flows.entry import check_all_guards
from services.portfolio.trades.trades_snapshot import build_today_snapshot
from schemas.api_schemas import PendingOrder

from core.config import settings
from core.risk_manager_config import risk_settings
import asyncio


logger = logging.getLogger(__name__)





# =====================================
# MANUAL ORDERS (Alpaca)
# =====================================


async def fetch_manual_orders() -> Optional[List[dict]]:

    endpoint = f"{settings.ALPACA_BASE_URL}/orders"
    headers = {
        "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": settings.ALPACA_API_SECRET,
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(endpoint, headers=headers)
            logger.debug(f"Alpaca API response: {response.status_code} - {response.text}")
        if response.status_code == 200:
            return response.json()

        logger.error(
            f"Error fetching Alpaca orders: "
            f"{response.status_code} - {response.text}"
        )
        return None

    except Exception as e:
        raise e
    
async def cancel_manual_order(order_id: str) -> dict:
 
    endpoint = f"{settings.ALPACA_BASE_URL}/orders/{order_id}"
    headers = {
        "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": settings.ALPACA_API_SECRET,
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.delete(endpoint, headers=headers)

        # Success: 204 No Content
        if response.status_code == 204:
            return {"message": "Order cancelled successfully", "order_id": order_id}

        # If already filled or canceled, Alpaca may return 422
        if response.status_code == 422:
            return {
                "message": "Order could not be cancelled (possibly already filled or cancelled)",
                "details": response.json(),
            }

    except Exception as e:
        logger.error(f"Error cancelling Alpaca order {order_id}: {e}")
        raise e


# ========================
# AUTO (DB) ORDERS
# ========================

async def fetch_auto_orders(db_conn) -> List[Dict]:
    """
    Fetch active auto orders from database.
    """
    orders = await fetch_active_auto_orders(db_conn)
    if not orders:
        logger.info("No today active auto orders found in DB.")
        return []

    return orders

async def delete_auto_order1(order_id: int, db_conn) -> Dict:
    """
    Deactivate auto order by ID.
    Returns a dict with status and order_id.
    """
    results = await delete_auto_order(db_conn=db_conn, order_id=order_id)

    if results is None:
        raise Exception(f"Order with ID {order_id} not found.")

    return results

# Combine both

async def normalize_manual_orders() -> List[Dict]:
    try:
        manual_orders = await fetch_manual_orders()
        normalized_manual_orders: List[Dict] = []

        if not manual_orders:
            return normalized_manual_orders

        for order in manual_orders:
            effective_stop = order.get("stop_price") or order.get("limit_price")

            normalized_manual_orders.append({
                "id": order.get("id"),
                "symbol": order.get("symbol"),
                "stop_price": float(effective_stop) if effective_stop else None,
                "status": order.get("status"),
                "source": "ALPACA"
            })

        return normalized_manual_orders

    except Exception as e:
        logger.exception("Failed to fetch/normalize manual (Alpaca) orders: %s", e)
        return []


async def normalize_auto_orders(db_conn) -> List[Dict]:
    try:
        auto_orders = await fetch_auto_orders(db_conn)
        normalized_auto_orders: List[Dict] = []

        if not auto_orders:
            return normalized_auto_orders

        for order in auto_orders:
            normalized_auto_orders.append({
                "id": str(order.get("id")),
                "symbol": order.get("symbol"),
                "stop_price": float(order.get("stop")) if order.get("stop") else None,
                "status": order.get("status"),
                "source": "DB"
            })

        return normalized_auto_orders

    except Exception as e:
        logger.exception("Failed to fetch/normalize DB auto orders: %s", e)
        return []


async def wrapup_pending_orders(db_conn) -> List[Dict]:
    try:
        manual_orders = await normalize_manual_orders()
        auto_orders = await normalize_auto_orders(db_conn)

        combined_orders = manual_orders + auto_orders

        logger.info(
            "Pending orders: %d total (%d Alpaca, %d DB)",
            len(combined_orders),
            len(manual_orders),
            len(auto_orders),
        )

        return combined_orders

    except Exception as e:
        logger.exception("Unexpected failure in wrapup_pending_orders: %s", e)
        return []


# Calculate and generate PendingOrder for UI to show
async def process_open_orders(
    db_conn,
    ib,
) -> List[PendingOrder]:
        """
        Build the Pending Orders table view.

        For each row from Alpaca+DB we run the full entry guards
        up-front (using the shared cached snapshot so this is near-free
        after the first call in a 5s window) so the UI can disable
        Send and surface a blocked_reason on rows that would fail
        anyway. The Send click itself posts to /entry-request, which
        re-runs the guards at click-time and places the bracket order.
        """
        client = IbClient(ib)
        combined_orders = await wrapup_pending_orders(db_conn)

        if not combined_orders:
            return []

        # Guards need a snapshot + current time. Snapshot is cached
        # server-side (SNAPSHOT_TTL_SECONDS=5) so this rarely round-trips.
        # Kick it off concurrently with the bid/ask fan-out so the two
        # I/O waits overlap.
        tz = pytz.timezone(settings.TIMEZONE)
        current_time = datetime.now(tz)

        snapshot_task = asyncio.create_task(build_today_snapshot(client, db_conn=db_conn))
        bid_ask_task = asyncio.gather(
            *(client.get_bid_ask_price(o["symbol"]) for o in combined_orders),
            return_exceptions=True,
        )
        snapshot = await snapshot_task
        bid_ask_results = await bid_ask_task

        processed_orders: List[PendingOrder] = []

        for order, bid_ask in zip(combined_orders, bid_ask_results):
            try:
                if isinstance(bid_ask, Exception):
                    logger.error(
                        "Error fetching bid/ask for %s: %s",
                        order["symbol"], bid_ask,
                    )
                    continue

                ask = bid_ask.ask
                position_size = calculate_position_size(
                    ask,
                    order["stop_price"],
                    risk_settings.RISK,
                )
                size = round(position_size * ask, 2)

                # Run every entry guard so the UI can disable Send and
                # show a blocked_reason on rows that would fail at
                # click-time anyway.
                verdict = check_all_guards(snapshot, current_time, order["symbol"])

                blocked_reason: Optional[str] = None
                cooldown_until: Optional[str] = None
                if not verdict.allowed:
                    blocked_reason = verdict.message
                    cooldown_until = verdict.cooldown_until

                processed_orders.append(
                    PendingOrder(
                        id=order["id"],
                        symbol=order["symbol"],
                        stop_price=order["stop_price"],
                        latest_price=ask,
                        position_size=position_size,
                        size=size,
                        status=order["status"],
                        source=order["source"],
                        blocked_reason=blocked_reason,
                        cooldown_until=cooldown_until,
                    )
                )
            except Exception as e:
                logger.error(f"Error processing {order['symbol']}: {e}")
                continue

        return processed_orders