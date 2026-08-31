"""Новости из RSS-лент."""
import asyncio
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

from app import config


def _parse(raw: bytes) -> list[dict]:
    feed = feedparser.parse(raw)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=20)
    items = []
    for entry in feed.entries[:25]:
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        published = None
        if entry.get("published_parsed"):
            published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        if published and published < cutoff:
            continue
        items.append({"title": title, "link": entry.get("link", ""),
                      "published": published})
    return items


async def _fetch(url: str) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as http:
            r = await http.get(url, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            return await asyncio.to_thread(_parse, r.content)
    except Exception:
        return []


async def headlines(limit: int = 20) -> list[dict]:
    if not config.RSS_FEEDS:
        return []
    results = await asyncio.gather(*(_fetch(u) for u in config.RSS_FEEDS))
    flat = [item for chunk in results for item in chunk]
    seen, out = set(), []
    for item in flat:
        key = item["title"].lower()[:60]
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out[:limit]
