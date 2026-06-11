"""
Daily premarket summary endpoints.

GET  /api/scanner/daily-summary       latest stored snapshot (any date), 404 if empty
POST /api/scanner/daily-summary       run gap up/down scans now, summarize, persist

The schedule (run once a day at 15:00 Helsinki) is intentionally NOT wired up
here — the user is implementing that separately. POST is the manual trigger.
"""
from __future__ import annotations

import logging
from datetime import date as _date
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from dependencies import get_db_conn, get_ib
from db.daily_summary import (
    get_daily_summary_by_date,
    get_latest_daily_summary,
    get_symbol_history,
)
from schemas.api_schemas import DailySummaryResponse, DailySummaryRow
from services.daily_summary import generate_daily_summary

logger = logging.getLogger(__name__)

# Mounted under /api/scanner to keep all scanner-page endpoints colocated. The
# existing scanner.router uses prefix="/api/scanner" and registers "" + "/news/{symbol}";
# adding a sibling router with the same prefix avoids stepping on those routes.
router = APIRouter(
    prefix="/api/scanner",
    tags=["Daily Summary"],
)


@router.get("/daily-summary", response_model=DailySummaryResponse)
async def read_daily_summary(db_conn=Depends(get_db_conn)):
    """Return the most recent snapshot. 404 if no summary has ever been run."""
    try:
        snap = await get_latest_daily_summary(db_conn)
    except Exception:
        logger.exception("Failed to read latest daily summary")
        raise HTTPException(status_code=500, detail="Failed to read daily summary")
    if snap is None:
        raise HTTPException(
            status_code=404,
            detail="No daily summary has been generated yet. POST to this endpoint to create one.",
        )
    return snap


@router.post("/daily-summary", response_model=DailySummaryResponse)
async def run_daily_summary(
    db_conn=Depends(get_db_conn),
    ib=Depends(get_ib),
):
    """
    Run both gap scans, distill per-ticker news via Claude into a few-word
    reason + 1-10 catalyst rating, and persist. Rerunning on the same date
    overwrites that day's rows.
    """
    try:
        return await generate_daily_summary(db_conn, ib)
    except Exception as e:
        logger.exception("Daily summary generation failed")
        raise HTTPException(status_code=500, detail=f"Daily summary failed: {e}")


# ---------------------------------------------------------------------------
# History endpoints — let the user replay past catalysts. Each row carries its
# own run_date so the frontend can render symbol-history tables without joins.
# ---------------------------------------------------------------------------

@router.get("/daily-summary/by-date/{run_date}", response_model=DailySummaryResponse)
async def read_daily_summary_by_date(run_date: _date, db_conn=Depends(get_db_conn)):
    try:
        snap = await get_daily_summary_by_date(db_conn, run_date)
    except Exception:
        logger.exception("Failed to read daily summary for %s", run_date)
        raise HTTPException(status_code=500, detail="Failed to read daily summary")
    if snap is None:
        raise HTTPException(
            status_code=404,
            detail=f"No daily summary stored for {run_date.isoformat()}.",
        )
    return snap


@router.get("/daily-summary/symbol/{symbol}", response_model=List[DailySummaryRow])
async def read_symbol_history(
    symbol: str,
    limit: int = 60,
    db_conn=Depends(get_db_conn),
):
    """Every catalyst entry ever recorded for `symbol`, newest first."""
    try:
        return await get_symbol_history(db_conn, symbol, limit=limit)
    except Exception:
        logger.exception("Failed to fetch symbol history for %s", symbol)
        raise HTTPException(status_code=500, detail="Failed to fetch symbol history")
