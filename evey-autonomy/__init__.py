"""Evey Autonomy Plugin — priority queue, planning, and heuristic reflection.
All scoring is heuristic (no LLM calls). stdlib only.
"""
import json
import math
import os
import sqlite3
import time
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))
GOALS_PATH = HERMES_HOME / "goals.md"
BRIDGE_DB = HERMES_HOME / "claude-bridge" / "bridge.db"
BRIDGE_INBOX = HERMES_HOME / "claude-bridge" / "outbox"
CHANNEL_PATH = HERMES_HOME / "claude-bridge" / "channel.jsonl"
MEMORY_SCORES = HERMES_HOME / "memories" / ".memory_scores.json"
CRON_PATH = HERMES_HOME / "cron" / "jobs.json"
AUTONOMY_LOG = HERMES_HOME / "workspace" / "orchestrator" / "autonomy-log.jsonl"

HEAVY_MODEL = "mimo-v2-pro"  # Free via OpenClaw — use for heavy tasks

def _get_hour():
    try:
        import zoneinfo
        from datetime import datetime
        return datetime.now(zoneinfo.ZoneInfo("Europe/Berlin")).hour
    except Exception:
        return (int(time.strftime("%H", time.gmtime())) + 1) % 24


TIME_PROFILES = {
    "morning":      (7, 10,  ["bridge_check", "health_check", "goal_review"],
                             ["heavy_research"]),
    "late_morning": (10, 12, ["research_deep", "code_change", "goal_work"],
                             []),
    "afternoon":    (12, 17, ["research_quick", "bridge_check", "goal_work"],
                             []),
    "evening":      (17, 21, ["goal_review", "cost_review", "light_research"],
                             ["heavy_delegation"]),
    "night":        (21, 23, ["memory_maintenance", "health_check"],
                             ["telegram_alerts", "heavy_delegation"]),
    "late_night":   (23, 7,  ["self_improve", "memory_maintenance"],
                             ["telegram_alerts", "heavy_delegation", "expensive_models"]),
}


def _time_context():
    h = _get_hour()
    for name, (start, end, recommended, avoid) in TIME_PROFILES.items():
        if start <= end:
            match = start <= h < end
        else:
            match = h >= start or h < end
        if match:
            return {"period": name, "hour": h,
                    "recommended": recommended, "avoid": avoid}
    return {"period": "afternoon", "hour": h, "recommended": [], "avoid": []}


def _safe_read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default if default is not None else {}


def _log_decision(entry):
    try:
        AUTONOMY_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry["logged_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(AUTONOMY_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _recent_decisions(n=10):
    if not AUTONOMY_LOG.exists():
        return []
    try:
        lines = AUTONOMY_LOG.read_text().strip().split("\n")
        out = []
        for line in lines[-n:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return out
    except Exception:
        return []


def _collect_bridge():
    """Check SQLite bridge DB and filesystem inbox for Mother tasks."""
    actions = []
    # SQLite bridge
    if BRIDGE_DB.exists():
        try:
            conn = sqlite3.connect(str(BRIDGE_DB), timeout=2)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, title, body, status FROM tasks "
                "WHERE status IN ('pending','in_progress') ORDER BY id DESC LIMIT 5"
            ).fetchall()
            for r in rows:
                actions.append({
                    "source": "bridge", "action": "process_bridge_task",
                    "description": f"Bridge task: {r['title']}" if r['title'] else "Bridge task",
                    "detail": (r["body"] or "")[:500],
                    "urgency": 10, "importance": 10, "recency": 10,
                    "task_type": "bridge_check",
                })
            conn.close()
        except Exception:
            pass
    # Filesystem inbox fallback
    if BRIDGE_INBOX.is_dir():
        for f in sorted(BRIDGE_INBOX.iterdir()):
            if f.is_file():
                try:
                    actions.append({
                        "source": "bridge", "action": "process_bridge_task",
                        "description": f"Bridge file: {f.name}",
                        "detail": f.read_text()[:500],
                        "urgency": 10, "importance": 10, "recency": 10,
                        "task_type": "bridge_check",
                    })
                except Exception:
                    pass
    # Channel messages
    if CHANNEL_PATH.exists():
        try:
            lines = CHANNEL_PATH.read_text().strip().split("\n")
            for line in lines[-5:]:
                try:
                    msg = json.loads(line)
                    if msg.get("from") in ("claude-code", "mother"):
                        actions.append({
                            "source": "bridge_channel", "action": "read_bridge_message",
                            "description": f"Mother msg: {msg.get('message', '')[:80]}",
                            "detail": msg.get("message", ""),
                            "urgency": 9, "importance": 9, "recency": 9,
                            "task_type": "bridge_check",
                        })
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass
    return actions


def _collect_goals():
    if not GOALS_PATH.exists():
        return []
    actions = []
    recently_worked = {d.get("description", "").lower()[:30]
                       for d in _recent_decisions(20)}
    try:
        in_active = False
        for line in GOALS_PATH.read_text().split("\n"):
            if line.strip() == "## Active":
                in_active = True
                continue
            if line.startswith("## ") and in_active:
                break
            if in_active and line.strip().startswith("- [ ]"):
                text = line.strip()[5:].strip()
                if any(text.lower()[:30] in w for w in recently_worked):
                    continue
                actions.append({
                    "source": "goals", "action": "advance_goal",
                    "description": text, "detail": text,
                    "urgency": 5, "importance": 7, "recency": 4,
                    "task_type": _classify_goal(text),
                })
    except Exception:
        pass
    return actions


def _classify_goal(text):
    t = text.lower()
    for kws, typ in [
        (["code", "plugin", "script", "implement", "fix", "build"], "code_change"),
        (["research", "find", "learn", "explore", "investigate"], "research_deep"),
        (["write", "blog", "post", "creative", "content"], "creative_writing"),
        (["monitor", "health", "uptime", "check"], "health_check"),
        (["memory", "consolidate", "prune"], "memory_maintenance"),
        (["cost", "budget", "spend"], "cost_review"),
    ]:
        if any(w in t for w in kws):
            return typ
    return "research_quick"


def _collect_memory():
    actions = []
    scores = _safe_read_json(MEMORY_SCORES, {})
    if scores:
        now = time.time()
        stale = sum(1 for d in scores.values()
                    if d.get("importance", 1) * math.exp(
                        -0.693 * (now - d.get("last_accessed", now)) / 86400 / 14
                    ) < 0.1)
        if stale >= 3:
            actions.append({
                "source": "memory", "action": "prune_stale_memories",
                "description": f"{stale} memories below decay threshold",
                "detail": "Run memory_decay then consolidate_daily_memory",
                "urgency": 4, "importance": 5, "recency": 3,
                "task_type": "memory_maintenance",
            })
    return actions


def _collect_cron():
    actions = []
    data = _safe_read_json(CRON_PATH, {})
    for job in data.get("jobs", []):
        if job.get("last_status") == "error" and job.get("enabled"):
            actions.append({
                "source": "cron", "action": "fix_cron_job",
                "description": f"Cron failing: {job.get('name', '?')}",
                "detail": job.get("last_error", "")[:200],
                "urgency": 7, "importance": 6, "recency": 8,
                "task_type": "health_check",
            })
    return actions


def _collect_time():
    actions = []
    ctx = _time_context()
    h = ctx["hour"]
    today = time.strftime("%Y-%m-%d")
    recent = _recent_decisions(30)
    if ctx["period"] == "morning":
        if not any(d.get("action") == "morning_briefing"
                   and d.get("logged_at", "").startswith(today) for d in recent):
            actions.append({
                "source": "time", "action": "morning_briefing",
                "description": "Morning briefing not sent yet",
                "detail": "Check bridge, goals, health, send V a plan",
                "urgency": 8, "importance": 7, "recency": 9,
                "task_type": "alert_v",
            })
    if 2 <= h < 4:
        if not any(d.get("action") == "self_improve_cycle"
                   and d.get("logged_at", "").startswith(today) for d in recent):
            actions.append({
                "source": "time", "action": "self_improve_cycle",
                "description": "Nightly self-improvement window",
                "detail": "consolidate_daily_memory -> update_identity -> goals review",
                "urgency": 6, "importance": 7, "recency": 8,
                "task_type": "self_improve",
            })
    return actions

ROUTING = {
    "bridge_check":      {"tools": ["mcp_mother_bridge_check_messages", "mcp_mother_bridge_list_tasks"], "models": [], "cost": "free"},
    "code_change":       {"tools": ["claude_bridge_task"], "models": [], "cost": "free"},
    "research_deep":     {"tools": ["delegate_parallel", "web_research"], "models": [HEAVY_MODEL, "nemotron3-super"], "cost": "free"},
    "research_quick":    {"tools": ["web_research", "delegate_with_model"], "models": [HEAVY_MODEL], "cost": "free"},
    "health_check":      {"tools": ["validate_output"], "models": [], "cost": "free"},
    "memory_maintenance":{"tools": ["memory_score", "memory_decay", "consolidate_daily_memory"], "models": ["qwen35-4b"], "cost": "free"},
    "cost_review":       {"tools": ["cost_check"], "models": [], "cost": "free"},
    "goal_review":       {"tools": ["evey_goals"], "models": [], "cost": "free"},
    "self_improve":      {"tools": ["reflect_on_output", "update_identity"], "models": ["qwen35-4b"], "cost": "free"},
    "alert_v":           {"tools": ["mqtt_publish_event"], "models": [], "cost": "free"},
    "creative_writing":  {"tools": ["delegate_with_model"], "models": ["dolphin3-local"], "cost": "free"},
    "simple_answer":     {"tools": [], "models": [], "cost": "free"},
}

DECIDE_SCHEMA = {
    "name": "autonomous_decide",
    "description": (
        "Decide what to work on next. Scans bridge tasks, goals, memory health, "
        "cron errors, and time-of-day. Returns the top action with tool/model "
        "recommendations. Call when starting a session, idle, or after completing work."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "context": {"type": "string", "description": "Current context (optional)"},
            "exclude_sources": {
                "type": "array", "items": {"type": "string"},
                "description": "Sources to skip, e.g. ['health']",
            },
        },
    },
}


def decide_handler(args, **kwargs):
    try:
        exclude = set(args.get("exclude_sources", []))
        collectors = {
            "bridge": _collect_bridge, "goals": _collect_goals,
            "memory": _collect_memory, "cron": _collect_cron,
            "time": _collect_time,
        }
        all_actions = []
        counts = {}
        for name, fn in collectors.items():
            if name in exclude:
                continue
            try:
                signals = fn()
                counts[name] = len(signals)
                all_actions.extend(signals)
            except Exception as e:
                counts[name] = f"err:{e}"

        tc = _time_context()
        if not all_actions:
            _log_decision({"action": "idle", "source": "fallback", "priority": 0})
            return json.dumps({"status": "idle", "time_context": tc,
                               "sources_checked": counts})

        # Deduplicate
        seen, unique = set(), []
        for a in all_actions:
            k = f"{a['source']}:{a['action']}:{a.get('description', '')[:40]}"
            if k not in seen:
                seen.add(k)
                unique.append(a)

        # Score: urgency * importance * recency (max 1000)
        for a in unique:
            a["priority_score"] = (max(1, min(10, a.get("urgency", 5)))
                                   * max(1, min(10, a.get("importance", 5)))
                                   * max(1, min(10, a.get("recency", 5))))
        unique.sort(key=lambda a: a["priority_score"], reverse=True)

        top = unique[0]
        rt = ROUTING.get(top.get("task_type", ""), ROUTING["simple_answer"])
        decision = {
            "status": "action",
            "action": top["action"],
            "description": top["description"],
            "source": top["source"],
            "priority_score": top["priority_score"],
            "detail": top.get("detail", ""),
            "recommended_tools": rt["tools"],
            "recommended_models": rt["models"],
            "cost_tier": rt["cost"],
            "time_context": tc,
            "queue_depth": len(unique),
            "next_actions": [{"action": a["action"], "description": a["description"][:80],
                              "priority": a["priority_score"]} for a in unique[1:4]],
            "sources_checked": counts,
        }
        _log_decision(decision)
        return json.dumps(decision)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})

PLAN_SCHEMA = {
    "name": "autonomous_plan",
    "description": (
        "Given a goal, return a multi-step plan with tool names, models, and "
        "estimated cost. Templates for: research, code, health, memory, goal-review. "
        "Constraint modes: free-only (default), fast, thorough."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "The goal to plan for"},
            "constraints": {"type": "string",
                            "description": "'free-only' (default), 'fast', 'thorough'"},
            "max_steps": {"type": "number", "description": "Max steps (default 8)"},
        },
        "required": ["goal"],
    },
}

TEMPLATES = {
    "research": [
        {"step": 1, "action": "Search 3 angles", "tool": "delegate_parallel",
         "model": HEAVY_MODEL, "cost": "free"},
        {"step": 2, "action": "Web search for sources", "tool": "web_research",
         "model": "", "cost": "free"},
        {"step": 3, "action": "Compile findings", "tool": "delegate_with_model",
         "model": HEAVY_MODEL, "cost": "free"},
    ],
    "code": [
        {"step": 1, "action": "Analyze requirements", "tool": "delegate_with_model",
         "model": HEAVY_MODEL, "cost": "free"},
        {"step": 2, "action": "Send to Claude Code", "tool": "claude_bridge_task",
         "model": "", "cost": "free"},
        {"step": 3, "action": "Monitor bridge", "tool": "mcp_mother_bridge_check_messages",
         "model": "", "cost": "free"},
        {"step": 4, "action": "Validate result", "tool": "validate_output",
         "model": "", "cost": "free"},
    ],
    "health": [
        {"step": 1, "action": "Check all services", "tool": "validate_output",
         "model": "", "cost": "free"},
        {"step": 2, "action": "Check cron jobs", "tool": "validate_output",
         "model": "", "cost": "free"},
        {"step": 3, "action": "Alert if issues", "tool": "mqtt_publish_event",
         "model": "", "cost": "free"},
    ],
    "memory": [
        {"step": 1, "action": "Score memories", "tool": "memory_score",
         "model": "", "cost": "free"},
        {"step": 2, "action": "Decay stale entries", "tool": "memory_decay",
         "model": "", "cost": "free"},
        {"step": 3, "action": "Consolidate", "tool": "consolidate_daily_memory",
         "model": "qwen35-4b", "cost": "free"},
    ],
    "goal_review": [
        {"step": 1, "action": "List goals", "tool": "evey_goals",
         "model": "", "cost": "free"},
        {"step": 2, "action": "Evaluate progress", "tool": "delegate_with_model",
         "model": "qwen35-4b", "cost": "free"},
        {"step": 3, "action": "Update goals", "tool": "evey_goals",
         "model": "", "cost": "free"},
    ],
}

# Map task classifications to template keys
_TYPE_TO_TEMPLATE = {
    "research_deep": "research", "research_quick": "research",
    "code_change": "code", "health_check": "health",
    "memory_maintenance": "memory", "goal_review": "goal_review",
    "cost_review": "goal_review", "self_improve": "memory",
    "creative_writing": "research",
}


def plan_handler(args, **kwargs):
    try:
        goal = args.get("goal", "")
        if not goal:
            return json.dumps({"error": "No goal provided"})
        constraints = args.get("constraints", "free-only")
        max_steps = min(args.get("max_steps", 8), 12)
        task_type = _classify_goal(goal)
        tpl_key = _TYPE_TO_TEMPLATE.get(task_type, "research")
        steps = [dict(s) for s in TEMPLATES.get(tpl_key, TEMPLATES["research"])][:max_steps]

        # Apply constraints
        if constraints == "fast":
            steps = steps[:2]
            for s in steps:
                if s["tool"] == "delegate_parallel":
                    s["tool"] = "delegate_with_model"
        elif constraints == "thorough":
            steps.append({"step": len(steps) + 1, "action": "Quality reflection",
                          "tool": "reflect_on_output", "model": "", "cost": "free"})

        # Renumber
        for i, s in enumerate(steps):
            s["step"] = i + 1

        return json.dumps({
            "status": "planned", "goal": goal, "task_type": task_type,
            "template": tpl_key, "steps": steps,
            "total_steps": len(steps),
            "constraints": constraints,
            "time_context": _time_context(),
        })
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})

REFLECT_SCHEMA = {
    "name": "autonomous_reflect",
    "description": (
        "Post-action quality scoring using heuristics (no LLM cost). "
        "Checks completeness (keyword overlap), length adequacy, and error "
        "detection. Returns score 1-10, assessment, and next action suggestion."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_description": {"type": "string", "description": "Original intent"},
            "result_text": {"type": "string", "description": "What was produced"},
            "model_used": {"type": "string", "description": "Model that did the work"},
        },
        "required": ["task_description", "result_text"],
    },
}

ERROR_KEYWORDS = {"error", "fail", "failed", "exception", "traceback",
                  "timeout", "refused", "denied", "unauthorized", "crash"}


def _heuristic_score(task, result):
    """Score result quality without calling an LLM."""
    score = 5.0
    assessment = []

    # 1. Completeness: keyword overlap between task and result
    task_words = set(task.lower().split()) - {"the", "a", "an", "is", "to", "and", "of", "in", "for"}
    result_lower = result.lower()
    if task_words:
        overlap = sum(1 for w in task_words if w in result_lower) / len(task_words)
        if overlap >= 0.6:
            score += 2
            assessment.append("Good keyword coverage")
        elif overlap >= 0.3:
            score += 1
            assessment.append("Partial keyword coverage")
        else:
            score -= 1
            assessment.append("Low relevance to task")

    # 2. Length adequacy
    rlen = len(result.strip())
    if rlen < 20:
        score -= 2
        assessment.append("Response too short")
    elif rlen < 100:
        score -= 1
        assessment.append("Response may be too brief")
    elif rlen > 10000:
        score -= 1
        assessment.append("Response may be bloated")
    else:
        score += 1
        assessment.append("Appropriate length")

    # 3. Error detection
    result_words = set(result_lower.split())
    errors_found = result_words & ERROR_KEYWORDS
    if errors_found:
        score -= 2
        assessment.append(f"Error keywords found: {', '.join(sorted(errors_found))}")
    else:
        score += 1
        assessment.append("No error indicators")

    return max(1, min(10, round(score))), "; ".join(assessment)


def reflect_handler(args, **kwargs):
    try:
        task = args.get("task_description", "")
        result = args.get("result_text", "")
        model = args.get("model_used", "")
        if not task:
            return json.dumps({"error": "task_description required"})

        score, assessment = _heuristic_score(task, result)

        if score >= 8:
            suggestion = "Quality work. Run autonomous_decide for next priority."
        elif score >= 5:
            suggestion = "Adequate. Consider refining, then move on."
        else:
            suggestion = (f"Needs rework. Try a different approach"
                          + (f" or model (was: {model})" if model else "")
                          + ".")

        entry = {"action": "reflection", "task": task[:200],
                 "score": score, "model": model, "assessment": assessment}
        _log_decision(entry)

        return json.dumps({
            "status": "reflected", "score": score,
            "label": ("excellent" if score >= 9 else "good" if score >= 7
                      else "adequate" if score >= 5 else "poor"),
            "assessment": assessment,
            "suggestion": suggestion,
        })
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})

def register(ctx):
    ctx.register_tool(name="autonomous_decide", toolset="evey_autonomy",
                      schema=DECIDE_SCHEMA, handler=decide_handler)
    ctx.register_tool(name="autonomous_plan", toolset="evey_autonomy",
                      schema=PLAN_SCHEMA, handler=plan_handler)
    ctx.register_tool(name="autonomous_reflect", toolset="evey_autonomy",
                      schema=REFLECT_SCHEMA, handler=reflect_handler)
