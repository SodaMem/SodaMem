# SodaMem MCP Server

Exposes [SodaMem](../README.md) — an evidence-grounded, temporal memory
store — as an [MCP](https://modelcontextprotocol.io) server, so any MCP
client (Claude Desktop, Cursor, etc.) can read and write a user's memory
directly. Analogous to mem0's OpenMemory MCP or Graphiti's `mcp_server/`,
but with one differentiator both of those lack: `get_context`, a **zero-LLM,
prompt-ready evidence block** — no client-side re-formatting of search
results needed.

Transport: **stdio** — what every MCP client in this category spawns, and
what `python -m mcp_server` runs. HTTP/SSE as an *inbound* transport is still
out of scope; use the `[server]` HTTP service (`server/app.py`) for a
network-reachable API.

## Two modes, and why you almost always want remote

Every coding tool spawns its **own** MCP server process. Claude Code spawns
one; Cursor spawns another; a hook firing on each prompt spawns a third. Per-
user stores are SQLite without WAL, so more than one process opening them
corrupts them (ADR 0001 §2) — which is exactly what used to happen, silently,
because this server opened stores directly while only the HTTP service took
the data-root lock.

| Mode | Selected by | Behavior |
|---|---|---|
| **remote** (default for `sodamem install`) | `SODAMEM_API_URL` is set | Every tool proxies to that running SodaMem service. Any number of clients can share one store, because only the service opens it. |
| **local** | `SODAMEM_API_URL` is unset | This process opens the stores itself **and takes the data-root lock**. Fine for exactly one client; a second one now fails at startup with `data_root_locked` rather than corrupting the store. |

The two modes return identical tool output — asserted by a parity test that
runs both backends against the same store
(`tests/test_mcp_backend.py::test_local_and_remote_return_the_same_shape`).

```bash
sodamem daemon ensure          # start the one service
sodamem install cursor         # writes a remote-mode config
```

## Install

```bash
pip install "sodamem[mcp]"
```

`sodamem[mcp]` depends on `sodamem[server]` (see `pyproject.toml`) because
this server reuses `server.stores.StoreManager` — the same
path-traversal-hardened, LRU-bounded per-user store cache the HTTP service
uses — instead of a second, parallel (and likely less careful)
implementation.

## Configuration (environment variables)

All variables use the `SODAMEM_` prefix shared with the HTTP service
(`server/settings.py`).

| Variable | Required | Description |
|---|---|---|
| `SODAMEM_API_URL` | remote mode | URL of a running SodaMem service. Setting it selects remote mode; every tool call is proxied there and no store is opened in this process. |
| `SODAMEM_API_KEY` | remote mode, if the service requires auth | Sent as `Authorization: Bearer`. |
| `SODAMEM_DATA_ROOT` | **local mode only** | Absolute path to the directory that holds one store subdirectory per `user_id`. Unlike the HTTP service (which defaults to `./data`), this is required — an MCP server is launched by an arbitrary client with an arbitrary working directory, so there is no safe implicit default. |
| `SODAMEM_USER_ID` | no | Default `user_id` for every tool call, for single-user setups. Each tool also accepts an explicit `user_id` argument, which always wins. If neither is present, the tool call fails — there is no unscoped path. |
| `SODAMEM_PROJECT_ID` | no | Default repo/workspace scope for `add_memories` / `search_memory` / `get_context`. Narrows retrieval to this project plus everything unstamped; omit it to search across all projects. `sodamem install` sets it from the git root. |
| `SODAMEM_LLM_PROVIDER` | only for `add_memories` | One of `openai`, `anthropic`, `deepseek`, `gemini`. |
| `SODAMEM_LLM_API_KEY` | only for `add_memories` | API key for the extraction LLM. |
| `SODAMEM_LLM_MODEL` | no | Overrides the provider's default model. |
| `SODAMEM_LLM_BASE_URL` | no | Overrides the provider's default base URL. |
| `SODAMEM_STORE_CACHE_MAX` | no | LRU cap on concurrently-open per-user stores (default 64). |
| `SODAMEM_MCP_LOG_LEVEL` | no | Python logging level for the server process (default `WARNING`). Always goes to stderr — stdout is reserved for the MCP JSON-RPC stream. |

`search_memory`, `get_context`, and `list_memories` work without any
`SODAMEM_LLM_*` configuration (they run zero LLM calls). `add_memories`
needs `SODAMEM_LLM_PROVIDER` + `SODAMEM_LLM_API_KEY` — without them it fails
loudly with a clear error, it does not silently skip extraction. In remote
mode those belong on the SERVICE, not here; `sodamem daemon ensure` warns at
start-up if they are missing rather than letting every write fail later.

## Client configuration

`sodamem install <client>` writes all of this for you — including the parts
below that are easy to get subtly wrong (VS Code reads `servers`, not
`mcpServers`; Zed nests the command; Codex uses TOML). Run
`sodamem clients` for the list. What follows is what it writes, for anyone
who would rather do it by hand.

### Remote mode (recommended)

```json
{
  "mcpServers": {
    "sodamem": {
      "command": "sodamem-mcp",
      "env": {
        "SODAMEM_API_URL": "http://127.0.0.1:8000",
        "SODAMEM_USER_ID": "you",
        "SODAMEM_PROJECT_ID": "acme-api-1a2b3c4d"
      }
    }
  }
}
```

No `SODAMEM_DATA_ROOT` and no `SODAMEM_LLM_*`: in remote mode this process
never opens a store and never runs extraction — the service does both. That
is what lets you paste the same block into every editor you use.

### Local mode — Claude Desktop

For a single client only. Add to `claude_desktop_config.json` (macOS:
`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "sodamem": {
      "command": "python",
      "args": ["-m", "mcp_server"],
      "env": {
        "SODAMEM_DATA_ROOT": "/Users/you/.sodamem/data",
        "SODAMEM_USER_ID": "you",
        "SODAMEM_LLM_PROVIDER": "openai",
        "SODAMEM_LLM_API_KEY": "sk-..."
      }
    }
  }
}
```

Or, after `pip install "sodamem[mcp]"`, use the console script instead of
`-m mcp_server`:

```json
{
  "mcpServers": {
    "sodamem": {
      "command": "sodamem-mcp",
      "env": {
        "SODAMEM_DATA_ROOT": "/Users/you/.sodamem/data",
        "SODAMEM_USER_ID": "you"
      }
    }
  }
}
```

### Cursor

Add to `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global) —
same shape as the `mcpServers` entry above.

If Cursor and Claude Desktop are both configured in LOCAL mode against the
same `SODAMEM_DATA_ROOT`, the second one to start now fails with
`data_root_locked`. That is the intended outcome, not a regression: point
both at a service instead (`sodamem daemon ensure`).

## Tools

| Tool | Purpose |
|---|---|
| `add_memories` | Persist a conversation (`[{role, content}, ...]`) into the user's store; extracts structured, evidence-grounded facts via an LLM. Needs `SODAMEM_LLM_*` — on THIS process in local mode, on the service in remote mode. |
| `search_memory` | Ranked BM25 + vector search over stored facts. Returns citable evidence cards with provenance — not a synthesized answer. |
| `get_context` | **Differentiator.** Prompt-ready text block for a query, plus the citation ids backing it. Zero extra LLM calls. Reach for this first. |
| `list_memories` | Enumerate active facts for a user, paginated. |
| `delete_memory` | Archive one fact by id (tombstone). SodaMem is an add-only, evidence-grounded store, and this server exposes no destructive path at all — this removes the fact from search/list/context results but retains the underlying source text for provenance/audit. Physical erasure exists only as an operator-gated REST purge (`?purge=true` + `SODAMEM_ALLOW_PURGE`), deliberately out of reach of an MCP client. Idempotent. |

Every tool takes an optional `user_id` argument; it falls back to
`SODAMEM_USER_ID` when omitted, and the call fails if neither is present.

## Verifying the server works

```bash
SODAMEM_DATA_ROOT=/tmp/sodamem-mcp-check python -m mcp_server
```

This blocks, speaking MCP JSON-RPC over stdio — it is meant to be launched
by a client, not run interactively. To smoke-test it yourself, drive it with
the official Python client (`mcp.client.stdio.stdio_client` +
`mcp.ClientSession`), which sends a real `initialize` request and can list
tools — see `tests/test_mcp_server.py` for a scripted example, or the
verification transcript in this feature's delivery notes.
