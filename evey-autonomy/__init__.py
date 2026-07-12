"""Hermes Autonomy Plugin — configurable priority queue, planning, and reflection.

This started as Evey's local autonomy plugin, but the defaults now target a
stock Hermes instance and can be customized without editing code.

Config file (optional): ``$HERMES_HOME/evey-autonomy.json``

Example:
{
  "operator_name": "operator",
  "timezone": "UTC",
  "heavy_model": "claude-sonnet-4-6",
  "cheap_model": "claude-haiku-4-5",
  "projects": [
    {"name": "my-app", "path": "/path/to/my-app", "base_branch": "main", "importance": 8}
  ],
  "bridge_peer_names": ["claude-code", "pr-agent"],
  "disabled_sources": []
}
"""
import json
import math
import os
import sqlite3
import subprocess
import time
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))
CONFIG_PATH = Path(os.environ.get("HERMES_AUTONOMY_CONFIG", HERMES_HOME / "evey-autonomy.json"))
GOALS_PATH = HERMES_HOME / "goals.md"
BRIDGE_DB = HERMES_HOME / "claude-bridge" / "bridge.db"
BRIDGE_INBOX = HERMES_HOME / "claude-bridge" / "outbox"
CHANNEL_PATH = HERMES_HOME / "claude-bridge" / "channel.jsonl"
MEMORY_SCORES = HERMES_HOME / "memories" / ".memory_scores.json"
CRON_PATH = HERMES_HOME / "cron" / "jobs.json"
AUTONOMY_LOG = HERMES_HOME / "workspace" / "orchestrator" / "autonomy-log.jsonl"

DEFAULTS = {
    "operator_name": os.environ.get("HERMES_OPERATOR_NAME", "operator"),
    "timezone": os.environ.get("HERMES_TIMEZONE", "UTC"),
    "heavy_model": os.environ.get("HERMES_AUTONOMY_HEAVY_MODEL", "claude-sonnet-4-6"),
    "cheap_model": os.environ.get("HERMES_AUTONOMY_CHEAP_MODEL", "claude-haiku-4-5"),
    "disabled_sources": [],
    "bridge_peer_names": ["claude-code", "pr-agent", "copilot"],
    "projects": [],
}

STOPWORDS = {"the", "a", "an", "is", "to", "and", "of", "in", "for", "with", "on", "my", "your"}
ERROR_KEYWORDS = {"error", "fail", "failed", "exception", "traceback", "timeout", "refused", "denied", "unauthorized", "crash"}


def _deep_merge(base, override):
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _safe_read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default if default is not None else {}


def _config():
    cfg = _deep_merge(DEFAULTS, _safe_read_json(CONFIG_PATH, {}))
    # Environment vars win even if a config file exists.
    cfg["operator_name"] = os.environ.get("HERMES_OPERATOR_NAME", cfg.get("operator_name", "operator"))
    cfg["timezone"] = os.environ.get("HERMES_TIMEZONE", cfg.get("timezone", "UTC"))
    cfg["heavy_model"] = os.environ.get("HERMES_AUTONOMY_HEAVY_MODEL", cfg.get("heavy_model", "claude-sonnet-4-6"))
    cfg["cheap_model"] = os.environ.get("HERMES_AUTONOMY_CHEAP_MODEL", cfg.get("cheap_model", "claude-haiku-4-5"))
    return cfg


def _model(kind="heavy"):
    cfg = _config()
    return cfg.get("heavy_model" if kind == "heavy" else "cheap_model") or ""


def _get_hour():
    tz = _config().get("timezone", "UTC")
    try:
        import zoneinfo
        from datetime import datetime
        return datetime.now(zoneinfo.ZoneInfo(tz)).hour
    except Exception:
        return int(time.strftime("%H", time.localtime()))


TIME_PROFILES = {
    "morning":      (7, 10,  ["bridge_check", "health_check", "goal_review", "project_check"], ["heavy_research"]),
    "late_morning": (10, 12, ["research_deep", "code_change", "goal_work", "project_work"], []),
    "afternoon":    (12, 17, ["research_quick", "bridge_check", "goal_work", "project_work"], []),
    "evening":      (17, 21, ["goal_review", "cost_review", "light_research"], ["heavy_delegation"]),
    "night":        (21, 23, ["memory_maintenance", "health_check"], ["user_alerts", "heavy_delegation"]),
    "late_night":   (23, 7,  ["self_improve", "memory_maintenance"], ["user_alerts", "heavy_delegation", "expensive_models"]),
}


def _time_context():
    h = _get_hour()
    for name, (start, end, recommended, avoid) in TIME_PROFILES.items():
        match = start <= h < end if start <= end else h >= start or h < end
        if match:
            return {"period": name, "hour": h, "recommended": recommended, "avoid": avoid, "timezone": _config().get("timezone")}
    return {"period": "afternoon", "hour": h, "recommended": [], "avoid": [], "timezone": _config().get("timezone")}


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


def _run(cmd, cwd=None, timeout=5):
    try:
        p = subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as e:
        return 124, "", str(e)


def _collect_bridge():
    """Check the optional Claude Code / PR-agent bridge for pending work."""
    actions = []
    peer_names = {str(x).lower() for x in _config().get("bridge_peer_names", [])}

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

    if CHANNEL_PATH.exists():
        try:
            lines = CHANNEL_PATH.read_text().strip().split("\n")
            for line in lines[-10:]:
                try:
                    msg = json.loads(line)
                    sender = str(msg.get("from", "")).lower()
                    text = msg.get("message") or msg.get("body") or ""
                    if not peer_names or sender in peer_names:
                        actions.append({
                            "source": "bridge_channel", "action": "read_bridge_message",
                            "description": f"Bridge msg: {text[:80]}",
                            "detail": text,
                            "urgency": 9, "importance": 9, "recency": 9,
                            "task_type": "bridge_check",
                        })
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass
    return actions


def _classify_goal(text):
    t = text.lower()
    for kws, typ in [
        (["pr", "pull request", "github", "issue", "branch", "merge", "deploy"], "project_work"),
        (["code", "plugin", "script", "implement", "fix", "build", "refactor"], "code_change"),
        (["research", "find", "learn", "explore", "investigate"], "research_deep"),
        (["write", "blog", "post", "creative", "content"], "creative_writing"),
        (["monitor", "health", "uptime", "check", "verify"], "health_check"),
        (["memory", "consolidate", "prune"], "memory_maintenance"),
        (["cost", "budget", "spend"], "cost_review"),
        (["email", "gmail", "receipt", "invoice"], "email_maintenance"),
    ]:
        if any(w in t for w in kws):
            return typ
    return "research_quick"


def _collect_goals():
    if not GOALS_PATH.exists():
        return []
    actions = []
    recently_worked = {d.get("description", "").lower()[:30] for d in _recent_decisions(20)}
    try:
        in_active = False
        for line in GOALS_PATH.read_text().split("\n"):
            stripped = line.strip()
            if stripped == "## Active":
                in_active = True
                continue
            if stripped.startswith("## ") and in_active:
                break
            if in_active and stripped.startswith("- [ ]"):
                text = stripped[5:].strip()
                if text.lower()[:30] in recently_worked:
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


def _collect_memory():
    actions = []
    scores = _safe_read_json(MEMORY_SCORES, {})
    if scores:
        now = time.time()
        stale = sum(1 for d in scores.values() if d.get("importance", 1) * math.exp(-0.693 * (now - d.get("last_accessed", now)) / 86400 / 14) < 0.1)
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
        if job.get("last_status") == "error" and job.get("enabled", True):
            actions.append({
                "source": "cron", "action": "fix_cron_job",
                "description": f"Cron failing: {job.get('name', '?')}",
                "detail": job.get("last_error", "")[:300],
                "urgency": 7, "importance": 6, "recency": 8,
                "task_type": "health_check",
            })
    return actions


def _collect_projects():
    """Collect low-cost local git project signals for configured local repos."""
    actions = []
    for project in _config().get("projects", []):
        path = Path(project.get("path", "")).expanduser()
        if not path.exists() or not (path / ".git").exists():
            continue
        name = project.get("name") or path.name
        base = project.get("base_branch", "main")
        importance = int(project.get("importance", 6))
        _, branch, _ = _run(["git", "branch", "--show-current"], cwd=str(path))
        _, status, _ = _run(["git", "status", "--short"], cwd=str(path))
        _, unpushed, _ = _run(["git", "log", f"origin/{base}..HEAD", "--oneline", "-5"], cwd=str(path))
        _, remote_delta, _ = _run(["git", "status", "--short", "--branch"], cwd=str(path))
        detail_parts = []
        if branch:
            detail_parts.append(f"branch={branch}")
        if remote_delta:
            detail_parts.append(remote_delta.split("\n", 1)[0])
        if status:
            detail_parts.append("dirty files:\n" + status[:400])
        if unpushed:
            detail_parts.append("unpushed commits:\n" + unpushed[:400])

        if status or (branch and branch not in (base, "master", "main")) or unpushed:
            actions.append({
                "source": "projects", "action": "review_project_state",
                "description": f"{name} has active local git state",
                "detail": "\n".join(detail_parts),
                "urgency": 6 if status else 5,
                "importance": importance,
                "recency": 8,
                "task_type": "project_work",
            })
    return actions


def _collect_time():
    actions = []
    cfg = _config()
    ctx = _time_context()
    h = ctx["hour"]
    today = time.strftime("%Y-%m-%d")
    recent = _recent_decisions(30)
    operator = cfg.get("operator_name", "user")
    if ctx["period"] == "morning":
        if not any(d.get("action") == "morning_briefing" and d.get("logged_at", "").startswith(today) for d in recent):
            actions.append({
                "source": "time", "action": "morning_briefing",
                "description": "Morning briefing not sent yet",
                "detail": f"Check bridge, goals, projects, health; send {operator} a concise plan",
                "urgency": 8, "importance": 7, "recency": 9,
                "task_type": "alert_user",
            })
    if 2 <= h < 4:
        if not any(d.get("action") == "self_improve_cycle" and d.get("logged_at", "").startswith(today) for d in recent):
            actions.append({
                "source": "time", "action": "self_improve_cycle",
                "description": "Nightly self-improvement window",
                "detail": "consolidate_daily_memory -> update_identity -> goals review",
                "urgency": 6, "importance": 7, "recency": 8,
                "task_type": "self_improve",
            })
    return actions


def _routing():
    heavy = _model("heavy")
    cheap = _model("cheap")
    return {
        "bridge_check":       {"tools": ["claude_bridge_check"], "models": [], "cost": "free/local"},
        "code_change":        {"tools": ["delegate_task", "claude_bridge_task", "terminal", "patch"], "models": [heavy], "cost": "configured"},
        "project_work":       {"tools": ["terminal", "read_file", "patch", "delegate_task", "github-pr-workflow"], "models": [heavy], "cost": "configured"},
        "research_deep":      {"tools": ["web_search", "web_extract", "delegate_task"], "models": [heavy], "cost": "configured"},
        "research_quick":     {"tools": ["web_search", "web_extract"], "models": [cheap], "cost": "configured"},
        "health_check":       {"tools": ["terminal", "validate_output"], "models": [], "cost": "free/local"},
        "memory_maintenance": {"tools": ["memory_score", "memory_decay", "consolidate_daily_memory"], "models": [cheap], "cost": "configured"},
        "cost_review":        {"tools": ["cost_check", "cost_analytics", "terminal"], "models": [], "cost": "free/local"},
        "goal_review":        {"tools": ["evey_goals"], "models": [], "cost": "free/local"},
        "self_improve":       {"tools": ["reflect_on_output", "update_identity"], "models": [cheap], "cost": "configured"},
        "alert_user":         {"tools": ["send_message"], "models": [], "cost": "free/local"},
        "email_maintenance":  {"tools": ["gmail-maintenance-automation", "cronjob"], "models": [cheap], "cost": "configured"},
        "creative_writing":   {"tools": ["delegate_task"], "models": [heavy], "cost": "configured"},
        "simple_answer":      {"tools": [], "models": [], "cost": "free"},
    }


DECIDE_SCHEMA = {
    "name": "autonomous_decide",
    "description": "Decide what to work on next for this Hermes instance. Scans bridge tasks, goals, project git state, memory health, cron errors, and time-of-day.",
    "parameters": {
        "type": "object",
        "properties": {
            "context": {"type": "string", "description": "Current context (optional)"},
            "exclude_sources": {"type": "array", "items": {"type": "string"}, "description": "Sources to skip, e.g. ['projects','time']"},
        },
    },
}


def decide_handler(args, **kwargs):
    try:
        cfg = _config()
        exclude = set(args.get("exclude_sources", []) or []) | set(cfg.get("disabled_sources", []) or [])
        collectors = {
            "bridge": _collect_bridge,
            "goals": _collect_goals,
            "projects": _collect_projects,
            "memory": _collect_memory,
            "cron": _collect_cron,
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
            return json.dumps({"status": "idle", "time_context": tc, "sources_checked": counts, "config_path": str(CONFIG_PATH)})

        seen, unique = set(), []
        for a in all_actions:
            k = f"{a['source']}:{a['action']}:{a.get('description', '')[:80]}"
            if k not in seen:
                seen.add(k)
                unique.append(a)

        for a in unique:
            a["priority_score"] = max(1, min(10, a.get("urgency", 5))) * max(1, min(10, a.get("importance", 5))) * max(1, min(10, a.get("recency", 5)))
        unique.sort(key=lambda a: a["priority_score"], reverse=True)

        top = unique[0]
        routing = _routing()
        rt = routing.get(top.get("task_type", ""), routing["simple_answer"])
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
            "next_actions": [{"source": a["source"], "action": a["action"], "description": a["description"][:100], "priority": a["priority_score"]} for a in unique[1:5]],
            "sources_checked": counts,
            "config_path": str(CONFIG_PATH),
        }
        _log_decision(decision)
        return json.dumps(decision)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


PLAN_SCHEMA = {
    "name": "autonomous_plan",
    "description": "Given a goal, return a multi-step plan with Hermes tool names, configured models, and estimated cost. Constraint modes: free-only, fast, thorough.",
    "parameters": {
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "The goal to plan for"},
            "constraints": {"type": "string", "description": "'free-only' (default), 'fast', 'thorough'"},
            "max_steps": {"type": "number", "description": "Max steps (default 8)"},
        },
        "required": ["goal"],
    },
}


def _templates():
    heavy = _model("heavy")
    cheap = _model("cheap")
    return {
        "research": [
            {"step": 1, "action": "Search current sources", "tool": "web_search", "model": "", "cost": "free/local-or-configured"},
            {"step": 2, "action": "Extract and verify sources", "tool": "web_extract", "model": "", "cost": "free/local-or-configured"},
            {"step": 3, "action": "Synthesize findings", "tool": "delegate_task", "model": heavy, "cost": "configured"},
        ],
        "code": [
            {"step": 1, "action": "Inspect repo and requirements", "tool": "read_file", "model": "", "cost": "free/local"},
            {"step": 2, "action": "Implement in branch/worktree", "tool": "delegate_task", "model": heavy, "cost": "configured"},
            {"step": 3, "action": "Run targeted tests and validators", "tool": "terminal", "model": "", "cost": "free/local"},
            {"step": 4, "action": "Open/update PR when clean", "tool": "github-pr-workflow", "model": "", "cost": "free/local"},
        ],
        "project": [
            {"step": 1, "action": "Check git status, branch, PRs, CI", "tool": "terminal", "model": "", "cost": "free/local"},
            {"step": 2, "action": "Fix or review active work", "tool": "delegate_task", "model": heavy, "cost": "configured"},
            {"step": 3, "action": "Verify and hand off via PR", "tool": "github-pr-workflow", "model": "", "cost": "free/local"},
        ],
        "health": [
            {"step": 1, "action": "Check service/process/runtime status", "tool": "terminal", "model": "", "cost": "free/local"},
            {"step": 2, "action": "Check cron jobs and logs", "tool": "terminal", "model": "", "cost": "free/local"},
            {"step": 3, "action": "Report or fix actionable issues", "tool": "send_message", "model": cheap, "cost": "configured"},
        ],
        "memory": [
            {"step": 1, "action": "Score memories", "tool": "memory_score", "model": "", "cost": "free/local"},
            {"step": 2, "action": "Decay stale entries", "tool": "memory_decay", "model": "", "cost": "free/local"},
            {"step": 3, "action": "Consolidate recent learnings", "tool": "consolidate_daily_memory", "model": cheap, "cost": "configured"},
        ],
        "goal_review": [
            {"step": 1, "action": "List goals", "tool": "evey_goals", "model": "", "cost": "free/local"},
            {"step": 2, "action": "Evaluate next action", "tool": "autonomous_decide", "model": "", "cost": "free/local"},
            {"step": 3, "action": "Update goals if progress changed", "tool": "evey_goals", "model": "", "cost": "free/local"},
        ],
        "email": [
            {"step": 1, "action": "Load Gmail maintenance automation skill", "tool": "skill_view", "model": "", "cost": "free/local"},
            {"step": 2, "action": "Classify receipts/invoices/promos safely", "tool": "email_screen", "model": cheap, "cost": "configured"},
            {"step": 3, "action": "Forward receipts and archive safe promos", "tool": "email_screen", "model": "", "cost": "free/local"},
        ],
    }


_TYPE_TO_TEMPLATE = {
    "research_deep": "research", "research_quick": "research",
    "code_change": "code", "project_work": "project", "health_check": "health",
    "memory_maintenance": "memory", "goal_review": "goal_review",
    "cost_review": "health", "self_improve": "memory", "creative_writing": "research",
    "email_maintenance": "email",
}


def plan_handler(args, **kwargs):
    try:
        goal = args.get("goal", "")
        if not goal:
            return json.dumps({"error": "No goal provided"})
        constraints = args.get("constraints", "free-only")
        max_steps = min(int(args.get("max_steps", 8)), 12)
        task_type = _classify_goal(goal)
        tpl_key = _TYPE_TO_TEMPLATE.get(task_type, "research")
        templates = _templates()
        steps = [dict(s) for s in templates.get(tpl_key, templates["research"])][:max_steps]

        if constraints == "fast":
            steps = steps[:2]
        elif constraints == "thorough":
            steps.append({"step": len(steps) + 1, "action": "Quality reflection", "tool": "autonomous_reflect", "model": "", "cost": "free/local"})

        for i, s in enumerate(steps):
            s["step"] = i + 1

        return json.dumps({
            "status": "planned", "goal": goal, "task_type": task_type,
            "template": tpl_key, "steps": steps, "total_steps": len(steps),
            "constraints": constraints, "time_context": _time_context(),
            "config_path": str(CONFIG_PATH),
        })
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


REFLECT_SCHEMA = {
    "name": "autonomous_reflect",
    "description": "Post-action quality scoring using heuristics (no LLM cost). Checks completeness, length adequacy, and error detection.",
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


def _heuristic_score(task, result):
    score = 5.0
    assessment = []
    task_words = set(task.lower().split()) - STOPWORDS
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
            suggestion = "Needs rework. Try a different approach" + (f" or model (was: {model})" if model else "") + "."
        entry = {"action": "reflection", "task": task[:200], "score": score, "model": model, "assessment": assessment}
        _log_decision(entry)
        return json.dumps({
            "status": "reflected", "score": score,
            "label": "excellent" if score >= 9 else "good" if score >= 7 else "adequate" if score >= 5 else "poor",
            "assessment": assessment,
            "suggestion": suggestion,
        })
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


def register(ctx):
    ctx.register_tool(name="autonomous_decide", toolset="evey_autonomy", schema=DECIDE_SCHEMA, handler=decide_handler)
    ctx.register_tool(name="autonomous_plan", toolset="evey_autonomy", schema=PLAN_SCHEMA, handler=plan_handler)
    ctx.register_tool(name="autonomous_reflect", toolset="evey_autonomy", schema=REFLECT_SCHEMA, handler=reflect_handler)
