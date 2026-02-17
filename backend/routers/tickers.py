from fastapi import APIRouter, HTTPException
from services.tickers import get_tickers,save_tickers
from schemas.api_schemas import TickerFile

router = APIRouter(
    prefix="/api/tickers",
    tags=["tickers"],
)

@router.get("/")
async def read_input_tickers():
    """
    Return all tickers or files in the tickers path.
    """
    try:
        return await get_tickers()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))




@router.post("/")
async def write_input_tickers(payload: TickerFile):
    """
    Save content to a ticker file.
    Body must include 'file' and 'content'.
    """
    try:
        return await save_tickers(payload.filename, payload.content)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))