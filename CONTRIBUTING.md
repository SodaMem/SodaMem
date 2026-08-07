# Contributing

## Setup

```bash
uv venv && uv pip install -e ".[dev,chroma,server,mcp,llm,anthropic]"
uv run pytest -q
```

Python 3.11+. Use all the extras — the server and MCP suites guard themselves
with `pytest.importorskip`, so a partial install *silently skips* them and you
get green output for code you never ran.

## Before you open a PR

```bash
uv run pytest -q
uv run ruff check sodamem server mcp_server sodamem_cli tests benchmarking
uv run lint-imports
```

All three run in CI. The third one surprises people; read on.

## The four things CI enforces that you cannot guess from the code

### 1. `lint-imports` — four architectural contracts

Defined in `.importlinter`. They are not style rules; each one exists because
breaking it caused a real failure.

| contract | what it forbids |
|---|---|
| layering | `answer` → `context` → `memory`, never the reverse |
| I5 | `memory.retrieval` must not import `sodamem.llm` |
| I3 | the core library must never import the service layer |
| hook path | the coding-tool hook stays stdlib-only, so it starts fast |

I5 is the load-bearing one: `search` and `build_context` are advertised as
making **zero LLM calls**. An import is how that stops being true — not a
call, an import, because the next person to edit the file sees the name in
scope and uses it.

### 2. `gate-i1-base-deps` — a base install must still collect

CI installs the package with **no extras** and runs `pytest --collect-only`
over the whole tree. Every file that needs an extra must skip itself:

```python
pytest.importorskip("pydantic_settings", reason="server tests require the [server] extra")

from server.settings import Settings   # after the guard, never before
```

The guard goes **before** the import it protects. A module-level `ImportError`
during collection aborts the entire run — every test disappears at once, and
that looks a lot like a green build.

### 3. No `/v1/*` handler may be a coroutine function

The rule is not "async is bad" — it is **do not run blocking store I/O on the
event loop**. SQLite, Chroma and the embedder are all synchronous. FastAPI runs
a plain `def` in its threadpool and an `async def` *on the loop*, so one
blocking `async def` stalls every other request: measured at 7.4s of `/health`
unavailability during a single ingest, long enough for the container
HEALTHCHECK to declare a healthy server dead.

`/health` is deliberately `async def` and must stay that way — it touches no
store, so running it on the loop is exactly what lets it answer while every
threadpool worker is busy.

`test_no_v1_route_handler_is_a_coroutine_function` discovers every module under
`server/routes/` that exposes a `router`, so a new file is covered the day it
is added. If you genuinely need `async def`, move the blocking call behind
`starlette.concurrency.run_in_threadpool` first, and say why in the PR.

### 4. `tests/gates/` — invariants, not features

These pin properties the project committed to (I1 no web framework in the base
install, I6 stores are version-checked). If one fails, do not adjust the gate
to match your change — the gate is the requirement.

## Style

Match the file you are editing. Two things this codebase does deliberately:

**Comments explain WHY, never WHAT.** A comment restating the code is noise; a
comment naming the failure the code prevents is the only durable
documentation. This is enforced socially, and one part mechanically:
`test_no_dangling_references_to_predecessor_repositories` fails on a pointer
into a repository the reader cannot open. The reasoning stays, the pointer
goes.

**No silent degradation.** If something cannot be done, raise. Do not fall back
to a default that produces a plausible-looking wrong answer — most of the
sharpest bugs in this project's history were a fallback that worked.

## Tests

A new test should name the failure it prevents, not the function it calls.
`test_supersession_always_writes` beats `test_maintainer_2`. If you are fixing
a bug, the test's docstring should be able to describe the bug in one sentence.

For anything touching retrieval or ingest, prefer the real code path over a
mock: `FactEventExtractorV2(provider=None)` is a real, documented,
zero-network fallback and `sodamem.llm.testing.ScriptedProvider` replays fixed
responses. Both beat hand-rolled stubs that can drift from what the code does.

## Benchmarks

`benchmarking/` needs the LongMemEval corpus, which is third-party and not in
this repository — see `benchmarking/README.md`. Benchmark code may import
product code; **product code must never import `benchmarking`**.

If you are claiming a change moves the score: one run per arm proves nothing.
Measured run-to-run spread on this harness is around ±13 questions, and a
single-run paired McNemar can return p≈0.05 for no change at all.

## Commits and PRs

Explain why, in the body, in whatever language you think in. A PR that changes
behaviour should say what the old behaviour was and what would have to be true
for the change to be wrong.

By contributing you agree your work is licensed under Apache-2.0, the same as
the rest of the project.
