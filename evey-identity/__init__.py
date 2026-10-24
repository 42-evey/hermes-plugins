"""Identity Plugin — self-updating personality.

Inspired by OpenClaw's diary pattern. The agent can update its own identity
based on what it learns. Adds a "Learned Behaviors" section to SOUL.md
that grows organically from experience.

Runs during self-improve cron (3am) or on-demand.
Reflection runs through Hermes (ctx.llm) on the user's active model.
"""

import json
import os
import re
import time
from pathlib import Path

SOUL_PATH = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))) / "SOUL.md"
MAX_LEARNED = 10  # Max learned behaviors to keep

_CTX = None  # set by register(); LLM calls go through ctx.llm


# Some thinking models put their reasoning inline in the reply text. Drop it,
# including an unclosed block cut off by max_tokens.
_REASONING = re.compile(r"(?is)<(think|thinking|reasoning)>.*?(?:</\1>|$)")


def _visible(text):
    return _REASONING.sub("", text or "").strip()


def call_llm(prompt, max_tokens=200, temperature=0.3):
    """One completion on the user's active model. Returns text or None."""
    if _CTX is None:
        return None
    try:
        result = _CTX.llm.complete(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens, temperature=temperature, purpose="evey-identity",
        )
    except Exception:
        return None
    return _visible(result.text) or None

SCHEMA = {
    "name": "update_identity",
    "description": (
        "Reflect on recent experiences and update your identity. "
        "Adds learned behaviors to SOUL.md based on what worked and what didn't. "
        "Use after completing a significant task, learning something new, "
        "or when your self-improve cron runs at 3am."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reflection": {
                "type": "string",
                "description": "What did you learn? What worked? What should you do differently?",
            },
            "behavior": {
                "type": "string",
                "description": "A new behavior rule to add (e.g., 'Always validate delegation results before reporting')",
            },
        },
        "required": ["reflection"],
    },
}

REFLECT_PROMPT = """Based on this reflection from an AI agent, extract ONE concise behavioral rule (max 15 words).

Reflection: {reflection}

Rules:
- Start with a verb (Always, Never, Prefer, Check, Use, etc.)
- Be specific and actionable
- Focus on what to DO, not what happened

Behavioral rule:"""


def _extract_rule(reflection):
    """Use the active model to distill reflection into a rule."""
    # Room for a thinking model's reasoning; keep only the first line of the answer.
    text = call_llm(REFLECT_PROMPT.format(reflection=reflection), max_tokens=512, temperature=0.3)
    for line in (text or "").splitlines():
        line = line.strip().lstrip("-*• ").strip().strip('"').strip()
        if line:
            return line
    return None


def handler(args, **kwargs):
    try:
        reflection = args.get("reflection", "")
        explicit_behavior = args.get("behavior", "")

        # Get or extract the behavior rule
        if explicit_behavior:
            rule = explicit_behavior
        else:
            rule = _extract_rule(reflection)
            if not rule:
                return json.dumps({"status": "skipped", "reason": "Could not extract rule"})

        # Read current SOUL.md
        if not SOUL_PATH.exists():
            return json.dumps({"error": "SOUL.md not found"})

        content = SOUL_PATH.read_text()

        # Add or update Learned Behaviors section
        marker = "## Learned Behaviors"
        date = time.strftime("%Y-%m-%d")

        if marker in content:
            # Extract existing behaviors
            parts = content.split(marker)
            before = parts[0]
            behaviors_text = parts[1] if len(parts) > 1 else ""
            behaviors = [l.strip() for l in behaviors_text.strip().split("\n") if l.strip().startswith("- ")]
            # Add new behavior
            behaviors.append(f"- {rule} ({date})")
            # Keep only the latest MAX_LEARNED
            behaviors = behaviors[-MAX_LEARNED:]
            content = before + marker + "\n" + "\n".join(behaviors) + "\n"
        else:
            # Create new section
            content += f"\n{marker}\n- {rule} ({date})\n"

        SOUL_PATH.write_text(content)

        return json.dumps({
            "status": "updated",
            "rule_added": rule,
            "reflection": reflection[:200],
            "total_behaviors": len([l for l in content.split("\n") if l.strip().startswith("- ") and "Learned" in content.split(l)[0]]),
        })

    except Exception as e:
        return json.dumps({"error": str(e)})


def register(ctx):
    global _CTX
    _CTX = ctx
    ctx.register_tool(name="update_identity", toolset="evey_identity",
        schema=SCHEMA, handler=handler)
