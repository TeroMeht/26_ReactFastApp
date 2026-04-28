"""
Simple fills / order-status snapshot.

The frontend polls ``GET /api/portfolio/fills`` every 30 seconds and renders
whatever today's IB order-status snapshot looks like at that moment. There is
no streaming, no event subscription, no diff broadcasting — every call asks
IB for fresh state and returns a list of plain dicts.

Why three IB calls before reading ``ib.trades()``:

* ``reqAllOpenOrdersAsync`` refreshes currently open orders.
* ``reqCompletedOrdersAsync(apiOnly=False)`` flips today's already-cancelled
  / already-filled orders out of their stale ``PreSubmitted`` cached state.
  The open-orders call alone never returns them.
* ``reqExecutionsAsync`` makes sure all of today's fills (incl. those done
  from TWS) are attached to their parent ``Trade`` objects.

Costs ~1 round-trip's worth of latency per call; that's fine at 30s cadence.
"""
from __future__ import annotations

import asyncio
import logging
import math
import sys
from datetime import datetime
from typing import List, Optional

import pytz
from ib_async import IB

logger = logging.getLogger(__name__)


HELSINKI_TZ = pytz.timezone("Europe/Helsinki")

# IB API uses these sentinels to mean "field not used by this order".
_UNSET_DOUBLE = sys.float_info.max  # 1.7976931348623157e+308
_UNSET_INTEGER = 2 ** 31 - 1        # 2147483647


def _clean_price(value):
    """Return a price suitable for the UI, or None if IB hasn't set one yet."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    if abs(v) >= _UNSET_DOUBLE:
        return None
    return value


def _clean_int(value):
    """Same idea as _clean_price but for integer-typed IB fields."""
    if value is None:
        return None
    try:
        v = int(value)
    except (TypeError, ValueError):
        return value
    if v >= _UNSET_INTEGER:
        return None
    return value


def _today_helsinki():
    return datetime.now(HELSINKI_TZ).date()


def _row_from_trade(trade) -> dict:
    """Normalize an ib_async Trade into the row shape the UI consumes."""
    order = getattr(trade, "order", None)
    status = getattr(trade, "orderStatus", None)
    contract = getattr(trade, "contract", None)
    fills = getattr(trade, "fills", None) or []

    # Latest fill time (Helsinki)
    last_time: Optional[str] = None
    if fills:
        try:
            latest_fill = max(fills, key=lambda f: f.execution.time)
            t = latest_fill.execution.time
            if t.tzinfo is None:
                t = t.replace(tzinfo=pytz.UTC)
            last_time = t.astimezone(HELSINKI_TZ).isoformat()
        except Exception:
            last_time = None

    # Weighted average fill price + total commission across all known fills
    avg_fill_price: Optional[float] = None
    total_filled_qty = 0.0
    total_commission = 0.0
    try:
        for f in fills:
            qty = float(f.execution.shares or 0)
            price = float(f.execution.price or 0)
            total_filled_qty += qty
            avg_fill_price = (avg_fill_price or 0.0) + qty * price
            if f.commissionReport and f.commissionReport.commission is not None:
                total_commission += float(f.commissionReport.commission)
        if total_filled_qty > 0 and avg_fill_price is not None:
            avg_fill_price = round(avg_fill_price / total_filled_qty, 4)
        else:
            avg_fill_price = None
    except Exception:
        avg_fill_price = None

    # Time the order was first known to us (for snapshot date filtering).
    #
    # ib_async records ``trade.log`` entries with naive datetimes that are
    # already in the local system timezone (Helsinki for this deployment),
    # NOT UTC like ``execution.time``. Treating them as UTC and then
    # converting to Helsinki was adding +3h (summer DST) and rendering
    # PreSubmitted STP rows three hours into the future.
    created_time: Optional[str] = None
    log = getattr(trade, "log", None) or []
    if log:
        try:
            t = log[0].time
            if t.tzinfo is None:
                t = HELSINKI_TZ.localize(t)
            created_time = t.astimezone(HELSINKI_TZ).isoformat()
        except Exception:
            created_time = None

    return {
        "orderId": getattr(order, "orderId", None),
        "permId": getattr(order, "permId", None) or getattr(status, "permId", None),
        "parentId": getattr(order, "parentId", None) or None,
        "symbol": getattr(contract, "symbol", None) if contract else None,
        "secType": getattr(contract, "secType", None) if contract else None,
        "action": getattr(order, "action", None),
        "orderType": getattr(order, "orderType", None),
        "totalQty": _clean_int(getattr(order, "totalQuantity", None)),
        "lmtPrice": _clean_price(getattr(order, "lmtPrice", None)),
        "auxPrice": _clean_price(getattr(order, "auxPrice", None)),
        "status": getattr(status, "status", None),
        "filled": getattr(status, "filled", 0),
        "remaining": getattr(status, "remaining", 0),
        "avgFillPrice": avg_fill_price,
        "commission": round(total_commission, 4) if total_commission else None,
        "lastFillTime": last_time,
        "createdTime": created_time,
    }


def _trade_belongs_to_today(trade) -> bool:
    """Keep any trade with a log entry or fill from today's Helsinki session."""
    today = _today_helsinki()

    log = getattr(trade, "log", None) or []
    for entry in log:
        try:
            t = entry.time
            if t.tzinfo is None:
                # See _row_from_trade — naive log timestamps are local
                # (Helsinki) wall-clock, not UTC.
                t = HELSINKI_TZ.localize(t)
            if t.astimezone(HELSINKI_TZ).date() == today:
                return True
        except Exception:
            continue

    for f in getattr(trade, "fills", None) or []:
        try:
            t = f.execution.time
            if t.tzinfo is None:
                t = t.replace(tzinfo=pytz.UTC)
            if t.astimezone(HELSINKI_TZ).date() == today:
                return True
        except Exception:
            continue

    # No timestamps at all — err on the side of showing it.
    return not log and not (getattr(trade, "fills", None) or [])


async def fetch_fills_today(ib: IB) -> List[dict]:
    """
    Refresh IB-side state, then return today's order/trade rows sorted with
    the most recent activity first. Called once per ``GET /fills`` hit; the
    UI is responsible for re-polling on whatever cadence it wants (30s).
    """
    try:
        await ib.reqAllOpenOrdersAsync()
    except Exception:
        logger.exception("reqAllOpenOrdersAsync failed")

    try:
        # apiOnly=False also includes orders cancelled / filled from TWS.
        await ib.reqCompletedOrdersAsync(apiOnly=False)
    except Exception:
        logger.exception("reqCompletedOrdersAsync failed")

    try:
        await ib.reqExecutionsAsync()
    except Exception:
        logger.exception("reqExecutionsAsync failed")

    # brief yield so ib_async finishes applying inbound updates before we
    # read the trade list.
    await asyncio.sleep(0.1)

    rows: List[dict] = []
    try:
        trades = ib.trades()
    except Exception:
        logger.exception("Failed to read ib.trades()")
        return rows

    for trade in trades:
        try:
            if not _trade_belongs_to_today(trade):
                continue
            rows.append(_row_from_trade(trade))
        except Exception:
            logger.exception("Error building fills row")
            continue

    def _sort_key(r: dict):
        return (
            r.get("lastFillTime") or "",
            r.get("createdTime") or "",
            r.get("permId") or 0,
        )

    rows.sort(key=_sort_key, reverse=True)
    return rows
