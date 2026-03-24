"""Evey Delegation Score Plugin — track delegation quality per model.

Two tools:
- delegation_log: Record a quality score after validate_output runs.
- delegation_stats: View aggregate stats per model, best/worst, recommendations.

Stores data in ~/.hermes/workspace/delegation-scores.jsonl (one JSON object per line).
Stdlib only — no external dependencies.
"""

import json
import os
from datetime import datetime, timedelta

SCORES_FILE = os.path.expanduser("~/.hermes/workspace/delegation-scores.jsonl")

VALID_TASK_TYPES = {"code", "research", "analysis", "creative", "summary"}

LOG_SCHEMA = {
    "name": "delegation_log",
    "description": (
        "Record a delegation quality score after validate_output. "
        "Call this to build up per-model quality data over time. "
        "Parameters: model, task_type (code/research/analysis/creative/summary), "
        "score (0-10), tokens_used."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "model": {
                "type": "string",
                "description": "Which model was used (e.g. 'mimo-v2-pro', 'nemotron-free')",
            },
            "task_type": {
                "type": "string",
                "enum": ["code", "research", "analysis", "creative", "summary"],
                "description": "Type of task: code, research, analysis, creative, or summary",
            },
            "score": {
                "type": "number",
                "description": "Quality score from validate_output (0-10)",
            },
            "tokens_used": {
                "type": "number",
                "description": "Total tokens consumed by the delegation",
            },
        },
        "required": ["model", "task_type", "score", "tokens_used"],
    },
}

STATS_SCHEMA = {
    "name": "delegation_stats",
    "description": (
        "View aggregate delegation quality stats per model. "
        "Shows avg score, call count, success rate, avg tokens, "
        "best model per task type, worst performers, and recommendation. "
        "Period: 'all' (default), 'today', or 'week'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "period": {
                "type": "string",
                "enum": ["all", "today", "week"],
                "description": "Time period to analyze (default: 'all')",
            },
        },
        "required": [],
    },
}


def _ensure_dir():
    """Create the workspace directory if it doesn't exist."""
    d = os.path.dirname(SCORES_FILE)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


def _read_entries(period="all"):
    """Read and filter JSONL entries by time period."""
    if not os.path.exists(SCORES_FILE):
        return []

    now = datetime.utcnow()
    if period == "today":
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        cutoff = now - timedelta(days=7)
    else:
        cutoff = None

    entries = []
    with open(SCORES_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if cutoff:
                ts = entry.get("timestamp", "")
                try:
                    entry_time = datetime.fromisoformat(ts)
                    if entry_time < cutoff:
                        continue
                except (ValueError, TypeError):
                    continue

            entries.append(entry)

    return entries


def log_handler(args, **kwargs):
    try:
        model = args.get("model", "").strip()
        task_type = args.get("task_type", "").strip().lower()
        score = args.get("score")
        tokens_used = args.get("tokens_used", 0)

        # Validate inputs
        if not model:
            return json.dumps({"status": "error", "error": "model is required"})
        if task_type not in VALID_TASK_TYPES:
            return json.dumps({
                "status": "error",
                "error": f"task_type must be one of: {', '.join(sorted(VALID_TASK_TYPES))}",
            })
        if score is None:
            return json.dumps({"status": "error", "error": "score is required"})

        score = max(0, min(10, int(score)))
        tokens_used = max(0, int(tokens_used))

        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "model": model,
            "task_type": task_type,
            "score": score,
            "tokens_used": tokens_used,
        }

        _ensure_dir()
        with open(SCORES_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")

        return json.dumps({
            "status": "logged",
            "entry": entry,
        })

    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


def stats_handler(args, **kwargs):
    try:
        period = args.get("period", "all").strip().lower()
        if period not in ("all", "today", "week"):
            period = "all"

        entries = _read_entries(period)

        if not entries:
            return json.dumps({
                "status": "no_data",
                "period": period,
                "message": "No delegation scores recorded yet. Use delegation_log after validate_output to start tracking.",
            })

        # Aggregate per model
        model_data = {}
        # Aggregate per task_type
        task_best = {}

        for e in entries:
            m = e.get("model", "unknown")
            tt = e.get("task_type", "unknown")
            sc = e.get("score", 0)
            tk = e.get("tokens_used", 0)

            if m not in model_data:
                model_data[m] = {"scores": [], "tokens": [], "tasks": {}}
            model_data[m]["scores"].append(sc)
            model_data[m]["tokens"].append(tk)

            if tt not in model_data[m]["tasks"]:
                model_data[m]["tasks"][tt] = []
            model_data[m]["tasks"][tt].append(sc)

            # Track best per task type
            if tt not in task_best:
                task_best[tt] = {}
            if m not in task_best[tt]:
                task_best[tt][m] = []
            task_best[tt][m].append(sc)

        # Build per-model stats
        models = {}
        for m, d in model_data.items():
            scores = d["scores"]
            tokens = d["tokens"]
            avg_score = round(sum(scores) / len(scores), 1)
            success_count = sum(1 for s in scores if s >= 7)
            success_rate = f"{round(100 * success_count / len(scores))}%"
            avg_tokens = round(sum(tokens) / len(tokens))

            models[m] = {
                "avg_score": avg_score,
                "calls": len(scores),
                "success_rate": success_rate,
                "avg_tokens": avg_tokens,
            }

        # Best model per task type
        best_for = {}
        for tt, model_scores in task_best.items():
            best_model = None
            best_avg = -1
            for m, scores in model_scores.items():
                avg = sum(scores) / len(scores)
                if avg > best_avg:
                    best_avg = avg
                    best_model = m
            if best_model:
                best_for[tt] = best_model

        # Worst performers (avg score < 5 with at least 3 calls)
        avoid = []
        for m, stats in models.items():
            if stats["avg_score"] < 5 and stats["calls"] >= 3:
                # Find which task types are worst
                worst_tasks = []
                for tt, scores in model_data[m]["tasks"].items():
                    tt_avg = round(sum(scores) / len(scores), 1)
                    if tt_avg < 5:
                        worst_tasks.append(f"{tt} (avg score: {tt_avg})")
                if worst_tasks:
                    avoid.append(f"{m} for {', '.join(worst_tasks)}")
                else:
                    avoid.append(f"{m} (avg score: {stats['avg_score']})")

        # Overall recommendation
        if models:
            best_overall = max(models.items(), key=lambda x: x[1]["avg_score"])
            recommendation = f"{best_overall[0]} is your most reliable model overall (avg {best_overall[1]['avg_score']}, {best_overall[1]['calls']} calls)"
        else:
            recommendation = "Not enough data yet"

        return json.dumps({
            "period": period,
            "total_entries": len(entries),
            "models": models,
            "best_for": best_for,
            "avoid": avoid,
            "recommendation": recommendation,
        })

    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


def register(ctx):
    ctx.register_tool(
        name="delegation_log",
        toolset="evey_delegation_score",
        schema=LOG_SCHEMA,
        handler=log_handler,
    )
    ctx.register_tool(
        name="delegation_stats",
        toolset="evey_delegation_score",
        schema=STATS_SCHEMA,
        handler=stats_handler,
    )
