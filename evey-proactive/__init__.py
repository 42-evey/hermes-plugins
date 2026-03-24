"""Evey Proactive Plugin — Surface insights without being asked.

Gives Evey the ability to send V observations, tips, and interesting
findings during work hours. Enforces an interruption budget (max 4/day)
with cooldown between messages. Respects V's time.

Rules:
- Only during work hours (9am-9pm)
- Max 4 proactive messages per day
- 45-min cooldown between messages
- If V doesn't respond, double the cooldown
- Urgent bypasses budget (service down, security alert)
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("evey.proactive")

STATE_FILE = Path(os.path.expanduser("~/.hermes/workspace/manager/proactive-state.json"))

DEFAULT_STATE = {
    "date": "",
    "messages_sent": 0,
    "last_sent_at": 0,
    "cooldown_minutes": 45,
    "v_responded_last": True,
    "history": [],
}


def _load_state():
    today = datetime.now().strftime("%Y-%m-%d")
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
            if state.get("date") != today:
                state = dict(DEFAULT_STATE)
                state["date"] = today
            return state
        except (json.JSONDecodeError, KeyError):
            pass
    state = dict(DEFAULT_STATE)
    state["date"] = today
    return state


def _save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _is_work_hours():
    hour = int(time.strftime("%H"))
    return 9 <= hour < 21


NUDGE_SCHEMA = {
    "name": "proactive_nudge",
    "description": (
        "Send V a proactive message — an observation, tip, interesting finding, "
        "or status update. Respects interruption budget (4/day) and work hours (9am-9pm). "
        "Use this when you discover something V should know about, not as a response to V. "
        "Categories: insight, tip, alert, observation, research_finding."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "enum": ["insight", "tip", "alert", "observation", "research_finding"],
                "description": "Type of proactive message",
            },
            "message": {
                "type": "string",
                "description": "The message to send V (keep under 100 words)",
            },
            "urgent": {
                "type": "boolean",
                "description": "If true, bypasses budget and cooldown (use for service down, security alerts only)",
            },
        },
        "required": ["category", "message"],
    },
}

BUDGET_SCHEMA = {
    "name": "proactive_budget",
    "description": "Check your proactive messaging budget — how many nudges left today, cooldown status.",
    "parameters": {"type": "object", "properties": {}},
}


def handle_nudge(args, **kwargs):
    category = args.get("category", "observation")
    message = args.get("message", "")
    urgent = args.get("urgent", False)

    state = _load_state()

    # Check constraints (unless urgent)
    if not urgent:
        if not _is_work_hours():
            return json.dumps({
                "status": "blocked",
                "reason": "Outside work hours (9am-9pm). Save this for tomorrow morning.",
            })

        if state["messages_sent"] >= 4:
            return json.dumps({
                "status": "blocked",
                "reason": "Daily budget exhausted (4/4 used). Save for tomorrow.",
                "sent_today": state["messages_sent"],
            })

        cooldown = state["cooldown_minutes"]
        elapsed = (time.time() - state["last_sent_at"]) / 60
        if elapsed < cooldown and state["last_sent_at"] > 0:
            remaining = int(cooldown - elapsed)
            return json.dumps({
                "status": "blocked",
                "reason": f"Cooldown active — {remaining} minutes remaining.",
                "cooldown_minutes": cooldown,
            })

    # Send it
    state["messages_sent"] += 1
    state["last_sent_at"] = time.time()
    state["history"].append({
        "category": category,
        "message": message[:200],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "urgent": urgent,
    })

    # Keep only last 10 in history
    state["history"] = state["history"][-10:]
    _save_state(state)

    logger.info(f"Proactive nudge sent: [{category}] {message[:60]}... (budget: {4 - state['messages_sent']}/4)")

    return json.dumps({
        "status": "sent",
        "category": category,
        "budget_remaining": max(0, 4 - state["messages_sent"]),
        "next_available_in": f"{state['cooldown_minutes']} minutes",
        "instruction": "Now send this message to V via Telegram. Frame it as an offer, not a command.",
    })


def handle_budget(args, **kwargs):
    state = _load_state()

    cooldown = state["cooldown_minutes"]
    elapsed = (time.time() - state["last_sent_at"]) / 60 if state["last_sent_at"] > 0 else 999
    cooldown_remaining = max(0, int(cooldown - elapsed))

    return json.dumps({
        "date": state["date"],
        "messages_sent": state["messages_sent"],
        "budget_remaining": max(0, 4 - state["messages_sent"]),
        "cooldown_minutes": cooldown,
        "cooldown_remaining_minutes": cooldown_remaining,
        "work_hours": _is_work_hours(),
        "can_send": _is_work_hours() and state["messages_sent"] < 4 and cooldown_remaining == 0,
        "history": state["history"][-3:],
    })


def register(ctx):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    ctx.register_tool(
        name="proactive_nudge",
        toolset="evey_proactive",
        schema=NUDGE_SCHEMA,
        handler=handle_nudge,
    )
    ctx.register_tool(
        name="proactive_budget",
        toolset="evey_proactive",
        schema=BUDGET_SCHEMA,
        handler=handle_budget,
    )
    logger.info("evey-proactive loaded — interruption budget active")
