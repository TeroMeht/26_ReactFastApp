from pydantic import BaseModel
from datetime import date, time


class AutoOrderResponse(BaseModel):
    Id: int
    Symbol: str
    Time: time
    Stop: float
    Date: date
    Status: str





class SaveTickerRequest(BaseModel):
    content: str


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



class EntryRequest(BaseModel):
    symbol: str
    entry_price: float
    stop_price: float
    position_size: int


class AddRequest(BaseModel):
    symbol: str
    total_risk: int 