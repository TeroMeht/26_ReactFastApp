import httpx
import logging
from typing import List, Optional, Dict
from db.orders_repo import AutoOrderRepository
from core.config import settings

logger = logging.getLogger(__name__)


class OrderService:
    def __init__(self,db_connection):
        self.base_url = settings.ALPACA_BASE_URL
        self.headers = {
            "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": settings.ALPACA_API_SECRET,
        }

        self.repository = AutoOrderRepository(db_connection)


    # =====================================
    # MANUAL ORDERS (Alpaca)
    # =====================================


    async def fetch_manual_orders(self) -> Optional[List[dict]]:
        """Fetch open orders from Alpaca API."""
        endpoint = f"{self.base_url}/orders"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(endpoint, headers=self.headers)
                logger.info(f"Alpaca API response: {response.status_code} - {response.text}")
            if response.status_code == 200:
                return response.json()

            logger.error(
                f"Error fetching Alpaca orders: "
                f"{response.status_code} - {response.text}"
            )
            return None

        except Exception as e:
            logger.error(f"Exception while fetching Alpaca orders: {e}")
            return None
        
    async def cancel_manual_order(self, order_id: str) -> dict:
        """
        Cancel an open order by ID.
        Alpaca returns 204 No Content on success.
        """
        endpoint = f"{self.base_url}/orders/{order_id}"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.delete(endpoint, headers=self.headers)

            # Success: 204 No Content
            if response.status_code == 204:
                return {"message": "Order cancelled successfully", "order_id": order_id}

            # If already filled or canceled, Alpaca may return 422
            if response.status_code == 422:
                return {
                    "message": "Order could not be cancelled (possibly already filled or cancelled)",
                    "details": response.json(),
                }

            response.raise_for_status()

        except Exception as e:
            logger.error(f"Error cancelling Alpaca order {order_id}: {e}")
            raise


    # ========================
    # AUTO (DB) ORDERS
    # ========================

    async def fetch_auto_orders(self) -> List[Dict]:
        """
        Fetch active auto orders from database.
        """
        orders = await self.repository.fetch_active_auto_orders()
        if not orders:
            logger.info("No active auto orders found in DB.")
            return []

        return orders

    async def deactivate_auto_order(self, order_id: int) -> Dict:
        """
        Deactivate auto order by ID.
        """
        rows_updated = await self.repository.update_auto_order_status(
            order_id=order_id,
            new_status="deactive",
        )

        if rows_updated == 0:
            return {"status": "not_found", "order_id": order_id}

        return {"status": "success", "order_id": order_id}