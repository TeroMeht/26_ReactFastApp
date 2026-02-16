from attr import dataclass
import httpx
import logging
from typing import List, Optional, Dict
from db.orders_repo import AutoOrderRepository
from services.portfolio import PortfolioService
from services.orders import calculate_position_size

from core.config import settings
import asyncio
from ib_async import IB

logger = logging.getLogger(__name__)

@dataclass
class Order:
    id:str
    symbol: str
    stop_price: float
    latest_price: float = 0.0   # default 0.0, will be updated from IB
    position_size: int = 0       # default 0, calculated later



class OrderService:
    def __init__(self,db_connection,ib: IB):

        self.ib=ib
        self.base_url = settings.ALPACA_BASE_URL
        self.headers = {
            "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": settings.ALPACA_API_SECRET,
        }

        self.repository = AutoOrderRepository(db_connection)
        self.portfolio_service = PortfolioService(ib)


    # =====================================
    # MANUAL ORDERS (Alpaca)
    # =====================================


    async def fetch_manual_orders(self) -> Optional[List[dict]]:
        """Fetch open orders from Alpaca API."""
        endpoint = f"{self.base_url}/orders"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(endpoint, headers=self.headers)
                logger.debug(f"Alpaca API response: {response.status_code} - {response.text}")
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



    async def wrapup_pending_orders(self) -> List[Dict]:
        """
        Fetch Alpaca open orders and DB auto orders,
        normalize them to a unified structure,
        and return combined list.
        """

        # --- Fetch Alpaca manual orders ---
        manual_orders = await self.fetch_manual_orders()

        # --- Fetch DB auto orders ---
        auto_orders = await self.fetch_auto_orders()

        if not manual_orders and not auto_orders:
            logger.info("No Alpaca or DB orders to process.")
            return []

        # --- Normalize Alpaca manual orders ---
        normalized_manual_orders = []
        if manual_orders:
            for order in manual_orders:
                normalized_manual_orders.append({
                    "id": order.get("id"),
                    "symbol": order.get("symbol"),
                    "stop_price": order.get("stop_price"),
                    "limit_price": order.get("limit_price"),
                    "source": "ALPACA"
                })

        # --- Normalize DB auto orders ---
        normalized_auto_orders = []
        for order in auto_orders:
            normalized_auto_orders.append({
                "id": order.get("Id"),
                "symbol": order.get("Symbol"),
                "stop_price": order.get("Stop"),
                "limit_price": None,  # DB orders don't have limit price
                "source": "DB"
            })

        # --- Combine both ---
        combined_orders = normalized_manual_orders + normalized_auto_orders

        logger.info(
            "Pending orders: %d total (%d Alpaca, %d DB)",
            len(combined_orders),
            len(normalized_manual_orders),
            len(normalized_auto_orders),
        )

        return combined_orders

    async def process_open_orders(self) -> List[Dict]:
        """
        1. Fetch + normalize orders
        2. Enrich with IB prices (parallel)
        3. Calculate position size
        4. Return structured output
        """

        combined_orders = await self.wrapup_pending_orders()

        if not combined_orders:
            return []

        # Filter valid orders first
        valid_orders = []
        for order in combined_orders:
            id = order.get("id")
            symbol = order.get("symbol")

            if not symbol or not id:
                continue

            stop_price = float(order.get("stop_price") or 0.0)
            limit_price = float(order.get("limit_price") or 0.0)

            effective_stop = stop_price or limit_price
            if effective_stop <= 0:
                continue

            valid_orders.append({
                "id": id,
                "symbol": symbol,
                "effective_stop": effective_stop
            })

        if not valid_orders:
            return []

        #  Fetch ALL bid/ask prices concurrently
        tasks = [
            self.portfolio_service.get_bid_ask_price(order["symbol"])
            for order in valid_orders
        ]

        bid_ask_results = await asyncio.gather(*tasks, return_exceptions=True)

        processed_orders: List[Order] = []

        for order, bid_ask in zip(valid_orders, bid_ask_results):
            try:

                if not bid_ask:
                    continue

                ask = bid_ask["ask"]
                if not ask or ask <= 0:
                    continue

                position_size = calculate_position_size(
                    ask,
                    order["effective_stop"],
                    settings.RISK
                )

                processed_orders.append(
                    Order(
                        id=order["id"],
                        symbol=order["symbol"],
                        stop_price=order["effective_stop"],
                        latest_price=ask,
                        position_size=position_size
                    )
                )
                logger.info(f"{processed_orders}")

            except Exception as e:
                logger.error(f"Error processing {order['symbol']}: {e}")
                continue

        return [order.__dict__ for order in processed_orders]