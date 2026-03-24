"""Evey Verification — check external targets exist before acting.

Prevents hallucinated actions: verify URLs, GitHub repos, API endpoints,
and local files before cloning, pushing, fetching, or writing.

Rule: "Confident" is not "verified." Always verify before external actions.
"""

import json
import os
import socket
import urllib.request
import urllib.error

VERIFY_URL_SCHEMA = {
    "name": "verify_url",
    "description": (
        "Check if a URL is reachable. Use BEFORE fetching, cloning, or "
        "referencing any external URL. Returns status code or error."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full URL to check"},
        },
        "required": ["url"],
    },
}

VERIFY_REPO_SCHEMA = {
    "name": "verify_repo",
    "description": (
        "Check if a GitHub repo exists. Use BEFORE any git clone, fork, "
        "or PR operation. Returns repo info or 'not found'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "owner": {"type": "string", "description": "GitHub user or org"},
            "repo": {"type": "string", "description": "Repository name"},
        },
        "required": ["owner", "repo"],
    },
}

VERIFY_ENDPOINT_SCHEMA = {
    "name": "verify_endpoint",
    "description": (
        "Check if an API endpoint responds. Use BEFORE making API calls "
        "to external services. Sends a HEAD request."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "API endpoint URL"},
            "method": {
                "type": "string",
                "description": "HTTP method (default HEAD)",
                "default": "HEAD",
            },
        },
        "required": ["url"],
    },
}

VERIFY_DNS_SCHEMA = {
    "name": "verify_dns",
    "description": "Check if a domain name resolves. Use before trusting a hostname.",
    "parameters": {
        "type": "object",
        "properties": {
            "hostname": {"type": "string", "description": "Domain to resolve"},
        },
        "required": ["hostname"],
    },
}


def verify_url(url):
    try:
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "evey-verification/1.0")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.dumps({
                "exists": True,
                "status": resp.status,
                "url": resp.url,
                "content_type": resp.headers.get("Content-Type", ""),
            })
    except urllib.error.HTTPError as e:
        return json.dumps({
            "exists": e.code != 404,
            "status": e.code,
            "error": str(e.reason),
        })
    except Exception as e:
        return json.dumps({"exists": False, "error": str(e)})


def verify_repo(owner, repo):
    url = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "evey-verification/1.0")
        req.add_header("Accept", "application/vnd.github+json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return json.dumps({
                "exists": True,
                "full_name": data.get("full_name", ""),
                "description": (data.get("description") or "")[:100],
                "stars": data.get("stargazers_count", 0),
                "language": data.get("language", ""),
                "archived": data.get("archived", False),
                "default_branch": data.get("default_branch", "main"),
            })
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return json.dumps({
                "exists": False,
                "error": f"Repository {owner}/{repo} does not exist",
            })
        return json.dumps({"exists": False, "error": f"HTTP {e.code}: {e.reason}"})
    except Exception as e:
        return json.dumps({"exists": False, "error": str(e)})


def verify_endpoint(url, method="HEAD"):
    try:
        req = urllib.request.Request(url, method=method.upper())
        req.add_header("User-Agent", "evey-verification/1.0")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.dumps({
                "reachable": True,
                "status": resp.status,
                "content_type": resp.headers.get("Content-Type", ""),
            })
    except urllib.error.HTTPError as e:
        return json.dumps({
            "reachable": True,
            "status": e.code,
            "note": "Endpoint exists but returned an error",
        })
    except Exception as e:
        return json.dumps({"reachable": False, "error": str(e)})


def verify_dns(hostname):
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
        ips = list(set(r[4][0] for r in results))
        return json.dumps({
            "resolves": True,
            "hostname": hostname,
            "addresses": ips[:5],
        })
    except socket.gaierror as e:
        return json.dumps({
            "resolves": False,
            "hostname": hostname,
            "error": str(e),
        })


TOOLS = [VERIFY_URL_SCHEMA, VERIFY_REPO_SCHEMA, VERIFY_ENDPOINT_SCHEMA, VERIFY_DNS_SCHEMA]


def run(tool_name, tool_input):
    if tool_name == "verify_url":
        return verify_url(**tool_input)
    elif tool_name == "verify_repo":
        return verify_repo(**tool_input)
    elif tool_name == "verify_endpoint":
        return verify_endpoint(**tool_input)
    elif tool_name == "verify_dns":
        return verify_dns(**tool_input)
    return json.dumps({"error": f"Unknown tool: {tool_name}"})


def register(ctx):
    for tool in TOOLS:
        ctx.register_tool(name=tool["name"], toolset="evey_verification",
                          schema=tool, handler=lambda args, t=tool["name"], **kw: run(t, args))
