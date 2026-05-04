import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Order:
    symbol: str
    action: str
    position_size: int
    contract_type : str
    entry_price: float = Optional
    stop_price: float = Optional
    


def calculate_entry_price(bid_ask: dict, stop_price: float, offset: float = 0.02) -> float:
    """
    Decide an entry limit price relative to the configured stop:
      - long  (ask > stop)  -> ask + offset
      - short (bid < stop)  -> bid - offset
    Raises ValueError if the quote is missing/invalid or the stop sits
    inside the spread (ask <= stop <= bid), which would leave direction
    ambiguous and previously returned None silently.
    """
    if not bid_ask:
        raise ValueError("calculate_entry_price: missing bid/ask quote")

    bid = bid_ask.get("bid")
    ask = bid_ask.get("ask")

    if not bid or not ask or bid <= 0 or ask <= 0:
        raise ValueError(
            f"calculate_entry_price: invalid quote (bid={bid}, ask={ask})"
        )

    if ask > stop_price:
        return round(ask + offset, 2)
    if bid < stop_price:
        return round(bid - offset, 2)

    raise ValueError(
        f"calculate_entry_price: stop_price {stop_price} sits inside "
        f"the spread (bid={bid}, ask={ask}); cannot pick a direction"
    )


def calculate_position_size(entry_price, stop_price, risk) -> int:
    """
    Risk-based sizing: |risk / (entry - stop)|, forced to int.
    Raises ValueError on bad inputs instead of silently returning None
    so callers see the failure rather than crashing on the next line.
    """
    if entry_price is None or stop_price is None or risk is None:
        raise ValueError(
            f"calculate_position_size: missing input "
            f"(entry={entry_price}, stop={stop_price}, risk={risk})"
        )

    risk_per_unit = entry_price - stop_price
    if risk_per_unit == 0:
        raise ValueError("Entry price and stop price cannot be the same.")

    return abs(int(risk / risk_per_unit))





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
        contract_type=data["contract_type"]
    )



    