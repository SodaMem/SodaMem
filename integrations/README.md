# Agent integrations

**SodaMem plugs into agent runtimes** — retain, recall, and context in the same
memory store. Three ways in, depending on what the runtime gives you: import it
directly, load it as a native plugin, or talk to it over MCP.

## Code-level integrations

Import SodaMem directly; no separate process or MCP hop required.

| Runtime | Where |
|---|---|
| **LangGraph** | [`adapters/README.md`](../adapters/README.md) |
| **CrewAI** | [`adapters/README.md`](../adapters/README.md) |
| **OpenAI Agents SDK** | [`adapters/README.md`](../adapters/README.md) |
| **Vercel AI SDK** | [`../sdk-ts/`](../sdk-ts/) |

## Native runtime plugins

Loaded into the runtime's own plugin system. **Not MCP** — the plugin hooks the
runtime's turn lifecycle directly, so it contributes memory to the prompt itself
and ingests closed turns on its own, instead of exposing tools and waiting to be
called. It reaches the store over HTTP against a running `sodamem daemon`.

These plugins are **maintained outside this repository**. `dsh` is a developer
preview that still makes breaking changes; a separate repo lets the plugin
follow that cadence without dragging SodaMem's releases along with it.

| Runtime | Where |
|---|---|
| **DeepSeek Harness** (`dsh`) | ↗ separate repo: [`SodaMem/dsh-plugin-sodamem`](https://github.com/SodaMem/dsh-plugin-sodamem) — npm `dsh-plugin-sodamem` |

## MCP integrations

Runtimes that talk to SodaMem as a separate MCP server process. Memory is
exposed as **tools**, so it reaches the model only on the turns where the model
chooses to call one.

| Runtime | Guide |
|---|---|
| **Hermes Agent** | [`hermes/README.md`](hermes/README.md) |
| **DeepSeek Harness** | [`deepseek-harness/README.md`](deepseek-harness/README.md) |
| **Generic / any MCP client** | [`../mcp_server/README.md`](../mcp_server/README.md) |

DeepSeek Harness appears in both sections, and the two are **alternatives, not
layers**. The native plugin is the recommended path and lives in its own repo;
the MCP bridge is the tool-based one and is documented here. **Do not install both against the same store** — recall would
fire twice and every turn would be ingested twice.

Also shipped: Claude Code / Cursor hooks via `sodamem install`.
