"""Evey Learner Plugin — learn from every interaction, apply lessons to future ones.

Two tools:
1. learn_from_interaction — extract and store a lesson after any delegation/tool use
2. apply_learnings — before a delegation, check past lessons for relevant patterns

Learnings persist in workspace/orchestrator/learnings.jsonl.
Unique to Evey: genuine experiential learning across sessions.
"""

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("evey.learner")

HERMES_HOME = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))
LEARNINGS_DIR = HERMES_HOME / "workspace" / "orchestrator"
LEARNINGS_FILE = LEARNINGS_DIR / "learnings.jsonl"

# Max learnings to keep (oldest pruned when exceeded)
MAX_LEARNINGS = 500

# How many recent learnings to search when applying
SEARCH_WINDOW = 200

LEARN_SCHEMA = {
    "name": "learn_from_interaction",
    "description": (
        "Extract a lesson from a completed task. Records what worked, what didn't, "
        "which model/tool was used, and what to do differently. Learnings persist "
        "across sessions and are used by apply_learnings to improve future work.\n\n"
        "Call this after any delegation, research, or non-trivial tool use."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "What was the task? Brief description.",
            },
            "model_or_tool": {
                "type": "string",
                "description": "Which model or tool was used?",
            },
            "quality_score": {
                "type": "number",
                "description": "Quality score 1-10 of the result",
            },
            "what_worked": {
                "type": "string",
                "description": "What went well?",
            },
            "what_failed": {
                "type": "string",
                "description": "What went wrong or could be improved?",
            },
            "do_differently": {
                "type": "string",
                "description": "What would you do differently next time?",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Keywords for this learning (e.g., 'research', 'code', 'delegation')",
            },
        },
        "required": ["task", "quality_score"],
    },
}

APPLY_SCHEMA = {
    "name": "apply_learnings",
    "description": (
        "Before starting a task, check past learnings for relevant lessons. "
        "Searches by keywords, model name, or task description. Returns matching "
        "lessons sorted by relevance so you can apply them to the current task.\n\n"
        "Call this before delegations or recurring tasks to avoid repeating mistakes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_description": {
                "type": "string",
                "description": "Description of the upcoming task",
            },
            "model_or_tool": {
                "type": "string",
                "description": "Model or tool you plan to use (optional)",
            },
            "max_results": {
                "type": "number",
                "description": "Max learnings to return (default: 5)",
            },
        },
        "required": ["task_description"],
    },
}


def _ensure_dir():
    """Create learnings directory if needed."""
    LEARNINGS_DIR.mkdir(parents=True, exist_ok=True)


def _read_learnings(limit=None):
    """Read learnings from JSONL file. Returns list of dicts."""
    if not LEARNINGS_FILE.exists():
        return []
    learnings = []
    try:
        with open(LEARNINGS_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        learnings.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except Exception:
        return []
    if limit:
        return learnings[-limit:]
    return learnings


def _append_learning(entry):
    """Append a learning entry and prune if needed."""
    _ensure_dir()
    with open(LEARNINGS_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    # Prune if over max
    learnings = _read_learnings()
    if len(learnings) > MAX_LEARNINGS:
        # Keep the newest MAX_LEARNINGS entries
        keep = learnings[-MAX_LEARNINGS:]
        with open(LEARNINGS_FILE, "w") as f:
            for entry in keep:
                f.write(json.dumps(entry) + "\n")
        logger.info(f"Pruned learnings: {len(learnings)} -> {MAX_LEARNINGS}")


def _relevance_score(learning, query_words, model_or_tool=None):
    """Score how relevant a learning is to the current task. Higher = more relevant."""
    score = 0.0

    # Text fields to search
    search_text = " ".join([
        learning.get("task", ""),
        learning.get("what_worked", ""),
        learning.get("what_failed", ""),
        learning.get("do_differently", ""),
        " ".join(learning.get("tags", [])),
    ]).lower()

    # Keyword overlap
    if query_words:
        matches = sum(1 for w in query_words if w in search_text)
        score += (matches / len(query_words)) * 10

    # Model/tool match bonus
    if model_or_tool:
        if model_or_tool.lower() in learning.get("model_or_tool", "").lower():
            score += 5

    # Recency bonus (newer = more relevant)
    try:
        age_days = (time.time() - learning.get("timestamp", 0)) / 86400
        if age_days < 1:
            score += 3
        elif age_days < 7:
            score += 2
        elif age_days < 30:
            score += 1
    except Exception:
        pass

    # Quality extremes are more informative (very good or very bad)
    q = learning.get("quality_score", 5)
    if q >= 9 or q <= 2:
        score += 2
    elif q >= 8 or q <= 3:
        score += 1

    return score


def learn_handler(args, **kwargs):
    try:
        task = args.get("task", "")
        model_or_tool = args.get("model_or_tool", "")
        quality_score = max(1, min(10, int(args.get("quality_score", 5))))
        what_worked = args.get("what_worked", "")
        what_failed = args.get("what_failed", "")
        do_differently = args.get("do_differently", "")
        tags = args.get("tags", [])

        if not task:
            return json.dumps({"status": "error", "error": "Task description required"})

        entry = {
            "timestamp": time.time(),
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "task": task[:500],
            "model_or_tool": model_or_tool,
            "quality_score": quality_score,
            "what_worked": what_worked[:500],
            "what_failed": what_failed[:500],
            "do_differently": do_differently[:500],
            "tags": tags[:10],
        }

        _append_learning(entry)

        total = len(_read_learnings())
        logger.info(f"Learning recorded: q={quality_score}, task={task[:50]}")

        return json.dumps({
            "status": "learned",
            "quality_score": quality_score,
            "task_preview": task[:100],
            "total_learnings": total,
        })
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


def apply_handler(args, **kwargs):
    try:
        task_description = args.get("task_description", "")
        model_or_tool = args.get("model_or_tool", "")
        max_results = min(args.get("max_results", 5), 20)

        if not task_description:
            return json.dumps({"status": "error", "error": "Task description required"})

        # Load recent learnings
        learnings = _read_learnings(limit=SEARCH_WINDOW)

        if not learnings:
            return json.dumps({
                "status": "no_learnings",
                "message": "No past learnings found. Use learn_from_interaction after tasks.",
                "applicable_lessons": [],
            })

        # Tokenize query
        stop_words = {"the", "a", "an", "is", "to", "and", "of", "in", "for", "with", "on", "at", "by"}
        query_words = [
            w for w in task_description.lower().split()
            if w not in stop_words and len(w) > 2
        ]

        # Score and rank
        scored = []
        for learning in learnings:
            score = _relevance_score(learning, query_words, model_or_tool)
            if score > 0:
                scored.append((score, learning))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:max_results]

        if not top:
            return json.dumps({
                "status": "no_matches",
                "message": "No relevant learnings found for this task type.",
                "total_learnings": len(learnings),
                "applicable_lessons": [],
            })

        lessons = []
        for score, learning in top:
            lesson = {
                "relevance_score": round(score, 1),
                "task": learning.get("task", ""),
                "model_or_tool": learning.get("model_or_tool", ""),
                "quality_score": learning.get("quality_score", 0),
                "date": learning.get("date", ""),
            }
            # Include actionable advice
            if learning.get("do_differently"):
                lesson["advice"] = learning["do_differently"]
            elif learning.get("what_failed"):
                lesson["advice"] = f"Avoid: {learning['what_failed']}"
            elif learning.get("what_worked"):
                lesson["advice"] = f"Repeat: {learning['what_worked']}"
            lessons.append(lesson)

        # Build a summary prompt injection
        advice_lines = []
        for l in lessons:
            if l.get("advice"):
                advice_lines.append(f"- {l['advice']} (from: {l['task'][:60]}, q={l['quality_score']})")

        return json.dumps({
            "status": "found",
            "total_learnings": len(learnings),
            "matches": len(top),
            "applicable_lessons": lessons,
            "advice_summary": "\n".join(advice_lines) if advice_lines else "No specific advice.",
        })
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


def register(ctx):
    ctx.register_tool(
        name="learn_from_interaction",
        toolset="evey_learner",
        schema=LEARN_SCHEMA,
        handler=learn_handler,
    )
    ctx.register_tool(
        name="apply_learnings",
        toolset="evey_learner",
        schema=APPLY_SCHEMA,
        handler=apply_handler,
    )
