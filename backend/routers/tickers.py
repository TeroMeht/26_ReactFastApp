from fastapi import APIRouter, HTTPException, Query
from schemas.api_schemas import SaveTickerRequest
from services.tickers import InputTickerService

router = APIRouter(
    prefix="/api/tickers",
    tags=["tickers"],
)

@router.get("/")
def read_input_tickers(file: str | None = Query(None)):
    service = InputTickerService()  # singleton service
    try:
        return service.get_tickers(file)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/")
def write_input_tickers(request: SaveTickerRequest, file: str = Query(...)):
    service = InputTickerService()  # singleton service
    try:
        return service.save_tickers(file, request.content)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))