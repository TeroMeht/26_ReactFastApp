import httpx
import logging
from typing import List, Optional, Dict
from db.pending_orders import *
from services.orders import calculate_position_size
from services.portfolio import PortfolioService
from schemas.api_schemas import PendingOrder

from core.config import settings
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

async def deactivate_auto_order1(order_id: int, db_conn) -> Dict:
    """
    Deactivate auto order by ID.
    Returns a dict with status and order_id.
    """
    results = await update_auto_order_status(
        db_conn=db_conn,
        order_id=order_id,
        new_status="deactive",
    )

    if results is None:
        raise Exception(f"Order with ID {order_id} not found.")

    return results

# Combine both

async def wrapup_pending_orders(db_conn) -> List[Dict]:

    # --- Fetch Alpaca manual orders ---
    manual_orders = await fetch_manual_orders()

    # --- Normalize Alpaca manual orders ---
    normalized_manual_orders = []

    if manual_orders:
        for order in manual_orders:
            # Prefer stop_price, fallback to limit_price
            effective_stop = order.get("stop_price") or order.get("limit_price")

            normalized_manual_orders.append({
                "id": order.get("id"),
                "symbol": order.get("symbol"),
                "stop_price": float(effective_stop),  # unified field
                "status": order.get("status"),
                "source": "ALPACA"
            })

    # --- Fetch DB auto orders ---
    auto_orders = await fetch_auto_orders(db_conn)


    # --- Normalize DB auto orders ---
    normalized_auto_orders = []

    if auto_orders:
        for order in auto_orders:
            normalized_auto_orders.append({
                "id": str(order.get("Id")),
                "symbol": order.get("Symbol"),
                "stop_price": float(order.get("Stop")),
                "status": order.get("Status"),
                "source": "DB"
            })

    # --- Combine both ---
    combined_orders = normalized_manual_orders + normalized_auto_orders

    logger.info("Pending orders: %d total (%d Alpaca, %d DB)",
        len(combined_orders),
        len(normalized_manual_orders),
        len(normalized_auto_orders),
    )

    return combined_orders


# Calculate and generate PendingOrder for UI to show
async def process_open_orders(db_conn,ib) -> List[PendingOrder]:
        
        portfolio_service = PortfolioService(ib,db_conn)
        combined_orders = await wrapup_pending_orders(db_conn)

        if not combined_orders:
            return []

        #  Fetch ALL bid/ask prices concurrently
        tasks = [portfolio_service.get_bid_ask_price(order["symbol"])
            for order in combined_orders
        ]

        bid_ask_results = await asyncio.gather(*tasks, return_exceptions=True)

        processed_orders: List[PendingOrder] = []

        for order, bid_ask in zip(combined_orders, bid_ask_results):
            try:
                ask = bid_ask["ask"]
                position_size = calculate_position_size(
                    ask,
                    order["stop_price"],
                    settings.RISK
                )
                
                size = position_size * ask

                processed_orders.append(
                    PendingOrder(
                        id=order["id"],
                        symbol=order["symbol"],
                        stop_price=order["stop_price"],
                        latest_price=ask,
                        position_size=position_size,
                        size = size,
                        status= order["status"],
                        source = order["source"]
                    )
                
                )
                logger.info(processed_orders)
            except Exception as e:
                logger.error(f"Error processing {order['symbol']}: {e}")
                continue

        return processed_orders