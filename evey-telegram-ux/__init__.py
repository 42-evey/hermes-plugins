"""Evey Telegram UX Plugin — rich formatted messages for Telegram.

Provides tools for sending nicely formatted status updates, delegation
result cards, and dashboard summaries via Telegram's HTML formatting.

Works alongside send_message tool — formats content as HTML cards that
look great in Telegram while gracefully falling back to plain text elsewhere.
"""

import json
import os
import time

STATUS_CARD_SCHEMA = {
    "name": "telegram_card",
    "description": (
        "Send a beautifully formatted status card to V via Telegram. "
        "Use for: delegation results, research summaries, health reports, goal updates. "
        "Formats with HTML (bold, italic, code blocks) automatically."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Card title (e.g., 'Research Result', 'Health Check', 'Delegation Complete')",
            },
            "fields": {
                "type": "object",
                "description": "Key-value pairs to display (e.g., {\"Model\": \"nemotron-free\", \"Score\": \"8/10\"})",
            },
            "body": {
                "type": "string",
                "description": "Main content text",
            },
            "footer": {
                "type": "string",
                "description": "Optional footer (timestamp, cost, etc.)",
            },
            "style": {
                "type": "string",
                "enum": ["info", "success", "warning", "error"],
                "description": "Card style — changes the icon (default: info)",
            },
        },
        "required": ["title"],
    },
}

QUICK_STATUS_SCHEMA = {
    "name": "telegram_status",
    "description": (
        "Send a quick one-line status update to V. "
        "Use for: progress notifications, brief updates, task completions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Status message",
            },
            "style": {
                "type": "string",
                "enum": ["info", "success", "warning", "error", "working"],
                "description": "Status style (default: info)",
            },
        },
        "required": ["message"],
    },
}

STYLE_ICONS = {
    "info": "i",
    "success": "+",
    "warning": "!",
    "error": "x",
    "working": "~",
}


def _escape_html(text):
    """Escape HTML special chars for Telegram."""
    if not text:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_card(title, fields=None, body=None, footer=None, style="info"):
    """Build an HTML-formatted card for Telegram."""
    icon = STYLE_ICONS.get(style, "i")
    parts = [f"<b>[{icon}] {_escape_html(title)}</b>"]

    if fields:
        for key, value in fields.items():
            parts.append(f"<b>{_escape_html(key)}:</b> {_escape_html(value)}")

    if body:
        # Preserve code blocks
        if "```" in body:
            # Simple code block handling
            segments = body.split("```")
            formatted = []
            for i, seg in enumerate(segments):
                if i % 2 == 1:  # Inside code block
                    formatted.append(f"<pre>{_escape_html(seg.strip())}</pre>")
                else:
                    formatted.append(_escape_html(seg))
            parts.append("\n".join(formatted))
        else:
            parts.append(_escape_html(body))

    if footer:
        parts.append(f"<i>{_escape_html(footer)}</i>")

    return "\n".join(parts)


def card_handler(args, **kwargs):
    try:
        title = args.get("title", "Status")
        fields = args.get("fields", {})
        body = args.get("body", "")
        footer = args.get("footer", "")
        style = args.get("style", "info")

        if not footer:
            footer = time.strftime("%H:%M %Z")

        formatted = _format_card(title, fields, body, footer, style)

        return json.dumps({
            "status": "formatted",
            "html": formatted,
            "plain": f"[{style.upper()}] {title}\n" + (body or ""),
            "note": "Use send_message to deliver this. The HTML will auto-detect.",
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def status_handler(args, **kwargs):
    try:
        message = args.get("message", "")
        style = args.get("style", "info")
        icon = STYLE_ICONS.get(style, "i")

        formatted = f"<b>[{icon}]</b> {_escape_html(message)}"

        return json.dumps({
            "status": "formatted",
            "html": formatted,
            "plain": f"[{icon}] {message}",
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def register(ctx):
    ctx.register_tool(
        name="telegram_card",
        toolset="evey_telegram",
        schema=STATUS_CARD_SCHEMA,
        handler=card_handler,
    )
    ctx.register_tool(
        name="telegram_status",
        toolset="evey_telegram",
        schema=QUICK_STATUS_SCHEMA,
        handler=status_handler,
    )
