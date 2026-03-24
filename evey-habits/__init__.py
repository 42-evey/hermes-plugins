"""Evey Habits — learn V's patterns over time.

Tracks:
- When V messages (hour distribution → predict active hours)
- Topics V asks about (frequency → anticipate interests)
- Response preferences (length, detail level, tone)
- Work rhythm (busy times, break patterns)

Call habits_log after every V interaction. Call habits_insights
to get a summary of learned patterns. Self-improve cron uses
this to update Evey's behavior.
"""

import json
import logging
import os
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("evey.habits")

HABITS_FILE = Path(os.path.expanduser("~/.hermes/workspace/manager/habits.json"))

DEFAULT_DATA = {
    "interactions": [],
    "hour_counts": {},
    "topic_counts": {},
    "avg_message_length": 0,
    "total_interactions": 0,
    "first_seen": "",
    "last_seen": "",
}


def _load():
    if HABITS_FILE.exists():
        try:
            return json.loads(HABITS_FILE.read_text())
        except (json.JSONDecodeError, KeyError):
            pass
    return dict(DEFAULT_DATA)


def _save(data):
    HABITS_FILE.parent.mkdir(parents=True, exist_ok=True)
    HABITS_FILE.write_text(json.dumps(data, indent=2))


LOG_SCHEMA = {
    "name": "habits_log",
    "description": (
        "Log an interaction with V to learn patterns. Call after V messages you. "
        "Over time this builds a profile of V's work habits, interests, and preferences."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "Brief topic of the interaction (e.g., 'scheduling', 'research request', 'status check', 'chat')",
            },
            "v_message_length": {
                "type": "number",
                "description": "Approximate character count of V's message",
            },
            "v_mood": {
                "type": "string",
                "enum": ["focused", "casual", "urgent", "curious", "frustrated"],
                "description": "V's apparent mood/energy",
            },
            "response_was_good": {
                "type": "boolean",
                "description": "Did V seem satisfied with your response? (true if no correction needed)",
            },
        },
        "required": ["topic"],
    },
}

INSIGHTS_SCHEMA = {
    "name": "habits_insights",
    "description": (
        "Get insights about V's patterns — active hours, favorite topics, "
        "communication preferences. Use during self-improve or when adapting behavior."
    ),
    "parameters": {"type": "object", "properties": {}},
}


def handle_log(args, **kwargs):
    data = _load()
    now = datetime.now()
    hour = str(now.hour)

    interaction = {
        "timestamp": now.isoformat(),
        "hour": now.hour,
        "day_of_week": now.strftime("%A"),
        "topic": args.get("topic", "general"),
        "v_message_length": args.get("v_message_length", 0),
        "v_mood": args.get("v_mood", ""),
        "response_was_good": args.get("response_was_good", True),
    }

    # Update counters
    data["hour_counts"][hour] = data["hour_counts"].get(hour, 0) + 1
    topic = args.get("topic", "general")
    data["topic_counts"][topic] = data["topic_counts"].get(topic, 0) + 1
    data["total_interactions"] = data.get("total_interactions", 0) + 1

    if not data.get("first_seen"):
        data["first_seen"] = now.isoformat()
    data["last_seen"] = now.isoformat()

    # Keep last 200 interactions
    data["interactions"].append(interaction)
    data["interactions"] = data["interactions"][-200:]

    # Running average message length
    msg_len = args.get("v_message_length", 0)
    if msg_len > 0:
        total = data.get("total_interactions", 1)
        old_avg = data.get("avg_message_length", 0)
        data["avg_message_length"] = round(old_avg + (msg_len - old_avg) / total)

    _save(data)
    return json.dumps({"status": "logged", "total_interactions": data["total_interactions"]})


def handle_insights(args, **kwargs):
    data = _load()

    if data["total_interactions"] == 0:
        return json.dumps({"status": "no_data", "message": "No interactions logged yet. Call habits_log after V messages."})

    # Peak hours (top 3)
    hours = data.get("hour_counts", {})
    peak_hours = sorted(hours.items(), key=lambda x: x[1], reverse=True)[:3]
    peak_display = [f"{h}:00 ({c} msgs)" for h, c in peak_hours]

    # Top topics
    topics = data.get("topic_counts", {})
    top_topics = sorted(topics.items(), key=lambda x: x[1], reverse=True)[:5]

    # Mood distribution from recent interactions
    recent = data["interactions"][-50:]
    moods = Counter(i.get("v_mood", "") for i in recent if i.get("v_mood"))
    mood_display = dict(moods.most_common(3))

    # Success rate
    good = sum(1 for i in recent if i.get("response_was_good", True))
    total = len(recent)
    success_rate = round(good / total * 100) if total else 0

    # Day distribution
    days = Counter(i.get("day_of_week", "") for i in data["interactions"] if i.get("day_of_week"))
    busiest_days = [d for d, _ in days.most_common(2)]

    return json.dumps({
        "status": "ok",
        "total_interactions": data["total_interactions"],
        "peak_hours": peak_display,
        "top_topics": [{"topic": t, "count": c} for t, c in top_topics],
        "moods": mood_display,
        "response_success_rate": f"{success_rate}%",
        "avg_message_length": data.get("avg_message_length", 0),
        "busiest_days": busiest_days,
        "tracking_since": data.get("first_seen", ""),
        "last_interaction": data.get("last_seen", ""),
        "recommendations": _generate_recommendations(data),
    })


def _generate_recommendations(data):
    """Generate actionable recommendations from patterns."""
    recs = []
    hours = data.get("hour_counts", {})
    if hours:
        peak = max(hours, key=hours.get)
        recs.append(f"V is most active around {peak}:00 — prioritize proactive nudges near this time")

    topics = data.get("topic_counts", {})
    if topics:
        top = max(topics, key=topics.get)
        recs.append(f"V asks about '{top}' most often — stay prepared on this topic")

    recent = data["interactions"][-20:]
    frustrated = sum(1 for i in recent if i.get("v_mood") == "frustrated")
    if frustrated > 3:
        recs.append("V has been frustrated recently — be extra careful with response quality")

    return recs


def register(ctx):
    HABITS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ctx.register_tool(name="habits_log", toolset="evey_habits", schema=LOG_SCHEMA, handler=handle_log)
    ctx.register_tool(name="habits_insights", toolset="evey_habits", schema=INSIGHTS_SCHEMA, handler=handle_insights)
    logger.info("evey-habits loaded — learning V's patterns")
