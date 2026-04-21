"""
Auto Assist service.

Per-symbol live session:
- seeds the bar history with the last 12 hours of 2-minute IB bars
- computes EMA9 on every bar (SMA9 seed, alpha = 2/(N+1))
- subscribes to IBKR market data (reqMktData) for tick updates
- aggregates ticks into rolling 2-minute OHLC bars continuing from the seed
- computes last-2-bar high (entry trigger) and last-5-bar low - 0.06 (stop)
- detects when an incoming tick breaks the last-2-bar high upwards and
  emits a breakout "signal" with a pre-computed position_size that the
  frontend can submit via the standard /api/portfolio/entry-request flow
- fans out tick / bar / levels / signal / stopped events to SSE subscribers

Follows the existing services/ structure and uses the shared IB instance
passed in from the FastAPI dependency.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ib_async import IB, Stock, Ticker

from core.config import settings

logger = logging.getLogger(__name__)


# ---------------- Parameters ----------------
BAR_SECONDS: int = 120          # 2-minute bars
HIGH_WINDOW: int = 2            # last 2 completed bars -> breakout level
LOW_WINDOW: int = 5             # last 5 completed bars -> stop level
STOP_BUFFER: float = 0.06       # subtracted from last-5-bar low to form stop
SEED_DURATION: str = "43200 S"  # 12h of history for seeding
BAR_SIZE: str = "2 mins"
EMA_PERIOD: int = 9
EMA_ALPHA: float = 2.0 / (EMA_PERIOD + 1)   # = 0.2


# ---------------- EMA helpers ----------------
def _compute_ema9_series(bars: List[Dict]) -> None:
    """
    Populate each bar dict with an 'ema9' field.

    Uses SMA9 as the seed on the 9th bar, then the classic recursive EMA
    from that point forward.  Bars before the 9th get ema9 = None.
    """
    ema: Optional[float] = None
    for i, bar in enumerate(bars):
        close = float(bar["close"])
        if i + 1 < EMA_PERIOD:
            bar["ema9"] = None
        elif i + 1 == EMA_PERIOD:
            sma = sum(float(b["close"]) for b in bars[: EMA_PERIOD]) / EMA_PERIOD
            ema = sma
            bar["ema9"] = round(ema, 6)
        else:
            assert ema is not None
            ema = EMA_ALPHA * close + (1.0 - EMA_ALPHA) * ema
            bar["ema9"] = round(ema, 6)


# ---------------- VWAP helpers ----------------
def _typical_price(bar: Dict) -> float:
    return (float(bar["high"]) + float(bar["low"]) + float(bar["close"])) / 3.0


def _compute_vwap_series(bars: List[Dict]) -> None:
    """
    Populate each bar with a running session VWAP over the series.

    Uses typical price * volume accumulation.  We do NOT reset within the
    series -- a 12 h seed broadly spans one trading session, and a continuous
    line is more useful for visualisation than occasional resets that the
    trader would have to reason about.
    """
    cum_pv = 0.0
    cum_v = 0.0
    for bar in bars:
        v = float(bar.get("volume") or 0.0)
        if v > 0:
            cum_pv += _typical_price(bar) * v
            cum_v += v
        bar["vwap"] = round(cum_pv / cum_v, 4) if cum_v > 0 else None


def _advance_ema9(last_ema: Optional[float], history_len: int,
                  new_bar: Dict, closes_seed: List[float]) -> Optional[float]:
    """
    Compute the EMA9 for a newly completed bar continuing from `last_ema`.

    * If we still don't have EMA_PERIOD bars total after appending the new
      one, returns None (warm-up).
    * If this is the (EMA_PERIOD)-th completed bar, seed with SMA9 of the
      last EMA_PERIOD closes (closes_seed, including new_bar["close"]).
    * Otherwise, apply the recursive EMA formula.
    """
    close = float(new_bar["close"])
    total = history_len  # already includes the new bar

    if total < EMA_PERIOD:
        return None
    if total == EMA_PERIOD:
        return round(sum(closes_seed[-EMA_PERIOD:]) / EMA_PERIOD, 6)
    assert last_ema is not None
    return round(EMA_ALPHA * close + (1.0 - EMA_ALPHA) * last_ema, 6)


# ---------------- Session ----------------
class AutoAssistSession:
    """
    One running session per ticker.  Owns the IB market data subscription,
    the rolling bar history and the list of SSE subscriber queues.
    """

    def __init__(self, ib: IB, symbol: str):
        self.ib = ib
        self.symbol = symbol.upper()
        self.contract = Stock(self.symbol, "SMART", "USD")

        self.ticker: Optional[Ticker] = None
        self.subscribers: List[asyncio.Queue] = []

        # rolling history of completed 2-min bars (each with ema9 + vwap)
        self.bars: List[Dict] = []
        self.current_bar: Optional[Dict] = None

        # running EMA9 state for the *last completed* bar
        self.last_ema9: Optional[float] = None

        # running VWAP accumulators for completed bars
        self.cum_pv: float = 0.0
        self.cum_v: float = 0.0

        # live-session cumulative volume delta tracking
        self._last_cum_volume: Optional[float] = None

        # signal cooldown - re-armed when a new bar opens
        self.signal_armed: bool = True
        self.running: bool = False

    # ---- lifecycle ----
    async def start(self) -> None:
        try:
            await self.ib.qualifyContractsAsync(self.contract)
        except Exception:
            logger.exception("Could not qualify contract for %s", self.symbol)

        # --- seed with last 12 hours of 2-min historical bars ---
        try:
            raw = await self.ib.reqHistoricalDataAsync(
                self.contract,
                endDateTime="",
                durationStr=SEED_DURATION,
                barSizeSetting=BAR_SIZE,
                whatToShow="TRADES",
                useRTH=False,
                formatDate=2,        # UTC datetimes where supported
                keepUpToDate=False,
            )
        except Exception:
            logger.exception("Historical seed request failed for %s", self.symbol)
            raw = []

        seeded: List[Dict] = []
        for b in raw or []:
            # ib_async returns BarData.date as datetime (intraday) or date (daily)
            ts_obj = b.date
            if isinstance(ts_obj, datetime):
                if ts_obj.tzinfo is None:
                    ts_obj = ts_obj.replace(tzinfo=timezone.utc)
                ts = ts_obj.timestamp()
            else:
                # daily bars shouldn't occur at 2-min barSize, but be defensive
                continue
            vol_raw = getattr(b, "volume", None)
            try:
                vol_val = float(vol_raw) if vol_raw is not None else 0.0
                if math.isnan(vol_val) or vol_val < 0:
                    vol_val = 0.0
            except (TypeError, ValueError):
                vol_val = 0.0
            seeded.append({
                "time": int(ts),
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": vol_val,
                "ema9": None,
                "vwap": None,
            })

        seeded.sort(key=lambda x: x["time"])
        _compute_ema9_series(seeded)
        _compute_vwap_series(seeded)
        self.bars = seeded
        self.last_ema9 = seeded[-1]["ema9"] if seeded else None
        # seed VWAP accumulators so the live session continues the running sum
        self.cum_pv = 0.0
        self.cum_v = 0.0
        for b in seeded:
            v = float(b.get("volume") or 0.0)
            if v > 0:
                self.cum_pv += _typical_price(b) * v
                self.cum_v += v
        logger.info("Auto Assist seeded %d historical bars for %s",
                    len(self.bars), self.symbol)

        # --- subscribe to live market data ---
        self.ticker = self.ib.reqMktData(self.contract, "", False, False)
        self.ticker.updateEvent += self._on_tick
        self.running = True
        logger.info("Auto Assist session started for %s", self.symbol)

    def stop(self) -> None:
        if self.ticker is not None:
            try:
                self.ticker.updateEvent -= self._on_tick
            except Exception:
                pass
            try:
                self.ib.cancelMktData(self.contract)
            except Exception:
                logger.exception("Failed to cancel market data for %s", self.symbol)
        self.running = False
        self._broadcast({"type": "stopped", "symbol": self.symbol})
        logger.info("Auto Assist session stopped for %s", self.symbol)

    # ---- subscribers ----
    def add_subscriber(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        # send a snapshot of the current state so late-joiners see existing bars
        q.put_nowait({
            "type": "state",
            "symbol": self.symbol,
            "bars": self.bars,
            **self._levels(),
        })
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

    # ---- helpers ----
    @staticmethod
    def _bar_start(ts: float) -> int:
        return int(ts - (ts % BAR_SECONDS))

    def _levels(self) -> dict:
        last2_high: Optional[float] = None
        last5_low: Optional[float] = None
        stop_level: Optional[float] = None

        if len(self.bars) >= HIGH_WINDOW:
            last2_high = max(b["high"] for b in self.bars[-HIGH_WINDOW:])
        if len(self.bars) >= LOW_WINDOW:
            last5_low = min(b["low"] for b in self.bars[-LOW_WINDOW:])
            stop_level = round(last5_low - STOP_BUFFER, 4)

        return {"last2_high": last2_high, "stop_level": stop_level}

    def _build_signal_payload(self, price: float, last2_high: float,
                              stop_level: float, now_ts: float,
                              bar_time: Optional[float] = None) -> dict:
        # Position sizing mirrors services/orders.calculate_position_size so
        # the auto-generated order uses the same math as the manual flow.
        risk_per_unit = price - stop_level
        if risk_per_unit <= 0:
            position_size = 0
        else:
            position_size = abs(int(settings.RISK / risk_per_unit))

        return {
            "type": "signal",
            "symbol": self.symbol,
            "price": round(price, 4),
            "last2_high": round(last2_high, 4),
            "stop_level": stop_level,
            "position_size": position_size,
            "contract_type": "stock",
            "bar_time": bar_time if bar_time is not None else now_ts,
            "ts": now_ts,
        }

    # ---- tick handler ----
    def _volume_delta(self, ticker: Ticker) -> float:
        """Return how many shares traded since the previous tick, or 0."""
        cum_vol = getattr(ticker, "volume", None)
        if cum_vol is None:
            return 0.0
        if isinstance(cum_vol, float) and math.isnan(cum_vol):
            return 0.0
        if cum_vol < 0:
            return 0.0
        delta = 0.0
        if self._last_cum_volume is not None and cum_vol >= self._last_cum_volume:
            delta = float(cum_vol) - float(self._last_cum_volume)
        self._last_cum_volume = float(cum_vol)
        return delta

    def _live_vwap(self) -> Optional[float]:
        """Running VWAP including the still-forming current bar."""
        if self.current_bar is None:
            return round(self.cum_pv / self.cum_v, 4) if self.cum_v > 0 else None
        cur_v = float(self.current_bar.get("volume") or 0.0)
        total_v = self.cum_v + cur_v
        if total_v <= 0:
            return None
        total_pv = self.cum_pv + _typical_price(self.current_bar) * cur_v
        return round(total_pv / total_v, 4)

    def _on_tick(self, ticker: Ticker) -> None:
        """Called synchronously by ib_async on every ticker update."""

        # prefer last-trade price; fall back to mid-price if no trades yet
        price = ticker.last
        if price is None or (isinstance(price, float) and math.isnan(price)) or price <= 0:
            bid = ticker.bid
            ask = ticker.ask
            if (bid is None or (isinstance(bid, float) and math.isnan(bid)) or bid <= 0
                    or ask is None or (isinstance(ask, float) and math.isnan(ask)) or ask <= 0):
                return
            price = (bid + ask) / 2.0

        now_ts = datetime.now(timezone.utc).timestamp()
        bar_ts = self._bar_start(now_ts)
        vol_delta = self._volume_delta(ticker)

        if self.current_bar is None:
            # If the seed is so fresh that its last bar is the current bucket,
            # continue filling it instead of starting a new one.
            if self.bars and self.bars[-1]["time"] == bar_ts:
                self.current_bar = self.bars.pop()
                # keep the last ema9 of the PREVIOUS bar as the running state
                self.last_ema9 = self.bars[-1]["ema9"] if self.bars else None
                # remove this bar's volume from cumulative so we don't double
                # count when it finishes again
                seed_v = float(self.current_bar.get("volume") or 0.0)
                if seed_v > 0:
                    self.cum_pv -= _typical_price(self.current_bar) * seed_v
                    self.cum_v -= seed_v
                self.current_bar["high"] = max(self.current_bar["high"], price)
                self.current_bar["low"] = min(self.current_bar["low"], price)
                self.current_bar["close"] = price
                self.current_bar["volume"] = seed_v + vol_delta
            else:
                self.current_bar = {
                    "time": bar_ts, "open": price, "high": price,
                    "low": price, "close": price,
                    "volume": vol_delta, "ema9": None, "vwap": None,
                }
        elif bar_ts != self.current_bar["time"]:
            # finalize previous bar
            finished = self.current_bar
            self.bars.append(finished)
            # fold its volume into running VWAP accumulators
            fv = float(finished.get("volume") or 0.0)
            if fv > 0:
                self.cum_pv += _typical_price(finished) * fv
                self.cum_v += fv
            finished["vwap"] = (
                round(self.cum_pv / self.cum_v, 4) if self.cum_v > 0 else None
            )
            # advance EMA9 using all closes so far
            new_ema = _advance_ema9(
                self.last_ema9,
                len(self.bars),
                finished,
                [b["close"] for b in self.bars],
            )
            self.last_ema9 = new_ema
            finished["ema9"] = new_ema
            self._broadcast({"type": "bar", **finished})
            # open new bar with whatever volume just ticked
            self.current_bar = {
                "time": bar_ts, "open": price, "high": price,
                "low": price, "close": price,
                "volume": vol_delta, "ema9": None, "vwap": None,
            }
            # re-arm signal on every new bar
            self.signal_armed = True
            self._broadcast({"type": "levels", **self._levels()})
        else:
            self.current_bar["high"] = max(self.current_bar["high"], price)
            self.current_bar["low"] = min(self.current_bar["low"], price)
            self.current_bar["close"] = price
            self.current_bar["volume"] = float(
                self.current_bar.get("volume") or 0.0
            ) + vol_delta

        # emit tick with the current (still-forming) bar so frontend can draw it
        self._broadcast({
            "type": "tick",
            "time": now_ts,
            "price": price,
            "bar_time": self.current_bar["time"],
            "bar_open": self.current_bar["open"],
            "bar_high": self.current_bar["high"],
            "bar_low": self.current_bar["low"],
            "bar_close": self.current_bar["close"],
            "bar_volume": self.current_bar.get("volume") or 0.0,
            "bar_vwap": self._live_vwap(),
        })

        # breakout detection: tick broke last-2 high upwards
        levels = self._levels()
        last2_high = levels["last2_high"]
        stop_level = levels["stop_level"]
        if (self.signal_armed
                and last2_high is not None
                and stop_level is not None
                and price > last2_high):
            self._broadcast(self._build_signal_payload(
                price, last2_high, stop_level, now_ts,
                bar_time=self.current_bar["time"],
            ))
            self.signal_armed = False


# ---------------- Module-level registry ----------------
SESSIONS: Dict[str, AutoAssistSession] = {}


async def start_session(ib: IB, symbol: str) -> AutoAssistSession:
    key = symbol.upper()
    existing = SESSIONS.get(key)
    if existing is not None and existing.running:
        return existing

    session = AutoAssistSession(ib, key)
    await session.start()
    SESSIONS[key] = session
    return session


def stop_session(symbol: str) -> bool:
    key = symbol.upper()
    session = SESSIONS.pop(key, None)
    if session is None:
        return False
    session.stop()
    return True


def get_session(symbol: str) -> Optional[AutoAssistSession]:
    return SESSIONS.get(symbol.upper())
