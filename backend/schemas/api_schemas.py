
from pydantic import BaseModel, field_validator,Field
from datetime import date, time
from datetime import datetime
from typing import Optional,Any,List
from decimal import Decimal

from core.config import settings


class TickerFile(BaseModel):
    filename: str
    content: str


# Pending orders router
class PendingOrder(BaseModel):
    id: str
    symbol: str
    stop_price: float
    latest_price: float
    position_size: int
    size: float
    status: str
    source: str
    


# Open risk row model
class OpenPosition(BaseModel):
    # List of currently armed exit strategies for this symbol. Replaces the
    # old exit_request:bool flag — multiple exit_requests rows can now exist
    # per symbol, so we surface their strategy names directly.
    exit_strategies: List[str] = []
    symbol: str
    contract_type:str
    allocation: float
    size: float
    avgcost: float
    auxprice: float
    position: float
    openrisk: float





class AlarmResponse(BaseModel):
    Id: int
    Symbol: str
    Time: time
    Alarm: str
    Date: date

class CreateAlarmRequest(BaseModel):
    Symbol: str
    Time: time
    Alarm: str
    Date: date






class CandleRow(BaseModel):
    Symbol: str
    Date:date
    Time: time
    Open: Decimal
    High: Decimal
    Low: Decimal
    Close: Decimal
    Volume: Decimal
    VWAP: Decimal
    EMA9: Decimal
    Avg_volume: Optional[Decimal] 
    Rvol: Decimal
    Relatr: Decimal


class ModifyOrderRequest(BaseModel):
    symbol: str
    new_quantity: float

    
class ModifyOrderByIdRequest(BaseModel):
    order_id: int
    new_quantity: float









ALLOWED_TRIM_PERCENTAGES = {Decimal("0.25"), Decimal("0.5"), Decimal("0.75"), Decimal("1")}


class UpdateExitRequest(BaseModel):
    symbol: str = Field(
        ...,
        min_length=1,
        description="Trading symbol (auto uppercased)"
    )
    trim_percentage: Decimal = Field(
        default=Decimal("1"),
        description="Fraction of the position to exit. Allowed: 0.25, 0.5, 0.75, 1 (1 = full exit)."
    )
    strategy: str = Field(
        ...,
        min_length=1,
        description=(
            "Which incoming exit trigger should fire this row. "
            "Must be one of settings.EXIT_TRIGGERS."
        ),
    )

    @field_validator("symbol")
    @classmethod
    def validate_and_uppercase_symbol(cls, v: str) -> str:
        v = v.strip().upper()

        if not v:
            raise ValueError("Symbol cannot be empty")

        return v

    @field_validator("trim_percentage")
    @classmethod
    def validate_trim_percentage(cls, v: Decimal) -> Decimal:
        # Normalize values like 1.0 → 1, 0.50 → 0.5 for comparison
        normalized = v.normalize() if v != 0 else v
        allowed_normalized = {p.normalize() for p in ALLOWED_TRIM_PERCENTAGES}
        if normalized not in allowed_normalized:
            raise ValueError(
                f"trim_percentage must be one of 0.25, 0.5, 0.75, or 1 (got {v})"
            )
        return v

    @field_validator("strategy")
    @classmethod
    def validate_strategy(cls, v: str) -> str:
        v = v.strip()
        allowed = set(settings.EXIT_TRIGGERS)
        if v not in allowed:
            raise ValueError(
                f"strategy must be one of {sorted(allowed)} (got '{v}')"
            )
        return v







# Exits
class ExitRequestResponse(BaseModel):
    symbol: str
    strategy: str
    trim_percentage: Decimal
    updated: datetime



# Watchlist streamer lähettää tällaisen sanoman POST endpointtiin, jossa tarkastetaan ensin että onko sille symbolille tilattu exit
class ExitRequest(BaseModel):
    date: date
    time: time
    alarm: str
    symbol: str

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("Symbol cannot be empty")
        return v

    @field_validator("alarm")
    @classmethod
    def validate_alarm(cls, v: str) -> str:
        v = v.strip()
        allowed = set(settings.EXIT_TRIGGERS)
        if v not in allowed:
            raise ValueError(
                f"alarm must be one of {sorted(allowed)} (got '{v}')"
            )
        return v
     
class ExitRequestResponseIB(BaseModel):
    symbol: str
    message: str
    order_id :Optional[int] = None




# Entry
class EntryRequest(BaseModel):
    symbol: str
    contract_type:str
    entry_price: float
    stop_price: float
    position_size: int
    
class EntryRequestResponse(BaseModel):
    allowed: bool
    message: str
    symbol: str
    parentOrderId: Optional[int] = None
    stopOrderId: Optional[int] = None


# Add
class AddRequest(BaseModel):
    symbol: str
    contract_type:str
    total_risk: int

class AddRequestResponse(BaseModel):
    allowed: bool
    message: str
    symbol: str
    new_order: Optional[Any] = None
    place_result: Optional[Any] = None
    modified_stp_qty: Optional[int] = None


# Entry attempts stats row (per-symbol per-day count for the UI table)
class EntryAttemptsRow(BaseModel):
    symbol: str
    attempts: int
    max_attempts: int
    remaining: int








# Scanner response

class ScannerResponse(BaseModel):
    symbol: str           # The stock symbol (e.g., "AAOI")
    date: date            # Date (e.g., "2026-03-04")
    time: time             # Time (e.g., "13:08:00")
    open: float           # Opening price (e.g., 40.6)
    high: float           # Highest price (e.g., 40.6)
    low: float            # Lowest price (e.g., 40.6)
    close: float          # Closing price (e.g., 40.6)
    volume: int           # Volume (e.g., 0)
    rvol: float           # Relative volume (e.g., 3.16)
    change: float


# schemas/api_schemas.py
class NewsItem(BaseModel):
    title: str
    summary: str
    url: str
    source: str
    published_at: str
    thumbnail: str




# ---------------- Auto Assist ----------------

class AutoAssistStartRequest(BaseModel):
    symbol: str = Field(..., min_length=1, description="Ticker to start streaming")

    @field_validator("symbol")
    @classmethod
    def _uppercase_symbol(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("symbol cannot be empty")
        return v


class AutoAssistBar(BaseModel):
    time: float                # UTC epoch seconds at bar-open
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None
    ema9: Optional[float] = None
    vwap: Optional[float] = None


class AutoAssistTick(BaseModel):
    time: float                # UTC epoch seconds of the tick
    price: float
    bar_time: float
    bar_open: float
    bar_high: float
    bar_low: float
    bar_close: float
    bar_volume: Optional[float] = None
    bar_vwap: Optional[float] = None


class AutoAssistSignal(BaseModel):
    symbol: str
    price: float               # price that broke the level (entry trigger)
    last2_high: float          # max high of previous 2 completed bars
    stop_level: float          # min low of last 5 completed bars minus 0.06
    position_size: int         # computed from configured per-trade risk
    contract_type: str = "stock"
    bar_time: float            # bar-open time (UTC sec) that contained the trigger
    ts: float


class AutoAssistState(BaseModel):
    symbol: str
    bars: list[AutoAssistBar]
    last2_high: Optional[float] = None
    stop_level: Optional[float] = None
