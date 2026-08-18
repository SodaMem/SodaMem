# Changelog

Notable changes per release. This file starts at the first public release —
everything before it was pre-release development on a private repository, and
retro-writing entries for commits nobody could see would be fiction.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html), with
the pre-1.0 caveat that **minor versions may break API** — see the note at the
bottom.

## [Unreleased]

### Fixed

- **Daemon logging** (#19) — every `INFO`/`DEBUG` record from `server/` and
  `sodamem/` was silently discarded under `sodamem daemon` and under the
  Docker image, including the per-request line in `server/app.py`. uvicorn's
  `LOGGING_CONFIG` has no `root` key, so the root logger kept Python's default
  `WARNING` level *and* had no handler: records were never created, let alone
  written. `create_app()` now installs a timestamped stderr handler and lowers
  the `server` / `sodamem` loggers to `INFO`, but only when the root logger has
  no handlers — a no-op under pytest or inside a host application that
  configured logging itself. `~/.sodamem/daemon.log` now contains the
  per-request lines. Scope is deliberate: `server` INFO becomes visible, while
  the `sodamem` library's own loggers stay at `WARNING` — turning those on
  would start writing user-derived content (extractor `raw_value` /
  `predicate_raw`) to disk in the clear, which is its own decision, not a side
  effect of a logging fix. Note: the `uvicorn.error` workaround added in #14 is now
  redundant (it still works and still produces no duplicate line); unwinding it
  is left to a follow-up.

## [0.1.1] — 2026-08-17

### Fixed

- **Tool timeout** — `MemoryTool.dispatch`'s 45s timeout guard no longer leaves a
  non-daemon worker thread behind after a call times out; the timed-out worker
  now runs as daemon so it can't block process exit.
- **BM25 retrieval** — CJK (Chinese/Japanese/Korean) tokenization support and an
  empty-corpus guard in `sodamem/memory/retrieval/bm25.py`.
- **Timestamp / file-lock robustness** — `_safe_fromtimestamp` in
  `sodamem/memory/_shared.py` now guards against out-of-range timestamps instead
  of raising, and `maintenance_lock.py` falls back gracefully on platforms
  without `fcntl` (e.g. Windows).

### Added

- **`integrations/` directory** — Hermes Agent and DeepSeek Harness MCP
  integration guides, the DeepSeek Harness `dsh` patch config, and the shared
  MCP server warm-up script (`scripts/sodamem_mcp_warm.py`).

## [0.1.0] — 2026-08-07

First public release.

### Added

- **Library** — `SodaMem.open()` / `.ingest()` / `.search()` /
  `.build_context()` / `.answer()` / `.dream()`. `build_context()` returns a
  prompt-ready block with citations and makes **zero LLM calls**; `answer()`
  runs the planner + reader path for multi-hop questions.
- **Evidence chain** — `FactEvent → SourceSpan → RawTurn` as foreign keys, so
  every retrieved memory can name the turn it came from.
- **Four time axes** — `occurred_*`, `valid_*`, `document_time`, `created_at`.
  Corrections are ADD-only: a new version plus a `SUPERSEDES` edge, never an
  in-place rewrite.
- **HTTP service** (`[server]`) — add / search / context / answer, batch write,
  `PATCH` supersede, events, jobs, metrics, token usage, Prometheus scrape, and
  `POST /v1/maintenance/dream`. `/v1/context` takes GET or POST.
- **MCP server** (`[mcp]`) — eight tools. Six are reads and always available;
  `add_memories` and `delete_memory` register only under
  `SODAMEM_MCP_ALLOW_WRITE=true`.
- **Agent framework adapters** — LangGraph, CrewAI, OpenAI Agents SDK, Vercel
  AI SDK. Scope is bound at construction and never appears in the schema the
  model sees.
- **TypeScript SDK** (`sdk-ts/`, npm `sodamem`) — zero runtime dependencies,
  ESM + CJS.
- **`sodamem install`** — writes MCP client config for Claude Code, Cursor and
  friends, including the write opt-in.
- **Web console** — browse and inspect memories per tenant, shipped in the
  image.
- **Benchmark artifacts** — 500 LongMemEval-S answers and 8,427 evidence rows
  published in `benchmarking/artifacts/`, re-gradable with any judge.

### Notes

- Automatic supersession and contradiction detection are **unconditional**.
  They previously sat behind an `IngestConfig` flag that defaulted to
  observe-only, which no HTTP or MCP deployment could reach — so no deployment
  ever superseded anything.
- Retrieval (`search`, `build_context`) makes no model call. This is enforced
  structurally: `lint-imports` forbids `memory.retrieval` from importing
  `sodamem.llm` at all.
- A base `pip install sodamem` pulls four dependencies and no web framework.

## Pre-1.0 compatibility

Semantic versioning says 0.x may break anything at any time. What this project
actually commits to before 1.0:

- **Breaking API changes go in a minor bump** (0.1 → 0.2), never a patch, and
  are listed here under `### Changed` with the migration.
- **Store format changes are versioned and checked.** A store records the
  schema version and a fingerprint of the extraction prompts; opening it with
  incompatible code raises `StoreVersionError` rather than reading it wrong.
  Any bump that needs a migration gets a note here.
- **`search` and `build_context` will not start calling an LLM.** If that ever
  changes it will be a new function, not new behaviour in these.

[Unreleased]: https://github.com/SodaMem/SodaMem/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/SodaMem/SodaMem/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/SodaMem/SodaMem/releases/tag/v0.1.0
