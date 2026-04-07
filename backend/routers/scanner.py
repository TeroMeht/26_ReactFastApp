from fastapi import APIRouter, Depends, HTTPException
from dependencies import get_ib
from typing import Optional,List,Dict
from schemas.api_schemas import ScannerResponse, NewsItem
from services.scanner import run_scanner_logic
import yfinance as yf
import feedparser

from datetime import datetime, timezone, timedelta

import logging
logger = logging.getLogger(__name__)



router = APIRouter(
    prefix="/api/scanner",
    tags=["IB Scanner"]
)


@router.get("", response_model=List[ScannerResponse])
async def run_scanner(preset_name: str,ib=Depends(get_ib)):
    try:
        return await run_scanner_logic(
            preset_name=preset_name,
            ib=ib
        )
    except ValueError as e:
        # For invalid preset etc.
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Scanner execution failed")
        raise HTTPException(status_code=500, detail="Scanner execution failed")






CUTOFF_24H = datetime.now(timezone.utc) - timedelta(hours=24)

def parse_dt(dt_str: str) -> datetime | None:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(dt_str, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None

def is_within_24h(dt_str: str) -> bool:
    dt = parse_dt(dt_str)
    return dt is not None and dt >= CUTOFF_24H


@router.get("/news/{symbol}", response_model=List[NewsItem])
async def get_symbol_news(symbol: str):
    try:
        news_items = []
        seen_urls = set()


        ticker = yf.Ticker(symbol)
        yf_news = ticker.news or []
        for item in yf_news:
            content = item.get("content", {})
            published_at = content.get("pubDate", "")
            if not is_within_24h(published_at):
                continue
            url = content.get("canonicalUrl", {}).get("url", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            news_items.append({
                "title": content.get("title", ""),
                "summary": content.get("summary", ""),
                "url": url,
                "source": content.get("provider", {}).get("displayName", "Yahoo Finance"),
                "published_at": published_at,
                "thumbnail": (content.get("thumbnail") or {}).get("resolutions", [{}])[0].get("url", ""),
            })
    except Exception:
        logger.warning(f"yfinance news unavailable for {symbol}, falling back to RSS")
        # Supplement (or fallback): RSS feed
        rss_url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
        feed = feedparser.parse(rss_url)
        for entry in feed.entries:
            published_at = ""
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                if dt < CUTOFF_24H:
                    continue
                published_at = dt.isoformat()
            url = entry.get("link", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            news_items.append({
                "title": entry.get("title", ""),
                "summary": entry.get("summary", ""),
                "url": url,
                "source": "Yahoo Finance RSS",
                "published_at": published_at,
                "thumbnail": "",
            })

        # Sort newest first
        news_items.sort(
            key=lambda x: parse_dt(x["published_at"]) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True
        )

        return news_items

    except Exception as e:
        logger.exception(f"Failed to fetch news for {symbol}")
        raise HTTPException(status_code=500, detail=f"News fetch failed for {symbol}")


