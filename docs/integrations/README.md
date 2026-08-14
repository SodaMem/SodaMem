# Agent integrations

Placeholder for third-party agent runtimes. Guides will be added per platform.

| Agent / harness | Integration path | Status |
|---|---|---|
| [PI Agent](https://github.com/) | `docs/integrations/pi-agent.md` | _coming soon_ |
| [Hermes Agent](https://github.com/) | `docs/integrations/hermes-agent.md` | _coming soon_ |
| [DeepSeek Harness](https://github.com/) | `docs/integrations/deepseek-harness.md` | _coming soon_ |

Common wiring pattern:

1. **Recall** — `GET /v1/context` or MCP `get_context` before each model call
2. **Retain** — MCP `add_memories` or HTTP `POST /v1/memories` after valuable turns
3. **Scope** — bind `user_id` at construction; stamp `project_id` per repo/workspace

See also: [`adapters/README.md`](../adapters/README.md), [`mcp_server/README.md`](../mcp_server/README.md).
