"""Evey Digest Plugin — aggregates overnight activity for V's morning briefing."""

import base64
import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))
LANGFUSE_URL = os.environ.get("LANGFUSE_HOST", "")
LANGFUSE_PK = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SK = os.environ.get("LANGFUSE_SECRET_KEY", "")
NTFY_URL = os.environ.get("NTFY_URL", "http://hermes-ntfy:80")
HTTP_TIMEOUT = 10

SCHEMA = {
    "name": "daily_digest",
    "description": (
        "Generate a morning digest for V. Aggregates overnight costs from Langfuse, "
        "cron job health, bridge task status, goal counts, and ntfy alerts from the last 24h. "
        "No parameters needed — just call it."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


def _http_get(url, headers=None):
    """GET request with timeout. Returns parsed JSON or None."""
    if headers:
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            return None
    else:
        from evey_utils import http_get_json
        return http_get_json(url, timeout=HTTP_TIMEOUT)


def _langfuse_auth_header():
    """Build Basic auth header for Langfuse."""
    creds = f"{LANGFUSE_PK}:{LANGFUSE_SK}"
    encoded = base64.b64encode(creds.encode()).decode()
    return {"Authorization": f"Basic {encoded}"}


def _get_costs():
    """Query Langfuse for last 24h cost data."""
    try:
        since = time.strftime(
            "%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 86400)
        )
        url = f"{LANGFUSE_URL}/api/public/traces?limit=100&fromTimestamp={since}"
        data = _http_get(url, _langfuse_auth_header())
        if not data or "data" not in data:
            return {"total": 0.0, "top_model": "unknown", "trace_count": 0}

        traces = data["data"]
        total_cost = 0.0
        model_costs = {}

        for trace in traces:
            cost = trace.get("totalCost") or 0.0
            total_cost += cost
            # Pull model from metadata or tags if available
            meta = trace.get("metadata") or {}
            model = meta.get("model") or trace.get("name") or "unknown"
            model_costs[model] = model_costs.get(model, 0.0) + cost

        top_model = max(model_costs, key=model_costs.get) if model_costs else "unknown"

        return {
            "total": round(total_cost, 4),
            "top_model": top_model,
            "trace_count": len(traces),
        }
    except Exception:
        return {"total": 0.0, "top_model": "unknown", "trace_count": 0}


def _get_cron():
    """Read cron job status from jobs.json."""
    try:
        jobs_path = HERMES_HOME / "cron" / "jobs.json"
        if not jobs_path.exists():
            return {"total": 0, "healthy": 0, "errors": 0}

        jobs = json.loads(jobs_path.read_text())
        total = len(jobs)
        errors = sum(
            1 for j in jobs if j.get("last_status") == "error"
            or j.get("enabled") is False
        )
        healthy = total - errors
        return {"total": total, "healthy": healthy, "errors": errors}
    except Exception:
        return {"total": 0, "healthy": 0, "errors": 0}


def _get_bridge():
    """Check bridge inbox/outbox for pending items."""
    try:
        bridge_dir = HERMES_HOME / "claude-bridge"
        inbox = bridge_dir / "inbox"
        outbox = bridge_dir / "outbox"

        inbox_count = len(list(inbox.iterdir())) if inbox.is_dir() else 0
        outbox_count = len(list(outbox.iterdir())) if outbox.is_dir() else 0

        return {"inbox": inbox_count, "outbox": outbox_count}
    except Exception:
        return {"inbox": 0, "outbox": 0}


def _get_goals():
    """Count active/completed goals from goals.md."""
    try:
        goals_path = HERMES_HOME / "goals.md"
        if not goals_path.exists():
            return {"active": 0, "completed": 0}

        content = goals_path.read_text()
        current_section = None
        active = 0
        completed = 0

        for line in content.split("\n"):
            if line.startswith("## "):
                current_section = line[3:].strip()
            elif line.strip().startswith("- "):
                if current_section == "Active":
                    active += 1
                elif current_section == "Completed":
                    completed += 1

        return {"active": active, "completed": completed}
    except Exception:
        return {"active": 0, "completed": 0}


def _get_alerts():
    """Check ntfy for alerts in last 24h."""
    try:
        url = f"{NTFY_URL}/evey-alerts/json?poll=1&since=24h"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read().decode()

        # ntfy returns newline-delimited JSON
        alerts = []
        for line in raw.strip().split("\n"):
            if line.strip():
                msg = json.loads(line)
                if msg.get("event") == "message":
                    alerts.append(msg.get("message", ""))

        return {"count": len(alerts), "latest": alerts[:3]}
    except Exception:
        return {"count": 0, "latest": []}


def handler(args, **kwargs):
    try:
        costs = _get_costs()
        cron = _get_cron()
        bridge = _get_bridge()
        goals = _get_goals()
        alerts = _get_alerts()

        pending = bridge["inbox"] + bridge["outbox"]

        digest = {
            "overnight": (
                f"{costs['trace_count']} API calls, "
                f"{cron['total']} cron jobs ran, "
                f"{alerts['count']} alerts"
            ),
            "costs": (
                f"${costs['total']:.2f} spent, "
                f"top model: {costs['top_model']}"
            ),
            "cron": (
                f"{cron['healthy']}/{cron['total']} jobs healthy, "
                f"{cron['errors']} errors"
            ),
            "bridge": f"{pending} pending tasks",
            "goals": (
                f"{goals['active']} active, "
                f"{goals['completed']} completed"
            ),
            "alerts": (
                f"{alerts['count']} alerts in 24h"
                + (f" — latest: {alerts['latest'][0]}" if alerts["latest"] else "")
            ),
        }

        return json.dumps(digest)

    except Exception as e:
        return json.dumps({"error": str(e)})


def register(ctx):
    ctx.register_tool(
        name="daily_digest",
        toolset="evey_digest",
        schema=SCHEMA,
        handler=handler,
    )
