# Agent integrations

**SodaMem plugs into agent runtimes over MCP** — retain, recall, and context in the same memory store.

## Code-level integrations

Import SodaMem directly; no separate process or MCP hop required.

| Runtime | Where |
|---|---|
| **LangGraph** | [`adapters/README.md`](../adapters/README.md) |
| **CrewAI** | [`adapters/README.md`](../adapters/README.md) |
| **OpenAI Agents SDK** | [`adapters/README.md`](../adapters/README.md) |
| **Vercel AI SDK** | [`../sdk-ts/`](../sdk-ts/) |

## MCP integrations

Runtimes that talk to SodaMem as a separate MCP server process.

| Runtime | Guide |
|---|---|
| **Hermes Agent** | [`hermes/README.md`](hermes/README.md) |
| **DeepSeek Harness** | [`deepseek-harness/README.md`](deepseek-harness/README.md) |
| **Generic / any MCP client** | [`../mcp_server/README.md`](../mcp_server/README.md) |

Also shipped: Claude Code / Cursor hooks via `sodamem install`.
