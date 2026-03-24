"""Evey Status Plugin — ONE tool call replaces 5-8 separate checks.

Calls the dashboard's /api/evey/status endpoint which aggregates:
bridge messages, pending tasks, MQTT events, costs, cron errors,
goals, and time context — all in a single local HTTP call.

This is FREE (local network), fast (<100ms), and should be Evey's
FIRST call every session instead of 5+ separate tool calls.
"""

import json
import os
import urllib.request
import urllib.error

DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://hermes-dashboard:8088")

SCHEMA = {
    "name": "status_check",
    "description": (
        "Get complete status in ONE call: bridge messages, pending tasks, MQTT events, "
        "costs/budget, cron errors, goals, time context. This is FREE (local API). "
        "Use this FIRST every session instead of calling bridge, MQTT, goals separately. "
        "Returns everything you need to decide what to work on."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "include": {
                "type": "string",
                "description": "Optional comma-separated sections to include (default: all). Options: bridge,channel,mqtt,costs,cron,goals,time",
            },
        },
    },
}


def handler(args, **kwargs):
    try:
        url = f"{DASHBOARD_URL}/api/evey/status"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())

        # Filter sections if requested
        include = args.get("include", "")
        if include:
            sections = [s.strip() for s in include.split(",")]
            data = {k: v for k, v in data.items() if k in sections or k == "time_context"}

        # Build human-readable summary
        parts = []

        bridge = data.get("bridge", {})
        if bridge.get("unread_messages", 0) > 0:
            parts.append(f"BRIDGE: {bridge['unread_messages']} unread message(s) from Mother")
            for m in bridge.get("messages", [])[:3]:
                parts.append(f"  [{m.get('ts', '')[:16]}] {m.get('preview', '')}")
        if bridge.get("pending_tasks", 0) > 0:
            parts.append(f"TASKS: {bridge['pending_tasks']} pending from Mother")
            for t in bridge.get("tasks", [])[:3]:
                parts.append(f"  [{t.get('priority', 'normal')}] {t.get('desc', '')}")

        channel = data.get("channel", [])
        if channel:
            latest = channel[-1]
            parts.append(f"CHANNEL: last from {latest.get('from', '?')} — {latest.get('preview', '')}")

        mqtt = data.get("mqtt_events", 0)
        if mqtt > 0:
            parts.append(f"MQTT: {mqtt} events buffered")

        costs = data.get("costs", {})
        rec = costs.get("recommendation", "free")
        parts.append(f"COSTS: ${costs.get('today', 0):.4f} ({costs.get('budget_pct', 0):.0f}% of daily budget) → {rec.upper()}")
        if rec == "freeze":
            parts.append("  ⚠ BUDGET CRITICAL: Use only free/local models")
        elif rec == "cautious":
            parts.append("  ⚠ Budget tight: prefer free models")

        cron = data.get("cron", {})
        if cron.get("errored", 0) > 0:
            parts.append(f"CRON: {cron['errored']} job(s) errored")
            for e in cron.get("errors", []):
                parts.append(f"  {e.get('name', '?')}: {e.get('error', '?')}")
        else:
            parts.append(f"CRON: {cron.get('total', 0)} jobs, all OK")

        goals = data.get("goals", {})
        parts.append(f"GOALS: {goals.get('active', 0)} active, {goals.get('completed', 0)} done")

        tc = data.get("time_context", "work")
        mode_hint = {
            "morning": "Morning — run briefing, check email",
            "work": "Work hours — active tasks, research, delegation",
            "evening": "Evening — compile daily report",
            "night": "Night — maintenance only, no Telegram",
        }
        parts.append(f"TIME: {mode_hint.get(tc, tc)}")

        if not parts:
            parts.append("All clear — no pending items.")

        summary = "\n".join(parts)

        # Return summary only — raw data is too large for context window
        return json.dumps({
            "summary": summary,
            "recommendation": rec,
        })

    except urllib.error.URLError as e:
        # Dashboard unreachable — fall back to minimal info
        return json.dumps({
            "summary": "Dashboard unreachable — run individual checks instead.",
            "raw": {},
            "recommendation": "cautious",
            "error": str(e)[:80],
        })
    except Exception as e:
        return json.dumps({"error": str(e)[:100]})


def register(ctx):
    ctx.register_tool(
        name="status_check",
        toolset="evey_status",
        schema=SCHEMA,
        handler=handler,
    )
