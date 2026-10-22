"""
Diagnose why the daily premarket summary grades every row D.

A row only gets a grade above D when BOTH inputs work:
  1. news fetch  (yfinance, via services.news)
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
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import yfinance as yf  # noqa: E402

from core.config import settings  # noqa: E402
from services import daily_summary as ds  # noqa: E402
from services.news import fetch_symbol_news_sync  # noqa: E402

# Show errors yfinance normally hides (faulty Yahoo responses etc.)
yf.config.debug.hide_exceptions = False


def check_news(symbol: str) -> list:
    print(f"\n--- NEWS: {symbol} ---")

    # yfinance Search, raw
    try:
        raw = yf.Search(symbol, news_count=20, max_results=1).news or []
        print(f"yfinance {yf.__version__} Search: {len(raw)} raw item(s)")
        for item in raw[:3]:
            ts = item.get("providerPublishTime")
            when = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
            print(f"  published={when}  related={item.get('relatedTickers')}  "
                  f"title={str(item.get('title'))[:70]!r}")
    except Exception:
        print("yfinance RAISED:")
        traceback.print_exc()

    # What the service actually ends up with (after the 24h filter)
    try:
        items = fetch_symbol_news_sync(symbol, 24)
    except Exception as e:
        print(f"service fetch RAISED: {type(e).__name__}: {e}")
        items = []
    print(f"=> service news after 24h filter: {len(items)} item(s), "
          f"sending newest {ds.CLAUDE_HEADLINES} to Claude")
    return items[:ds.CLAUDE_HEADLINES]


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
            max_tokens=ds.CLAUDE_MAX_TOKENS,
            messages=[{"role": "user",
                       "content": ds._ticker_reason_prompt(symbol, None, None, news)}],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content)
        print(f"stop_reason={msg.stop_reason}  max_tokens={ds.CLAUDE_MAX_TOKENS}  "
              f"output_tokens={msg.usage.output_tokens}  "
              f"blocks={[b.type for b in msg.content]}")
        print(f"raw reply: {text!r}")
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
