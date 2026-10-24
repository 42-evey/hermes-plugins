"""Cache Plugin — smart caching for delegation results.

Wraps delegate_with_model with a SHA-256 content-addressed cache.
Cache hits return instantly at zero cost. 24-hour TTL, 100-entry LRU.

Saves tokens on recurring questions: daily checks, repeated research,
status queries. Cost-aware caching for AI delegation.

Calls go through Hermes (ctx.llm) on the user's active model. A `model`
argument is honoured only when the operator sets
plugins.entries.evey-cache.llm.allow_model_override: true in config.yaml;
otherwise the active model is used and the result says so.
"""

import hashlib
import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("evey.cache")

_CTX = None  # set by register(); LLM calls go through ctx.llm

HERMES_HOME = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))
CACHE_DIR = HERMES_HOME / "workspace" / "orchestrator"
CACHE_FILE = CACHE_DIR / "delegation-cache.json"

# Cache settings
MAX_ENTRIES = 100
TTL_SECONDS = 86400  # 24 hours

SCHEMA = {
    "name": "cached_delegate",
    "description": (
        "Delegate a task with smart caching. Hashes model+goal to check a local "
        "cache first. If a matching result exists within 24h, returns it instantly "
        "at ZERO cost. Otherwise delegates normally and caches the result.\n\n"
        "Use for: recurring questions, daily status checks, repeated research topics, "
        "any delegation where the answer doesn't change frequently.\n\n"
        "Cache: 100 entries max, 24h TTL, LRU eviction."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "model": {
                "type": "string",
                "description": "Model id to use (optional; default is the active model)",
            },
            "goal": {
                "type": "string",
                "description": "The task or question",
            },
            "context": {
                "type": "string",
                "description": "Extra context (optional — NOT included in cache key)",
            },
            "max_tokens": {
                "type": "number",
                "description": "Max response length (default: 2000)",
            },
            "bypass_cache": {
                "type": "boolean",
                "description": "Force a fresh delegation, ignoring cache (default: false)",
            },
        },
        "required": ["goal"],
    },
}


def _cache_key(model, goal):
    """Generate a cache key from model + goal."""
    raw = f"{model.strip().lower()}::{goal.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _load_cache():
    """Load cache from disk. Returns dict."""
    if not CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(CACHE_FILE.read_text())
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_cache(cache):
    """Save cache to disk with LRU eviction."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Evict expired entries
    now = time.time()
    cache = {
        k: v for k, v in cache.items()
        if now - v.get("cached_at", 0) < TTL_SECONDS
    }

    # LRU eviction if over max
    if len(cache) > MAX_ENTRIES:
        # Sort by last_accessed (oldest first), keep newest MAX_ENTRIES
        sorted_keys = sorted(
            cache.keys(),
            key=lambda k: cache[k].get("last_accessed", 0),
        )
        for k in sorted_keys[:len(cache) - MAX_ENTRIES]:
            del cache[k]

    try:
        CACHE_FILE.write_text(json.dumps(cache, indent=2))
    except OSError as e:
        logger.warning(f"Failed to save cache: {e}")

    return cache


def _delegate_fresh(model, goal, context, max_tokens):
    """One ctx.llm call. Returns (content, tokens, model_used, note).

    With no model, runs on the user's active model. With a model, asks Hermes
    for it; that needs the operator grant
    plugins.entries.evey-cache.llm.allow_model_override: true, and without it
    the call falls back to the active model and says so in the note.
    """
    if _CTX is None:
        raise RuntimeError("plugin not registered")
    from agent.plugin_llm import PluginLlmTrustError

    prompt = goal
    if context:
        prompt = f"CONTEXT:\n{context}\n\nTASK:\n{goal}\n\nIMPORTANT: Always respond in English."

    kwargs = dict(messages=[{"role": "user", "content": prompt}], max_tokens=int(max_tokens),
                  temperature=0.7, timeout=300, purpose="evey-cache")
    note = None
    try:
        result = _CTX.llm.complete(model=model, **kwargs) if model else _CTX.llm.complete(**kwargs)
    except PluginLlmTrustError:
        note = (f"model '{model}' not allowed (set plugins.entries.evey-cache.llm."
                "allow_model_override: true); used the active model")
        result = _CTX.llm.complete(**kwargs)
    content = (result.text or "").strip()
    if not content:
        raise RuntimeError("model returned an empty response")
    return content, result.usage.total_tokens or 0, result.model, note


def handler(args, **kwargs):
    try:
        model = (args.get("model") or "").strip()
        goal = args.get("goal", "")
        context = args.get("context", "")
        max_tokens = args.get("max_tokens", 2000)
        bypass = args.get("bypass_cache", False)

        if not goal:
            return json.dumps({"status": "error", "error": "No goal provided"})

        key = _cache_key(model, goal)
        now = time.time()

        # Check cache (unless bypass requested)
        if not bypass:
            cache = _load_cache()
            if key in cache:
                entry = cache[key]
                age_sec = now - entry.get("cached_at", 0)

                if age_sec < TTL_SECONDS:
                    # Cache hit
                    entry["last_accessed"] = now
                    entry["hit_count"] = entry.get("hit_count", 0) + 1
                    _save_cache(cache)

                    logger.info(
                        f"Cache HIT: key={key}, age={age_sec:.0f}s, "
                        f"hits={entry['hit_count']}, model={model}"
                    )

                    return json.dumps({
                        "status": "cache_hit",
                        "result": entry["result"],
                        "model": entry.get("model", model),
                        "cached_at": entry.get("cached_at_str", ""),
                        "age_seconds": round(age_sec),
                        "hit_count": entry["hit_count"],
                        "tokens_saved": entry.get("tokens", 0),
                        "cost": "zero (cached)",
                    })

        # Cache miss — delegate fresh
        logger.info(f"Cache MISS: key={key}, delegating to {model or 'active model'}")
        try:
            content, tokens, model_used, note = _delegate_fresh(model, goal, context, max_tokens)
        except Exception as e:
            return json.dumps({
                "status": "delegation_failed",
                "error": str(e)[:300],
                "cache_key": key,
            })

        if not content or not content.strip():
            return json.dumps({
                "status": "empty_response",
                "error": "Model returned empty response",
                "model": model,
            })

        # Store in cache
        cache = _load_cache()
        cache[key] = {
            "result": content,
            "model": model_used,
            "goal_preview": goal[:200],
            "tokens": tokens,
            "cached_at": now,
            "cached_at_str": time.strftime("%Y-%m-%d %H:%M:%S"),
            "last_accessed": now,
            "hit_count": 0,
        }
        cache = _save_cache(cache)

        out = {
            "status": "cache_miss",
            "result": content,
            "model": model_used,
            "tokens": tokens,
            "cache_key": key,
            "cache_size": len(cache),
        }
        if note:
            out["note"] = note
        return json.dumps(out)

    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


def register(ctx):
    global _CTX
    _CTX = ctx
    ctx.register_tool(
        name="cached_delegate",
        toolset="evey_cache",
        schema=SCHEMA,
        handler=handler,
    )
