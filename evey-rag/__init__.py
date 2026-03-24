"""Evey RAG — Search the knowledge base via Qdrant vector search.

Provides semantic search over all indexed documents: plugins, skills, configs,
research findings, memories, and goals.
"""

import json
import os
import urllib.request

QDRANT_URL = "http://hermes-qdrant:6333"
LITELLM_URL = "http://hermes-litellm:4000"
LITELLM_KEY = os.environ.get("LITELLM_KEY", os.environ.get("OPENAI_API_KEY", ""))
EMBED_MODEL = "arctic-embed"
COLLECTION = "evey-knowledge"

SEARCH_SCHEMA = {
    "name": "knowledge_search",
    "description": (
        "Search Evey's knowledge base for relevant information. "
        "Uses semantic search over all indexed docs: plugins, skills, config, "
        "research findings, memories, goals, hooks. "
        "Use this when you need to recall how something works, find a config, "
        "or look up past research."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language search query",
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default 5)",
                "default": 5,
            },
            "doc_type": {
                "type": "string",
                "description": "Filter by type: plugin, skill, config, research, memory, goals, personality, hook, docs",
                "enum": ["plugin", "skill", "config", "research", "memory", "goals", "personality", "hook", "docs", "context"],
            },
        },
        "required": ["query"],
    },
}

STATS_SCHEMA = {
    "name": "knowledge_stats",
    "description": "Get statistics about the knowledge base: total vectors, types, sources.",
    "parameters": {"type": "object", "properties": {}},
}


def _embed(text):
    data = json.dumps({"model": EMBED_MODEL, "input": text}).encode()
    headers = {"Content-Type": "application/json"}
    if LITELLM_KEY:
        headers["Authorization"] = f"Bearer {LITELLM_KEY}"
    req = urllib.request.Request(
        f"{LITELLM_URL}/v1/embeddings",
        data=data,
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["data"][0]["embedding"]


def _search(vector, limit=5, doc_type=None):
    body = {"vector": vector, "limit": limit, "with_payload": True}
    if doc_type:
        body["filter"] = {"must": [{"key": "type", "match": {"value": doc_type}}]}
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{QDRANT_URL}/collections/{COLLECTION}/points/search",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def knowledge_search(query, limit=5, doc_type=None):
    try:
        vector = _embed(query)
        result = _search(vector, limit, doc_type)
        hits = result.get("result", [])
        if not hits:
            return json.dumps({"results": [], "message": "No relevant results found."})

        results = []
        for hit in hits:
            p = hit.get("payload", {})
            results.append({
                "source": p.get("source", "unknown"),
                "type": p.get("type", "unknown"),
                "description": p.get("description", ""),
                "score": round(hit.get("score", 0), 3),
                "content": p.get("content", "")[:2000],
            })
        return json.dumps({"results": results, "total": len(results)})
    except Exception as e:
        return json.dumps({"error": str(e)})


def knowledge_stats():
    try:
        req = urllib.request.Request(f"{QDRANT_URL}/collections/{COLLECTION}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            info = json.loads(resp.read())
        result = info.get("result", {})

        # Get type distribution via scroll
        scroll_data = json.dumps({
            "limit": 500,
            "with_payload": ["source", "type"],
        }).encode()
        scroll_req = urllib.request.Request(
            f"{QDRANT_URL}/collections/{COLLECTION}/points/scroll",
            data=scroll_data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(scroll_req, timeout=10) as resp:
            scroll = json.loads(resp.read())

        types = {}
        sources = set()
        for pt in scroll.get("result", {}).get("points", []):
            p = pt.get("payload", {})
            t = p.get("type", "unknown")
            types[t] = types.get(t, 0) + 1
            sources.add(p.get("source", "unknown"))

        return json.dumps({
            "total_points": result.get("points_count", 0),
            "indexed_vectors": result.get("indexed_vectors_count", 0),
            "status": result.get("status", "unknown"),
            "types": types,
            "unique_sources": len(sources),
            "collection": COLLECTION,
            "embed_model": EMBED_MODEL,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


TOOLS = [SEARCH_SCHEMA, STATS_SCHEMA]

def run(tool_name, tool_input):
    if tool_name == "knowledge_search":
        return knowledge_search(**tool_input)
    elif tool_name == "knowledge_stats":
        return knowledge_stats()
    return json.dumps({"error": f"Unknown tool: {tool_name}"})


def register(ctx):
    for tool in TOOLS:
        ctx.register_tool(name=tool["name"], toolset="evey_rag",
                          schema=tool, handler=lambda args, t=tool["name"], **kw: run(t, args))
