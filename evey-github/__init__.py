"""Evey GitHub Plugin — monitor 42-evey repos, stars, issues, and PRs.

Tools:
  github_status — Check all 42-evey repos: stars, forks, open issues, recent activity
  github_pr_status — Check status of our PRs on upstream repos
"""

import json
import os
import urllib.request
import urllib.error

ORG = "42-evey"
UPSTREAM_REPO = "NousResearch/hermes-agent"

# Read-only GitHub API — no auth needed for public repos.
# For write operations (PRs, issues), Evey delegates to Mother via bridge.
HEADERS = {"User-Agent": "evey-github/1.0", "Accept": "application/vnd.github+json"}


def _fetch(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


STATUS_SCHEMA = {
    "name": "github_status",
    "description": (
        "Check all 42-evey GitHub repos: stars, forks, open issues, last push, "
        "and recent activity. Shows repo health at a glance."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "description": "Optional: check only one repo (e.g. 'hermes-plugins'). Default: all.",
            },
        },
    },
}

PR_SCHEMA = {
    "name": "github_pr_status",
    "description": (
        "Check status of 42-evey's pull requests on NousResearch/hermes-agent. "
        "Shows open/closed/merged status, comments, and review status."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}


def status_handler(args, **kwargs):
    repo_filter = None
    if isinstance(args, dict):
        repo_filter = args.get("repo", "").strip()
    elif isinstance(args, str) and args.strip():
        repo_filter = args.strip()

    if repo_filter:
        repos = [_fetch(f"https://api.github.com/repos/{ORG}/{repo_filter}")]
    else:
        repos = _fetch(f"https://api.github.com/users/{ORG}/repos?per_page=30&sort=updated")

    if isinstance(repos, dict) and "error" in repos:
        return f"Error fetching repos: {repos['error']}"

    if not isinstance(repos, list):
        repos = [repos]

    lines = [f"GitHub: {ORG} ({len(repos)} repos)\n"]
    total_stars = 0

    for r in repos:
        if isinstance(r, dict) and "name" in r:
            name = r["name"]
            stars = r.get("stargazers_count", 0)
            forks = r.get("forks_count", 0)
            issues = r.get("open_issues_count", 0)
            pushed = (r.get("pushed_at") or "")[:10]
            desc = (r.get("description") or "")[:50]
            total_stars += stars

            lines.append(f"  {name}: {stars} stars, {forks} forks, {issues} issues")
            if desc:
                lines.append(f"    {desc}")
            lines.append(f"    Last push: {pushed}")

    lines.append(f"\nTotal stars: {total_stars}")
    return "\n".join(lines)


def pr_handler(args, **kwargs):
    data = _fetch(
        f"https://api.github.com/repos/{UPSTREAM_REPO}/pulls?state=all&per_page=10"
        f"&head={ORG}"
    )

    if isinstance(data, dict) and "error" in data:
        return f"Error: {data['error']}"

    if not data:
        return "No PRs found from 42-evey"

    lines = [f"PRs from {ORG} on {UPSTREAM_REPO}:\n"]

    for pr in data:
        num = pr["number"]
        title = pr["title"][:55]
        state = pr["state"].upper()
        merged = pr.get("merged_at")
        comments = pr.get("comments", 0)
        reviews = pr.get("review_comments", 0)
        created = pr["created_at"][:10]

        if merged:
            icon = "MERGED"
        elif state == "OPEN":
            icon = "OPEN"
        else:
            icon = "CLOSED"

        lines.append(f"  [{icon}] #{num} {title}")
        lines.append(f"    Created: {created} | Comments: {comments} | Reviews: {reviews}")
        if state == "OPEN":
            lines.append(f"    URL: {pr['html_url']}")

    return "\n".join(lines)


def register(ctx):
    ctx.register_tool(
        name=STATUS_SCHEMA["name"],
        toolset="hermes-cli",
        schema=STATUS_SCHEMA,
        handler=status_handler,
    )
    ctx.register_tool(
        name=PR_SCHEMA["name"],
        toolset="hermes-cli",
        schema=PR_SCHEMA,
        handler=pr_handler,
    )
