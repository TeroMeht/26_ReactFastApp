import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BidAsk:
    symbol: str
    bid: float
    ask: float


@dataclass
class Order:
    symbol: str
    action: str
    position_size: int
    contract_type : str
    entry_price: float = Optional
    stop_price: float = Optional


@dataclass
class OrderBuilder:
    symbol: str
    entry_price: float
    stop_price: float
    contract_type: str
    position_size: int



def calculate_entry_price(bid_ask: BidAsk, stop_price: float, offset: float = 0.02) -> float:
    """
    Pick an entry limit price relative to the configured stop:
      - long  (ask > stop) -> ask + offset
      - short (bid < stop) -> bid - offset

    """
    bid = bid_ask.bid
    ask = bid_ask.ask

    if bid_ask.ask > stop_price:
        return round(ask + offset, 2)
    if bid_ask.bid < stop_price:
        return round(bid - offset, 2)

    raise ValueError(
        f"calculate_entry_price: stop_price {stop_price} sits inside "
        f"the spread (bid={bid}, ask={ask}); cannot pick a direction"
    )


def calculate_position_size(entry_price, stop_price, risk) -> int:

    if entry_price is None or stop_price is None or risk is None:
        raise ValueError(
            f"calculate_position_size: missing input "
            f"(entry={entry_price}, stop={stop_price}, risk={risk})"
        )

    risk_per_unit = entry_price - stop_price

    if risk_per_unit == 0:
        raise ValueError("Entry price and stop price cannot be the same.")

    size = abs(int(risk / risk_per_unit))
    
    if size <= 0:
        # abs(entry - stop) > risk -> risk budget can't cover a single
        # share at this stop distance. Surface the numbers so the log /
        # UI can explain the reject.
        raise ValueError(
            f"Position size rounds to 0: risk-per-share "
            f"{abs(risk_per_unit):.2f} exceeds risk budget {risk:.2f} "
            f"(entry={entry_price}, stop={stop_price})."
        )
    return size





def build_order(spec: OrderBuilder) -> Order:
    """
    Turn an ``OrderBuilder`` spec into a placeable ``Order``.

    Pure packager: does no sizing, no bid/ask handling. The caller
    is responsible for having computed ``position_size`` already
    (usually via ``calculate_position_size``); we only validate it
    and derive the trade direction from entry vs stop.

    Raises ValueError on a non-positive size or an entry/stop pair
    that leaves direction ambiguous (entry == stop).
    """
    symbol = spec.symbol.upper()
    entry_price = float(spec.entry_price)
    stop_price = float(spec.stop_price)
    position_size = int(spec.position_size)

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
        contract_type=spec.contract_type,
    )



    