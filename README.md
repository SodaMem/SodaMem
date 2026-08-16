<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/SodaMem/SodaMem/main/docs/assets/logo-dark.webp">
  <img src="https://raw.githubusercontent.com/SodaMem/SodaMem/main/docs/assets/logo.webp" alt="SodaMem" width="260">
</picture>

**A self-evolving, agentic memory layer for AI agents.**

Most memory systems store what you said and stop there — correct today, silently wrong the moment your life changes. SodaMem evolves alongside your agent: facts get superseded instead of overwritten, entity profiles rebuild on demand instead of drifting silently stale, and every answer still traces back to the exact turn it came from. Recall costs zero LLM calls, so the same question gets the same answer every time.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/SodaMem/SodaMem/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://github.com/SodaMem/SodaMem/blob/main/pyproject.toml)
[![LongMemEval](https://img.shields.io/badge/LongMemEval-92.8%25-brightgreen.svg)](https://github.com/SodaMem/SodaMem/tree/main/benchmarking/artifacts/)
[![LoCoMo](https://img.shields.io/badge/LoCoMo-86.88%25-brightgreen.svg)](https://github.com/SodaMem/SodaMem/blob/main/benchmarking/README.md#locomo-cat-1-4)
[![Discussions](https://img.shields.io/github/discussions/SodaMem/SodaMem?logo=github&label=discussions)](https://github.com/SodaMem/SodaMem/discussions)

<!-- langs -->
**English** · [简体中文](https://github.com/SodaMem/SodaMem/blob/main/docs/i18n/README.zh-CN.md) · [日本語](https://github.com/SodaMem/SodaMem/blob/main/docs/i18n/README.ja.md) · [한국어](https://github.com/SodaMem/SodaMem/blob/main/docs/i18n/README.ko.md) · [Français](https://github.com/SodaMem/SodaMem/blob/main/docs/i18n/README.fr.md) · [Español](https://github.com/SodaMem/SodaMem/blob/main/docs/i18n/README.es.md) · [Deutsch](https://github.com/SodaMem/SodaMem/blob/main/docs/i18n/README.de.md) · [Português](https://github.com/SodaMem/SodaMem/blob/main/docs/i18n/README.pt-BR.md)
<!-- /langs -->

[Agent integrations](#agent-integrations) · [Benchmark](#benchmark) · [Quick start](#quick-start) · [Why another memory layer](#why-another-memory-layer) · [Install](#install) · [Use it from anywhere](#use-it-from-anywhere) · [Coding tools](#coding-tools) · [Self-hosting](#self-hosting) · [Contributing](#contributing)

<img src="https://raw.githubusercontent.com/SodaMem/SodaMem/main/docs/assets/benchmark-cost-accuracy.webp" alt="Cost-accuracy trade-off on LongMemEval-S: SodaMem sits in the high-accuracy, low-cost quadrant" width="760">

*Accuracy against estimated API cost per question. The quadrant that matters is up and to the left.*

</div>

---

## Agent integrations

| Runtime | How | Guide |
|---|---|---|
| **Hermes Agent** | MCP | [`integrations/hermes/README.md`](integrations/hermes/README.md) |
| **DeepSeek Harness** | MCP | [`integrations/deepseek-harness/README.md`](integrations/deepseek-harness/README.md) |
| **Generic / any MCP client** | MCP | [`mcp_server/README.md`](mcp_server/README.md) |
| **LangGraph** | Python adapter | [`adapters/README.md`](adapters/README.md) |
| **CrewAI** | Python adapter | [`adapters/README.md`](adapters/README.md) |
| **OpenAI Agents SDK** | Python adapter | [`adapters/README.md`](adapters/README.md) |
| **Vercel AI SDK** | TS adapter | [`sdk-ts/`](sdk-ts/) |
| **Claude Code, Cursor, and other coding clients** | CLI + hooks | see [Coding tools](#coding-tools) |

Full index, including MCP tool schemas and adapter details: [`integrations/README.md`](integrations/README.md).

---

## Benchmark

<div align="center">
  <img src="https://raw.githubusercontent.com/SodaMem/SodaMem/main/docs/assets/benchmark-longmemeval.webp" alt="LongMemEval: SodaMem 92.8%, Hindsight 91.4%, Mem0 OSS 91.0%" width="720">
</div>

**92.8% (464/500)** on LongMemEval.

| | |
|---|---|
| reader / planner / judge | `deepseek-v4-flash` |
| judge prompts | the LongMemEval benchmark's own `evaluate_qa.py` templates, byte-identical |
| store | `longmemeval_s_500_Hobs_entitysubj`, 500 users / 235,840 facts |

**Every answer and every retrieved memory is published** in
[`benchmarking/artifacts/`](https://github.com/SodaMem/SodaMem/tree/main/benchmarking/artifacts/) — 500 answers verbatim,
8,427 evidence rows. Re-grade them with any judge, or hand the retrieved
context to your own reader and see what the number does — swap the reader
and the score moves, which is why the artifacts are the point, not just the
92.8%. Neither needs access to anything of ours.

<div align="center">
  <img src="https://raw.githubusercontent.com/SodaMem/SodaMem/main/docs/assets/benchmark-locomo.webp" alt="LoCoMo: SodaMem 86.88%, MemMachine 91.69%, Hindsight 89.61%, MIRIX 85.38%, Memobase 75.78%, Mem0 OSS 66.88%" width="720">
</div>

**86.88% (1338/1540)** on LoCoMo. End-to-end QA accuracy, LLM-as-judge.

| | |
|---|---|
| reader / planner / judge | `deepseek-v4-flash` |
| judge prompts | the LongMemEval benchmark's own templates, byte-copied |
| store | `locomo10_Hobs`, 10 user stores / 2,905 fact events |
| code | a pre-release build — this repository's published history begins at v0.1.0 |

**No per-question artifacts are published for LoCoMo** — no answers, no retrieved
context, no run directory. What is published is
[the LoCoMo section of `benchmarking/README.md`](https://github.com/SodaMem/SodaMem/blob/main/benchmarking/README.md#locomo-cat-1-4):
the per-category breakdown, the per-conversation spread, provenance and repro steps.

---

## Quick start

This is the Python path. Wiring into an agent framework or MCP client? See [Agent integrations](#agent-integrations). Calling it from TypeScript/Node? See [Use it from anywhere](#use-it-from-anywhere). Running it as a shared service? See [Self-hosting](#self-hosting).

### Example

```bash
pip install "sodamem[chroma,llm]"
```

```python
from sodamem import SodaMem
from sodamem.llm import create_provider_from_env      # SODAMEM_LLM_API_KEY etc.
from sodamem.memory.ingest.extractor import FactEventExtractorV2

mem = SodaMem.open("./data", extractor=FactEventExtractorV2(create_provider_from_env()))

mem.ingest(
    [{"role": "user", "content": "Actually I moved from Kauai to Oahu."}],
    user_id="u1", session_id="s1", session_time="2023-05-25",
)

block = mem.build_context("where am I staying?", user_id="u1", token_budget=1000)
print(block.text)        # prompt-ready — zero LLM calls
print(block.citations)   # the exact evidence behind every line of it
```

`SodaMem.open()` creates `./data` if it isn't there. Only `.ingest()` needs the
extractor — drop that argument for a read-only store and `search` /
`build_context` work exactly the same.

**Nothing about you leaves the machine.** No telemetry, no analytics, no
callback — the only outbound request the default install ever makes is a
one-time download of the 90 MB MiniLM embedding model into
`~/.cache/chroma/`, and after that it talks to nothing but your disk. Pre-seed
that cache and it runs air-gapped.

---

## Why another memory layer

Most memory systems store *what* you said. The questions that break them are
*when it stopped being true* and *where it came from* — and those need a data
model, not a bigger vector index.

| the question | the usual answer | SodaMem |
|---|---|---|
| Where did this memory come from? | a similarity score and some metadata | `FactEvent → SourceSpan → RawTurn`, a foreign-key chain down to the exact turn |
| The user changed their mind — now what? | overwrite; the old value is gone | ADD-only plus a `SUPERSEDES` edge; the old version closes with a `valid_until` and stays readable |
| "I moved to Chicago last year" vs "I move next year" | one timestamp | four time axes: occurred / valid / said / stored |
| What does one retrieval cost? | an LLM call per retrieval | `build_context` makes **zero**, and returns a prompt-ready block with citations |
| Same query twice — same answer? | depends on the model's sampling | deterministic fusion: same store, same query, same result |
| Why did it forget X? | no answer | `/v1/events` records every add, supersede and delete, with its reason |

Two of these are worth a closer look — the rest is what the table already says.

### Every memory carries its receipt

A retrieved memory is not a floating string. It points at the turn that
produced it:

```
evidence_id  = ev_fact:fact_6ada707b…
support      = "Can you recommend a good beach on Oahu that's not too crowded?"
predicate    = user wants a not-too-crowded beach on Oahu
entities     = location=Oahu | occasion=birthday
source       = session_40 / turn_10          ← the exact turn, not "some chat"
date         = 2023-05-25
```

`FactEvent → SourceSpan → RawTurn` is a foreign-key chain, not a similarity
score, so *"why do you think that about me?"* has an answer.

### Four time axes, not one timestamp

| field | question it answers |
|---|---|
| `occurred_start` / `occurred_end` | when the event happened |
| `valid_from` / `valid_until` | when the fact was true |
| `document_time` | when the user said it |
| `created_at` | when we stored it |

One timestamp cannot separate "I *moved* to Chicago last year" from "I *will
move* next year", or express a fact that stopped being true.

---

## Install

| extra | what it adds |
|---|---|
| *(base)* | data model, storage, BM25 retrieval, ingest — **four dependencies, none heavy** |
| `chroma` | vector search + the local ONNX embedder (`SodaMem.open()` needs this) |
| `llm` | OpenAI-compatible providers (OpenAI / DeepSeek / Gemini wire format) |
| `anthropic` | the Anthropic provider (its own SDK) |
| `answer` | the planner + reader answer path |
| `server` | the HTTP service (FastAPI + uvicorn — three packages, deliberately) |
| `mcp` | MCP server surface |

Base install pulls `pydantic`, `numpy`, `rank-bm25`, `python-dateutil` — and
a CI gate fails the build if that list grows by accident.

---

## Use it from anywhere

**HTTP** — `add` / `search` / `context` / `answer`, plus batch write,
supersede, events, metrics, token usage:

```bash
curl -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  localhost:8000/v1/context \
  -d '{"user_id":"u1","query":"what do they prefer?","token_budget":1000}'
```

`/v1/context` and `/v1/search` both take a JSON body; `/v1/context` also
answers a plain GET with query params, since it is a pure read. The one
Python-only exception is `build_context(organizer=...)`, which runs an
LLM-backed organizer over the retrieved set for questions like "list
everything you know about me" — `/v1/context` never accepts one, so the
zero-LLM guarantee over HTTP can't be flipped by a request parameter.

**SDKs** — TypeScript over HTTP ([`sdk-ts/`](https://github.com/SodaMem/SodaMem/tree/main/sdk-ts/), zero runtime
dependencies, ESM + CJS):

```bash
npm i sodamem
```

```typescript
import { SodaMemClient } from "sodamem";

const mem = new SodaMemClient({ baseUrl: "http://localhost:8000", apiKey: process.env.SODAMEM_API_KEY! });
const block = await mem.context({ user_id: "u1", query: "what do they prefer?", token_budget: 1000 });
```

Python talks to the library directly — `import sodamem` and you are already
past the network.

**Agent frameworks** — LangGraph, CrewAI, OpenAI Agents SDK, Vercel AI SDK.
Scope is bound when you construct the tools and never appears in the schema
the model sees: a `user_id` the model can choose is a `user_id` it can
hallucinate.

**MCP** — 8 tools, including `entity_timeline` (one entity's history in order,
each item still pointing at its source) and `explore_memory` (walk the graph
outward). Six are reads and always available; the two that mutate
(`add_memories`, `delete_memory`) appear only under
`SODAMEM_MCP_ALLOW_WRITE=true`, which `sodamem install` writes for you into
the client config it generates.

**Web console** — browse and inspect memories per tenant, shipped in the image.

---

## Coding tools

**Step 1.** Start the daemon — the one process that owns the stores:

```
sodamem daemon ensure
```

**Step 2.** Wire a client to it:

```
sodamem install claude-code
```

Every client gets the MCP tool surface. Four also get **hooks**, so memory is
recalled and retained without the model having to decide to call a tool —
which in a coding session it mostly doesn't, because it's busy reading files.

What hooks can do is not uniform, because the hook systems aren't. This is
what each client actually supports, and `sodamem clients` prints the same
thing:

| Client | Recall | Retain |
|---|---|---|
| Claude Code | every prompt | every turn + session end |
| GitHub Copilot CLI | every prompt | every turn |
| Cursor | session start (project brief) | — |
| Codex CLI | session start (project brief) | — |
| Claude Desktop, VS Code, Windsurf, Zed, OpenCode | MCP tools only | MCP tools only |

Cursor's `beforeSubmitPrompt` can read a prompt but cannot inject anything
(its docs list exactly three events that can, and that isn't one), and
neither Cursor nor Codex hands a hook a transcript path — so there is nothing
for a retain hook to read. Those two get a project brief at session start and
write through the `add_memories` tool. We don't install a hook that can only
ever do nothing.

Three things worth knowing before you run it:

**One daemon, many editors.** Per-user stores are SQLite without WAL, so
exactly one process may open them (ADR 0001 §2). `install` therefore points
every client at a running service by default rather than letting each spawn
its own — and if you deliberately choose a local store (`--local-store`), a
second client now refuses to start instead of quietly corrupting the first
one's data.

**Memories are scoped to the repo.** `install` derives a `project_id` from
the git root (a `git worktree` resolves to its parent repo, so one branch per
task is not one memory bank per task). Narrowing, not partitioning: anything
you told SodaMem outside a project still surfaces inside every project, and
dropping the key answers "how did I fix this in the other repo?".

**Retain needs extraction credentials.** Recall is zero-LLM and works
without them; storing facts does not. `sodamem daemon ensure` says so up
front rather than accepting every write and failing the job afterwards.

```
sodamem install claude-code --dry-run      # print what would change
sodamem install cursor vscode zed          # several at once
sodamem daemon status                      # what is actually answering
```

Existing config is merged, not replaced — other MCP servers, other settings
and hand-written TOML comments survive — and the first write of any file
leaves a `.sodamem-backup` beside it.

## Self-hosting

One command:

```
cp .env.example .env      # then set SODAMEM_API_KEY
docker compose up -d
```

**Auth is on by default.** `docker-compose.yml` never sets
`SODAMEM_AUTH_DISABLED` — the server refuses to start if `SODAMEM_API_KEY`
is unset (see `server/settings.py`), so there is no accidentally-open
deployment. Set the key in `.env` before the first `docker compose up`.

**Run exactly one worker.** `--workers 1` is a correctness constraint, not a
throughput setting: per-user stores are SQLite databases opened without WAL,
and two processes writing the same user's store corrupt it. The shipped
`CMD` states it explicitly, and the server takes an exclusive lock on its
data root at startup — a second process pointed at the same directory
refuses to start with `data_root_locked` rather than quietly corrupting
data. Horizontal scaling needs an external job store first
(`docs/adr/0001-control-plane-db.md`).

Full operations reference — calling the API, admin endpoints, metrics,
maintenance, backups, upgrades — lives in
[`docs/self-hosting.md`](docs/self-hosting.md).

---

## Contributing

Bugs, features and PRs are welcome. Read `CONTRIBUTING.md` first — it
describes four rules CI enforces that the code itself does not announce.

| | |
|---|---|
| [CONTRIBUTING.md](https://github.com/SodaMem/SodaMem/blob/main/CONTRIBUTING.md) | setup, and the four things CI checks that you cannot guess from reading the source |
| [SECURITY.md](https://github.com/SodaMem/SodaMem/blob/main/SECURITY.md) | how to report a vulnerability privately, and where the trust boundary actually is |
| [CHANGELOG.md](https://github.com/SodaMem/SodaMem/blob/main/CHANGELOG.md) | what changed per release, and what pre-1.0 compatibility does and does not promise |
| [CODE_OF_CONDUCT.md](https://github.com/SodaMem/SodaMem/blob/main/CODE_OF_CONDUCT.md) | Contributor Covenant 2.1 |

## Acknowledgements

Early contributions from [@sunjiajunsunjiajun](https://github.com/sunjiajunsunjiajun) and [@Lum1104](https://github.com/Lum1104) helped shape the work this project grew out of.
Thank you.

## License

[Apache-2.0](https://github.com/SodaMem/SodaMem/blob/main/LICENSE). Copyright 2026 FENGRONG WAN.
