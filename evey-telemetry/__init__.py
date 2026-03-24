"""Evey Telemetry Plugin — Structured logging and observability.

Emits structured JSON events for every tool call, delegation, error,
and key agent action. Events go to a rotating log file at
~/.hermes/telemetry/events.jsonl for dashboard consumption.

Provides a telemetry_query tool for Evey to inspect her own metrics.
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("evey.telemetry")

TELEMETRY_DIR = Path(os.path.expanduser("~/.hermes/telemetry"))
EVENTS_FILE = TELEMETRY_DIR / "events.jsonl"
MAX_EVENTS_FILE_SIZE = 10 * 1024 * 1024  # 10MB before rotation

# In-memory metrics for the current session
SESSION_METRICS = {
    "session_id": str(uuid.uuid4())[:8],
    "start_time": datetime.now(timezone.utc).isoformat(),
    "tool_calls": 0,
    "delegations": 0,
    "errors": 0,
    "total_tokens": 0,
    "events_emitted": 0,
}


def _ensure_dir():
    TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)


def _rotate_if_needed():
    """Rotate events file if it exceeds max size."""
    if EVENTS_FILE.exists() and EVENTS_FILE.stat().st_size > MAX_EVENTS_FILE_SIZE:
        rotated = TELEMETRY_DIR / f"events.{int(time.time())}.jsonl"
        EVENTS_FILE.rename(rotated)
        # Keep only last 5 rotated files
        rotated_files = sorted(TELEMETRY_DIR.glob("events.*.jsonl"))
        for old in rotated_files[:-5]:
            old.unlink(missing_ok=True)


def emit_event(event_type, data=None):
    """Write a structured event to the telemetry log."""
    try:
        _ensure_dir()
        _rotate_if_needed()
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "sid": SESSION_METRICS["session_id"],
            "type": event_type,
            **(data or {}),
        }
        with open(EVENTS_FILE, "a") as f:
            f.write(json.dumps(event, default=str) + "\n")
        SESSION_METRICS["events_emitted"] += 1
    except Exception as e:
        logger.debug(f"Telemetry emit failed: {e}")


def track_tool_call(tool_name, duration_ms=0, success=True, tokens=0, error=None):
    """Track a tool call event."""
    SESSION_METRICS["tool_calls"] += 1
    if tokens:
        SESSION_METRICS["total_tokens"] += tokens
    if not success:
        SESSION_METRICS["errors"] += 1
    emit_event("tool_call", {
        "tool": tool_name,
        "duration_ms": duration_ms,
        "success": success,
        "tokens": tokens,
        "error": str(error)[:200] if error else None,
    })


def track_delegation(model, task_type, duration_ms=0, success=True, tokens=0, error=None):
    """Track a delegation event."""
    SESSION_METRICS["delegations"] += 1
    if tokens:
        SESSION_METRICS["total_tokens"] += tokens
    if not success:
        SESSION_METRICS["errors"] += 1
    emit_event("delegation", {
        "model": model,
        "task_type": task_type,
        "duration_ms": duration_ms,
        "success": success,
        "tokens": tokens,
        "error": str(error)[:200] if error else None,
    })


def track_error(source, error_msg, severity="error"):
    """Track an error event."""
    SESSION_METRICS["errors"] += 1
    emit_event("error", {
        "source": source,
        "message": str(error_msg)[:500],
        "severity": severity,
    })


def track_cron(job_name, status, duration_ms=0, output_preview=None):
    """Track a cron job execution."""
    emit_event("cron", {
        "job": job_name,
        "status": status,
        "duration_ms": duration_ms,
        "output_preview": str(output_preview)[:200] if output_preview else None,
    })


# ---- Tool: telemetry_query ----

QUERY_SCHEMA = {
    "name": "telemetry_query",
    "description": (
        "Query Evey's telemetry data. View recent events, session metrics, "
        "error rates, delegation performance, and tool usage patterns. "
        "Use this to understand your own performance and identify issues."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query_type": {
                "type": "string",
                "enum": ["session_metrics", "recent_errors", "recent_events", "delegation_stats", "tool_stats"],
                "description": "What to query",
            },
            "limit": {
                "type": "number",
                "description": "Max results to return (default: 20)",
            },
        },
        "required": ["query_type"],
    },
}


def _read_recent_events(limit=50, event_type=None):
    """Read recent events from the log file."""
    if not EVENTS_FILE.exists():
        return []
    events = []
    try:
        with open(EVENTS_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    if event_type is None or ev.get("type") == event_type:
                        events.append(ev)
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return events[-limit:]


def query_handler(args, **kwargs):
    query_type = args.get("query_type", "session_metrics")
    limit = int(args.get("limit", 20))

    if query_type == "session_metrics":
        metrics = dict(SESSION_METRICS)
        metrics["uptime_seconds"] = int(
            (datetime.now(timezone.utc) - datetime.fromisoformat(metrics["start_time"])).total_seconds()
        )
        return json.dumps({"status": "ok", "metrics": metrics})

    elif query_type == "recent_errors":
        errors = _read_recent_events(limit, "error")
        return json.dumps({"status": "ok", "count": len(errors), "errors": errors})

    elif query_type == "recent_events":
        events = _read_recent_events(limit)
        return json.dumps({"status": "ok", "count": len(events), "events": events})

    elif query_type == "delegation_stats":
        delegations = _read_recent_events(200, "delegation")
        if not delegations:
            return json.dumps({"status": "ok", "message": "No delegation events yet"})
        models = {}
        for d in delegations:
            m = d.get("model", "unknown")
            if m not in models:
                models[m] = {"calls": 0, "successes": 0, "total_tokens": 0, "total_ms": 0}
            models[m]["calls"] += 1
            if d.get("success"):
                models[m]["successes"] += 1
            models[m]["total_tokens"] += d.get("tokens", 0)
            models[m]["total_ms"] += d.get("duration_ms", 0)
        for m in models:
            c = models[m]["calls"]
            models[m]["success_rate"] = f"{models[m]['successes']/c*100:.0f}%" if c else "0%"
            models[m]["avg_ms"] = int(models[m]["total_ms"] / c) if c else 0
        return json.dumps({"status": "ok", "models": models, "total_delegations": len(delegations)})

    elif query_type == "tool_stats":
        tool_calls = _read_recent_events(500, "tool_call")
        if not tool_calls:
            return json.dumps({"status": "ok", "message": "No tool call events yet"})
        tools = {}
        for tc in tool_calls:
            t = tc.get("tool", "unknown")
            if t not in tools:
                tools[t] = {"calls": 0, "errors": 0, "total_ms": 0}
            tools[t]["calls"] += 1
            if not tc.get("success"):
                tools[t]["errors"] += 1
            tools[t]["total_ms"] += tc.get("duration_ms", 0)
        for t in tools:
            c = tools[t]["calls"]
            tools[t]["error_rate"] = f"{tools[t]['errors']/c*100:.0f}%" if c else "0%"
            tools[t]["avg_ms"] = int(tools[t]["total_ms"] / c) if c else 0
        return json.dumps({"status": "ok", "tools": tools, "total_calls": len(tool_calls)})

    return json.dumps({"status": "error", "message": f"Unknown query type: {query_type}"})


def register(ctx):
    _ensure_dir()
    emit_event("plugin_loaded", {"plugin": "evey-telemetry", "version": "1.0.0"})
    ctx.register_tool(
        name="telemetry_query",
        toolset="evey_telemetry",
        schema=QUERY_SCHEMA,
        handler=query_handler,
    )
    logger.info("evey-telemetry plugin loaded — structured logging active")
