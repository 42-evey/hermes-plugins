"""Evey Cost Guard Plugin — budget enforcement + analytics via Langfuse.

Tracks API costs in real-time and enforces spending limits.
Queries Langfuse for actual cost data so numbers are real, not estimated.
Includes per-model analytics with token ratios and cost-saving recommendations.
"""

import json
import os
import time
import urllib.request
import urllib.error
from base64 import b64encode
from pathlib import Path

# Langfuse connection (same network)
LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "")
LANGFUSE_PUBLIC = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET = os.environ.get("LANGFUSE_SECRET_KEY", "")

# Budget file — persists across restarts
BUDGET_PATH = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))) / "cost-budget.json"

# Default daily budget (USD)
DEFAULT_DAILY_BUDGET = 1.00
DEFAULT_TASK_BUDGET = 0.25

CHECK_SCHEMA = {
    "name": "cost_check",
    "description": (
        "Check current API spending. Shows today's total cost, cost per model, "
        "remaining budget, and whether you're within limits. "
        "Use this BEFORE starting expensive operations like delegation or research. "
        "Returns: total_cost, budget_remaining, models breakdown, warning if over 80%."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "period": {
                "type": "string",
                "enum": ["today", "hour", "week"],
                "description": "Time period to check (default: today)",
            },
        },
    },
}

BUDGET_SCHEMA = {
    "name": "cost_set_budget",
    "description": (
        "Set or view spending budgets. Budgets are soft limits — you'll get "
        "warnings at 80% and a strong warning at 100%, but won't be blocked. "
        "Use 'view' to see current budgets, 'set' to change them."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["view", "set"],
                "description": "View or set budgets",
            },
            "daily_budget": {
                "type": "number",
                "description": "Daily spending limit in USD (e.g., 1.00)",
            },
            "task_budget": {
                "type": "number",
                "description": "Per-task spending limit in USD (e.g., 0.25)",
            },
        },
        "required": ["action"],
    },
}


def _check_langfuse_config():
    """Return an error dict if Langfuse is not configured, else None."""
    missing = []
    if not LANGFUSE_HOST:
        missing.append("LANGFUSE_HOST")
    if not LANGFUSE_PUBLIC:
        missing.append("LANGFUSE_PUBLIC_KEY")
    if not LANGFUSE_SECRET:
        missing.append("LANGFUSE_SECRET_KEY")
    if missing:
        return {
            "error": (
                f"Langfuse not configured — missing env vars: {', '.join(missing)}. "
                "Add them to the hermes-agent environment in docker-compose.yml."
            ),
            "total_cost": 0,
            "models": {},
        }
    return None


def _get_auth_header():
    """Base64 auth for Langfuse API."""
    creds = f"{LANGFUSE_PUBLIC}:{LANGFUSE_SECRET}"
    return b64encode(creds.encode()).decode()


def _load_budget():
    """Load budget config from disk."""
    if BUDGET_PATH.exists():
        try:
            return json.loads(BUDGET_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "daily_budget": DEFAULT_DAILY_BUDGET,
        "task_budget": DEFAULT_TASK_BUDGET,
    }


def _save_budget(budget):
    """Save budget config to disk."""
    BUDGET_PATH.parent.mkdir(parents=True, exist_ok=True)
    BUDGET_PATH.write_text(json.dumps(budget, indent=2))


def _query_langfuse_costs(from_timestamp):
    """Query Langfuse for traces since timestamp, return cost breakdown."""
    config_err = _check_langfuse_config()
    if config_err:
        return config_err

    auth = _get_auth_header()
    url = f"{LANGFUSE_HOST}/api/public/traces?fromTimestamp={from_timestamp}"

    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        return {"error": f"Langfuse query failed: {e}", "total_cost": 0, "models": {}}

    traces = data.get("data", [])
    models = {}
    total_cost = 0.0
    total_input = 0
    total_output = 0

    for trace in traces:
        usage = trace.get("usage") or {}
        model = (trace.get("metadata") or {}).get("model", "unknown")
        cost = usage.get("totalCost") or 0
        inp = usage.get("input") or 0
        out = usage.get("output") or 0

        if model not in models:
            models[model] = {"calls": 0, "cost": 0, "input_tokens": 0, "output_tokens": 0}
        models[model]["calls"] += 1
        models[model]["cost"] += cost
        models[model]["input_tokens"] += inp
        models[model]["output_tokens"] += out

        total_cost += cost
        total_input += inp
        total_output += out

    return {
        "total_cost": round(total_cost, 6),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_traces": len(traces),
        "models": {
            k: {**v, "cost": round(v["cost"], 6)}
            for k, v in sorted(models.items(), key=lambda x: -x[1]["cost"])
        },
    }


def handle_check(args, **kwargs):
    """Check current spending against budget."""
    try:
        period = args.get("period", "today")
        budget = _load_budget()

        # Calculate time range
        now = time.time()
        if period == "hour":
            from_ts = time.strftime("%Y-%m-%dT%H:00:00Z", time.gmtime(now - 3600))
        elif period == "week":
            from_ts = time.strftime("%Y-%m-%dT00:00:00Z", time.gmtime(now - 7 * 86400))
        else:  # today
            from_ts = time.strftime("%Y-%m-%dT00:00:00Z", time.gmtime())

        costs = _query_langfuse_costs(from_ts)

        if "error" in costs:
            return json.dumps(costs)

        daily_budget = budget.get("daily_budget", DEFAULT_DAILY_BUDGET)
        remaining = round(daily_budget - costs["total_cost"], 6)
        pct_used = (costs["total_cost"] / daily_budget * 100) if daily_budget > 0 else 0

        result = {
            "period": period,
            "total_cost": f"${costs['total_cost']:.4f}",
            "daily_budget": f"${daily_budget:.2f}",
            "remaining": f"${remaining:.4f}",
            "percent_used": f"{pct_used:.1f}%",
            "traces": costs["total_traces"],
            "tokens": {
                "input": costs["total_input_tokens"],
                "output": costs["total_output_tokens"],
            },
            "models": costs["models"],
        }

        # Add warnings
        if pct_used >= 100:
            result["warning"] = (
                "BUDGET EXCEEDED! You've spent more than your daily limit. "
                "Use only free/local models until tomorrow. "
                "Consider pausing non-essential delegations."
            )
        elif pct_used >= 80:
            result["warning"] = (
                "Approaching budget limit (80%+). "
                "Switch to free models for remaining tasks."
            )

        return json.dumps(result)

    except Exception as e:
        return json.dumps({"error": str(e)})


def handle_budget(args, **kwargs):
    """View or set budget limits."""
    try:
        action = args.get("action", "view")
        budget = _load_budget()

        if action == "view":
            return json.dumps({
                "daily_budget": f"${budget.get('daily_budget', DEFAULT_DAILY_BUDGET):.2f}",
                "task_budget": f"${budget.get('task_budget', DEFAULT_TASK_BUDGET):.2f}",
                "note": "Budgets are soft limits — warnings at 80% and 100%.",
            })

        elif action == "set":
            if "daily_budget" in args:
                budget["daily_budget"] = float(args["daily_budget"])
            if "task_budget" in args:
                budget["task_budget"] = float(args["task_budget"])
            _save_budget(budget)
            return json.dumps({
                "status": "updated",
                "daily_budget": f"${budget['daily_budget']:.2f}",
                "task_budget": f"${budget['task_budget']:.2f}",
            })

        return json.dumps({"error": f"Unknown action: {action}"})

    except Exception as e:
        return json.dumps({"error": str(e)})


# --- Analytics (merged from evey-cost-analytics) ---

LOCAL_MODELS = {"qwen35-4b", "qwen35-9b", "hermes3:8b", "dolphin3:8b", "dolphin-mistral:7b"}

ANALYTICS_SCHEMA = {
    "name": "cost_analytics",
    "description": (
        "Detailed cost analytics from Langfuse observations. "
        "Shows per-model breakdown: call count, input/output tokens, cost, "
        "token ratios, top consumer, and cost-saving recommendations. "
        "Use for deep dives into spending patterns."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "period": {
                "type": "string",
                "enum": ["today", "24h", "week"],
                "description": "Time period to analyze (default: 24h)",
            },
            "limit": {
                "type": "number",
                "description": "Max observations to analyze (default: 100, max: 500)",
            },
        },
    },
}


def _format_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _compute_ratio(input_tokens, output_tokens):
    if output_tokens == 0:
        return "N/A" if input_tokens == 0 else f"{input_tokens}:0"
    ratio = input_tokens / output_tokens
    return f"{ratio:.0f}:1" if ratio >= 1 else f"1:{1 / ratio:.0f}"


def handle_analytics(args, **kwargs):
    try:
        config_err = _check_langfuse_config()
        if config_err:
            return json.dumps(config_err)

        period = args.get("period", "24h")
        limit = min(args.get("limit", 100), 500)
        now = time.time()
        if period == "today":
            from_ts = time.strftime("%Y-%m-%dT00:00:00Z", time.gmtime())
        elif period == "week":
            from_ts = time.strftime("%Y-%m-%dT00:00:00Z", time.gmtime(now - 7 * 86400))
        else:
            from_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 86400))

        auth = _get_auth_header()
        url = (
            f"{LANGFUSE_HOST}/api/public/observations"
            f"?type=GENERATION&limit={limit}&fromTimestamp={from_ts}"
        )
        try:
            req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
            return json.dumps({"error": f"Langfuse query failed: {e}"})

        observations = data.get("data", [])
        model_stats = {}
        total_cost = 0.0
        total_input = 0
        total_output = 0

        for obs in observations:
            model = obs.get("model") or "unknown"
            usage = obs.get("usage") or {}
            cost = obs.get("calculatedTotalCost") or 0
            inp = usage.get("input") or usage.get("promptTokens") or 0
            out = usage.get("output") or usage.get("completionTokens") or 0

            if model not in model_stats:
                model_stats[model] = {"calls": 0, "input": 0, "output": 0, "cost": 0.0}
            model_stats[model]["calls"] += 1
            model_stats[model]["input"] += inp
            model_stats[model]["output"] += out
            model_stats[model]["cost"] += cost
            total_cost += cost
            total_input += inp
            total_output += out

        models_list = []
        for name, stats in sorted(model_stats.items(), key=lambda x: -x[1]["cost"]):
            avg_input = stats["input"] // stats["calls"] if stats["calls"] > 0 else 0
            models_list.append({
                "name": name, "calls": stats["calls"],
                "input": stats["input"], "output": stats["output"],
                "cost": round(stats["cost"], 6), "avg_input": avg_input,
                "io_ratio": _compute_ratio(stats["input"], stats["output"]),
            })

        # Recommendation
        recommendation = "Costs are minimal. Current model usage is efficient."
        if models_list and total_cost >= 0.001:
            expensive = max(models_list, key=lambda m: m["cost"])
            if expensive["cost"] > 0 and expensive["name"] not in LOCAL_MODELS:
                if expensive["avg_input"] < 2000:
                    recommendation = (
                        f"{expensive['name']} has small prompts (avg {_format_tokens(expensive['avg_input'])} input). "
                        "Consider using qwen35-4b for simple tasks to save costs."
                    )
                else:
                    recommendation = (
                        f"{expensive['name']} is the top spender at ${expensive['cost']:.4f}. "
                        "Review if all calls require this model's capability."
                    )

        return json.dumps({
            "period": period, "observations_analyzed": len(observations),
            "total_cost": round(total_cost, 6),
            "total_input_tokens": total_input, "total_output_tokens": total_output,
            "input_output_ratio": _compute_ratio(total_input, total_output),
            "models": models_list, "recommendation": recommendation,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def register(ctx):
    ctx.register_tool(
        name="cost_check",
        toolset="evey_cost_guard",
        schema=CHECK_SCHEMA,
        handler=handle_check,
    )
    ctx.register_tool(
        name="cost_set_budget",
        toolset="evey_cost_guard",
        schema=BUDGET_SCHEMA,
        handler=handle_budget,
    )
    ctx.register_tool(
        name="cost_analytics",
        toolset="evey_cost_guard",
        schema=ANALYTICS_SCHEMA,
        handler=handle_analytics,
    )
