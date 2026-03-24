"""Evey Scheduler — manage V's schedule.

Simple but functional schedule manager. Stores events in a JSON file.
Evey can add events, list upcoming items, and remove completed ones.
Morning briefing cron reads this to tell V what's on today.

Future: integrate with Google Calendar, Samsung watch, n8n webhooks.
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("evey.scheduler")

SCHEDULE_FILE = Path(os.path.expanduser("~/.hermes/workspace/manager/schedule.json"))


def _load():
    if SCHEDULE_FILE.exists():
        try:
            return json.loads(SCHEDULE_FILE.read_text())
        except (json.JSONDecodeError, KeyError):
            pass
    return {"events": []}


def _save(data):
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULE_FILE.write_text(json.dumps(data, indent=2))


ADD_SCHEMA = {
    "name": "schedule_add",
    "description": (
        "Add an event or reminder to V's schedule. "
        "Examples: meeting at 2pm, deadline Friday, reminder to check deploy tomorrow. "
        "Evey manages V's calendar."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Event title (e.g., 'Team standup', 'Deploy to prod')",
            },
            "when": {
                "type": "string",
                "description": "Date/time (e.g., '2026-03-21 14:00', 'tomorrow 9am', 'Friday')",
            },
            "duration_minutes": {
                "type": "number",
                "description": "Duration in minutes (default: 30)",
            },
            "category": {
                "type": "string",
                "enum": ["meeting", "deadline", "reminder", "task", "personal"],
                "description": "Event category",
            },
            "notes": {
                "type": "string",
                "description": "Additional notes",
            },
        },
        "required": ["title", "when"],
    },
}

LIST_SCHEMA = {
    "name": "schedule_list",
    "description": (
        "List V's upcoming schedule. Shows today's events by default, "
        "or specify a date range. Use in morning briefings."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "days_ahead": {
                "type": "number",
                "description": "How many days ahead to show (default: 1 = today only)",
            },
            "category": {
                "type": "string",
                "description": "Filter by category (optional)",
            },
        },
    },
}

REMOVE_SCHEMA = {
    "name": "schedule_remove",
    "description": "Remove a completed or cancelled event by its ID.",
    "parameters": {
        "type": "object",
        "properties": {
            "event_id": {
                "type": "string",
                "description": "Event ID to remove",
            },
        },
        "required": ["event_id"],
    },
}


def _parse_when(when_str):
    """Best-effort date parsing. Returns ISO string."""
    now = datetime.now()
    w = when_str.lower().strip()

    # Handle relative dates
    if "tomorrow" in w:
        base = now + timedelta(days=1)
        time_part = w.replace("tomorrow", "").strip()
    elif "today" in w:
        base = now
        time_part = w.replace("today", "").strip()
    elif w in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
        days_ahead = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                      "friday": 4, "saturday": 5, "sunday": 6}
        target = days_ahead[w]
        current = now.weekday()
        delta = (target - current) % 7
        if delta == 0:
            delta = 7
        base = now + timedelta(days=delta)
        time_part = ""
    else:
        # Try direct parse
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%m/%d %H:%M", "%d %H:%M"):
            try:
                return datetime.strptime(when_str.strip(), fmt).replace(year=now.year).isoformat()
            except ValueError:
                continue
        return when_str  # Give up, store as-is

    # Parse time part
    if time_part:
        time_part = time_part.replace("at ", "").strip()
        for fmt in ("%H:%M", "%I%p", "%I:%M%p", "%I %p"):
            try:
                t = datetime.strptime(time_part, fmt)
                base = base.replace(hour=t.hour, minute=t.minute, second=0)
                break
            except ValueError:
                continue
    else:
        base = base.replace(hour=9, minute=0, second=0)  # Default 9am

    return base.isoformat()


def handle_add(args, **kwargs):
    data = _load()
    event_id = f"ev-{int(time.time())}"
    event = {
        "id": event_id,
        "title": args.get("title", ""),
        "when": _parse_when(args.get("when", "")),
        "when_raw": args.get("when", ""),
        "duration_minutes": args.get("duration_minutes", 30),
        "category": args.get("category", "task"),
        "notes": args.get("notes", ""),
        "created_at": datetime.now().isoformat(),
        "status": "active",
    }
    data["events"].append(event)
    _save(data)
    logger.info(f"Schedule: added '{event['title']}' at {event['when']}")
    return json.dumps({"status": "added", "event": event})


def handle_list(args, **kwargs):
    data = _load()
    days_ahead = int(args.get("days_ahead", 1))
    category = args.get("category", "")

    now = datetime.now()
    cutoff = now + timedelta(days=days_ahead)

    events = []
    for ev in data["events"]:
        if ev.get("status") != "active":
            continue
        if category and ev.get("category") != category:
            continue
        # Include all events (even if we can't parse the date)
        events.append(ev)

    # Sort by when field
    events.sort(key=lambda e: e.get("when", ""))

    if not events:
        return json.dumps({"status": "empty", "message": f"No events in the next {days_ahead} day(s)."})

    return json.dumps({
        "status": "ok",
        "count": len(events),
        "events": events,
        "period": f"next {days_ahead} day(s)",
    })


def handle_remove(args, **kwargs):
    data = _load()
    event_id = args.get("event_id", "")
    found = False
    for ev in data["events"]:
        if ev["id"] == event_id:
            ev["status"] = "removed"
            found = True
            break
    if found:
        _save(data)
        return json.dumps({"status": "removed", "event_id": event_id})
    return json.dumps({"status": "not_found", "event_id": event_id})


def register(ctx):
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    ctx.register_tool(name="schedule_add", toolset="evey_scheduler", schema=ADD_SCHEMA, handler=handle_add)
    ctx.register_tool(name="schedule_list", toolset="evey_scheduler", schema=LIST_SCHEMA, handler=handle_list)
    ctx.register_tool(name="schedule_remove", toolset="evey_scheduler", schema=REMOVE_SCHEMA, handler=handle_remove)
    logger.info("evey-scheduler loaded — V's schedule manager active")
