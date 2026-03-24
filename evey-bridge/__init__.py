"""Evey Bridge Plugin — bidirectional communication with Claude Code (Mother).

Directory roles:
  inbox/   — Evey writes tasks FOR Claude Code to process
  outbox/  — Claude Code writes results/tasks FOR Evey to read
  channel.jsonl — real-time messages both directions
  archive/ — compressed old conversations (auto-rotated)

Claude Code sends as "mother" in channel.jsonl.
"""

import json
import os
import time
import gzip
import shutil
from pathlib import Path

BRIDGE_DIR = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))) / "claude-bridge"
ARCHIVE_DIR = BRIDGE_DIR / "archive"
MAX_CHANNEL_LINES = 200  # compress when channel exceeds this

TASK_SCHEMA = {
    "name": "claude_bridge_task",
    "description": (
        "Send a coding task to Claude Code (Opus) via the bridge. "
        "Use for: code changes, file edits, bug fixes, new features, patches, reviews. "
        "Claude Code will process it and put the result in the outbox."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_type": {
                "type": "string",
                "enum": ["code-change", "review", "research", "patch", "new-file"],
                "description": "Type of task",
            },
            "description": {
                "type": "string",
                "description": "Clear description of what needs to be done",
            },
            "context": {
                "type": "string",
                "description": "Relevant context: file paths, error messages, constraints",
            },
            "priority": {
                "type": "string",
                "enum": ["low", "normal", "high"],
                "description": "Task priority (default: normal)",
            },
        },
        "required": ["task_type", "description"],
    },
}

MESSAGE_SCHEMA = {
    "name": "claude_bridge_message",
    "description": (
        "Send a quick message to Claude Code via the bridge channel. "
        "Use for: status updates, questions, notifications — not full coding tasks."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The message to send",
            },
        },
        "required": ["message"],
    },
}

CHECK_SCHEMA = {
    "name": "claude_bridge_check",
    "description": (
        "Check the Claude Code bridge for completed tasks and messages. "
        "Returns any results in the outbox and recent channel messages."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}


def _ensure_dirs():
    for d in ["inbox", "outbox", "active"]:
        (BRIDGE_DIR / d).mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


def _auto_compress():
    """Compress channel.jsonl when it gets too large. Keeps last 50 lines."""
    channel = BRIDGE_DIR / "channel.jsonl"
    if not channel.exists():
        return
    lines = channel.read_text().strip().split("\n")
    if len(lines) <= MAX_CHANNEL_LINES:
        return

    # Archive old lines
    ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    archive_path = ARCHIVE_DIR / f"channel_{ts}.jsonl.gz"
    old_lines = lines[:-50]
    with gzip.open(archive_path, "wt") as f:
        f.write("\n".join(old_lines) + "\n")

    # Keep recent 50 lines
    channel.write_text("\n".join(lines[-50:]) + "\n")

    # Retention: keep 7 days of archives
    cutoff = time.time() - 7 * 86400
    for f in sorted(ARCHIVE_DIR.iterdir()):
        if f.stat().st_mtime < cutoff:
            f.unlink()


def handle_task(args, **kwargs):
    try:
        _ensure_dirs()
        task_id = str(int(time.time()))
        task_type = args.get("task_type", "code-change")
        description = args.get("description", "")
        context = args.get("context", "")
        priority = args.get("priority", "normal")

        task_content = (
            f"type: {task_type}\n"
            f"priority: {priority}\n"
            f"created_by: evey\n"
            f"created_at: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
            f"description: |\n"
        )
        for line in description.split("\n"):
            task_content += f"  {line}\n"

        if context:
            task_content += f"context: |\n"
            for line in context.split("\n"):
                task_content += f"  {line}\n"

        task_path = BRIDGE_DIR / "inbox" / f"{task_id}.yaml"
        task_path.write_text(task_content)

        return json.dumps({
            "status": "sent",
            "task_id": task_id,
            "message": f"Task {task_id} sent to Claude Code. Check outbox/ for results.",
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def handle_message(args, **kwargs):
    try:
        _ensure_dirs()
        message = args.get("message", "")
        entry = json.dumps({
            "from": "evey",
            "to": "claude-code",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "type": "message",
            "message": message,
        })
        channel = BRIDGE_DIR / "channel.jsonl"
        with open(channel, "a") as f:
            f.write(entry + "\n")

        return json.dumps({
            "status": "sent",
            "message": "Message sent to Claude Code via channel.",
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def handle_check(args, **kwargs):
    try:
        _ensure_dirs()
        _auto_compress()
        results = []

        # Check outbox (Claude Code → Evey results/tasks)
        outbox = BRIDGE_DIR / "outbox"
        for f in sorted(outbox.iterdir()):
            if f.is_file():
                results.append({"type": "task_from_mother", "file": f.name, "content": f.read_text()[:2000]})
                # Move processed files to active/
                f.rename(BRIDGE_DIR / "active" / f.name)

        # Check recent channel messages from mother/claude-code
        channel = BRIDGE_DIR / "channel.jsonl"
        last_read_file = BRIDGE_DIR / ".last_read"
        last_read_ts = ""
        if last_read_file.exists():
            last_read_ts = last_read_file.read_text().strip()

        if channel.exists():
            lines = channel.read_text().strip().split("\n")
            new_messages = []
            for line in lines[-10:]:
                try:
                    msg = json.loads(line)
                    if msg.get("from") in ("claude-code", "mother"):
                        msg_ts = msg.get("timestamp", "")
                        if msg_ts > last_read_ts:
                            new_messages.append(msg)
                except json.JSONDecodeError:
                    pass

            if new_messages:
                for msg in new_messages:
                    results.append({"type": "channel_message", "message": msg})
                # Mark as read
                latest_ts = max(m.get("timestamp", "") for m in new_messages)
                last_read_file.write_text(latest_ts)

        if not results:
            return json.dumps({"status": "empty", "message": "No new messages from Mother."})

        return json.dumps({"status": "ok", "results": results, "count": len(results)})
    except Exception as e:
        return json.dumps({"error": str(e)})


def register(ctx):
    ctx.register_tool(
        name="claude_bridge_task",
        toolset="evey_bridge",
        schema=TASK_SCHEMA,
        handler=handle_task,
    )
    ctx.register_tool(
        name="claude_bridge_message",
        toolset="evey_bridge",
        schema=MESSAGE_SCHEMA,
        handler=handle_message,
    )
    ctx.register_tool(
        name="claude_bridge_check",
        toolset="evey_bridge",
        schema=CHECK_SCHEMA,
        handler=handle_check,
    )
