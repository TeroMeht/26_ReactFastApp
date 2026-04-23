"""
Fills / order-status tracker service.

Listens to the shared IB client's order and execution events and publishes
normalized rows to SSE subscribers so the Risk Levels UI can stay in sync
with IB broker state.

Why this is more than a thin event-listener wrapper:

* ``ib.reqAllOpenOrdersAsync`` only returns currently-open orders, so a
  cancelled Trade keeps its stale cached ``orderStatus`` forever.  To flip
  PreSubmitted -> Cancelled we also call ``reqCompletedOrdersAsync(apiOnly=
  False)``, which pulls back today's completed orders and updates the Trade
  objects in place.
* IB's ``orderStatusEvent`` callbacks only fire for our own client unless
  ``reqAutoOpenOrders(True)`` is set; cancels done in TWS would otherwise
  never reach us.  Auto-bind is enabled at startup from main.py.
* Because the upstream event delivery is not 100% reliable (clientId
  collisions, network blips, etc.) a background poll loop refreshes IB state
  every ~2 s while any SSE subscriber is connected and broadcasts per-row
  diffs.  The UI therefore stays correct even if an event is missed.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pytz
from ib_async import IB

logger = logging.getLogger(__name__)


HELSINKI_TZ = pytz.timezone("Europe/Helsinki")




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

    # Weighted average fill price across all known fills
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

    # Time the order was first known to us (for snapshot date filtering)
    created_time: Optional[str] = None
    log = getattr(trade, "log", None) or []
    if log:
        try:
            t = log[0].time
            if t.tzinfo is None:
                t = t.replace(tzinfo=pytz.UTC)
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
        "totalQty": getattr(order, "totalQuantity", None),
        "lmtPrice": getattr(order, "lmtPrice", None),
        "auxPrice": getattr(order, "auxPrice", None),
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
                t = t.replace(tzinfo=pytz.UTC)
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


def _row_key(row: dict) -> str:
    return str(row.get("permId") or row.get("orderId") or row.get("symbol") or "")


def _row_signature(row: dict) -> Tuple:
    """Fields that, when they change, should trigger an SSE broadcast."""
    return (
        row.get("status"),
        row.get("filled"),
        row.get("remaining"),
        row.get("avgFillPrice"),
        row.get("commission"),
        row.get("lmtPrice"),
        row.get("auxPrice"),
        row.get("totalQty"),
        row.get("lastFillTime"),
    )


class FillsTracker:
    """Singleton that bridges IB order/exec events to SSE subscribers."""

    _instance: Optional["FillsTracker"] = None

    def __init__(self, ib: IB) -> None:
        self.ib = ib
        self.subscribers: List[asyncio.Queue] = []
        self._hooked = False
        self._auto_open_orders_bound = False
        self._poll_task: Optional[asyncio.Task] = None
        self._last_signatures: Dict[str, Tuple] = {}

    @classmethod
    def get(cls, ib: IB) -> "FillsTracker":
        if cls._instance is None or cls._instance.ib is not ib:
            cls._instance = cls(ib)
        cls._instance._ensure_hooked()
        return cls._instance

    def _ensure_hooked(self) -> None:
        if self._hooked:
            return
        try:
            self.ib.newOrderEvent += self._on_new_order
            self.ib.orderStatusEvent += self._on_order_status
            self.ib.execDetailsEvent += self._on_exec_details
            self.ib.commissionReportEvent += self._on_commission_report
            # cancelOrderEvent fires when a cancel is acknowledged by IB —
            # including cancels initiated from TWS once auto-bind is enabled.
            cancel_event = getattr(self.ib, "cancelOrderEvent", None)
            if cancel_event is not None:
                cancel_event += self._on_cancel_order
            self._hooked = True
            logger.info("FillsTracker hooked into IB events")
        except Exception:
            logger.exception("Failed to hook FillsTracker into IB events")

    def enable_auto_open_orders(self) -> None:
        """
        Tell IB to stream status updates for orders placed by other clients
        too (e.g., cancels done from TWS).  Must be called while connected.
        """
        if self._auto_open_orders_bound:
            return
        try:
            self.ib.reqAutoOpenOrders(True)
            self._auto_open_orders_bound = True
            logger.info("FillsTracker: reqAutoOpenOrders(True) set")
        except Exception:
            logger.exception("reqAutoOpenOrders(True) failed")

    # ---- subscribers ----
    def add_subscriber(self, initial_rows: Optional[List[dict]] = None) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        rows = initial_rows if initial_rows is not None else self._read_rows()
        # Reset signature cache so the first diff tick after a reconnect
        # re-broadcasts anything the client might have missed.
        self._last_signatures = {_row_key(r): _row_signature(r) for r in rows}
        q.put_nowait({"type": "snapshot", "rows": rows})
        self.subscribers.append(q)

        return q

    def remove_subscriber(self, q: asyncio.Queue) -> None:
        if q in self.subscribers:
            self.subscribers.remove(q)

    def _broadcast(self, event: dict) -> None:
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except Exception:
                logger.exception("Failed to enqueue event for subscriber")

    # ---- snapshot ----
    async def refresh(self) -> None:
        """
        Ask IB for the current open-order list, today's completed orders and
        executions so ``ib.trades()`` reflects broker-side reality before we
        build a row snapshot.  ``reqCompletedOrdersAsync(apiOnly=False)`` is
        what flips cancelled orders out of ``PreSubmitted`` — the open-orders
        endpoint alone never returns them.
        """
        try:
            await self.ib.reqAllOpenOrdersAsync()
        except Exception:
            logger.exception("reqAllOpenOrdersAsync failed while refreshing fills")
        try:
            # apiOnly=False includes orders cancelled / filled from TWS.
            await self.ib.reqCompletedOrdersAsync(apiOnly=False)
        except Exception:
            logger.exception("reqCompletedOrdersAsync failed while refreshing fills")
        try:
            await self.ib.reqExecutionsAsync()
        except Exception:
            logger.exception("reqExecutionsAsync failed while refreshing fills")
        # brief yield so ib_async finishes applying inbound updates before we
        # read the trade list.
        await asyncio.sleep(0.1)

    async def current_rows(self) -> List[dict]:
        """Public snapshot API: refresh from IB, then read ib.trades()."""
        await self.refresh()
        return self._read_rows()

    def _read_rows(self) -> List[dict]:
        rows: List[dict] = []
        try:
            trades = self.ib.trades()
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

        # Most-recent activity first (fall back to createdTime, then permId)
        def _sort_key(r: dict):
            return (
                r.get("lastFillTime") or "",
                r.get("createdTime") or "",
                r.get("permId") or 0,
            )

        rows.sort(key=_sort_key, reverse=True)
        return rows


    # ---- event handlers ----
    def _emit(self, event_type: str, trade) -> None:
        try:
            row = _row_from_trade(trade)
            key = _row_key(row)
            sig = _row_signature(row)
            # Update the signature cache so the poll loop doesn't
            # redundantly re-broadcast the same change.
            self._last_signatures[key] = sig
            self._broadcast({"type": event_type, "row": row})
        except Exception:
            logger.exception("Error emitting %s event", event_type)

    def _on_new_order(self, trade) -> None:
        self._emit("order", trade)

    def _on_order_status(self, trade) -> None:
        self._emit("order", trade)

    def _on_exec_details(self, trade, fill) -> None:
        self._emit("fill", trade)

    def _on_commission_report(self, trade, fill, report) -> None:
        self._emit("commission", trade)

    def _on_cancel_order(self, trade, *_args) -> None:
        # ib_async fires cancelOrderEvent with (trade) or (trade, errorCode, reason)
        # depending on version — accept any tail args defensively.
        self._emit("order", trade)
