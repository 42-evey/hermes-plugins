"""Evey News — AI news monitoring via SearXNG.

Scans for trending AI agent news, model releases, framework updates.
Returns curated findings for Evey to review, share, or act on.
Use during research crons or when idle.
"""

import json
import logging
import os
import time
import urllib.request
import urllib.error
from datetime import datetime

logger = logging.getLogger("evey.news")

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://hermes-searxng:8080")

NEWS_QUERIES = [
    "AI agent framework release 2026",
    "free AI model new release",
    "hermes-agent NousResearch",
    "autonomous AI agent news",
    "MiMo Xiaomi model update",
]

SCHEMA = {
    "name": "news_scan",
    "description": (
        "Scan AI news for interesting developments. Searches multiple queries "
        "via SearXNG and returns curated results. Use during research crons "
        "or when looking for things to share on Moltbook/X."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "custom_query": {
                "type": "string",
                "description": "Custom search query (optional — defaults to AI agent news)",
            },
            "max_results": {
                "type": "number",
                "description": "Max results per query (default: 3)",
            },
        },
    },
}


def _search(query, max_results=3):
    """Search via SearXNG JSON API."""
    try:
        params = urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "categories": "general,news,it",
            "time_range": "week",
            "language": "en",
        })
        url = f"{SEARXNG_URL}/search?{params}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        results = []
        for r in data.get("results", [])[:max_results]:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", "")[:200],
                "source": r.get("engine", ""),
            })
        return results
    except Exception as e:
        logger.warning(f"Search failed for '{query}': {e}")
        return []


import urllib.parse


def handler(args, **kwargs):
    custom = args.get("custom_query", "")
    max_results = int(args.get("max_results", 3))

    queries = [custom] if custom else NEWS_QUERIES
    all_results = []
    seen_urls = set()

    for q in queries:
        results = _search(q, max_results)
        for r in results:
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                r["query"] = q
                all_results.append(r)
        time.sleep(1)  # Be nice to SearXNG

    if not all_results:
        return json.dumps({"status": "empty", "message": "No news found. SearXNG may be slow."})

    return json.dumps({
        "status": "ok",
        "count": len(all_results),
        "results": all_results,
        "queries_searched": len(queries),
        "tip": "Review results. If something is interesting, use proactive_nudge to tell V or moltbook_post to share.",
    })


def register(ctx):
    ctx.register_tool(name="news_scan", toolset="evey_news", schema=SCHEMA, handler=handler)
    logger.info("evey-news loaded — AI news monitoring active")
