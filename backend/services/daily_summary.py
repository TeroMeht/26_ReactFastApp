"""
Daily premarket summary service.

Orchestration:

1.   Run IB's gap_up_scan and gap_down_scan presets through the existing
     services.scanner.run_scanner_logic pipeline.
2.   Take the top 5 by % change on each side (the same sort the frontend already
     uses).
3.   For each of those 10 tickers, fetch news via the same yfinance/RSS path
     the /api/scanner/news/{symbol} endpoint uses, then ask Claude to distill
     the most relevant headline into a few-word "why is it moving" reason.
4.   Pull SPY and QQQ premarket changes + a couple of headlines and ask Claude
     for a one-line overall-market summary.
5.   Persist the whole snapshot via db.daily_summary.upsert_daily_summary so
     the scanner page can read it back without rerunning the LLM.

All Anthropic calls are gated on settings.ANTHROPIC_API_KEY being present; if
it isn't, the service still runs and stores the scan + news headlines, just
with empty reason/summary strings so the UI is still useful.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple  # Tuple used by _parse_ticker_response

import asyncpg
import feedparser
import yfinance as yf
from ib_async import IB

from core.config import settings
from db.daily_summary import upsert_daily_summary
from services.scanner import run_scanner_logic

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# News fetch — mirrors routers/scanner.py's /news/{symbol} logic, but as a
# plain async function so the service can call it without going through HTTP.
# ---------------------------------------------------------------------------

def _parse_dt(dt_str: str) -> Optional[datetime]:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(dt_str, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def _fetch_symbol_news_sync(symbol: str, cutoff: datetime) -> List[Dict]:
    """
    Blocking news fetch (yfinance + Yahoo RSS fallback). Designed to be run
    via asyncio.to_thread because yfinance / feedparser are sync.
    Returns newest-first list of {title, summary, url, source, published_at}.
    """
    news_items: List[Dict] = []
    seen_urls: set[str] = set()

    try:
        ticker = yf.Ticker(symbol)
        yf_news = ticker.news or []
        for item in yf_news:
            content = item.get("content", {}) or {}
            published_at = content.get("pubDate", "") or ""
            dt = _parse_dt(published_at)
            if dt is None or dt < cutoff:
                continue
            url = (content.get("canonicalUrl") or {}).get("url", "") or ""
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            news_items.append({
                "title": content.get("title", "") or "",
                "summary": content.get("summary", "") or "",
                "url": url,
                "source": (content.get("provider") or {}).get("displayName", "Yahoo Finance"),
                "published_at": published_at,
            })
    except Exception:
        logger.warning("yfinance news unavailable for %s, falling back to RSS", symbol)
        try:
            rss_url = (
                f"https://feeds.finance.yahoo.com/rss/2.0/headline"
                f"?s={symbol}&region=US&lang=en-US"
            )
            feed = feedparser.parse(rss_url)
            for entry in feed.entries:
                published_at = ""
                if getattr(entry, "published_parsed", None):
                    dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    if dt < cutoff:
                        continue
                    published_at = dt.isoformat()
                url = entry.get("link", "") or ""
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                news_items.append({
                    "title": entry.get("title", "") or "",
                    "summary": entry.get("summary", "") or "",
                    "url": url,
                    "source": "Yahoo Finance RSS",
                    "published_at": published_at,
                })
        except Exception:
            logger.exception("Failed to fetch news for %s", symbol)

    news_items.sort(
        key=lambda x: _parse_dt(x["published_at"]) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return news_items


async def _fetch_symbol_news(symbol: str) -> List[Dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    return await asyncio.to_thread(_fetch_symbol_news_sync, symbol, cutoff)


# ---------------------------------------------------------------------------
# Anthropic summarization
# ---------------------------------------------------------------------------

# Lazy import + module-level client cache so the rest of the backend doesn't
# need the anthropic package installed unless this endpoint is actually used.
_anthropic_client = None


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client
    if not settings.ANTHROPIC_API_KEY:
        return None
    try:
        from anthropic import Anthropic  # type: ignore
    except ImportError:
        logger.warning(
            "anthropic SDK not installed; install with `uv add anthropic` "
            "to enable LLM-backed daily summaries."
        )
        return None
    _anthropic_client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _anthropic_client


def _claude_complete_sync(prompt: str, max_tokens: int = 60) -> str:
    """Single short completion. Returns "" on any failure so callers can keep going."""
    client = _get_anthropic_client()
    if client is None:
        return ""
    try:
        msg = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        # The SDK returns a list of content blocks; the first text block is what we want.
        for block in msg.content:
            text = getattr(block, "text", None)
            if text:
                return text.strip()
    except Exception:
        logger.exception("Anthropic completion failed")
    return ""


async def _claude_complete(prompt: str, max_tokens: int = 60) -> str:
    return await asyncio.to_thread(_claude_complete_sync, prompt, max_tokens)


def _ticker_reason_prompt(
    symbol: str,
    change: Optional[float],
    rvol: Optional[float],
    headlines: List[Dict],
) -> str:
    """
    Ask Claude to produce a few-word reason AND a 1-10 catalyst strength that
    blends news impact, gap size, and relative volume. Output is strict JSON
    so we can parse it deterministically.
    """
    direction = "up" if (change or 0) >= 0 else "down"
    change_str = f"{change:+.2f}%" if change is not None else "n/a"
    rvol_str = f"{rvol:.2f}" if rvol is not None else "n/a"
    bullets = "\n".join(
        f"- {h['title']}" + (f" — {h['summary'][:200]}" if h.get("summary") else "")
        for h in headlines[:3]
    ) or "(no news available)"
    return (
        f"You are a premarket trading analyst. Rate the catalyst behind a stock's move.\n\n"
        f"Stock: {symbol}\n"
        f"Premarket move: {direction} {change_str}\n"
        f"Relative volume (RVol): {rvol_str}  (>2 = elevated, <1 = weak conviction)\n"
        f"News headlines (last 24h):\n{bullets}\n\n"
        f"Task: return STRICT JSON with two fields:\n"
        f'  "reason": a short phrase (max ~10 words, no period) explaining WHY '
        f"the stock is moving based on the news. If no clear catalyst exists in "
        f'the headlines, use "No clear catalyst".\n'
        f'  "catalyst_strength": an integer 1-10 that BLENDS:\n'
        f"     - News impact: stock dilution / missed earnings / SEC probe = strong negative; "
        f"double earnings beat / FDA approval / buyout / huge contract = strong positive. "
        f"Direction does NOT change the score — both up and down are catalysts, scale is "
        f"about how actionable and conviction-building the move is.\n"
        f"     - Gap size: |change| > 10% is big, > 20% is huge.\n"
        f"     - RVol: > 2 confirms institutional follow-through; < 1 means thin and untrustworthy.\n"
        f"   RULES:\n"
        f"     - Big gap + strong news + RVol > 2 → 8-10.\n"
        f"     - Strong news + decent gap + RVol > 1 → 6-7.\n"
        f"     - Big gap but no clear news catalyst and RVol < 1.5 → 3-4.\n"
        f"     - Tiny gap or no signal → 1-2.\n\n"
        f'Respond with ONLY the JSON object, nothing else. Example: {{"reason": "FDA approval for lead drug", "catalyst_strength": 9}}'
    )


# Best-effort JSON extraction — Claude usually obeys "JSON only", but sometimes
# wraps it in ```json fences or adds a stray sentence. This grabs the first
# {...} block and parses it; returns (reason, strength) with safe defaults.
_JSON_BLOCK_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def _parse_ticker_response(text: str) -> Tuple[str, Optional[int]]:
    if not text:
        return "", None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_BLOCK_RE.search(text)
        if not m:
            return text.strip().split("\n")[0][:120], None
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return text.strip().split("\n")[0][:120], None

    reason = str(obj.get("reason") or "").strip()
    raw_strength = obj.get("catalyst_strength")
    strength: Optional[int] = None
    if isinstance(raw_strength, (int, float)):
        try:
            strength = max(1, min(10, int(round(float(raw_strength)))))
        except (ValueError, TypeError):
            strength = None
    elif isinstance(raw_strength, str):
        try:
            strength = max(1, min(10, int(round(float(raw_strength)))))
        except ValueError:
            strength = None
    return reason, strength


# ---------------------------------------------------------------------------
# Top entry point
# ---------------------------------------------------------------------------

async def generate_daily_summary(
    db_conn: asyncpg.Connection,
    ib: IB,
) -> Dict:
    """
    Run both gap scans, distill news per top mover via Claude, persist the
    snapshot to daily_summary_row, and return it in the same shape
    db.get_latest_daily_summary returns. The router serves this dict back
    to the client.
    """
    logger.info("Daily summary: running gap up + gap down scans")
    gap_up_task = asyncio.create_task(run_scanner_logic("gap_up_scan", ib))
    gap_down_task = asyncio.create_task(run_scanner_logic("gap_down_scan", ib))
    gap_up_results, gap_down_results = await asyncio.gather(gap_up_task, gap_down_task)

    # The scanner already returns ScannerResponse rows; take top 5 each side by
    # signed change. Up = highest positive first, Down = lowest negative first.
    top_up = sorted(gap_up_results, key=lambda r: r.change, reverse=True)[:5]
    top_down = sorted(gap_down_results, key=lambda r: r.change)[:5]

    # Build the list of (side, rank, ScannerResponse) work items.
    work = (
        [("up", i + 1, r) for i, r in enumerate(top_up)] +
        [("down", i + 1, r) for i, r in enumerate(top_down)]
    )

    # Fetch news + summarize concurrently. Each item independent → asyncio.gather.
    async def _process_item(side: str, rank: int, row) -> Dict:
        symbol = row.symbol
        change = float(row.change) if row.change is not None else None
        rvol = float(row.rvol) if row.rvol is not None else None
        news = await _fetch_symbol_news(symbol)

        # Always send the rating prompt so Claude can score "big gap, no news,
        # weak rvol" appropriately low — we don't gate on news presence.
        raw = await _claude_complete(
            _ticker_reason_prompt(symbol, change, rvol, news),
            max_tokens=180,
        )
        reason, strength = _parse_ticker_response(raw)

        if news:
            headline = news[0].get("title", "") or ""
            news_url = news[0].get("url", "") or ""
        else:
            headline = ""
            news_url = ""
            if not reason:
                reason = "No clear catalyst"

        return {
            "side": side,
            "rank": rank,
            "symbol": symbol,
            "change": change,
            "rvol": rvol,
            "catalyst_strength": strength,
            "reason": reason,
            "headline": headline,
            "news_url": news_url,
        }

    rows = await asyncio.gather(*(_process_item(s, r, row) for s, r, row in work))

    run_date = date.today()
    # Stamp the date on each row so the response matches the shape
    # db.get_latest_daily_summary returns (and DailySummaryRow.run_date).
    for r in rows:
        r["run_date"] = run_date

    await upsert_daily_summary(db_conn, run_date=run_date, rows=rows)

    # Mirror the shape db.get_latest_daily_summary returns so the router doesn't
    # have to round-trip through Postgres just to serve the response.
    return {
        "run_date": run_date,
        "created_at": datetime.now(timezone.utc),
        "rows": rows,
    }
