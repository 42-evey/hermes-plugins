"""Shared utilities for Evey plugins — LLM calls, HTTP helpers, retry logic.

All plugins should use these instead of rolling their own urllib code.
- call_llm(): Quick LLM call, returns string or None
- call_model(): Full LLM call with usage info, retries, reasoning recovery
- http_get(): GET with error handling
- http_post_json(): POST JSON with error handling
"""

import json
import logging
import os
import time
import urllib.request
import urllib.error

logger = logging.getLogger("evey.utils")

LITELLM_URL = os.environ.get("OPENAI_BASE_URL", "")
LITELLM_KEY = os.environ.get("OPENAI_API_KEY", "")


def call_llm(model, prompt, max_tokens=200, temperature=0.3, retries=2):
    """Quick LLM call. Returns content string or None on failure."""
    result = call_model(model, prompt, max_tokens=max_tokens, temperature=temperature, retries=retries)
    return result.get("content") if result else None


def call_model(model, prompt, max_tokens=2000, temperature=0.7, retries=2, timeout=60):
    """Full LLM call via LiteLLM with retry, reasoning recovery, and usage tracking.

    Returns dict: {"content": str, "tokens": int, "model": str, "attempts": int}
    Returns None on total failure.
    """
    data = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode()

    for attempt in range(1, retries + 2):
        try:
            req = urllib.request.Request(
                f"{LITELLM_URL}/chat/completions",
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {LITELLM_KEY}",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read())

            msg = result["choices"][0]["message"]
            content = msg.get("content", "") or ""
            usage = result.get("usage", {})

            # Reasoning recovery — extract from think-only responses
            if not content.strip():
                reasoning = (
                    msg.get("reasoning_content")
                    or msg.get("reasoning")
                    or (msg.get("provider_specific_fields") or {}).get("reasoning_content")
                )
                if reasoning and str(reasoning).strip():
                    content = str(reasoning).strip()

            if not content.strip():
                if attempt <= retries:
                    time.sleep(2 ** (attempt - 1))
                    continue
                return None

            return {
                "content": content.strip(),
                "tokens": usage.get("total_tokens", 0),
                "model": model,
                "attempts": attempt,
            }

        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, KeyError) as e:
            if attempt <= retries:
                time.sleep(2 ** (attempt - 1))
                continue
            logger.warning(f"call_model({model}) failed after {retries + 1} attempts: {e}")
            return None
        except Exception as e:
            logger.warning(f"call_model({model}) unexpected error: {e}")
            return None


def http_get(url, timeout=10):
    """HTTP GET with error handling. Returns response text or None."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read().decode()
    except Exception:
        return None


def http_get_json(url, timeout=10):
    """HTTP GET returning parsed JSON dict or None."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def http_post_json(url, data_dict, headers=None, timeout=10):
    """HTTP POST JSON with error handling. Returns parsed dict or None."""
    try:
        data = json.dumps(data_dict).encode()
        hdrs = {"Content-Type": "application/json"}
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, data=data, headers=hdrs)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        return None
