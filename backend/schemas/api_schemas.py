
from pydantic import BaseModel, field_validator,Field
from datetime import date, time
from datetime import datetime
from typing import Optional,Any
from decimal import Decimal


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
    exit_request:bool
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








     
class UpdateExitRequest(BaseModel):
    symbol: str = Field(
        ...,
        min_length=1,
        description="Trading symbol (auto uppercased)"
    )
    requested: bool

    @field_validator("symbol")
    @classmethod
    def validate_and_uppercase_symbol(cls, v: str) -> str:
        v = v.strip().upper()

        if not v:
            raise ValueError("Symbol cannot be empty")

        return v
    






# Exits

# Watchlist streamer lähettää tällaisen sanoman POST endpointtiin, jossa tarkastetaan ensin että onko sille symbolille tilattu exit
class ExitRequest(BaseModel):
    date: date
    time: time
    alarm: str
    symbol: str
     




class ExitRequestResponse(BaseModel):
    symbol: str
    exitrequested:bool
    updated: datetime

class ExitRequestResponseIB(BaseModel):
    symbol: str
    message: str
    order_id :Optional[int] = None

# Portfolio


class EntryRequest(BaseModel):
    symbol: str
    entry_price: float
    stop_price: float
    position_size: int
    contract_type:str


class EntryRequestResponse(BaseModel):
    allowed: bool
    message: str
    symbol: str
    parentOrderId: Optional[int] = None
    stopOrderId: Optional[int] = None


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
