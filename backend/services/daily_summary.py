"""
Daily premarket summary service.

Orchestration:

1.   Run IB's gap_up_scan and gap_down_scan presets through the existing
     services.scanner.run_scanner_logic pipeline.
2.   Take the top 5 by % change on each side (the same sort the frontend already
     uses).
3.   For each of those 10 tickers, fetch news via the same yfinance/RSS path
     the /api/scanner/news/{symbol} endpoint uses, then ask Claude to apply
     the Catalyst Value Equation (CVE) rubric — see docs/CATALYST_EVALUATION.md
     — and return a structured evaluation: catalyst sentence, type, magnitude,
     speed, and caveats. Grade and risk-allocation cap are derived server-side
     from (magnitude, speed) using the §4 table in the rubric.
4.   Persist the whole snapshot via db.daily_summary.upsert_daily_summary so
     the scanner page can read it back without rerunning the LLM.

All Anthropic calls are gated on settings.ANTHROPIC_API_KEY being present; if
it isn't, the service still runs and stores the scan + news headlines, with
every row collapsing to grade D / 0% sizing — i.e. the rubric's safe default.

Note: the CVE rubric defined in docs/CATALYST_EVALUATION.md is duplicated
inline in _ticker_reason_prompt below so the LLM sees the same definitions
the trader is reading. If you change the rubric, update both.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

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


# ---------------------------------------------------------------------------
# CVE rubric — must mirror docs/CATALYST_EVALUATION.md
# ---------------------------------------------------------------------------

# Sizing caps come straight from the §4 grade table in the rubric. The LLM is
# also asked for a sizing number, but we recompute it server-side from the
# grade so the cap is always consistent with the table even if the LLM picks
# a different number. This is the single source of truth.
_GRADE_TO_SIZING_PCT: Dict[str, int] = {
    "A+": 80,
    "A":  30,
    "B":  15,
    "C":   5,
    "D":   0,
}

# Magnitude×Speed → Grade lookup (also from §4 of the rubric). Order within
# the pair doesn't matter — we normalise by sorting the two scores.
_SCORE_ORDER = {"Absolute": 3, "Yes": 2, "Maybe": 1, "No": 0}


def _normalise_score(raw: object) -> str:
    """Coerce LLM output to one of {Absolute, Yes, Maybe, No}. Unknown → "No"
    so an unparseable answer collapses to D, per the "starts at D" bias."""
    if not isinstance(raw, str):
        return "No"
    s = raw.strip().lower()
    if s.startswith("absol"):
        return "Absolute"
    if s == "yes":
        return "Yes"
    if s.startswith("maybe") or s == "partial":
        return "Maybe"
    return "No"


def _normalise_catalyst_type(raw: object) -> str:
    if not isinstance(raw, str):
        return "none"
    s = raw.strip().lower()
    if s.startswith("confirm") or s in ("event", "material"):
        return "confirmed"
    if s.startswith("cover") or s in ("upgrade", "initiation", "13d"):
        return "coverage"
    if s.startswith("narr") or s in ("sector", "macro", "social"):
        return "narrative"
    return "none"


def _grade_from_scores(magnitude: str, speed: str) -> str:
    """Map (Magnitude, Speed) to a letter grade, ignoring order."""
    pair = sorted([magnitude, speed], key=lambda x: -_SCORE_ORDER.get(x, 0))
    hi, lo = pair[0], pair[1]
    if "No" in (hi, lo):
        return "D"
    if hi == "Absolute" and lo == "Absolute":
        return "A+"
    if hi == "Yes" and lo == "Yes":
        return "A"
    # Absolute × Yes is one step better than Yes × Yes — keep it at A.
    if hi == "Absolute" and lo == "Yes":
        return "A"
    if hi == "Yes" and lo == "Maybe":
        return "B"
    # Absolute × Maybe is a known edge case — the rubric's worked examples treat
    # "huge implied move but ambiguous timing" as B (sized smaller than Yes×Yes A).
    if hi == "Absolute" and lo == "Maybe":
        return "B"
    if hi == "Maybe" and lo == "Maybe":
        return "C"
    return "D"


def _ticker_reason_prompt(
    symbol: str,
    change: Optional[float],
    rvol: Optional[float],
    headlines: List[Dict],
) -> str:
    """Build the CVE prompt for a single ticker.

    The prompt embeds the §3-§6 rubric from docs/CATALYST_EVALUATION.md so the
    LLM has the same definitions the trader is reading. Output is strict JSON;
    we recompute the grade and sizing server-side from the two scores so the
    LLM cannot accidentally inflate the grade — its job is only to score
    Magnitude and Speed and to write the reasoning, not to decide the cap.
    """
    direction = "up" if (change or 0) >= 0 else "down"
    change_str = f"{change:+.2f}%" if change is not None else "n/a"
    rvol_str = f"{rvol:.2f}" if rvol is not None else "n/a"
    bullets = "\n".join(
        f"- {h['title']}" + (f" — {h['summary'][:200]}" if h.get("summary") else "")
        for h in headlines[:3]
    ) or "(no news available)"
    return (
        "You are a premarket trading analyst applying the Catalyst Value Equation (CVE).\n"
        "Default bias: every trade starts at D (don't trade). Make the catalyst prove\n"
        "itself. If you cannot name a concrete catalyst in one sentence, grade is D.\n\n"
        "RUBRIC\n"
        "  Two independent scores, each one of: Absolute, Yes, Maybe, No.\n"
        "    Magnitude — how big is the implied repricing this catalyst alone should\n"
        "                drive? (NOT company size; NOT current move size.)\n"
        "    Speed     — must this be priced today / at open / in the first hour?\n"
        "                Or is it a slow-burn thesis that could grind for a week?\n"
        "  Score values:\n"
        "    Absolute  unambiguous, market-moving, hard to argue with\n"
        "    Yes       clearly present but with a debatable element\n"
        "    Maybe     possibly there; requires interpretation\n"
        "    No        not present / cannot be evidenced\n\n"
        "CATALYST TYPES (pick one)\n"
        "  confirmed  hard public event: earnings, M&A, FDA, index inclusion,\n"
        "             regulatory ruling, post-IPO analyst initiations\n"
        "  coverage   re-rating by flow-moving parties: multi-bank upgrades, 13D,\n"
        "             unusual options tied to a date\n"
        "  narrative  sector sympathy, macro/commodity theme, social-media attention\n"
        "  none       no identifiable catalyst — default to this\n\n"
        "WHAT IS *NOT* A CATALYST\n"
        "  - 'big move on the chart' is a reaction, not a catalyst.\n"
        "  - Pure technical setups (breakout, MA cross, gap-and-go).\n"
        "  - 'It missed and faded' — disappointment is not a catalyst.\n\n"
        "INPUTS\n"
        f"  Stock: {symbol}\n"
        f"  Premarket move: {direction} {change_str}\n"
        f"  Relative volume (RVol): {rvol_str}\n"
        f"  News headlines (last 24h):\n{bullets}\n\n"
        "OUTPUT — STRICT JSON, no markdown fences, no prose around it.\n"
        '  "reason"        : one sentence naming the actual catalyst. If none,\n'
        '                    use "No clear catalyst".\n'
        '  "catalyst_type" : confirmed | coverage | narrative | none\n'
        '  "magnitude"     : Absolute | Yes | Maybe | No\n'
        '  "speed"         : Absolute | Yes | Maybe | No\n'
        '  "notes"         : caveats — already in price? float/liquidity? peer\n'
        '                    flow? Confidence checks the reader should know.\n\n'
        'Example:\n'
        '  {"reason": "Post-quiet-period analyst initiations from 4 banks",\n'
        '   "catalyst_type": "coverage", "magnitude": "Absolute", "speed": "Absolute",\n'
        '   "notes": "First-day moves are crowded; watch float and opening drive."}\n'
    )


# Best-effort JSON extraction — Claude usually obeys "JSON only", but sometimes
# wraps it in ```json fences or adds a stray sentence. _JSON_BLOCK_RE catches a
# brace-balanced object via the greedy {...} with no nested braces (matches our
# flat output shape).
_JSON_BLOCK_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def _parse_ticker_response(text: str) -> Dict[str, object]:
    """Parse the LLM output into a normalised CVE dict.

    Returns a dict with: reason, catalyst_type, magnitude, speed, grade,
    sizing_pct, notes. The grade and sizing_pct are derived server-side from
    the two scores so the LLM cannot inflate them. Missing / unparseable
    inputs collapse to D / 0% per the "every trade starts at D" bias.
    """
    fallback: Dict[str, object] = {
        "reason": "",
        "catalyst_type": "none",
        "magnitude": "No",
        "speed": "No",
        "grade": "D",
        "sizing_pct": 0,
        "notes": "",
    }
    if not text:
        return fallback

    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_BLOCK_RE.search(text)
        if not m:
            fallback["reason"] = text.strip().split("\n")[0][:200]
            return fallback
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            fallback["reason"] = text.strip().split("\n")[0][:200]
            return fallback

    reason = str(obj.get("reason") or "").strip()
    notes = str(obj.get("notes") or "").strip()
    catalyst_type = _normalise_catalyst_type(obj.get("catalyst_type"))
    magnitude = _normalise_score(obj.get("magnitude"))
    speed = _normalise_score(obj.get("speed"))
    # If the LLM said the catalyst type is "none" or the reason is empty /
    # "no clear catalyst", force the grade to D regardless of the score
    # values it returned — the rubric is explicit that no catalyst → D.
    if catalyst_type == "none" or not reason or reason.lower().startswith("no clear"):
        grade = "D"
    else:
        grade = _grade_from_scores(magnitude, speed)
    sizing_pct = _GRADE_TO_SIZING_PCT.get(grade, 0)

    return {
        "reason": reason,
        "catalyst_type": catalyst_type,
        "magnitude": magnitude,
        "speed": speed,
        "grade": grade,
        "sizing_pct": sizing_pct,
        "notes": notes,
    }


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

        # Always send the rubric prompt so Claude can return "no catalyst →
        # grade D" cleanly when news is empty — we don't gate on news presence.
        # The token budget is larger than the old version because the structured
        # output (reason + 4 fields + notes) needs more room than a one-liner.
        raw = await _claude_complete(
            _ticker_reason_prompt(symbol, change, rvol, news),
            max_tokens=400,
        )
        cve = _parse_ticker_response(raw)

        if news:
            headline = news[0].get("title", "") or ""
            news_url = news[0].get("url", "") or ""
        else:
            headline = ""
            news_url = ""
            if not cve["reason"]:
                cve["reason"] = "No clear catalyst"

        return {
            "side": side,
            "rank": rank,
            "symbol": symbol,
            "change": change,
            "rvol": rvol,
            "catalyst_type": cve["catalyst_type"],
            "magnitude": cve["magnitude"],
            "speed": cve["speed"],
            "grade": cve["grade"],
            "sizing_pct": cve["sizing_pct"],
            "reason": cve["reason"],
            "notes": cve["notes"],
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
