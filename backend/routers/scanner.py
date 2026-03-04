from fastapi import APIRouter, Depends, HTTPException
from dependencies import get_ib
from typing import Optional,List,Dict
from schemas.api_schemas import ScannerResponse
from services.scanner import run_scanner_logic

import logging
logger = logging.getLogger(__name__)



router = APIRouter(
    prefix="/api/scanner",
    tags=["IB Scanner"]
)


@router.get("/", response_model=List[ScannerResponse])
async def run_scanner(preset_name: str,ib=Depends(get_ib)):
    try:
        return await run_scanner_logic(
            preset_name=preset_name,
            ib=ib
        )
    except ValueError as e:
        # For invalid preset etc.
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Scanner execution failed")
        raise HTTPException(status_code=500, detail="Scanner execution failed")
