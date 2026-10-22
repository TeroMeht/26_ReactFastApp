from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import yfinance as yf

NEWS_COUNT = 10
NEWS_LOOKBACK_HOURS = 24


def _to_news_item(item: Dict, symbol: str, cutoff: datetime) -> Optional[Dict]:
    """Convert one yf.Search news result into a NewsItem dict.
    Returns None if it is about another ticker, older than `cutoff`, or has no link."""
    # Search is a text search; skip articles tagged only with other tickers.
    related = item.get("relatedTickers") or []
    if related and symbol not in related:
        return None
    ts = item.get("providerPublishTime")
    if not isinstance(ts, (int, float)):
        return None
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    if dt < cutoff:
        return None
    url = item.get("link") or ""
    if not url:
        return None
    resolutions = (item.get("thumbnail") or {}).get("resolutions") or []
    return {
        "title": item.get("title") or "",
        "summary": "",  # Search results carry no summary
        "url": url,
        "source": item.get("publisher") or "Yahoo Finance",
        "published_at": dt.isoformat(),
        "thumbnail": (resolutions[0].get("url") or "") if resolutions else "",
    }


def fetch_symbol_news_sync(symbol: str, hours: int = NEWS_LOOKBACK_HOURS) -> List[Dict]:
    """Newest-first list of {title, summary, url, source, published_at, thumbnail}
    published within the last `hours` hours."""
    symbol = symbol.upper()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    items: List[Dict] = []
    seen: set[str] = set()

    for raw in yf.Search(symbol, news_count=NEWS_COUNT, max_results=1).news or []:
        item = _to_news_item(raw, symbol, cutoff)
        if item is None or item["url"] in seen:
            continue
        seen.add(item["url"])
        items.append(item)

    items.sort(key=lambda x: x["published_at"], reverse=True)  # all UTC isoformat
    return items
