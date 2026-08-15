# SodaMem × Hermes Agent

Connect SodaMem memory to Hermes Agent for retention, retrieval, context recall, and **full memory-graph export** (`graph_export`).

- Platforms: Windows / macOS / Linux
- Transport: MCP (stdio)

Related guides: [DeepSeek Harness](../deepseek-harness/README.md) · [Generic MCP](../../mcp_server/README.md)

---

## Quick start

### 1. Install

```bash
cd SodaMem
pip install -e .
pip install "mcp>=1.9.0,<2"
```

### 2. Configure Hermes

Add the following to Hermes `config.yaml`:

```yaml
mcp_servers:
  sodamem:
    command: '<your-python-exe>'
    args: ['<path-to>/sodamem_mcp_warm.py']
    env:
      SODAMEM_DATA_ROOT: '<your-data-root>'      # e.g. C:/Users/you/.sodamem/data
      SODAMEM_USER_ID: 'default'
      SODAMEM_MCP_ALLOW_WRITE: '1'               # enables add_memories / delete_memory
      SODAMEM_LLM_PROVIDER: 'deepseek'           # fact extraction for add_memories
      SODAMEM_LLM_API_KEY: '<your-key>'
      SODAMEM_LLM_MODEL: 'deepseek-chat'
```

> ⚠️ Hermes filters subprocess environment variables. Put the LLM key under `env:`; a shell export alone is not enough.

The warm-up script referenced by `args` prevents the first tool call from timing out. See
[`scripts/sodamem_mcp_warm.py`](../../scripts/sodamem_mcp_warm.py):

```python
# sodamem_mcp_warm.py
import sys, logging
logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
import chromadb  # noqa: F401 — preload heavy dependencies
import onnxruntime  # noqa: F401
from mcp_server.main import main

if __name__ == "__main__":
    main()
```

### 3. Restart

Fully quit and restart Hermes (MCP does not hot-reload). New sessions can then use the `mcp_sodamem_*` tools.

---

## Available tools

| Tool | Purpose |
|---|---|
| `add_memories` | Store conversations and extract structured facts with an LLM (requires `SODAMEM_LLM_*`) |
| `search_memory` | BM25 + vector retrieval with traceable evidence cards |
| `get_context` | **Prompt-ready context block** with zero LLM calls; recommended before answering |
| `list_memories` | Paginate through active facts |
| `entity_timeline` | Retrieve the complete timeline for one entity |
| `explore_memory` | Traverse the graph from one node with BFS (depth ≤ 3) |
| `refine_search` | Search with structured filters such as entity, time window, and confidence |
| `graph_export` | **Export the complete memory graph** |
| `delete_memory` | Archive a fact by ID (tombstone, not physical deletion) |

---

## Viewing the memory graph (`graph_export`)

One call exports a user's **complete graph**: all active facts, entities, edges, and optionally the source evidence.

**MCP call:**

```text
graph_export(user_id="default", include_evidence=false, max_nodes=500)
```

**HTTP call:**

```text
GET /v1/graph?user_id=default&include_evidence=true&max_nodes=500
```

**Response shape:**

```json
{
  "user_id": "default",
  "count": {"facts": 2, "entities": 3, "edges": 8, "spans": 0},
  "facts": [...],
  "entities": [...],
  "edges": [...],
  "spans": [...],
  "truncated": false
}
```

**Parameters:**

| Parameter | Default | Description |
|---|---|---|
| `user_id` | `SODAMEM_USER_ID` | Required unless supplied by the environment |
| `include_evidence` | `false` | Include source evidence spans |
| `max_nodes` | `500` | Node limit; sets `truncated=true` when exceeded |

**Example Hermes requests:**

> "Export my memory graph" → `graph_export`  
> "Which entities are in my memory?" → inspect the `entities` field  
> "Visualize my graph" → pass the returned nodes and edges to any graph-rendering library

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No module named 'fcntl'` | On Windows, update to a SodaMem version whose maintenance lock handles platforms without `fcntl` |
| First tool call times out or hangs | Launch with the warm-up script described above |
| `add_memories` reports missing LLM credentials | Check that `SODAMEM_LLM_*` is inside the `env:` block |
| `graph_export` is missing | Update SodaMem and fully restart Hermes |
| Multiple clients share one data directory | Do not run multiple local writers against one `SODAMEM_DATA_ROOT`; use HTTP remote mode (`SODAMEM_API_URL`) |
