# AC7 — measured `GET /v1/context` latency

Recall sits on the synchronous path between the user pressing enter and the
first token, and the daemon runs `--workers 1` by design (ADR 0001 §2). So the
timeout is set from a measurement, not a hope.

**Every number below was produced by a run of
`dsh-plugin/scripts/measure-context-latency.mjs` against a real daemon. No
number here was estimated.**

## The blocker, stated plainly

A *representative* measurement — one over a store of realistic size — was **not
possible in this environment**, because no `SODAMEM_LLM_API_KEY` is available:
`POST /v1/memories` refuses every write with
`config_invalid: IngestClient requires an extractor (FactEventExtractorV2) — got None`,
even with `infer=false` and `async_mode=false`. With no way to ingest a single
fact, there is no populated store to measure against, and no store existed at
the default data root (`~/.sodamem/data` is absent on this machine).

What was done instead: the harness was written, run, and its output recorded
verbatim below against an **empty** store. That measures the floor — HTTP,
routing, and an empty retrieval — and nothing about retrieval work over real
data. It is reported as a floor and must not be read as a p50 for a real store.

## Run 1 — empty store (floor only)

Command:

```
sodamem daemon ensure --api-url http://127.0.0.1:8765 --api-key devkey \
  --data-root <scratchpad>/ac7-data

SODAMEM_API_URL=http://127.0.0.1:8765 \
SODAMEM_API_KEY=devkey \
SODAMEM_USER_ID=ac7 \
node scripts/measure-context-latency.mjs 50
```

Daemon: SodaMem 0.0.1, `schema_version` 1, `auth` enabled, default daemon flags
from `sodamem daemon ensure` (single worker), fresh empty data root, macOS
arm64, Node 22.22.2, loopback.

Store size: **0 facts, 0 sessions** (the blocker above).

Query set: the script's built-in five —
`what do I prefer for backend work?`, `where do I live?`,
`what did we decide about the release process?`,
`which database are we using?`, `what is my timezone?` — cycled over 50
sequential requests, `token_budget` 1200, one warm-up request excluded.

Measured (milliseconds, wall clock around `fetch` + body read):

| min | p50 | p95 | p99 | max |
|---|---|---|---|---|
| 70.0 | 71.9 | 89.6 | 111.9 | 111.9 |

## What this does and does not justify

- It does **not** justify the 1500 ms recall timeout as "measured headroom over
  a real store". That claim is still unmeasured.
- It does establish that the fixed cost of the call — process boundary, HTTP,
  auth, routing, empty retrieval — is already ~70 ms p50 on loopback with a
  warm daemon, and that the single-worker tail reaches ~112 ms with nothing to
  retrieve.
- The plugin is built so that being wrong here is cheap: exceeding 1500 ms
  caches `''`, the turn proceeds with no memory, and nothing blocks the user.

## To close this properly

Set `SODAMEM_LLM_PROVIDER` / `SODAMEM_LLM_API_KEY`, ingest a store of stated
size, and re-run the command above with `>= 50` iterations. Add the result as
"Run 2 — populated store" with the store size and daemon flags named, exactly
as Run 1 is.
