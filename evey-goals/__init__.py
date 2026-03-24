"""Evey Goals Plugin — autonomous objective management."""

import json
import os
import re
import time
from pathlib import Path

GOALS_PATH = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))) / "goals.md"

SCHEMA = {
    "name": "evey_goals",
    "description": (
        "Manage your goals. Actions: list (show all goals), "
        "add (add a new goal to Active or Backlog), "
        "complete (move a goal to Completed), "
        "remove (remove a goal entirely), "
        "review (get a summary for goal review). "
        "You own your goals — add, complete, and evolve them freely."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "add", "complete", "remove", "review"],
                "description": "What to do with goals",
            },
            "goal": {
                "type": "string",
                "description": "The goal text (for add/complete/remove)",
            },
            "section": {
                "type": "string",
                "enum": ["Active", "Backlog"],
                "description": "Which section to add to (default: Active)",
            },
        },
        "required": ["action"],
    },
}


def _read_goals():
    if not GOALS_PATH.exists():
        return "# Evey's Goals\n\n## Active\n\n## Completed\n\n## Backlog\n"
    return GOALS_PATH.read_text()


def _write_goals(content):
    GOALS_PATH.write_text(content)


def _parse_sections(content):
    sections = {}
    current = None
    for line in content.split("\n"):
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current and line.strip():
            sections[current].append(line)
    return sections


def handler(args, **kwargs):
    try:
        action = args.get("action", "list")
        goal_text = args.get("goal", "")
        section = args.get("section", "Active")
        content = _read_goals()

        if action == "list" or action == "review":
            sections = _parse_sections(content)
            active = sections.get("Active", [])
            completed = sections.get("Completed", [])
            backlog = sections.get("Backlog", [])

            result = {
                "active": active,
                "active_count": len(active),
                "completed": completed,
                "completed_count": len(completed),
                "backlog": backlog,
                "backlog_count": len(backlog),
            }

            if action == "review":
                result["review_prompt"] = (
                    f"You have {len(active)} active goals, {len(completed)} completed, "
                    f"{len(backlog)} in backlog. Review each active goal — "
                    "update progress, complete finished ones, add new ones if inspired."
                )

            return json.dumps(result)

        elif action == "add":
            if not goal_text:
                return json.dumps({"error": "Need a goal to add"})

            marker = f"## {section}"
            if marker in content:
                content = content.replace(
                    marker,
                    f"{marker}\n- [ ] {goal_text}",
                )
                _write_goals(content)
                return json.dumps({"status": "added", "goal": goal_text, "section": section})
            else:
                return json.dumps({"error": f"Section '{section}' not found in goals.md"})

        elif action == "complete":
            if not goal_text:
                return json.dumps({"error": "Need a goal to complete"})

            # Find and check off the goal, move to Completed
            lines = content.split("\n")
            found = False
            new_lines = []
            completed_goal = ""

            for line in lines:
                if not found and goal_text.lower() in line.lower() and "- [ ]" in line:
                    completed_goal = line.replace("- [ ]", "- [x]").strip()
                    found = True
                    # Don't add here — we'll add to Completed section
                else:
                    new_lines.append(line)

            if found and "## Completed" in content:
                content = "\n".join(new_lines)
                content = content.replace(
                    "## Completed",
                    f"## Completed\n{completed_goal}",
                )
                _write_goals(content)
                return json.dumps({"status": "completed", "goal": completed_goal})
            elif not found:
                return json.dumps({"error": f"Goal matching '{goal_text}' not found in Active"})

        elif action == "remove":
            if not goal_text:
                return json.dumps({"error": "Need a goal to remove"})

            lines = content.split("\n")
            new_lines = [l for l in lines if goal_text.lower() not in l.lower()]

            if len(new_lines) < len(lines):
                _write_goals("\n".join(new_lines))
                return json.dumps({"status": "removed", "goal": goal_text})
            else:
                return json.dumps({"error": f"Goal matching '{goal_text}' not found"})

        return json.dumps({"error": f"Unknown action: {action}"})

    except Exception as e:
        return json.dumps({"error": str(e)})


def register(ctx):
    ctx.register_tool(
        name="evey_goals",
        toolset="evey_goals",
        schema=SCHEMA,
        handler=handler,
    )
