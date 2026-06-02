"""
Live streaming market scanner service.

Maintains two long-lived IB scanner subscriptions (+5% gap up and -5% gap
down). Whenever IB pushes a ranking update, this service:

  1. Diffs the new symbol list against the cached one.
  2. For *new* symbols, requests streaming market data (price/volume/change).
   3. Drops mkt-data for symbols that left the scan.
  4. Builds a fresh snapshot and broadcasts it over per-connection SSE
     queues to every subscribed frontend client.

Phase-1 (MVP) columns only: symbol, rank, price, change, change_percent,
volume, time_added. Heavier fields (Bid/Ask, IV, MarketCap, RVOL, RelATR)
will be wired up in a later phase with their own enrichment pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from typing import Dict, List, Optional

from ib_async import IB, ScannerSubscription, Stock, Ticker

from helpers.scanner_presets import SCANNER_PRESETS
from schemas.api_schemas import LiveScannerRow, LiveScannerUpdate


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Subscriber registry — one asyncio.Queue per connected SSE client.
# Push is fan-out: every queue receives every update.
# ---------------------------------------------------------------------------
class _SubscriberHub:
    def __init__(self) -> None:
        self._subscribers: List[asyncio.Queue[LiveScannerUpdate]] = []
        self._lock = asyncio.Lock()

    async def add(self) -> asyncio.Queue[LiveScannerUpdate]:
        q: asyncio.Queue[LiveScannerUpdate] = asyncio.Queue(maxsize=64)
        async with self._lock:
            self._subscribers.append(q)
        logger.info("LiveScanner SSE client connected (n=%d)", len(self._subscribers))
        return q

    async def remove(self, q: asyncio.Queue) -> None:
        async with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)
        logger.info("LiveScanner SSE client disconnected (n=%d)", len(self._subscribers))

    async def broadcast(self, update: LiveScannerUpdate) -> None:
        # Snapshot under lock to avoid mutation during iteration.
        async with self._lock:
            targets = list(self._subscribers)
        for q in targets:
            try:
                q.put_nowait(update)
            except asyncio.QueueFull:
                # Slow consumer — drop the oldest and try again so we
                # keep current data flowing instead of stalling.
                try:
                    q.get_nowait()
                    q.put_nowait(update)
                except Exception:
                    logger.warning("Dropping update for slow SSE consumer")

    def count(self) -> int:
        return len(self._subscribers)


# ---------------------------------------------------------------------------
# Per-side state: subscription handle + symbol -> Ticker map + last snapshot.
# ---------------------------------------------------------------------------
class _SideState:
    def __init__(self, side: str, preset_name: str) -> None:
        self.side = side                                # "up" or "down"
        self.preset_name = preset_name
        self.subscription = None                        # the ScannerSubscription handle from IB
        self.tickers: Dict[str, Ticker] = {}            # symbol -> streaming Ticker
        self.first_seen: Dict[str, str] = {}            # symbol -> ISO timestamp
        self.ranks: Dict[str, int] = {}                 # symbol -> rank


# ---------------------------------------------------------------------------
# Manager — singleton wired up in main.py lifespan.
# ---------------------------------------------------------------------------
class LiveScannerManager:
    def __init__(self, ib: IB) -> None:
        self.ib = ib
        self.hub = _SubscriberHub()
        self.up = _SideState("up", "live_gap_up_scan")
        self.down = _SideState("down", "live_gap_down_scan")
        self._started = False
        self._stopping = False

    # ----- lifecycle ------------------------------------------------------
    async def start(self) -> None:
        if self._started:
            return
        logger.info("Starting LiveScannerManager")
        await self._start_side(self.up)
        await self._start_side(self.down)
        self._started = True

    async def stop(self) -> None:
        self._stopping = True
        logger.info("Stopping LiveScannerManager")
        for side in (self.up, self.down):
            try:
                if side.subscription is not None:
                    self.ib.cancelScannerSubscription(side.subscription)
            except Exception:
                logger.exception("Failed to cancel scanner subscription for %s", side.side)
            for sym, ticker in list(side.tickers.items()):
                try:
                    self.ib.cancelMktData(ticker.contract)
                except Exception:
                    logger.exception("Failed to cancel mkt data for %s", sym)
            side.tickers.clear()
        self._started = False

    # ----- subscription wiring -------------------------------------------
    async def _start_side(self, side: _SideState) -> None:
        preset = SCANNER_PRESETS.get(side.preset_name)
        if not preset:
            logger.error("Missing preset %s — live scanner side '%s' disabled",
                         side.preset_name, side.side)
            return
        sub = ScannerSubscription(**preset)

        # reqScannerSubscription returns a list-like handle whose
        # `updateEvent` fires every time IB pushes new ranking data.
        handle = self.ib.reqScannerSubscription(sub)
        side.subscription = handle

        def _on_update(items=handle):
            # Schedule async handler — updateEvent fires synchronously.
            asyncio.create_task(self._handle_scan_update(side, list(items)))

        handle.updateEvent += _on_update
        logger.info("Subscribed to %s (%s)", side.side, side.preset_name)

    async def _handle_scan_update(self, side: _SideState, items: list) -> None:
        try:
            # Extract qualifying symbols + rank order.
            new_ranks: Dict[str, int] = {}
            for it in items:
                try:
                    sym = it.contractDetails.contract.symbol
                    new_ranks[sym] = int(it.rank)
                except Exception:
                    continue

            # Add new symbols (start mkt data subscription each).
            for sym, rank in new_ranks.items():
                if sym not in side.tickers:
                    await self._subscribe_mktdata(side, sym)
                side.ranks[sym] = rank
                side.first_seen.setdefault(
                    sym,
                    _iso_now(),
                )

            # Drop symbols that left the scan.
            for sym in list(side.tickers.keys()):
                if sym not in new_ranks:
                    await self._unsubscribe_mktdata(side, sym)
                    side.ranks.pop(sym, None)
                    side.first_seen.pop(sym, None)

            await self._broadcast_side(side)
        except Exception:
            logger.exception("Error handling scan update for %s", side.side)

    async def _subscribe_mktdata(self, side: _SideState, symbol: str) -> None:
        try:
            contract = Stock(symbol, "SMART", "USD")
            await self.ib.qualifyContractsAsync(contract)
            # genericTickList "" gives default fields incl. last, volume.
            # Streaming (snapshot=False) so we get continuous updates.
            ticker = self.ib.reqMktData(contract, "", False, False)
            side.tickers[symbol] = ticker

            def _on_tick(t=ticker, s=side):
                # On any tick update, broadcast a fresh snapshot for this side.
                # Cheap because we just read cached ticker fields and push.
                asyncio.create_task(self._broadcast_side(s))

            ticker.updateEvent += _on_tick
            logger.debug("Subscribed mkt data: %s (%s)", symbol, side.side)
        except Exception:
            logger.exception("Failed to subscribe mkt data for %s", symbol)

    async def _unsubscribe_mktdata(self, side: _SideState, symbol: str) -> None:
        ticker = side.tickers.pop(symbol, None)
        if ticker is None:
            return
        try:
            self.ib.cancelMktData(ticker.contract)
        except Exception:
            logger.exception("Failed to cancel mkt data for %s", symbol)

    # ----- snapshot build + push -----------------------------------------
    async def _broadcast_side(self, side: _SideState) -> None:
        if self._stopping:
            return
        rows = self._build_rows(side)
        update = LiveScannerUpdate(
            side=side.side,
            rows=rows,
            connected=self.ib.isConnected(),
            ts=_time.time(),
        )
        await self.hub.broadcast(update)

    def _build_rows(self, side: _SideState) -> List[LiveScannerRow]:
        rows: List[LiveScannerRow] = []
        for symbol, ticker in side.tickers.items():
            price = _safe_num(getattr(ticker, "last", None)) \
                or _safe_num(getattr(ticker, "marketPrice", lambda: None)() if callable(getattr(ticker, "marketPrice", None)) else None) \
                or _safe_num(getattr(ticker, "close", None))
            close = _safe_num(getattr(ticker, "close", None))
            change_abs: Optional[float] = None
            change_pct: Optional[float] = None
            if price is not None and close not in (None, 0):
                change_abs = round(price - close, 4)
                change_pct = round((price - close) / close * 100, 2)
            volume = _safe_int(getattr(ticker, "volume", None))

            rows.append(LiveScannerRow(
                symbol=symbol,
                rank=side.ranks.get(symbol, 0),
                price=price,
                change=change_abs,
                change_percent=change_pct,
                volume=volume,
                time_added=side.first_seen.get(symbol, _iso_now()),
            ))
        # Stable ordering by absolute %change so biggest movers are on top.
        rows.sort(
            key=lambda r: abs(r.change_percent) if r.change_percent is not None else 0,
            reverse=True,
        )
        return rows

    # ----- public snapshot for HTTP status --------------------------------
    def status(self) -> dict:
        return {
            "connected": self.ib.isConnected(),
            "started": self._started,
            "subscribers": self.hub.count(),
            "up_symbols": len(self.up.tickers),
            "down_symbols": len(self.down.tickers),
        }

    def current_snapshot(self) -> List[LiveScannerUpdate]:
        """Build a current snapshot for both sides — used to bootstrap a
        freshly connected SSE client without waiting for the next IB update.
        """
        return [
            LiveScannerUpdate(
                side="up",
                rows=self._build_rows(self.up),
                connected=self.ib.isConnected(),
                ts=_time.time(),
            ),
            LiveScannerUpdate(
                side="down",
                rows=self._build_rows(self.down),
                connected=self.ib.isConnected(),
                ts=_time.time(),
            ),
        ]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _safe_num(v) -> Optional[float]:
    """Return float, or None for NaN / None / non-numeric. IB sets unfilled
    ticker fields to float('nan') which is hostile to JSON encoders."""
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:   # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _safe_int(v) -> Optional[int]:
    f = _safe_num(v)
    if f is None:
        return None
    try:
        return int(f)
    except (TypeError, ValueError):
        return None


def _iso_now() -> str:
    # UTC ISO-8601 with 'Z'.
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
