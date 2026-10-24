"""Evey Delegate Model Plugin — delegate a task to a model via Hermes.

Bypasses hermes delegate_task entirely (which has bugs and requires
parent_agent context). Instead, makes one out-of-band completion through
Hermes (ctx.llm). Works from ANY context: Telegram, cron, plugins, anywhere.

By default every call runs on the user's active provider and model. A
`model` argument is honoured only when the operator grants it in
config.yaml:

    plugins:
      entries:
        evey-delegate-model:
          llm:
            allow_model_override: true

Without the grant the call falls back to the active model and the result
says so. Sensitive content never uses a `model` override.

Each call gets up to 3 retries with exponential backoff.
"""

import concurrent.futures
import json
import logging
import os
import time

logger = logging.getLogger("evey.delegate_model")

# Telemetry integration — track all delegations
try:
    from importlib import import_module
    _telemetry = import_module("evey-telemetry")
except Exception:
    _telemetry = None

_CTX = None  # set by register(); LLM calls go through ctx.llm

# Max retries per call
MAX_RETRIES_PER_MODEL = 3

SCHEMA = {
    "name": "delegate_with_model",
    "description": (
        "Delegate a task to a model in a separate, out-of-band call. Runs on the "
        "active model unless a model is given and allowed; detects sensitive content."
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
        "required": ["goal"],
    },
}

# Sensitive keywords — never sent to a model override
SENSITIVE_PATTERNS = [
    "password", "api key", "api_key", "secret key", "secret_key",
    "token", "credential", "private key",
    "confidential", ".env", "ssh key", "certificate",
    "auth token", "bearer", "my password", "my credentials",
]


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
    """Check if the task contains sensitive data that shouldn't go to a model override."""
    t = text.lower()
    return any(p in t for p in SENSITIVE_PATTERNS)


def _call_model(model, prompt, max_tokens=2000):
    """One ctx.llm call with retry logic — works from any context.

    model=None runs on the user's active model. Retries up to
    MAX_RETRIES_PER_MODEL times with exponential backoff; empty responses
    count as failures. Returns (content, tokens, attempts, model_used, note).
    Raises on total failure after all retries exhausted.
    """
    if _CTX is None:
        raise RuntimeError("plugin not registered")
    from agent.plugin_llm import PluginLlmTrustError

    note = None
    last_error = None
    for attempt in range(1, MAX_RETRIES_PER_MODEL + 1):
        kwargs = dict(messages=[{"role": "user", "content": prompt}], max_tokens=int(max_tokens),
                      temperature=0.7, timeout=300, purpose="evey-delegate-model")
        if model:
            kwargs["model"] = model
        try:
            try:
                result = _CTX.llm.complete(**kwargs)
            except PluginLlmTrustError:
                note = (f"model '{model}' not allowed (set plugins.entries.evey-delegate-model.llm."
                        "allow_model_override: true); used the active model")
                model = None
                kwargs.pop("model", None)
                result = _CTX.llm.complete(**kwargs)
        except Exception as e:
            last_error = f"attempt {attempt}/{MAX_RETRIES_PER_MODEL}: {e}"
            logger.warning(last_error)
            if attempt < MAX_RETRIES_PER_MODEL:
                time.sleep(2 ** (attempt - 1))  # 1s, 2s backoff
            continue

        content = (result.text or "").strip()
        if content:
            if attempt > 1:
                logger.info(f"{result.model} succeeded on retry {attempt}")
            return content, result.usage.total_tokens or 0, attempt, result.model, note

        last_error = f"{result.model} returned empty response (attempt {attempt})"
        logger.warning(last_error)
        if attempt < MAX_RETRIES_PER_MODEL:
            time.sleep(2 ** (attempt - 1))
    raise RuntimeError(last_error or "no response")


def handler(args, **kwargs):
    try:
        model = (args.get("model") or "").strip()
        goal = args.get("goal", "")
        context = args.get("context", "")
        max_tokens = args.get("max_tokens", 2000)

        if not goal:
            return json.dumps({"status": "error", "error": "No goal provided"})

        full_text = f"{goal} {context}"
        prompt = goal
        if context:
            prompt = f"CONTEXT:\n{context}\n\nTASK:\n{goal}\n\nIMPORTANT: Always respond in English."

        task_type = _detect_task_type(goal)

        # SECURITY: sensitive content never goes to a model override
        sensitive = _is_sensitive(full_text)
        if sensitive:
            use_model = None
            routing_reason = "SENSITIVE content detected — active model only, no override"
        elif model:
            use_model = model
            routing_reason = f"User-specified: {model}"
        else:
            use_model = None
            routing_reason = f"Active model ({task_type} task)"

        try:
            content, tokens, retries_used, model_used, note = _call_model(use_model, prompt, max_tokens)
        except Exception as e:
            last_error = str(e)
            logger.error(f"Delegation failed after retries: {last_error[:200]}")
            if _telemetry:
                _telemetry.track_error("delegation", f"Delegation failed: {last_error[:100]}")
            return json.dumps({
                "status": "failed",
                "error": f"Delegation failed (retried {MAX_RETRIES_PER_MODEL}x)",
                "last_error": last_error[:200],
                "routing": routing_reason,
            })

        logger.info(f"Delegation succeeded: model={model_used}, retries={retries_used}, tokens={tokens}")
        # Emit telemetry event
        if _telemetry:
            _telemetry.track_delegation(
                model=model_used, task_type=task_type,
                success=True, tokens=tokens,
            )
        out = {
            "status": "success",
            "model_used": model_used,
            "model_retries": retries_used,
            "routing": routing_reason,
            "sensitive": sensitive,
            "result": content,
            "tokens": tokens,
        }
        if note:
            out["note"] = note
        return json.dumps(out)

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
    global _CTX
    _CTX = ctx
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
