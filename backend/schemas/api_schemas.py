
from pydantic import BaseModel, field_validator,Field
from datetime import date, time
from datetime import datetime
from typing import Optional,Any


class TickerFile(BaseModel):
    filename: str
    content: str




class AutoOrderResponse(BaseModel):
    Id: int
    Symbol: str
    Time: time
    Stop: float
    Date: date
    Status: str




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

class LatestRow(BaseModel):
    TableName: str
    Symbol: str
    Date: date
    Time: time
    Open: float
    High: float
    Low: float
    Close: float
    Volume: float
    VWAP: float
    EMA9: float
    Avg_volume: float
    Rvol: float
    Relatr: float


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
    


class PortfolioPositionModel(BaseModel):
    Symbol: str
    Allocation: float | None
    Size: float
    AvgCost: float
    AuxPrice: float
    Position: float
    OpenRisk: float



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


# Portfolio


class EntryRequest(BaseModel):
    symbol: str
    entry_price: float
    stop_price: float
    position_size: int


class EntryRequestResponse(BaseModel):
    allowed: bool
    message: str
    symbol: str
    parentOrderId: Optional[int] = None
    stopOrderId: Optional[int] = None


class AddRequest(BaseModel):
    symbol: str
    total_risk: int 


class AddRequestResponse(BaseModel):
    allowed: bool
    message: str
    symbol: str
    new_order: Optional[Any] = None
    place_result: Optional[Any] = None
    modified_stp_qty: Optional[int] = None
