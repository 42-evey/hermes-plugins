"""Evey Moltbook Plugin — social presence on the AI agent network.

Moltbook is a social network for AI agents (acquired by Meta March 2026).
Evey is registered as evey-cc. This plugin lets her:
- Check her feed and notifications (heartbeat)
- Reply to comments on her posts
- Post new content (max 2/day, quality over quantity)

Priority: replies > upvotes > comments on others > new posts
"""

import json
import logging
import os
import time
import urllib.request
import urllib.error
from pathlib import Path

logger = logging.getLogger("evey.moltbook")

API = "https://www.moltbook.com/api/v1"
MOLTBOOK_KEY = os.environ.get("MOLTBOOK_API_KEY", "")
STATE_FILE = Path(os.path.expanduser("~/.hermes/workspace/moltbook-state.json"))


def _api(method, path, body=None):
    if not MOLTBOOK_KEY:
        return {"error": "MOLTBOOK_API_KEY not set"}
    url = f"{API}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Authorization": f"Bearer {MOLTBOOK_KEY}", "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": True, "code": e.code, "message": e.read().decode()[:200]}
    except Exception as e:
        return {"error": True, "message": str(e)[:200]}


def _load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"posts_today": 0, "date": "", "last_heartbeat": 0}


def _save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


HEARTBEAT_SCHEMA = {
    "name": "moltbook_heartbeat",
    "description": (
        "Check Moltbook notifications, DMs, and feed. Returns: "
        "new comments to reply to, unread DMs, trending posts, karma stats. "
        "Call this periodically to stay engaged."
    ),
    "parameters": {"type": "object", "properties": {}},
}

REPLY_SCHEMA = {
    "name": "moltbook_reply",
    "description": (
        "Reply to a comment on one of your Moltbook posts. "
        "Provide the post_id and comment_id to reply to, plus your response."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "post_id": {"type": "string", "description": "Post ID to reply on"},
            "comment_id": {"type": "string", "description": "Comment ID to reply to"},
            "content": {"type": "string", "description": "Your reply (be thoughtful, technical, genuine)"},
        },
        "required": ["post_id", "comment_id", "content"],
    },
}

POST_SCHEMA = {
    "name": "moltbook_post",
    "description": (
        "Create a new Moltbook post. MAX 2 per day. Quality over quantity. "
        "Only post when you have genuine value — an insight, a finding, something others can learn from. "
        "Do NOT use this to advertise."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Post title (concise, interesting)"},
            "content": {"type": "string", "description": "Post content (genuine insight, not advertising)"},
            "submolt": {"type": "string", "description": "Community (agents, builds, memory, tooling, infrastructure, etc.)"},
        },
        "required": ["title", "content", "submolt"],
    },
}


def handle_heartbeat(args, **kwargs):
    home = _api("GET", "/home")
    if not home or home.get("error"):
        return json.dumps({"status": "error", "message": str(home)[:200]})

    acct = home.get("your_account", {})
    activity = home.get("activity_on_your_posts", [])
    dms = home.get("your_direct_messages", {})
    explore = home.get("explore", [])

    # Pending replies
    pending_replies = []
    for a in activity:
        if a.get("new_notification_count", 0) > 0:
            pending_replies.append({
                "post_id": a["post_id"],
                "post_title": a["post_title"][:50],
                "new_comments": a["new_notification_count"],
                "commenters": a.get("latest_commenters", []),
                "fetch_comments": f"Use moltbook_reply after reading comments",
            })

    # Trending posts worth engaging with
    trending = []
    explore_list = explore if isinstance(explore, list) else []
    for p in explore_list[:5]:
        trending.append({
            "title": p.get("title", "?")[:60],
            "author": p.get("author", {}).get("name", "?"),
            "upvotes": p.get("upvotes", 0),
            "submolt": p.get("submolt", {}).get("name", "?") if isinstance(p.get("submolt"), dict) else "?",
        })

    state = _load_state()
    today = time.strftime("%Y-%m-%d")
    if state.get("date") != today:
        state["posts_today"] = 0
        state["date"] = today
    state["last_heartbeat"] = time.time()
    _save_state(state)

    return json.dumps({
        "status": "ok",
        "karma": acct.get("karma", 0),
        "followers": acct.get("followerCount", 0),
        "pending_replies": pending_replies,
        "unread_dms": int(dms.get("unread_message_count", 0)),
        "pending_dm_requests": int(dms.get("pending_request_count", 0)),
        "trending": trending,
        "posts_today": state["posts_today"],
        "posts_remaining": max(0, 2 - state["posts_today"]),
        "priority": "Reply to comments first, then engage with trending, post last.",
    })


def handle_reply(args, **kwargs):
    post_id = args.get("post_id", "")
    comment_id = args.get("comment_id", "")
    content = args.get("content", "")

    if not all([post_id, comment_id, content]):
        return json.dumps({"error": "Need post_id, comment_id, and content"})

    result = _api("POST", f"/posts/{post_id}/comments", {
        "content": content,
        "parent_id": comment_id,
    })

    if result and result.get("success"):
        # Mark notifications as read
        _api("POST", f"/notifications/read-by-post/{post_id}")
        return json.dumps({"status": "replied", "post_id": post_id})

    return json.dumps({"status": "error", "message": str(result)[:200]})


def handle_post(args, **kwargs):
    state = _load_state()
    today = time.strftime("%Y-%m-%d")
    if state.get("date") != today:
        state["posts_today"] = 0
        state["date"] = today

    if state["posts_today"] >= 2:
        return json.dumps({
            "status": "blocked",
            "reason": "Daily post limit reached (2/2). Focus on replies and engagement instead.",
        })

    title = args.get("title", "")
    content = args.get("content", "")
    submolt = args.get("submolt", "agents")

    result = _api("POST", "/posts", {"title": title, "content": content, "submolt": submolt})

    if result and result.get("success"):
        state["posts_today"] += 1
        _save_state(state)
        post = result.get("post", {})
        return json.dumps({
            "status": "posted",
            "id": post.get("id", "?"),
            "submolt": submolt,
            "posts_remaining": max(0, 2 - state["posts_today"]),
        })

    return json.dumps({"status": "error", "message": str(result)[:200]})


def register(ctx):
    ctx.register_tool(name="moltbook_heartbeat", toolset="evey_moltbook", schema=HEARTBEAT_SCHEMA, handler=handle_heartbeat)
    ctx.register_tool(name="moltbook_reply", toolset="evey_moltbook", schema=REPLY_SCHEMA, handler=handle_reply)
    ctx.register_tool(name="moltbook_post", toolset="evey_moltbook", schema=POST_SCHEMA, handler=handle_post)
    logger.info("evey-moltbook loaded — social presence active")
