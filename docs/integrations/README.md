# Agent integrations

| Agent / harness | Guide | Status |
|---|---|---|
| **Hermes Agent** | [`../../HERMES_INTEGRATION.md`](../../HERMES_INTEGRATION.md) | Ready |
| **DeepSeek Harness** | [`../../DEEPSEEK_HARNESS_INTEGRATION.md`](../../DEEPSEEK_HARNESS_INTEGRATION.md) | Ready |
| PI Agent | — | Coming soon |

Common wiring pattern:

1. **Recall** — `GET /v1/context` or MCP `get_context` before each model call
2. **Retain** — MCP `add_memories` or HTTP `POST /v1/memories` after valuable turns
3. **Scope** — bind `user_id` at construction; stamp `project_id` per repo/workspace

See also: [`adapters/README.md`](../adapters/README.md), [`mcp_server/README.md`](../mcp_server/README.md).
