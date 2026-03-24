# evey-rag

Semantic search over Evey's knowledge base via Qdrant.

## Requirements

- **Qdrant** running at `hermes-qdrant:6333` with collection `evey-knowledge`
- **Ollama** running at `hermes-ollama:11434` with `snowflake-arctic-embed2` model
- Index populated via `scripts/rag-index.py` (run once, then `--incremental` after changes)

## Tools

- `knowledge_search` — Semantic search with optional type filter (plugin, skill, config, research, memory, goals, personality, hook, docs)
- `knowledge_stats` — Collection stats: total vectors, type distribution, unique sources

## Setup

```bash
# Pull embedding model
docker exec hermes-ollama ollama pull snowflake-arctic-embed2

# Create collection (if not exists)
curl -X PUT http://localhost:6333/collections/evey-knowledge \
  -H 'Content-Type: application/json' \
  -d '{"vectors": {"size": 1024, "distance": "Cosine"}}'

# Index documents
python3 scripts/rag-index.py

# Incremental re-index after changes
python3 scripts/rag-index.py --incremental
```
