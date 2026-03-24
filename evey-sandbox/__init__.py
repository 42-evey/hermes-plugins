"""Evey Secure Reader — read files from V-approved folders with PII scrubbing.

Fast tool. No Docker, no code execution. Just read + clean + return.
V controls which folders are accessible via config/sandbox.yaml.
"""

import json
import os
import re
from pathlib import Path

BASE_DIR = Path("/mnt/v/evey")
CONFIG_PATH = Path(os.path.expanduser("~/.hermes/config/sandbox.yaml"))
if not CONFIG_PATH.exists():
    CONFIG_PATH = BASE_DIR / "config" / "sandbox.yaml"

# Inline PII patterns (no yaml dependency needed at runtime)
PII_PATTERNS = [
    (re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'), '[EMAIL]'),
    (re.compile(r'(\+?[0-9]{1,4}[\s.-]?)?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{2,4}'), '[PHONE]'),
    (re.compile(r'\b\d{6}/?\d{3,4}\b'), '[ID_NUMBER]'),
    (re.compile(r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b'), '[CARD]'),
    (re.compile(r'(?:sk-|pk-|Bearer\s+)[A-Za-z0-9_-]{20,}'), '[API_KEY]'),
    (re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'), '[IP]'),
]

MAX_FILE_SIZE = 500_000

ALLOWED_EXTENSIONS = {
    '.txt', '.md', '.py', '.js', '.ts', '.json', '.yaml', '.yml',
    '.csv', '.html', '.css', '.java', '.c', '.cpp', '.h', '.rs',
    '.go', '.sh', '.sql', '.tex', '.bib', '.xml', '.cfg', '.ini', '.log',
}

# Cache config on first load
_config_cache = None


def _load_config():
    global _config_cache
    if _config_cache:
        return _config_cache
    try:
        import yaml
        with open(CONFIG_PATH) as f:
            _config_cache = yaml.safe_load(f)
    except Exception:
        # Fallback: parse allowed_folders manually
        _config_cache = {"allowed_folders": [], "pii": {"enabled": True}}
        if CONFIG_PATH.exists():
            in_folders = False
            for line in CONFIG_PATH.read_text().splitlines():
                stripped = line.strip()
                if stripped == "allowed_folders:":
                    in_folders = True
                    continue
                if in_folders and stripped.startswith("- "):
                    folder = stripped[2:].strip()
                    if not folder.startswith("#"):
                        _config_cache["allowed_folders"].append(folder)
                elif in_folders and not stripped.startswith("-") and not stripped.startswith("#") and stripped:
                    in_folders = False
    return _config_cache


def _win_to_wsl(path_str):
    """Convert Windows path D:\\Foo\\Bar to /mnt/d/Foo/Bar."""
    if len(path_str) >= 2 and path_str[1] == ':':
        drive = path_str[0].lower()
        rest = path_str[2:].replace('\\', '/')
        return f"/mnt/{drive}{rest}"
    return path_str.replace('\\', '/')


def _resolve_allowed():
    """Get list of resolved allowed folder paths."""
    config = _load_config()
    folders = []
    for f in config.get("allowed_folders", []):
        resolved = _win_to_wsl(f)
        if not os.path.isabs(resolved):
            resolved = str(BASE_DIR / resolved)
        folders.append(os.path.realpath(resolved))
    return folders


def _is_allowed(filepath):
    """Check if file is inside an allowed folder."""
    real = os.path.realpath(filepath)
    for folder in _resolve_allowed():
        if real.startswith(folder + "/") or real == folder:
            return True
    return False


def _scrub_pii(text):
    """Remove PII patterns from text."""
    config = _load_config()
    if not config.get("pii", {}).get("enabled", True):
        return text
    for pattern, replacement in PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


READ_SCHEMA = {
    "name": "secure_read",
    "description": (
        "Read a file from V's approved folders. Content is PII-scrubbed automatically.\n"
        "READ-ONLY. Cannot modify, delete, or execute anything.\n"
        "Use sandbox_list to see available folders."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path (Windows D:\\... or Unix /mnt/...)",
            },
            "lines": {
                "type": "number",
                "description": "Max lines to return (default: all, max 2000)",
            },
        },
        "required": ["path"],
    },
}

SEARCH_SCHEMA = {
    "name": "secure_search",
    "description": (
        "Search for files or content inside V's approved folders.\n"
        "PII-scrubbed results. Cannot access folders outside the whitelist."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "folder": {
                "type": "string",
                "description": "Which allowed folder to search in",
            },
            "pattern": {
                "type": "string",
                "description": "Filename glob pattern (e.g., '*.py', '*.md')",
            },
            "grep": {
                "type": "string",
                "description": "Search file contents for this text (optional)",
            },
            "max_results": {
                "type": "number",
                "description": "Max results to return (default: 20)",
            },
        },
        "required": ["folder"],
    },
}

LIST_SCHEMA = {
    "name": "sandbox_list",
    "description": "List available folders and their contents. Shows what you can access.",
    "parameters": {
        "type": "object",
        "properties": {
            "folder": {
                "type": "string",
                "description": "List contents of a specific allowed folder (optional)",
            },
        },
    },
}


def read_handler(args, **kwargs):
    try:
        path_str = args.get("path", "")
        max_lines = min(args.get("lines", 2000), 2000)

        path_str = _win_to_wsl(path_str)
        if not os.path.isabs(path_str):
            path_str = str(BASE_DIR / path_str)

        if not _is_allowed(path_str):
            return json.dumps({
                "error": "ACCESS DENIED — this folder is not in V's whitelist",
                "allowed": _resolve_allowed(),
            })

        if not os.path.isfile(path_str):
            return json.dumps({"error": f"File not found: {path_str}"})

        ext = os.path.splitext(path_str)[1].lower()
        if ext and ext not in ALLOWED_EXTENSIONS:
            return json.dumps({"error": f"File type '{ext}' not allowed"})

        size = os.path.getsize(path_str)
        if size > MAX_FILE_SIZE:
            return json.dumps({"error": f"File too large: {size} bytes (max {MAX_FILE_SIZE})"})

        with open(path_str, 'r', errors='replace') as f:
            lines = f.readlines()[:max_lines]

        content = "".join(lines)
        clean = _scrub_pii(content)

        return json.dumps({
            "path": path_str,
            "lines": len(lines),
            "size": size,
            "content": clean,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def search_handler(args, **kwargs):
    try:
        folder = args.get("folder", "")
        pattern = args.get("pattern", "*")
        grep_text = args.get("grep", "")
        max_results = min(args.get("max_results", 20), 50)

        folder = _win_to_wsl(folder)
        if not os.path.isabs(folder):
            folder = str(BASE_DIR / folder)

        if not _is_allowed(folder):
            return json.dumps({
                "error": "ACCESS DENIED — this folder is not in V's whitelist",
                "allowed": _resolve_allowed(),
            })

        folder_path = Path(folder)
        if not folder_path.is_dir():
            return json.dumps({"error": f"Not a directory: {folder}"})

        matches = []
        for f in sorted(folder_path.rglob(pattern))[:200]:
            if not f.is_file():
                continue
            if f.stat().st_size > MAX_FILE_SIZE:
                continue

            entry = {"path": str(f), "size": f.stat().st_size}

            if grep_text:
                try:
                    content = f.read_text(errors='replace')
                    if grep_text.lower() in content.lower():
                        # Find matching lines
                        matching_lines = []
                        for i, line in enumerate(content.splitlines(), 1):
                            if grep_text.lower() in line.lower():
                                matching_lines.append(f"{i}: {_scrub_pii(line.strip()[:200])}")
                        entry["matches"] = matching_lines[:5]
                        matches.append(entry)
                except Exception:
                    pass
            else:
                matches.append(entry)

            if len(matches) >= max_results:
                break

        return json.dumps({"folder": folder, "pattern": pattern, "results": matches, "count": len(matches)})
    except Exception as e:
        return json.dumps({"error": str(e)})


def list_handler(args, **kwargs):
    try:
        specific = args.get("folder", "")
        allowed = _resolve_allowed()

        if specific:
            specific = _win_to_wsl(specific)
            if not os.path.isabs(specific):
                specific = str(BASE_DIR / specific)

            if not _is_allowed(specific):
                return json.dumps({"error": "ACCESS DENIED", "allowed": allowed})

            p = Path(specific)
            if not p.is_dir():
                return json.dumps({"error": "Not a directory"})

            items = []
            for f in sorted(p.iterdir())[:100]:
                items.append({
                    "name": f.name,
                    "type": "dir" if f.is_dir() else "file",
                    "size": f.stat().st_size if f.is_file() else None,
                })
            return json.dumps({"folder": specific, "items": items})

        return json.dumps({
            "allowed_folders": allowed,
            "access": "read-only",
            "pii_scrubbing": "enabled",
            "note": "V controls this list in config/sandbox.yaml",
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def register(ctx):
    ctx.register_tool(name="secure_read", toolset="evey_sandbox", schema=READ_SCHEMA, handler=read_handler)
    ctx.register_tool(name="secure_search", toolset="evey_sandbox", schema=SEARCH_SCHEMA, handler=search_handler)
    ctx.register_tool(name="sandbox_list", toolset="evey_sandbox", schema=LIST_SCHEMA, handler=list_handler)
