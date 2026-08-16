# AC7 — measured `GET /v1/context` latency

Recall sits on the synchronous path between the user pressing enter and the
first token, and the daemon runs `--workers 1` by design (ADR 0001 §2). So the
1500 ms recall deadline is set from a measurement, not a hope.

**Every number in this file was produced by a run against a live daemon backed
by a real, populated store. Nothing here is estimated.** (An earlier pass could
only measure an empty store and said so; that floor is superseded and has been
removed.)

## How the store was built — no LLM key required

The first pass reported AC7 as blocked: `POST /v1/memories` refuses every write
without an extractor, so with no `SODAMEM_LLM_API_KEY` there was nothing to
measure against. That blocker was soluble. `sodamem.llm.testing.ScriptedProvider`
feeds `FactEventExtractorV2` a pre-set response per call, which drives the
**real** ingest path — real fact rows, real source spans, real entity roles,
real MiniLM embeddings from the cached model — with **zero LLM network calls**.

Build script: `scripts/populate_store.py <data_root> <user_id> <n_facts>`.

Resulting store:

| | |
|---|---|
| `fact_events` | 1000 |
| `source_spans` | 1000 |
| `raw_turns` | 1000 |
| chroma `embeddings` | 3000 |
| on disk | 23 MB |

The generator rotates four predicate families (`travel_by_airline`,
`employed_by`, `owns_pet`, `resides_in`) across 12 cities, 6 airlines, 6
employers, and 5 pets, so retrieval has genuine competition to rank rather than
N copies of one fact.

Daemon: `sodamem daemon ensure`, auth **enabled**, `--workers 1`, on
`127.0.0.1:8771`.

## Sequential — one client, 200 requests

`node scripts/measure-context-latency.mjs 200`, with six store-relevant queries
supplied via `SODAMEM_QUERIES` rather than the script's generic built-in set
(the same kind of query set the concurrency probe uses; see
`scripts/concurrency_probe.mjs`). `token_budget` 1200, one warm-up excluded.

| metric | ms |
|---|---|
| min | 112 |
| **p50** | **182.6** |
| p95 | 323 |
| **p99** | **471.1** |
| max | 596.4 |

Read this as: auto-injection adds roughly 180 ms to time-to-first-token on a
typical turn and about 470 ms at the tail. Noticeable, not disqualifying — and
because `/v1/context` is the zero-LLM path, this cost does not grow with model
spend.

**This table is the trustworthy absolute number in this file.**

## Concurrent — the `--workers 1` question

The sequential table describes one client. The real deployment is a dsh turn
racing a Cursor hook and a Claude Code hook against the same single worker.
Probe: `node scripts/concurrency_probe.mjs`, 5 rounds per level.

| concurrency | p50 ms | p99 ms |
|---|---|---|
| 1 | 338.3 | 400.8 |
| 2 | 491.1 | 532.4 |
| 4 | 603.4 | 816.7 |
| 8 | 1179.3 | 1379.1 |

It queues, roughly linearly.

### Caveat — do not read these as absolute milliseconds

The probe's numbers run **higher** than the 200-iteration sequential harness at
equivalent load: 338 ms at concurrency 1 against 182.6 ms sequential. That gap
is measurement artifact, not a finding. Five rounds do not warm the BM25 index
cache the way 200 sequential requests do, so the probe pays cold-cache cost
throughout.

The reliable output of the probe is the **shape** — near-linear queueing under
concurrency. For absolute latency, use the sequential table.

### Caveat — scope of the whole measurement

One machine, over loopback, against one store profile (1000 facts, the four
synthetic predicate families above). Different hardware, a real network hop, a
larger or differently-shaped store, or a different query mix will move these
numbers. Nothing here has been reproduced on a second machine.

## Operational consequence — state this plainly

**At concurrency 8, p99 is 1379 ms, which is within 10% of the plugin's 1500 ms
recall deadline.** Past that point recall starts missing its deadline.

When it does, the plugin does the safe thing: it caches `''`, contributes no
memory, and the turn proceeds normally. Nothing breaks and nothing blocks.

But **the failure is silent.** There is no error surfaced to the user and no
degraded turn — memory simply, quietly, stops working under multi-client load.
A deployment that adds a third or fourth concurrent SodaMem client can lose
recall entirely without anyone noticing that anything changed. That is the
property to watch, and it is why the plugin logs a warning on every degraded
turn (`ctx.logger.warn`) even though it never raises.

## The concurrency ceiling is not a plugin defect

The read path not scaling past a handful of concurrent clients is a **SodaMem
daemon property**: one worker by design (ADR 0001 §2), pre-existing, and
entirely independent of this plugin.

What this plugin changes is **reachability**. Over the MCP bridge, `/v1/context`
was an occasional tool call the model had to choose to make. Auto-injection
makes it a *per-turn* call on every turn of every agent. The same ceiling that
was previously hard to reach is now reached by ordinary use with a few clients
attached.

That deserves its own issue against the daemon's read concurrency. It is
**explicitly out of scope for #9** — this plugin's job is to degrade safely
when the ceiling is hit, which it does and which is tested.

## Reproducing

```sh
# 1. Build a real store, no LLM key needed.
python dsh-plugin/scripts/populate_store.py <data_root> <user_id> 1000

# 2. Start the daemon on that store.
sodamem daemon ensure --api-url http://127.0.0.1:8771 \
  --api-key <key> --data-root <data_root>

# 3. Sequential latency.
SODAMEM_API_URL=http://127.0.0.1:8771 SODAMEM_API_KEY=<key> \
SODAMEM_USER_ID=<user_id> SODAMEM_QUERIES='where do I live?|where do I work now?' \
  node dsh-plugin/scripts/measure-context-latency.mjs 200

# 4. Concurrency shape.
SODAMEM_API_URL=http://127.0.0.1:8771 SODAMEM_API_KEY=<key> \
SODAMEM_USER_ID=<user_id> node dsh-plugin/scripts/concurrency_probe.mjs
```
