<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/SodaMem/SodaMem/main/docs/assets/logo-dark.webp">
  <img src="https://raw.githubusercontent.com/SodaMem/SodaMem/main/docs/assets/logo.webp" alt="SodaMem" width="260">
</picture>

**Evidence-grounded temporal memory for AI agents.**

Every memory can name the turn it came from, and knows when it stopped being true.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/SodaMem/SodaMem/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://github.com/SodaMem/SodaMem/blob/main/pyproject.toml)
[![LongMemEval](https://img.shields.io/badge/LongMemEval-92.8%25-brightgreen.svg)](https://github.com/SodaMem/SodaMem/tree/main/benchmarking/artifacts/)
[![LoCoMo](https://img.shields.io/badge/LoCoMo-86.88%25-brightgreen.svg)](https://github.com/SodaMem/SodaMem/blob/main/benchmarking/README.md#locomo-cat-1-4)
[![Discussions](https://img.shields.io/github/discussions/SodaMem/SodaMem?logo=github&label=discussions)](https://github.com/SodaMem/SodaMem/discussions)

<!-- langs -->
**English** · [简体中文](https://github.com/SodaMem/SodaMem/blob/main/docs/i18n/README.zh-CN.md) · [日本語](https://github.com/SodaMem/SodaMem/blob/main/docs/i18n/README.ja.md) · [한국어](https://github.com/SodaMem/SodaMem/blob/main/docs/i18n/README.ko.md) · [Français](https://github.com/SodaMem/SodaMem/blob/main/docs/i18n/README.fr.md) · [Español](https://github.com/SodaMem/SodaMem/blob/main/docs/i18n/README.es.md) · [Deutsch](https://github.com/SodaMem/SodaMem/blob/main/docs/i18n/README.de.md) · [Português](https://github.com/SodaMem/SodaMem/blob/main/docs/i18n/README.pt-BR.md)
<!-- /langs -->

<img src="https://raw.githubusercontent.com/SodaMem/SodaMem/main/docs/assets/benchmark-cost-accuracy.webp" alt="Cost-accuracy trade-off on LongMemEval-S: SodaMem sits in the high-accuracy, low-cost quadrant" width="760">

*Accuracy against estimated API cost per question. The quadrant that matters is up and to the left.*

</div>

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
context to your own reader and see what the number does. Neither needs access
to anything of ours.

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

```bash
pip install "sodamem[chroma,llm]"
```

```python
from sodamem import SodaMem
from sodamem.llm import create_provider_from_env      # SODAMEM_LLM_API_KEY etc.
from sodamem.memory.ingest.extractor import FactEventExtractorV2

# Writing needs a model to extract facts with; reading never does.
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

Each row is expanded below, and each one is something you can check in this
repository rather than take on faith.

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
score. When a user asks *"why do you think that about me?"* there is an
answer. When compliance asks where a stored fact came from, there is a row.

### Four time axes, not one timestamp

| field | question it answers |
|---|---|
| `occurred_start` / `occurred_end` | when the event happened |
| `valid_from` / `valid_until` | when the fact was true |
| `document_time` | when the user said it |
| `created_at` | when we stored it |

One timestamp cannot separate "I *moved* to Chicago last year" from "I *will
move* next year", and cannot express a fact that stopped being true.

Corrections are **ADD-only**: a new version plus a `SUPERSEDES` edge, never an
in-place rewrite. `PATCH /v1/memories/{id}` closes the old version with a
`valid_until` and leaves it readable — that is the whole difference from
`DELETE`.

### Two retrieval tiers, and the cheap one is genuinely free

| tier | LLM calls | for |
|---|---|---|
| `search` / `build_context` | **zero** | the default path: deterministic BM25 + vector + entity fusion |
| `answer` | planner loop | hard multi-hop questions worth the tokens |

`build_context` returns a **prompt-ready block with citations** and makes no
model call. Most systems hand you a list of records and leave the assembly —
and the token budgeting, and the dedup — to you.

There is a third, in-between tier: `build_context(organizer=...)` runs an
LLM-backed organizer (value-board, enumeration-sweep) over the retrieved set
for questions like "list every X you know about me". It is Python-only on
purpose — `/v1/context` never accepts one, so the zero-LLM guarantee on that
route cannot be flipped by a request parameter.

### Retrieval you can audit

Same query, same store, same result, every time. `/v1/events` records every
add, supersede and delete with its reason, so *"why did the agent forget X"*
is answerable after the fact instead of a shrug.

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
answers a plain GET with query params, since it is a pure read.

**SDKs** — TypeScript over HTTP ([`sdk-ts/`](https://github.com/SodaMem/SodaMem/tree/main/sdk-ts/), zero runtime
dependencies, ESM + CJS). Python talks to the library directly — `import
sodamem` and you are already past the network.

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

```
sodamem daemon ensure            # the one process that owns the stores
sodamem install claude-code      # wire a client to it
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

That builds the image, starts the server on `http://localhost:8000`, serves
the web console at `http://localhost:8000/console`, and persists all data in
a named Docker volume (`sodamem-data`, mounted at `/data` inside the
container) — nothing is written to the host filesystem directly, and nothing
survives only in the container's writable layer.

The console is compiled inside the image (a dedicated `console-builder`
stage), so nothing on the host needs Node installed. Running the server
outside Docker is different: `console/dist` won't exist until you run
`npm install && npm run build` in `console/`, and until then the API starts
normally and just logs that the console isn't mounted.

**Auth is on by default.** `docker-compose.yml` never sets
`SODAMEM_AUTH_DISABLED` — the server refuses to start if `SODAMEM_API_KEY`
is unset (see `server/settings.py`), so there is no accidentally-open
deployment. Set the key in `.env` before the first `docker compose up`.

Every other knob (`SODAMEM_LLM_PROVIDER`, `SODAMEM_STORE_CACHE_MAX`,
`SODAMEM_CORS_ORIGINS`, ...) is documented with defaults in `.env.example`.

**Run exactly one worker.** `--workers 1` is a correctness constraint, not a
throughput setting: per-user stores are SQLite databases opened without WAL,
and two processes writing the same user's store corrupt it. The shipped
`CMD` states it explicitly, and the server takes an exclusive lock on its
data root at startup — a second process pointed at the same directory
refuses to start with `data_root_locked` rather than quietly corrupting
data. Horizontal scaling needs an external job store first
(`docs/adr/0001-control-plane-db.md`).

### Calling it

```
# liveness — unauthenticated, touches no store
curl http://localhost:8000/health
# {"status":"ok","version":"0.0.1","schema_version":1,"auth":"enabled"}

# a real endpoint needs the API key (Authorization: Bearer, or X-API-Key)
curl http://localhost:8000/v1/search \
  -X POST -H "Content-Type: application/json" \
  -H "Authorization: Bearer $SODAMEM_API_KEY" \
  -d '{"user_id":"alice","query":"favorite color"}'
```

Full route list and request/response shapes are in `server/models.py` and
served live at `/docs` (Swagger UI) once the container is up.

### Operating it

`/v1/admin/*` answers the questions that otherwise need a shell inside the
container. The web console's **Ops** page is the same data with a UI.

```
# effective configuration — every secret reported as set/not-set, never masked
curl -H "Authorization: Bearer $SODAMEM_API_KEY" localhost:8000/v1/admin/config

# mint a named key; the plaintext is returned ONCE and is not recoverable
curl -X POST -H "Authorization: Bearer $SODAMEM_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"ci-pipeline"}' localhost:8000/v1/admin/keys

# who called what, most recent first (rolling window, not an archive)
curl -H "Authorization: Bearer $SODAMEM_API_KEY" localhost:8000/v1/admin/requests

# disk + workload shape
curl -H "Authorization: Bearer $SODAMEM_API_KEY" localhost:8000/v1/admin/stats
```

Named keys exist for **attribution**, not isolation: every request records
which key made it, so "who is hammering /v1/search" has an answer. There are
no roles or per-key scopes — any live key can read ops data and manage other
keys. `SODAMEM_API_KEY` keeps working exactly as before and cannot be revoked
through the API, which makes it the way back in if every named key is
revoked.

### Latency and cost

Both are instruments, not numbers we ask you to take on faith.

```
# per-route latency percentiles over this process's recent requests
curl -H "Authorization: Bearer $SODAMEM_API_KEY" localhost:8000/v1/metrics

# cumulative LLM token spend, split by ingest vs answer
curl -H "Authorization: Bearer $SODAMEM_API_KEY" localhost:8000/v1/usage
```

Both are in-process and reset on restart — counters for "what is this
deployment doing right now", not a billing record. For anything that has to
outlive a restart, scrape the Prometheus endpoint instead:

```
curl -H "Authorization: Bearer $SODAMEM_API_KEY" localhost:8000/metrics
```

It exposes request counts (a true monotonic counter, not the latency ring),
duration quantiles in seconds, and cumulative LLM tokens per operation. It
sits behind the same API key as everything else, so a scrape config needs a
`bearer_token`. There is no OpenTelemetry exporter: OTel earns its cost on
distributed tracing, and this is one process — scraping covers the actual
need at zero added dependencies. Routes with no traffic are
**absent** rather than reported as zero, because a zero reads as "this is
instantaneous" or "this is free".

We do not publish a headline latency number: it depends on hardware, store
size, embedder and concurrency to a degree that makes a single figure close to
meaningless. Measure it on your own deployment with `/v1/metrics`.

For cost, `/v1/usage` splits ingest from answer deliberately: ingest is
output-token heavy and answer is input-heavy, so a single total hides the only
comparison worth making.

### Maintenance

Entity profiles are rebuilt on demand, never on a timer — SodaMem ships no
scheduler, because when to spend those tokens is a deployment decision:

```
curl -H "Authorization: Bearer $SODAMEM_API_KEY" -H "Content-Type: application/json" \
  localhost:8000/v1/maintenance/dream -d '{"user_id":"u1","async":true}'
```

Idempotent, resumable, and safe to overlap: a second call while one is running
returns `status:"already_running"` and does nothing. The response carries
`remaining_stale`, so a cron entry that just runs every hour converges without
anyone having to pick a batch size.

### Data

All state lives under the `sodamem-data` volume: per-user stores, the Chroma
vector index, and the control-plane database (`/data/.control/`) holding job
records, API keys and the request log. Job status now survives a restart —
`GET /v1/jobs/{id}` no longer answers 404 for a job that was in flight during
a deploy. Back it up like any other named volume:

```
docker run --rm -v sodamem-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/sodamem-data.tar.gz -C /data .
```

### Upgrading

```
git pull
docker compose up -d --build
```

The named volume is untouched by a rebuild — only the image changes. Check
`schema_version` in `/health` before and after if you're jumping multiple
releases; a store-schema bump would need a migration note here (none exist
yet).

### Running without Compose

```
docker build -t sodamem .
docker run -d \
  -e SODAMEM_API_KEY=... \
  -p 8000:8000 \
  -v sodamem-data:/data \
  sodamem
```

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
