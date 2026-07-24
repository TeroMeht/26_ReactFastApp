
from pydantic import BaseModel, field_validator,Field
from datetime import date, time
from datetime import datetime
from typing import Optional,Any,List
from decimal import Decimal

from core.config import settings


# --- Watchlist ---------------------------------------------------------------
# These replace the old TickerFile / file-based ticker flow. The watchlist lives
# in two DB tables (watchlist + watchlist_strategies); the streamer reads it at
# startup and only fires the strategies a user has bound to a given ticker.




class WatchlistCreateRequest(BaseModel):
    """Body for POST /api/watchlist and PUT /api/watchlist/{symbol}."""
    symbol: str = Field(..., min_length=1, description="Ticker (auto-uppercased)")
    strategies: List[str] = Field(
        default_factory=list,
        description="Entry strategy names to bind to this ticker.",
    )



class WatchlistStrategiesRequest(BaseModel):
    """Body for PUT /api/watchlist/{symbol}/strategies (replaces strategy set)."""
    strategies: List[str] = Field(default_factory=list)


class WatchlistRow(BaseModel):
    """One row returned by GET /api/watchlist."""
    id: int
    symbol: str
    strategies: List[str]
    created_at: datetime


# Live order tracker -- single row in the SSE feed / snapshot
class LiveOrder(BaseModel):
    perm_id: int
    order_id: int
    symbol: Optional[str] = None
    sec_type: Optional[str] = None
    action: Optional[str] = None
    order_type: Optional[str] = None
    total_qty: float = 0.0
    lmt_price: Optional[float] = None
    aux_price: Optional[float] = None
    parent_id: int = 0
    status: Optional[str] = None
    filled: float = 0.0
    remaining: float = 0.0
    avg_fill_price: float = 0.0
    last_error: Optional[str] = None
    last_error_code: Optional[int] = None
    submitted_at: float = 0.0


class CancelOrderResult(BaseModel):
    status: str
    order_id: int
    symbol: Optional[str] = None
    filled: float = 0.0
    remaining: float = 0.0
    message: Optional[str] = None


# One entry in the chronological order activity log
class OrderLogEntry(BaseModel):
    ts: float
    perm_id: int = 0
    order_id: int = 0
    symbol: Optional[str] = None
    action: Optional[str] = None
    order_type: Optional[str] = None
    total_qty: float = 0.0
    lmt_price: Optional[float] = None
    aux_price: Optional[float] = None
    status: Optional[str] = None
    filled: float = 0.0
    remaining: float = 0.0
    avg_fill_price: float = 0.0
    last_error: Optional[str] = None
    last_error_code: Optional[int] = None


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
    # old exit_request:bool flag -- multiple exit_requests rows can now exist
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
        normalized = v.normalize() if v != 0 else v
        allowed_normalized = {p.normalize() for p in ALLOWED_TRIM_PERCENTAGES}
        if normalized not in allowed_normalized:
            raise ValueError(
                f"trim_percentage must be one of 0.25, 0.5, 0.75, or 1 (got {v})"
            )
        return v




# Exits
class ExitRequestResponse(BaseModel):
    symbol: str
    strategy: str
    trim_percentage: Decimal
    updated: datetime


# Watchlist streamer sends this body to the POST endpoint which first checks
# whether the symbol has an armed exit request.
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



class ExitRequestResponseIB(BaseModel):
    symbol: str
    message: str
    order_id :Optional[int] = None


# --- Custom (user-defined) price-target exits ----------------------------
# A custom exit is a real IB LIMIT order placed at target_price for a
# trim_percentage slice of the open position. On fill, the fill listener
# resizes the symbol's STP (or cancels it on a 100% trim) — same end
# state the strategy-based exit flow produces.


class CreateCustomExitRequest(BaseModel):
    symbol: str = Field(..., min_length=1)
    target_price: Decimal = Field(
        ..., gt=0, description="Limit price at which IB will execute the exit."
    )
    trim_percentage: Decimal = Field(
        default=Decimal("1"),
        description="Fraction of the position to exit. Allowed: 0.25, 0.5, 0.75, 1.",
    )

    @field_validator("symbol")
    @classmethod
    def _upper(cls, v: str) -> str:
        v = v.strip().upper()
        if not v:
            raise ValueError("symbol cannot be empty")
        return v

    @field_validator("trim_percentage")
    @classmethod
    def _trim(cls, v: Decimal) -> Decimal:
        normalized = v.normalize() if v != 0 else v
        allowed_normalized = {p.normalize() for p in ALLOWED_TRIM_PERCENTAGES}
        if normalized not in allowed_normalized:
            raise ValueError(
                f"trim_percentage must be one of 0.25, 0.5, 0.75, or 1 (got {v})"
            )
        return v


class CustomExitResponse(BaseModel):
    # IB-only — no DB row, so no internal id and no created/updated stamps.
    # `perm_id` is what the frontend passes back on cancel.
    symbol: str
    contract_type: str = ""
    order_id: int
    perm_id: Optional[int] = None
    target_price: Decimal
    # Null for externally-placed LIMIT orders when we can't derive trim
    # (e.g. position size unavailable).
    trim_percentage: Optional[Decimal] = None
    action: str  # SELL (long exit) or BUY (short exit)
    quantity: int
    status: str  # IB status (Submitted, PreSubmitted, …) or 'armed' on create


# Entry
class EntryRequest(BaseModel):
    symbol: str
    contract_type: str
    entry_price: float
    stop_price: float
    position_size: int


class EntryRequestResponse(BaseModel):
    allowed: bool
    message: str
    symbol: str
    parentOrderId: Optional[int] = None
    stopOrderId: Optional[int] = None
    # When the entry is blocked by the loss cooldown, this is the ISO-8601
    # timestamp at which entries will be allowed again. The UI keeps a
    # cooldown banner up (with a countdown) until this moment.
    reason: Optional[str] = None  # e.g. "loss_cooldown"
    cooldown_until: Optional[str] = None


# Lockout status -- proactive view of the loss-cooldown so the UI can
# show a countdown *before* the user attempts an entry. Polled by the
# global LockoutBanner.
class LockoutStatusResponse(BaseModel):
    locked: bool
    reason: Optional[str] = None  # "loss_cooldown" when locked
    message: str = ""
    cooldown_until: Optional[str] = None  # ISO-8601 with tz
    streak: int = 0  # current consecutive-loss count


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
    # When the add is blocked by the open-position cooldown, this is the
    # ISO-8601 timestamp at which adds will be allowed again. Mirrors the
    # entry flow's loss_cooldown surface.
    reason: Optional[str] = None  # e.g. "add_cooldown"
    cooldown_until: Optional[str] = None


# Trade log row -- realized PnL today for one symbol. Aggregated from IB
# CommissionReport.realizedPNL across today's fills. No DB persistence; the
# cost basis comes from IB so positions opened on any prior day count.
class TradeLogRow(BaseModel):
    symbol: str
    realized_pnl: float = 0.0
    commission: float = 0.0
    net_pnl: float = 0.0
    fills: int = 0
    last_fill_time: Optional[str] = None
    is_loss: bool = False


class TradeLogResponse(BaseModel):
    rows: List[TradeLogRow]
    realized_pnl: float = 0.0
    total_commission: float = 0.0
    net_pnl: float = 0.0
    symbol_count: int = 0


# Entry attempts stats row (per-symbol per-day count for the UI table)
class EntryAttemptsRow(BaseModel):
    symbol: str
    attempts: int
    max_attempts: int
    remaining: int


# Wrapper around per-symbol rows that also carries the daily-total cap.
# Lets the UI render a "Total" footer next to the per-ticker rows.
class EntryAttemptsResponse(BaseModel):
    rows: List[EntryAttemptsRow]
    total_attempts: int
    max_total: int
    total_remaining: int


# Scanner response

# ---------------- Live Scanner (streaming) ----------------
# A single qualifying ticker row in the live scanner. Phase-1 ("light")
# columns only -- heavy enrichment (Bid/Ask, IV, MarketCap, RVOL, RelATR)
# is deferred to a later phase.
class LiveScannerRow(BaseModel):
    symbol: str
    rank: int                          # rank inside the IB scan result
    price: Optional[float] = None      # last trade price
    change: Optional[float] = None     # absolute $ change vs previous close
    change_percent: Optional[float] = None  # percent gap (signed)
    volume: Optional[int] = None       # cumulative session volume
    time_added: str                    # ISO-8601 timestamp when first seen


# Wire message pushed over SSE. side tells the frontend which table
# this update belongs to. rows is the *full current snapshot* for that
# side so the client can simply replace its state on receive.
class LiveScannerUpdate(BaseModel):
    side: str                          # "up" or "down"
    rows: List[LiveScannerRow]
    connected: bool                    # IB connection status at time of push
    ts: float                          # epoch seconds


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


# ---------------- Daily premarket summary ----------------
# Produced by services.daily_summary, persisted in daily_summary_run /
# daily_summary_row, served by routers.daily_summary.

class DailySummaryRow(BaseModel):
    """One ticker's row in the daily premarket snapshot.

    The catalyst evaluation follows the Catalyst Value Equation rubric in
    docs/CATALYST_EVALUATION.md: Magnitude × Speed → Grade → daily-risk cap.
    The four CVE text fields use a constrained vocabulary, but we don't
    enforce it via Enum here — the LLM occasionally returns a near-miss
    ("ABSOLUTE" / "absolute"), and we'd rather store it than fail the row.
    The frontend normalises on display.

    Defaults match the rubric's "every trade starts at D" bias: if the LLM
    couldn't produce a clean reading, the row collapses to D / 0% sizing so
    a partial response is never silently treated as a tradeable signal.
    """
    # run_date is included on every row so symbol-history queries can return
    # a flat list without losing the date dimension.
    run_date: date
    side: str               # "up" or "down"
    rank: int               # 1..5 within the side
    symbol: str
    change: Optional[float] = None    # % change at scan time, signed
    rvol: Optional[float] = None      # relative volume at scan time

    # --- CVE evaluation ---
    catalyst_type: str = "none"   # confirmed | coverage | narrative | none
    magnitude: str = "No"         # Absolute | Yes | Maybe | No
    speed: str = "No"             # Absolute | Yes | Maybe | No
    grade: str = "D"              # A+ | A | B | C | D
    sizing_pct: int = 0           # 0..80, the rubric's daily-risk cap for this grade
    reason: str = ""              # short sentence naming the catalyst itself
    notes: str = ""               # caveats: float, peer flow, already in price, etc.

    headline: str = ""      # the headline the eval was derived from
    news_url: str = ""      # link to that headline


class DailySummaryResponse(BaseModel):
    run_date: date
    created_at: datetime
    rows: List[DailySummaryRow] = []


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
    time: float
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None
    ema9: Optional[float] = None
    vwap: Optional[float] = None


class AutoAssistTick(BaseModel):
    time: float
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
    price: float
    last2_high: float
    stop_level: float
    position_size: int
    contract_type: str = "stock"
    bar_time: float
    ts: float


class AutoAssistState(BaseModel):
    symbol: str
    bars: list[AutoAssistBar]
    last2_high: Optional[float] = None
    stop_level: Optional[float] = None
