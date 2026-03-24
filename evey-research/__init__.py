"""Evey Research Plugin — real web research with SearXNG + Crawl4AI.

Provides tools for searching the web, extracting page content,
and saving findings to the knowledge library with proper citations.
Uses the stack's own SearXNG and Crawl4AI services.
"""

import json
import os
import time
import urllib.request
import urllib.error
import urllib.parse

SEARXNG_URL = os.environ.get("SEARXNG_URL", "")
CRAWL4AI_URL = "http://hermes-crawl4ai:11235"


WEB_SEARCH_SCHEMA = {
    "name": "web_research",
    "description": (
        "Search the web using SearXNG and return real results with URLs. "
        "Use for: finding current information, verifying facts, discovering resources. "
        "Returns titles, URLs, and snippets from real web pages."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query",
            },
            "max_results": {
                "type": "number",
                "description": "Max results to return (default: 5, max: 15)",
            },
            "categories": {
                "type": "string",
                "description": "Search categories: general, science, it, news (default: general)",
            },
        },
        "required": ["query"],
    },
}

EXTRACT_SCHEMA = {
    "name": "web_extract",
    "description": (
        "Extract clean text content from a URL using Crawl4AI. "
        "Use for: reading articles, documentation, papers, blog posts. "
        "Returns markdown-formatted text content."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "URL to extract content from",
            },
            "max_chars": {
                "type": "number",
                "description": "Max characters to return (default: 5000)",
            },
        },
        "required": ["url"],
    },
}

SAVE_FINDING_SCHEMA = {
    "name": "save_finding",
    "description": (
        "Save a research finding to the knowledge library with proper citation. "
        "Findings are saved as markdown files in ~/.hermes/knowledge/."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "Topic name (used as filename)",
            },
            "content": {
                "type": "string",
                "description": "Research content in markdown format",
            },
            "sources": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of source URLs",
            },
        },
        "required": ["topic", "content"],
    },
}


def search_handler(args, **kwargs):
    try:
        query = args.get("query", "")
        max_results = min(args.get("max_results", 5), 15)
        categories = args.get("categories", "general")

        if not SEARXNG_URL:
            return json.dumps({"error": "SEARXNG_URL not configured"})

        params = urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "categories": categories,
        })
        url = f"{SEARXNG_URL}/search?{params}"
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "X-Forwarded-For": "127.0.0.1",
        })

        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        results = []
        for r in data.get("results", [])[:max_results]:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", "")[:300],
                "engine": r.get("engine", ""),
            })

        return json.dumps({
            "query": query,
            "count": len(results),
            "results": results,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def extract_handler(args, **kwargs):
    try:
        url = args.get("url", "")
        max_chars = args.get("max_chars", 5000)

        if not url:
            return json.dumps({"error": "No URL provided"})

        # Try Crawl4AI first
        try:
            payload = json.dumps({
                "urls": [url],
                "word_count_threshold": 10,
            }).encode()
            req = urllib.request.Request(
                f"{CRAWL4AI_URL}/crawl",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())

            if data.get("results"):
                content = data["results"][0].get("markdown", "")
                if content:
                    return json.dumps({
                        "url": url,
                        "content": content[:max_chars],
                        "chars": len(content),
                        "source": "crawl4ai",
                    })
        except Exception:
            pass

        # Fallback: direct fetch with urllib
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; Evey/1.0; +https://evey.cc)",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")

        # Basic HTML stripping
        import re
        text = re.sub(r'<script[^>]*>.*?</script>', '', raw, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()

        return json.dumps({
            "url": url,
            "content": text[:max_chars],
            "chars": len(text),
            "source": "direct",
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def save_handler(args, **kwargs):
    try:
        topic = args.get("topic", "untitled")
        content = args.get("content", "")
        sources = args.get("sources", [])

        # Sanitize filename
        import re
        safe_name = re.sub(r'[^\w\-]', '-', topic.lower()).strip('-')
        if not safe_name:
            safe_name = "untitled"

        knowledge_dir = os.path.expanduser("~/.hermes/knowledge")
        os.makedirs(knowledge_dir, exist_ok=True)

        filepath = os.path.join(knowledge_dir, f"{safe_name}.md")

        # Build document
        doc = f"# {topic}\n\n"
        doc += f"*Researched: {time.strftime('%Y-%m-%d %H:%M %Z')}*\n\n"
        doc += content + "\n"

        if sources:
            doc += "\n## Sources\n\n"
            for s in sources:
                doc += f"- {s}\n"

        with open(filepath, "w") as f:
            f.write(doc)

        return json.dumps({
            "status": "saved",
            "path": filepath,
            "topic": topic,
            "chars": len(doc),
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def register(ctx):
    ctx.register_tool(name="web_research", toolset="evey_research", schema=WEB_SEARCH_SCHEMA, handler=search_handler)
    ctx.register_tool(name="web_extract", toolset="evey_research", schema=EXTRACT_SCHEMA, handler=extract_handler)
    ctx.register_tool(name="save_finding", toolset="evey_research", schema=SAVE_FINDING_SCHEMA, handler=save_handler)
