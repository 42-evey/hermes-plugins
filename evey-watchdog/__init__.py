"""Evey Watchdog — self-monitoring and alerting.

Tracks Evey's last activity. If she goes silent for 2+ hours during work
hours (9am-9pm), sends an alert to ntfy. The heartbeat cron should call
watchdog_heartbeat regularly to prove she's alive.

Also provides watchdog_status for Evey to check her own uptime/activity.
"""

import json
import logging
import os
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("evey.watchdog")

STATE_FILE = Path(os.path.expanduser("~/.hermes/workspace/watchdog-state.json"))
NTFY_URL = os.environ.get("NTFY_URL", "http://hermes-ntfy:80")
SILENT_THRESHOLD_MINUTES = 120  # 2 hours
WORK_HOURS = (9, 21)  # 9am to 9pm


def _load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, KeyError):
            pass
    return {
        "last_heartbeat": 0,
        "last_activity": "",
        "total_heartbeats": 0,
        "alerts_sent_today": 0,
        "alert_date": "",
    }


def _save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _is_work_hours():
    hour = int(time.strftime("%H"))
    return WORK_HOURS[0] <= hour < WORK_HOURS[1]


def _send_ntfy_alert(message):
    """Send alert to ntfy evey-alerts topic."""
    try:
        data = message.encode()
        req = urllib.request.Request(
            f"{NTFY_URL}/evey-alerts",
            data=data,
            headers={"Title": "Evey Watchdog Alert", "Priority": "high", "Tags": "warning"},
        )
        urllib.request.urlopen(req, timeout=5)
        logger.info(f"Watchdog alert sent: {message[:60]}")
        return True
    except Exception as e:
        logger.error(f"Watchdog alert failed: {e}")
        return False


HEARTBEAT_SCHEMA = {
    "name": "watchdog_heartbeat",
    "description": (
        "Record a heartbeat — proves Evey is alive and active. "
        "Call this during cron jobs or after completing tasks. "
        "If no heartbeat for 2+ hours during work hours, V gets an alert."
    ),
    "parameters": {"type": "object", "properties": {
        "activity": {
            "type": "string",
            "description": "Brief description of what you just did (e.g., 'completed context-sync', 'ran research cron')",
        },
    }},
}

STATUS_SCHEMA = {
    "name": "watchdog_status",
    "description": "Check watchdog state — last heartbeat time, silence duration, alert status.",
    "parameters": {"type": "object", "properties": {}},
}


def handle_heartbeat(args, **kwargs):
    state = _load_state()
    now = time.time()
    activity = args.get("activity", "heartbeat")

    # Check if we were silent too long (before recording new heartbeat)
    was_silent = False
    if state["last_heartbeat"] > 0:
        silent_minutes = (now - state["last_heartbeat"]) / 60
        if silent_minutes > SILENT_THRESHOLD_MINUTES and _is_work_hours():
            was_silent = True

    state["last_heartbeat"] = now
    state["last_activity"] = activity
    state["total_heartbeats"] = state.get("total_heartbeats", 0) + 1

    # Reset daily alert counter
    today = time.strftime("%Y-%m-%d")
    if state.get("alert_date") != today:
        state["alerts_sent_today"] = 0
        state["alert_date"] = today

    _save_state(state)

    result = {
        "status": "alive",
        "activity": activity,
        "total_heartbeats": state["total_heartbeats"],
    }

    if was_silent:
        result["warning"] = f"You were silent for {int(silent_minutes)} minutes"

    return json.dumps(result)


def handle_status(args, **kwargs):
    state = _load_state()
    now = time.time()

    if state["last_heartbeat"] > 0:
        silent_minutes = int((now - state["last_heartbeat"]) / 60)
        last_time = datetime.fromtimestamp(state["last_heartbeat"]).strftime("%H:%M:%S")
    else:
        silent_minutes = -1
        last_time = "never"

    is_silent = silent_minutes > SILENT_THRESHOLD_MINUTES and _is_work_hours()

    return json.dumps({
        "last_heartbeat": last_time,
        "last_activity": state.get("last_activity", "none"),
        "silent_minutes": silent_minutes,
        "is_silent_alert": is_silent,
        "total_heartbeats": state.get("total_heartbeats", 0),
        "alerts_sent_today": state.get("alerts_sent_today", 0),
        "work_hours": _is_work_hours(),
        "threshold_minutes": SILENT_THRESHOLD_MINUTES,
    })


def register(ctx):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Check if Evey has been silent on startup
    state = _load_state()
    if state["last_heartbeat"] > 0 and _is_work_hours():
        silent_minutes = (time.time() - state["last_heartbeat"]) / 60
        today = time.strftime("%Y-%m-%d")
        if silent_minutes > SILENT_THRESHOLD_MINUTES and state.get("alerts_sent_today", 0) < 3:
            if state.get("alert_date") != today:
                state["alerts_sent_today"] = 0
                state["alert_date"] = today
            _send_ntfy_alert(f"Evey has been silent for {int(silent_minutes)} minutes during work hours. Last activity: {state.get('last_activity', 'unknown')}")
            state["alerts_sent_today"] = state.get("alerts_sent_today", 0) + 1
            _save_state(state)

    ctx.register_tool(name="watchdog_heartbeat", toolset="evey_watchdog", schema=HEARTBEAT_SCHEMA, handler=handle_heartbeat)
    ctx.register_tool(name="watchdog_status", toolset="evey_watchdog", schema=STATUS_SCHEMA, handler=handle_status)
    logger.info("evey-watchdog loaded — monitoring for silence")
