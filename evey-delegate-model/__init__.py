"""Evey Delegate Model Plugin — call any model directly via LiteLLM.

Bypasses hermes delegate_task entirely (which has bugs and requires
parent_agent context). Instead, calls LiteLLM's OpenAI-compatible API
directly. Works from ANY context: Telegram, cron, plugins, anywhere.

Supports autonomous fallback — tries up to 4 models if the first fails.
Each model gets up to 3 retries with exponential backoff before moving
to the next model in the chain.
"""

import concurrent.futures
import json
import logging
import os
import time
import urllib.request
import urllib.error

logger = logging.getLogger("evey.delegate_model")

# Telemetry integration — track all delegations
try:
    from importlib import import_module
    _telemetry = import_module("evey-telemetry")
except Exception:
    _telemetry = None

LITELLM_URL = os.environ.get("OPENAI_BASE_URL", "")
LITELLM_KEY = os.environ.get("OPENAI_API_KEY", "")

# Max retries per individual model before falling through to the next
MAX_RETRIES_PER_MODEL = 3

SCHEMA = {
    "name": "delegate_with_model",
    "description": (
        "Delegate a task to another model via LiteLLM. Supports smart routing, "
        "sensitivity detection, and automatic fallback across free/local/paid models."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "model": {
                "type": "string",
                "description": "Model to use (e.g., 'nemotron-free', 'deepseek-r1-local', 'qwen-coder-free')",
            },
            "goal": {
                "type": "string",
                "description": "The task or question for the model",
            },
            "context": {
                "type": "string",
                "description": "Extra context to include (optional)",
            },
            "max_tokens": {
                "type": "number",
                "description": "Max response length (default: 2000)",
            },
        },
        "required": ["model", "goal"],
    },
}

FALLBACK_CHAIN = [
    "mimo-v2-pro",       # FREE via OpenClaw — confirmed working with reasoning:exclude
    "nemotron-free",     # CONFIRMED working, no think-block issues
    "llama70b-free",     # works when not rate limited
]

# Smart routing — ALL delegation goes to FREE CLOUD models
# NEVER use local models for delegation (GPU overload → black screen)
# Local Ollama is ONLY for hermes-agent brain's smart routing (cheap turns)
# Priority: confirmed-working models first, then others as fallback
# mimo-v2-pro: FREE, 1T params, works with reasoning:exclude
# nemotron-free: FREE, reliable, no think-block issues
# llama70b-free: FREE, reliable but rate-limited
# step-flash-free: FREE, works with reasoning:exclude (MoE 196B)
# qwen-coder-free: FREE, works with reasoning:exclude (best coder)
TASK_ROUTING = {
    "code": ["mimo-v2-pro", "qwen-coder-free", "nemotron-free"],
    "reasoning": ["mimo-v2-pro", "step-flash-free", "nemotron-free"],
    "research": ["mimo-v2-pro", "nemotron-free", "llama70b-free"],
    "creative": ["mimo-v2-pro", "nemotron-free", "llama70b-free"],
    "summary": ["mimo-v2-pro", "nemotron-free", "nemotron-nano-free"],
    "analysis": ["mimo-v2-pro", "step-flash-free", "nemotron-free"],
    "uncensored": ["mimo-v2-pro", "llama70b-free"],
    "vision": ["mimo-v2-pro"],
    "multimodal": ["mimo-v2-pro"],
}

# Sensitive keywords — route to LOCAL ONLY (never external)
SENSITIVE_PATTERNS = [
    "password", "api key", "api_key", "secret key", "secret_key",
    "token", "credential", "private key",
    "confidential", ".env", "ssh key", "certificate",
    "auth token", "bearer", "my password", "my credentials",
]
# Sensitive data → ONE local model only (never spin up multiple local models)
LOCAL_ONLY_MODELS = ["qwen35-4b"]  # Single small model, minimal GPU impact


def _detect_task_type(goal):
    """Auto-detect task type from the goal text."""
    g = goal.lower()
    if any(w in g for w in ["code", "function", "script", "bug", "implement", "refactor", "python", "javascript"]):
        return "code"
    if any(w in g for w in ["reason", "think", "logic", "math", "proof", "step by step", "analyze why"]):
        return "reasoning"
    if any(w in g for w in ["research", "find", "search", "latest", "news", "what is", "compare"]):
        return "research"
    if any(w in g for w in ["write", "story", "creative", "poem", "imagine", "roleplay"]):
        return "creative"
    if any(w in g for w in ["summarize", "summary", "tldr", "brief", "compress", "shorten"]):
        return "summary"
    if any(w in g for w in ["uncensored", "unfiltered", "no restrictions", "honest opinion"]):
        return "uncensored"
    if any(w in g for w in ["image", "picture", "photo", "vision", "see", "look at", "describe this image", "video"]):
        return "vision"
    return "research"  # default


def _is_sensitive(text):
    """Check if the task contains sensitive data that shouldn't go to external models."""
    t = text.lower()
    return any(p in t for p in SENSITIVE_PATTERNS)


def _call_model(model, prompt, max_tokens=2000):
    """Direct call to LiteLLM with retry logic — works from any context.

    Retries up to MAX_RETRIES_PER_MODEL times with exponential backoff.
    Treats empty/whitespace-only responses as failures that trigger retry.
    Returns (content, usage, attempts) on success.
    Raises on total failure after all retries exhausted.
    """
    last_error = None
    for attempt in range(1, MAX_RETRIES_PER_MODEL + 1):
        try:
            data = json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.7,
            }).encode()

            req = urllib.request.Request(
                f"{LITELLM_URL}/chat/completions",
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {LITELLM_KEY}",
                },
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())

            msg = result["choices"][0]["message"]
            content = msg.get("content", "") or ""
            usage = result.get("usage", {})

            # Recover from think-only responses (content=null, reasoning in separate field)
            # Models like nemotron, deepseek-r1 return reasoning_content but no content
            if not content.strip():
                # Check all known reasoning fields
                reasoning = (
                    msg.get("reasoning_content")
                    or msg.get("reasoning")
                    or (msg.get("provider_specific_fields") or {}).get("reasoning_content")
                    or (msg.get("provider_specific_fields") or {}).get("reasoning")
                )
                if reasoning and str(reasoning).strip():
                    content = str(reasoning).strip()
                    logger.info(f"{model} returned think-only response, recovered from reasoning_content ({len(content)} chars)")

            # Treat empty/whitespace-only responses as failures (after reasoning recovery)
            if not content or not content.strip():
                last_error = f"{model} returned empty response (attempt {attempt})"
                logger.warning(last_error)
                if attempt < MAX_RETRIES_PER_MODEL:
                    time.sleep(2 ** (attempt - 1))  # 1s, 2s backoff
                    continue
                raise RuntimeError(last_error)

            if attempt > 1:
                logger.info(f"{model} succeeded on retry {attempt}")
            return content, usage, attempt

        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, OSError, KeyError) as e:
            last_error = f"{model} attempt {attempt}/{MAX_RETRIES_PER_MODEL}: {e}"
            logger.warning(last_error)
            if attempt < MAX_RETRIES_PER_MODEL:
                time.sleep(2 ** (attempt - 1))  # 1s, 2s backoff
                continue
            raise RuntimeError(last_error) from e


def handler(args, **kwargs):
    try:
        model = args.get("model", "")
        goal = args.get("goal", "")
        context = args.get("context", "")
        max_tokens = args.get("max_tokens", 2000)

        full_text = f"{goal} {context}"
        prompt = goal
        if context:
            prompt = f"CONTEXT:\n{context}\n\nTASK:\n{goal}\n\nIMPORTANT: Always respond in English."

        # SECURITY: Check for sensitive content — force local models
        sensitive = _is_sensitive(full_text)
        if sensitive:
            models_to_try = LOCAL_ONLY_MODELS
            routing_reason = "SENSITIVE content detected — using LOCAL models only"
        elif model:
            # User specified a model — respect it but add fallbacks
            models_to_try = [model] + [m for m in FALLBACK_CHAIN if m != model]
            routing_reason = f"User-specified: {model}"
        else:
            # Smart routing — pick best model for task type
            task_type = _detect_task_type(goal)
            models_to_try = TASK_ROUTING.get(task_type, FALLBACK_CHAIN)
            routing_reason = f"Auto-routed: {task_type} → {models_to_try[0]}"

        last_error = None
        models_tried = []
        for attempt_model in models_to_try[:4]:
            try:
                content, usage, retries_used = _call_model(attempt_model, prompt, max_tokens)
                models_tried.append({"model": attempt_model, "status": "success", "retries": retries_used})
                logger.info(
                    f"Delegation succeeded: model={attempt_model}, "
                    f"retries={retries_used}, tokens={usage.get('total_tokens', 0)}"
                )
                # Emit telemetry event
                if _telemetry:
                    task_type = _detect_task_type(goal)
                    _telemetry.track_delegation(
                        model=attempt_model, task_type=task_type,
                        success=True, tokens=usage.get("total_tokens", 0),
                    )
                return json.dumps({
                    "status": "success",
                    "model_used": attempt_model,
                    "model_retries": retries_used,
                    "models_tried": models_tried,
                    "routing": routing_reason,
                    "sensitive": sensitive,
                    "result": content,
                    "tokens": usage.get("total_tokens", 0),
                })
            except Exception as e:
                last_error = str(e)
                models_tried.append({"model": attempt_model, "status": "failed", "error": last_error[:100]})
                logger.warning(f"Model {attempt_model} exhausted all retries: {last_error[:100]}")
                continue

        logger.error(
            f"All models failed after retries. Tried: {[m['model'] for m in models_tried]}"
        )
        if _telemetry:
            _telemetry.track_error("delegation", f"All models failed: {[m['model'] for m in models_tried]}")
        return json.dumps({
            "status": "failed",
            "error": f"All {min(4, len(models_to_try))} models failed (each retried {MAX_RETRIES_PER_MODEL}x)",
            "last_error": last_error[:200] if last_error else "unknown",
            "models_tried": models_tried,
        })

    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


PARALLEL_SCHEMA = {
    "name": "delegate_parallel",
    "description": (
        "Run MULTIPLE tasks across different models IN PARALLEL. "
        "Each task gets its own model and runs concurrently. "
        "Use this for research, analysis, or any work that can be split up.\n\n"
        "Example: research 3 topics simultaneously, or get 3 different model opinions.\n"
        "Max 3 parallel tasks. Each task follows the same routing as delegate_with_model."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "description": "List of tasks to run in parallel",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "Short label for this task"},
                        "model": {"type": "string", "description": "Model to use (optional — auto-routes if empty)"},
                        "goal": {"type": "string", "description": "The task or question"},
                        "context": {"type": "string", "description": "Extra context (optional)"},
                        "max_tokens": {"type": "number", "description": "Max response (default: 2000)"},
                    },
                    "required": ["id", "goal"],
                },
            },
        },
        "required": ["tasks"],
    },
}


def _run_single_task(task):
    """Run a single delegated task — used by parallel handler."""
    try:
        result = json.loads(handler(task))
        result["task_id"] = task.get("id", "?")
        return result
    except Exception as e:
        return {"task_id": task.get("id", "?"), "status": "error", "error": str(e)}


def parallel_handler(args, **kwargs):
    tasks = args.get("tasks", [])
    if not tasks:
        return json.dumps({"status": "error", "error": "No tasks provided"})
    if len(tasks) > 6:
        tasks = tasks[:6]

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(tasks), 3)) as pool:
        futures = {pool.submit(_run_single_task, t): t for t in tasks}
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    # Sort by original task order
    task_ids = [t.get("id", "") for t in tasks]
    results.sort(key=lambda r: task_ids.index(r.get("task_id", "")) if r.get("task_id", "") in task_ids else 99)

    succeeded = sum(1 for r in results if r.get("status") == "success")
    return json.dumps({
        "status": "complete",
        "total": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    })


def register(ctx):
    ctx.register_tool(
        name="delegate_with_model",
        toolset="evey_delegate",
        schema=SCHEMA,
        handler=handler,
    )
    ctx.register_tool(
        name="delegate_parallel",
        toolset="evey_delegate",
        schema=PARALLEL_SCHEMA,
        handler=parallel_handler,
    )
