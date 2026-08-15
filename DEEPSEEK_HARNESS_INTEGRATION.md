# SodaMem × DeepSeek Harness

Connect SodaMem memory to [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (`dsh`) for retention, retrieval, context recall, and full memory-graph export (`graph_export`).

- Platforms: Windows / macOS / Linux
- Transport: MCP (stdio); tool names look like `mcp__sodamem__*`

Related guides: [Hermes](./HERMES_INTEGRATION.md) · [Generic MCP](./mcp_server/README.md)

---

## Quick start

### 1. Install

Run this from the **repository root** (the directory containing `pyproject.toml`, not the `sodamem/` package directory):

```bash
cd SodaMem
pip install -e ".[mcp]"
```

### 2. Start the memory service (remote mode recommended)

When multiple clients such as Harness, Cursor, or Hermes share one store:

```bash
sodamem daemon ensure          # defaults to http://127.0.0.1:8000
```

Configure write credentials in the daemon environment or `.env` file:

```text
SODAMEM_LLM_PROVIDER=deepseek
SODAMEM_LLM_API_KEY=<your-key>
SODAMEM_LLM_MODEL=deepseek-chat
SODAMEM_MCP_ALLOW_WRITE=1
```

For a single Harness client, you may skip the daemon and use the local configuration below.

### 3. Configure DeepSeek Harness

Repository example: [`examples/sodamem-dsh.patch.yml`](./examples/sodamem-dsh.patch.yml)

```yaml
- insert:
    - id: mcp-sodamem
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        transport: stdio
        serverName: sodamem
        command: sodamem-mcp
        args: []
        env:
          SODAMEM_API_URL: 'http://127.0.0.1:8000'
          SODAMEM_USER_ID: 'default'
        toolCallTimeoutMs: 120000
        failOnStartupError: true
```

For **local mode (Harness only)**, remove `SODAMEM_API_URL` and use:

```yaml
env:
  SODAMEM_DATA_ROOT: 'C:/Users/you/.sodamem/data'   # must be an absolute path
  SODAMEM_USER_ID: 'default'
  SODAMEM_MCP_ALLOW_WRITE: '1'
  SODAMEM_LLM_PROVIDER: 'deepseek'
  SODAMEM_LLM_API_KEY: '<your-key>'
  SODAMEM_LLM_MODEL: 'deepseek-chat'
```

On Windows, if `sodamem-mcp` is not on `PATH` or the first call times out, use an absolute Python path and the warm-up script:

```yaml
command: 'C:\\Users\\you\\miniconda3\\python.exe'
args: ['C:\\Users\\you\\path\\to\\SodaMem\\scripts\\sodamem_mcp_warm.py']
```

See [`scripts/sodamem_mcp_warm.py`](./scripts/sodamem_mcp_warm.py).

Start Harness:

```bash
npx @deepseek-ai/dsh web --patch ./examples/sodamem-dsh.patch.yml
```

Configure the Harness main-model API key separately under **Settings → Models**.

### 4. Restart

Fully quit and restart `dsh` (MCP does not hot-reload). New sessions should expose `mcp__sodamem__*` tools.

Try:

- "Remember that I live in Shanghai" → `add_memories`
- "According to my memory, where do I live?" → prefer `get_context`

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
| `refine_search` | Search with structured filters |
| `graph_export` | Export the complete memory graph |
| `delete_memory` | Archive a fact by ID (tombstone, not physical deletion) |

Write tools appear only when `SODAMEM_MCP_ALLOW_WRITE=1` (also accepts `true`, `yes`, or `on`).

Recommended loop: valuable turn → `add_memories`; before answering → **`get_context`**.

---

## Memory graph (`graph_export`)

```text
graph_export(user_id="default", include_evidence=false, max_nodes=500)
```

HTTP equivalent:

```text
GET /v1/graph?user_id=default&include_evidence=true&max_nodes=500
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `mcp__sodamem__*` tools are missing | Confirm the `--patch` file loaded, then fully restart `dsh` |
| First tool call times out | Increase `toolCallTimeoutMs`, use the warm-up script, or switch to the remote daemon |
| `add_memories` lacks credentials | Local: put them in patch `env`; remote: put them in the **daemon** environment |
| Write tools are missing | Set `SODAMEM_MCP_ALLOW_WRITE=1` |
| `data_root_locked` or corruption risk with multiple clients | Do not run two local writers against one `SODAMEM_DATA_ROOT`; use `SODAMEM_API_URL` |
| `No module named 'fcntl'` | Update to a SodaMem version whose maintenance lock supports Windows |
| `os` has no attribute `uname` | Update to a version that uses `platform.system()`, then retry `sodamem daemon ensure` |
| Incorrect `user_id` | Set `SODAMEM_USER_ID` or pass `user_id` explicitly |
