"""Evey Memory Consolidation — extracts facts from daily conversations.

Runs daily at 3am. Queries Langfuse for yesterday's traces, uses local
qwen35-4b to extract key facts, updates MEMORY.md and Qdrant vectors.
"""

import json
import os
import time
import urllib.request
import urllib.error
from base64 import b64encode
from pathlib import Path

LITELLM_URL = os.environ.get("OPENAI_BASE_URL", "")
LITELLM_KEY = os.environ.get("OPENAI_API_KEY", "")
LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "")
QDRANT_URL = "http://hermes-qdrant:6333"
OLLAMA_URL = "http://hermes-ollama:11434"
MEMORY_PATH = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))) / "memories" / "MEMORY.md"
EXTRACT_MODEL = "mimo-v2-pro"
EMBED_MODEL = "snowflake-arctic-embed2"
CHAR_LIMIT = 4200

SCORE_PROMPT = """Rate the importance of this fact for an AI agent's long-term memory (1-10).

10 = Critical (security rule, user preference, architecture decision)
7-9 = Important (learned behavior, tool discovery, cost insight)
4-6 = Useful (research finding, model comparison, minor observation)
1-3 = Trivial (greeting, routine check, temporary state)

Fact: {fact}

Reply with ONLY a number 1-10:"""

EXTRACT_PROMPT = """Extract 3-5 key facts from these AI agent conversation traces.

Rules:
- Only novel, useful facts (not greetings, errors, tool calls)
- Format: "- [category] fact" where category is one of: learned, decided, discovered, created, fixed
- Use COMPACT language — no filler words, abbreviate where clear
- Be specific, terse, max 15 words per fact
- Skip anything trivial or repetitive

TRACES:
{traces}

KEY FACTS:"""

SCHEMA = {
    "name": "consolidate_daily_memory",
    "description": (
        "Extract key facts from yesterday's conversations and store them. "
        "Updates MEMORY.md with new learnings and adds vectors to Qdrant. "
        "Run this daily or when you want to consolidate recent knowledge."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "hours_back": {
                "type": "number",
                "description": "How many hours back to look (default: 24)",
            },
        },
    },
}


def _langfuse_query(hours_back=24):
    from_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - hours_back * 3600))
    lf_pub = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    lf_sec = os.environ.get("LANGFUSE_SECRET_KEY", "")
    auth = b64encode(f"{lf_pub}:{lf_sec}".encode()).decode()
    url = f"{LANGFUSE_HOST}/api/public/traces?fromTimestamp={from_ts}"
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        traces = data.get("data", [])
        summaries = []
        for t in traces[:20]:
            name = t.get("name", "")
            inp = t.get("input", "")
            out = t.get("output", "")
            if isinstance(inp, dict): inp = inp.get("messages", [{}])[-1].get("content", "") if isinstance(inp.get("messages"), list) else str(inp)[:200]
            if isinstance(out, dict): out = out.get("choices", [{}])[0].get("message", {}).get("content", "") if isinstance(out.get("choices"), list) else str(out)[:200]
            if inp or out:
                summaries.append(f"{name}: {str(inp)[:150]} -> {str(out)[:150]}")
        return summaries
    except Exception:
        return ["Memory consolidation unavailable"]


def _load_call_llm():
    import importlib.util as _iu, os as _os
    _spec = _iu.spec_from_file_location("evey_utils", _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "evey_utils.py"))
    _eu = _iu.module_from_spec(_spec)
    _spec.loader.exec_module(_eu)
    return _eu.call_llm


def _extract_facts(trace_summaries):
    call_llm = _load_call_llm()
    text = "\n".join(trace_summaries[:15])
    result = call_llm(EXTRACT_MODEL, EXTRACT_PROMPT.format(traces=text[:3000]), max_tokens=300, temperature=0.3)
    return result or "Extraction failed"


def _score_fact(fact):
    """Score a fact's importance (1-10) using local model."""
    call_llm = _load_call_llm()
    text = call_llm(EXTRACT_MODEL, SCORE_PROMPT.format(fact=fact), max_tokens=5, temperature=0)
    if text:
        try:
            return int("".join(c for c in text if c.isdigit())[:2])
        except ValueError:
            pass
    return 5


def _update_memory(new_facts):
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    current = MEMORY_PATH.read_text() if MEMORY_PATH.exists() else ""

    # Score each fact and only keep important ones (score >= 5)
    scored_facts = []
    for fact in new_facts.split("\n"):
        if fact.strip() and fact.strip().startswith("-"):
            score = _score_fact(fact)
            if score >= 5:
                scored_facts.append(f"{fact.strip()} [importance:{score}]")

    if not scored_facts:
        return len(current)

    date_header = f"\n## Learned {time.strftime('%Y-%m-%d')}\n"
    updated = current + date_header + "\n".join(scored_facts) + "\n"

    # Trim: remove oldest LOW-scored facts first when over limit
    if len(updated) > CHAR_LIMIT:
        lines = updated.split("\n")
        # Sort removable lines by importance (keep headers/structure)
        removable = [(i, l) for i, l in enumerate(lines) if "[importance:" in l]
        removable.sort(key=lambda x: int(x[1].split("importance:")[1].split("]")[0]) if "importance:" in x[1] else 10)
        while len("\n".join(lines)) > CHAR_LIMIT and removable:
            idx = removable.pop(0)[0]
            if idx < len(lines):
                lines.pop(idx)
                removable = [(i if i < idx else i-1, l) for i, l in removable]
        updated = "\n".join(lines)

    MEMORY_PATH.write_text(updated)
    return len(updated)


def _embed_to_qdrant(facts):
    import hashlib
    for i, fact in enumerate(facts.split("\n")):
        if not fact.strip() or not fact.startswith("-"):
            continue
        try:
            data = json.dumps({"model": EMBED_MODEL, "prompt": fact}).encode()
            req = urllib.request.Request(f"{OLLAMA_URL}/api/embeddings", data=data,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                vector = json.loads(resp.read())["embedding"]
            point_id = int(hashlib.md5(f"memory:{time.strftime('%Y%m%d')}:{i}".encode()).hexdigest()[:15], 16)
            # Extract category from fact format "- [category] fact text"
            category = "general"
            fact_text = fact.strip()
            if fact_text.startswith("- [") and "]" in fact_text:
                category = fact_text.split("[")[1].split("]")[0]
            upsert = json.dumps({"points": [{"id": point_id, "vector": vector, "payload": {
                "source": "memory-consolidation", "type": "learned-fact",
                "category": category,
                "content": fact_text, "date": time.strftime("%Y-%m-%d"),
            }}]}).encode()
            req = urllib.request.Request(f"{QDRANT_URL}/collections/evey-knowledge/points",
                data=upsert, method="PUT", headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass


def handler(args, **kwargs):
    try:
        hours = args.get("hours_back", 24)
        traces = _langfuse_query(hours)
        if not traces:
            return json.dumps({"status": "empty", "message": "No traces found"})
        facts = _extract_facts(traces)
        mem_size = _update_memory(facts)
        _embed_to_qdrant(facts)
        return json.dumps({
            "status": "consolidated",
            "traces_analyzed": len(traces),
            "facts_extracted": facts,
            "memory_size": mem_size,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def register(ctx):
    ctx.register_tool(name="consolidate_daily_memory", toolset="evey_memory",
        schema=SCHEMA, handler=handler)
