import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Order:
    symbol: str
    action: str
    position_size: int
    entry_price: float = Optional
    stop_price: float = Optional



def calculate_position_size(entry_price, stop_price, risk):

    try:
        risk_per_unit = entry_price - stop_price
        if risk_per_unit == 0:
            raise ValueError("Entry price and stop price cannot be the same.")
        
        position_size = abs(int(risk / risk_per_unit))  # force integer
        return position_size
    
    except Exception as e:
        logger.error("Error calculating position size:", e)
        return None





def build_order(data: dict) -> Order:
    """
    Validate incoming order payload and build Order dataclass.
    Raises ValueError if invalid.
    """

    required_fields = ["symbol", "entry_price", "stop_price", "position_size"]

    for field in required_fields:
        if field not in data:
            raise ValueError(f"Missing required field: {field}")

    symbol = data["symbol"].upper()
    entry_price = float(data["entry_price"])
    stop_price = float(data["stop_price"])
    position_size = int(data["position_size"])

    if position_size <= 0:
        raise ValueError("Position size must be greater than 0")

    # Determine direction automatically
    if entry_price > stop_price:
        action = "BUY"
    elif entry_price < stop_price:
        action = "SELL"
    else:
        raise ValueError("Entry price and stop price cannot be equal")

    logger.info(
        f"Building order: {symbol} {action} "
        f"entry={entry_price} stop={stop_price} size={position_size}"
    )

    return Order(
        symbol=symbol,
        action=action,
        position_size=position_size,
        entry_price=entry_price,
        stop_price=stop_price,
    )



    