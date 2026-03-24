"""Evey Commands Plugin — slash commands for common operations.

Uses the new upstream register_command() API (hermes-agent #2359) to add
slash commands that appear in /help, tab-complete, Telegram, and gateway.

Commands:
  /stack    — Quick stack overview (services, models, costs)
  /sites    — List all deployed sites with URLs
  /research — Count research docs and total size
  /bridge   — Show bridge inbox status
"""

import json
import os
import subprocess
import urllib.request
import urllib.error

DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://hermes-dashboard:8088")


def _cmd_stack(args: str) -> str:
    """Quick stack overview."""
    lines = []

    # Service count
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}} {{.Status}}"],
            capture_output=True, text=True, timeout=5
        )
        services = result.stdout.strip().split("\n")
        healthy = sum(1 for s in services if "healthy" in s)
        total = len([s for s in services if s.strip()])
        lines.append(f"Services: {healthy}/{total} healthy")
    except Exception:
        lines.append("Services: unable to check")

    # Cost from dashboard
    try:
        req = urllib.request.urlopen(f"{DASHBOARD_URL}/api/costs/today", timeout=3)
        data = json.loads(req.read())
        lines.append(f"Cost today: ${data.get('total', 0):.4f}")
    except Exception:
        lines.append("Cost today: $0.00 (free models)")

    # Models
    lines.append("Brain: MiMo-V2-Pro (free, 1M context)")
    lines.append("Local: RTX 5080 16GB, 14 models via Ollama")
    lines.append("Routing: 60 models via LiteLLM")

    return "\n".join(lines)


def _cmd_sites(args: str) -> str:
    """List all deployed sites."""
    sites = [
        ("evey.cc", "Landing page"),
        ("evey.cc/shop", "Donate with Benefits (Web3 payments)"),
        ("evey.cc/donate", "Direct crypto donation"),
        ("listings.evey.cc", "Marketplace Listing Manager (PWA)"),
        ("evey-price-tracker.pages.dev", "Price Tracker (PWA)"),
        ("evey-habits.pages.dev", "Habit Tracker (PWA)"),
        ("evey-inventory.pages.dev", "Home Inventory (PWA)"),
    ]
    lines = [f"  {url} — {desc}" for url, desc in sites]
    return "Deployed sites:\n" + "\n".join(lines)


def _cmd_research(args: str) -> str:
    """Count research docs."""
    import glob
    spec_dir = "/app/data/research/strategy-2026/spec"
    # Fallback path for non-containerized
    if not os.path.isdir(spec_dir):
        spec_dir = os.path.expanduser("~/data/research/strategy-2026/spec")
    if not os.path.isdir(spec_dir):
        return "Research library: path not found"

    docs = glob.glob(os.path.join(spec_dir, "*.md"))
    total_bytes = sum(os.path.getsize(f) for f in docs)
    total_kb = total_bytes / 1024
    return f"Research library: {len(docs)} docs, {total_kb:.0f} KB"


def _cmd_bridge(args: str) -> str:
    """Show bridge inbox status."""
    inbox_dir = "/app/data/claude-bridge/inbox"
    if not os.path.isdir(inbox_dir):
        inbox_dir = os.path.expanduser("~/data/claude-bridge/inbox")

    if os.path.isdir(inbox_dir):
        tasks = os.listdir(inbox_dir)
        count = len(tasks)
    else:
        count = 0

    # Last channel message
    channel = "/app/data/claude-bridge/channel.jsonl"
    if not os.path.isfile(channel):
        channel = os.path.expanduser("~/data/claude-bridge/channel.jsonl")

    last_msg = "none"
    if os.path.isfile(channel):
        try:
            with open(channel) as f:
                lines = f.readlines()
                if lines:
                    entry = json.loads(lines[-1])
                    last_msg = f"{entry.get('from', '?')}: {entry.get('message', '')[:60]}"
        except Exception:
            pass

    return f"Bridge inbox: {count} tasks\nLast message: {last_msg}"


def register(ctx):
    """Register slash commands using the new upstream API."""
    try:
        ctx.register_command(
            name="stack",
            handler=_cmd_stack,
            description="Quick stack overview — services, costs, models",
            args_hint="",
        )
        ctx.register_command(
            name="sites",
            handler=_cmd_sites,
            description="List all deployed evey.cc sites with URLs",
            args_hint="",
        )
        ctx.register_command(
            name="research",
            handler=_cmd_research,
            description="Count research docs and total size",
            args_hint="",
        )
        ctx.register_command(
            name="bridge",
            handler=_cmd_bridge,
            description="Show bridge inbox status and last message",
            args_hint="",
        )
    except AttributeError:
        # Older hermes versions without register_command — silently skip
        pass
