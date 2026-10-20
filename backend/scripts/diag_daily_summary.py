"""
Diagnose why the daily premarket summary grades every row D.

A row only gets a grade above D when BOTH inputs work:
  1. news fetch  (yfinance, falling back to Yahoo RSS)
  2. the Claude call (ANTHROPIC_API_KEY + ANTHROPIC_MODEL)

This script tests each one separately and prints what it got, so the
failing step is obvious. Nothing is written to the database.

Run from backend/:

    uv run python scripts/diag_daily_summary.py ALHC T VZ
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import feedparser  # noqa: E402
import yfinance as yf  # noqa: E402

from core.config import settings  # noqa: E402
from services import daily_summary as ds  # noqa: E402


def check_news(symbol: str) -> list:
    print(f"\n--- NEWS: {symbol} ---")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    # yfinance, raw
    try:
        raw = yf.Ticker(symbol).news or []
        print(f"yfinance {yf.__version__}: {len(raw)} raw item(s)")
        for item in raw[:3]:
            content = item.get("content") or {}
            pub = content.get("pubDate")
            print(f"  pubDate={pub!r}  parses_as={ds._parse_dt(pub or '')}  "
                  f"title={str(content.get('title'))[:70]!r}")
        if raw and not (raw[0].get("content")):
            print(f"  !! item has no 'content' key; keys are: {list(raw[0].keys())}")
    except Exception:
        print("yfinance RAISED:")
        traceback.print_exc()

    # RSS, raw (the service only tries this if yfinance raises)
    try:
        feed = feedparser.parse(
            f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
        )
        status = getattr(feed, "status", "n/a")
        print(f"RSS: http status={status}, {len(feed.entries)} entr(ies)"
              + (f", bozo={feed.bozo_exception!r}" if feed.bozo else ""))
    except Exception:
        print("RSS RAISED:")
        traceback.print_exc()

    # What the service actually ends up with (after the 24h filter)
    items = ds._fetch_symbol_news_sync(symbol, cutoff)
    print(f"=> service news after 24h filter: {len(items)} item(s)")
    return items


def check_claude(symbol: str, news: list) -> None:
    print(f"\n--- CLAUDE: {symbol} ---")
    key = settings.ANTHROPIC_API_KEY or ""
    print(f"ANTHROPIC_API_KEY set: {bool(key)} (length {len(key)})")
    print(f"ANTHROPIC_MODEL: {settings.ANTHROPIC_MODEL!r}")

    client = ds._get_anthropic_client()
    if client is None:
        print("=> no client (missing key or anthropic package)")
        return
    try:
        msg = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=400,
            messages=[{"role": "user",
                       "content": ds._ticker_reason_prompt(symbol, None, None, news)}],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content)
        print(f"stop_reason={msg.stop_reason}")
        print(f"raw reply: {text[:500]!r}")
        print(f"=> parsed: {ds._parse_ticker_response(text.strip())}")
    except Exception:
        print("=> Claude call RAISED (this is what the service swallows):")
        traceback.print_exc()


def main() -> None:
    symbols = sys.argv[1:] or ["ALHC", "T", "VZ"]
    for sym in symbols:
        news = check_news(sym)
        check_claude(sym, news)


if __name__ == "__main__":
    main()
