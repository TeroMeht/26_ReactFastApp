from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel

from services.tickers import InputTickerService
from core.config import Settings, settings


router = APIRouter(
    prefix="/api/tickers",
    tags=["tickers"],
)


# Dependency injection
def get_settings() -> Settings:
    return settings


class SaveTickerRequest(BaseModel):
    content: str


@router.get("/")
def read_input_tickers(
    file: str | None = Query(None),
    settings: Settings = Depends(get_settings),
):
    service = InputTickerService(settings.INPUT_TICKERS_PATH)

    try:
        return service.get_tickers(file)

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/")
def write_input_tickers(
    request: SaveTickerRequest,
    file: str = Query(...),
    settings: Settings = Depends(get_settings),
):
    service = InputTickerService(settings.INPUT_TICKERS_PATH)

    try:
        return service.save_tickers(file, request.content)

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))