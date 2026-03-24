"""Evey Session Guard — checkpoint session state to prevent memory loss.

Saves current task context, decisions, and progress before compression
or long-running operations. Hooks into pre_tool_call to auto-checkpoint
before delegate_task calls.
"""

import json
import os
import time

CHECKPOINT_DIR = os.path.expanduser("~/.hermes/workspace/checkpoints")

CHECKPOINT_SCHEMA = {
    "name": "session_checkpoint",
    "description": (
        "Save a checkpoint of current work state. Use before long tasks, "
        "compression, or when you have important context that might be lost. "
        "Checkpoints persist across sessions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "description": "Short label for this checkpoint",
            },
            "context": {
                "type": "string",
                "description": "What you're working on, key decisions made, next steps",
            },
            "active_tasks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of in-progress tasks",
            },
        },
        "required": ["label", "context"],
    },
}

RESTORE_SCHEMA = {
    "name": "session_restore",
    "description": (
        "Restore the last checkpoint or a specific one. "
        "Use at session start to recover context from previous work."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "description": "Specific checkpoint label to restore (optional — latest if omitted)",
            },
        },
    },
}


def checkpoint_handler(args, **kwargs):
    try:
        label = args.get("label", "auto")
        context = args.get("context", "")
        active_tasks = args.get("active_tasks", [])

        os.makedirs(CHECKPOINT_DIR, exist_ok=True)

        checkpoint = {
            "label": label,
            "context": context,
            "active_tasks": active_tasks,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "task_id": kwargs.get("task_id", ""),
        }

        # Save with timestamp
        safe_label = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)
        filepath = os.path.join(CHECKPOINT_DIR, f"{safe_label}.json")
        with open(filepath, "w") as f:
            json.dump(checkpoint, f, indent=2)

        # Also save as "latest"
        latest = os.path.join(CHECKPOINT_DIR, "latest.json")
        with open(latest, "w") as f:
            json.dump(checkpoint, f, indent=2)

        return json.dumps({
            "status": "saved",
            "label": label,
            "path": filepath,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def restore_handler(args, **kwargs):
    try:
        label = args.get("label", "")

        if label:
            safe_label = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)
            filepath = os.path.join(CHECKPOINT_DIR, f"{safe_label}.json")
        else:
            filepath = os.path.join(CHECKPOINT_DIR, "latest.json")

        if not os.path.exists(filepath):
            # List available checkpoints
            available = []
            if os.path.isdir(CHECKPOINT_DIR):
                for f in sorted(os.listdir(CHECKPOINT_DIR)):
                    if f.endswith(".json") and f != "latest.json":
                        available.append(f.replace(".json", ""))
            return json.dumps({
                "status": "not_found",
                "available": available,
                "message": "No checkpoint found" if not available else f"Available: {', '.join(available)}",
            })

        with open(filepath) as f:
            checkpoint = json.load(f)

        return json.dumps({
            "status": "restored",
            "checkpoint": checkpoint,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def register(ctx):
    ctx.register_tool(name="session_checkpoint", toolset="evey_session", schema=CHECKPOINT_SCHEMA, handler=checkpoint_handler)
    ctx.register_tool(name="session_restore", toolset="evey_session", schema=RESTORE_SCHEMA, handler=restore_handler)
