"""Adaptive memory — importance scoring and decay for Evey's memories.

Tracks memory importance using exponential decay. Memories that aren't
accessed lose importance over time. Memories that are frequently accessed
or explicitly boosted retain importance.

Scoring model:
  decayed_score = importance * e^(-0.693 * days_since_access / half_life)

Default half_life: 14 days (score halves every 2 weeks without access)
"""

import json
import math
import os
import time
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))
MEMORY_DIR = HERMES_HOME / "memories"
SCORES_FILE = MEMORY_DIR / ".memory_scores.json"


def _load_scores():
    if SCORES_FILE.exists():
        try:
            return json.loads(SCORES_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_scores(scores):
    SCORES_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCORES_FILE.write_text(json.dumps(scores, indent=2))


def _decay_score(importance, last_accessed, half_life_days=14):
    """Exponential decay: score halves every half_life_days."""
    days_since = (time.time() - last_accessed) / 86400
    return importance * math.exp(-0.693 * days_since / half_life_days)


SCORE_SCHEMA = {
    "name": "memory_score",
    "description": (
        "Score and rank memories by importance. Shows which memories are most/least "
        "valuable based on recency, access frequency, and importance. "
        "Use 'rank' to see all scores, 'boost' to increase a memory's importance, "
        "'access' to mark a memory as recently used."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["rank", "boost", "access"],
                "description": "rank=show all scores, boost=increase importance, access=mark as used",
            },
            "memory_key": {
                "type": "string",
                "description": "Memory key to boost/access (first few words of the memory)",
            },
        },
        "required": ["action"],
    },
}

DECAY_SCHEMA = {
    "name": "memory_decay",
    "description": (
        "Run decay on all memories. Old, unaccessed memories get lower scores. "
        "Returns memories flagged for removal (below threshold) and healthy ones. "
        "Use this during memory consolidation to prune stale memories."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "threshold": {
                "type": "number",
                "description": "Score threshold below which memories are flagged (default: 0.1)",
            },
        },
    },
}


def handle_score(args, **kwargs):
    action = args.get("action", "rank")
    scores = _load_scores()
    now = time.time()

    if action == "rank":
        ranked = []
        for key, data in scores.items():
            decayed = _decay_score(
                data.get("importance", 1.0),
                data.get("last_accessed", now),
            )
            ranked.append({
                "key": key,
                "importance": data.get("importance", 1.0),
                "decayed_score": round(decayed, 3),
                "accesses": data.get("accesses", 0),
                "last_accessed": time.strftime(
                    "%Y-%m-%d", time.gmtime(data.get("last_accessed", now))
                ),
            })
        ranked.sort(key=lambda x: x["decayed_score"], reverse=True)
        return json.dumps({"status": "ok", "memories": ranked, "count": len(ranked)})

    elif action == "boost":
        key = args.get("memory_key", "")
        if not key:
            return json.dumps({"error": "memory_key required for boost"})
        if key not in scores:
            scores[key] = {"importance": 1.0, "accesses": 0, "last_accessed": now, "created": now}
        scores[key]["importance"] = min(scores[key]["importance"] + 0.5, 5.0)
        scores[key]["last_accessed"] = now
        _save_scores(scores)
        return json.dumps({
            "status": "boosted", "key": key,
            "new_importance": scores[key]["importance"],
        })

    elif action == "access":
        key = args.get("memory_key", "")
        if not key:
            return json.dumps({"error": "memory_key required for access"})
        if key not in scores:
            scores[key] = {"importance": 1.0, "accesses": 0, "last_accessed": now, "created": now}
        scores[key]["accesses"] = scores[key].get("accesses", 0) + 1
        scores[key]["last_accessed"] = now
        _save_scores(scores)
        return json.dumps({
            "status": "accessed", "key": key,
            "accesses": scores[key]["accesses"],
        })

    return json.dumps({"error": f"Invalid action: {action}"})


def handle_decay(args, **kwargs):
    threshold = args.get("threshold", 0.1)
    scores = _load_scores()
    now = time.time()

    flagged = []
    healthy = []
    for key, data in scores.items():
        decayed = _decay_score(
            data.get("importance", 1.0),
            data.get("last_accessed", now),
        )
        entry = {
            "key": key,
            "score": round(decayed, 3),
            "days_since_access": round((now - data.get("last_accessed", now)) / 86400),
        }
        if decayed < threshold:
            flagged.append(entry)
        else:
            healthy.append(entry)

    return json.dumps({
        "status": "ok",
        "healthy_count": len(healthy),
        "flagged_for_removal": flagged,
        "suggestion": (
            f"{len(flagged)} memories below threshold {threshold}. "
            "Consider consolidating or removing them."
            if flagged
            else "All memories are healthy."
        ),
    })


def register(ctx):
    ctx.register_tool(
        name="memory_score",
        toolset="evey_memory",
        schema=SCORE_SCHEMA,
        handler=handle_score,
    )
    ctx.register_tool(
        name="memory_decay",
        toolset="evey_memory",
        schema=DECAY_SCHEMA,
        handler=handle_decay,
    )
