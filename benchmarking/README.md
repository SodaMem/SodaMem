# benchmarking/

The LongMemEval rig: code only. No dataset, no run output.

The published store-of-record result is **92.8% (464/500)** in
[`artifacts/`](artifacts/). **Typed Answer Schema (TAS)** lives under
[`protocol_v1.0/`](protocol_v1.0/) as the answer-side discipline; re-measure
after protocol changes before quoting a TAS score.

## Repository boundary and artifact policy

`benchmarking/` owns benchmark harnesses and benchmark-only tests. Reusable
SodaMem runtime/API code and its contract tests remain in product directories.
Benchmark code may import product code; product code must never import
`benchmarking`.

Run all Python tests, including the benchmark harness suite, with `pytest` (or
`uv run pytest`). Pytest is configured to collect both `tests/` and
`benchmarking/tests/` by default.

Never commit raw datasets, OBS/Chroma/SQLite stores, provider responses,
`answers.jsonl`, raw traces, secrets, `.env` files, or locally generated run
directories. `benchmarking/results/` remains ignored for new output. Source
manifests or ID lists may be tracked only when licensing and privacy are
explicit and they contain no conversation or evidence payload.

## Why no data lives here

`questions_slim.json` and `anchor_slim2_labels.json` carry LongMemEval
questions and their gold answers — a third-party corpus. This repository's
public release is already blocked on one attribution question (see
`COPYRIGHT_TODO.md`); committing benchmark content would add a second,
avoidable one. Point the scripts at the data instead:

```bash
export SODAMEM_BENCH_DATA=/path/to/bench-data       # questions_slim.json, anchor_slim2_labels.json
export SODAMEM_BENCH_STORES=/path/to/frozen_stores  # <root>/<user_id>/memory.db
export SODAMEM_BENCH_SRC=/path/to/questions.json    # full LongMemEval, only for dataset prep
export SODAMEM_BENCH_RESULTS=/path/to/results       # optional, defaults to ./results (gitignored)
```

Every variable is checked before the first LLM call; a missing one exits with
one line naming it. Nothing falls back to a path that happens to exist on one
machine — that is how a run silently scores the wrong store.

## Scripts

| script | what it does |
|---|---|
| `run_s500.py` | The full regression. Per-question subprocess, resume-safe, official LongMemEval judge, paired McNemar against an anchor run. |
| `answer_one_question.py` | One question in its own process. `run_s500.py` invokes it; also useful standalone for debugging. |
| `extract_questions.py` | One-time: slims the 257 MB LongMemEval `questions.json` into `questions_slim.json`. |
| `paths.py` | Resolves the four environment variables above; every miss raises. |

```bash
python benchmarking/run_s500.py --out results/my_run --concurrency 6
python benchmarking/run_s500.py --only q193,q053     # a subset
python benchmarking/run_s500.py --range 1-250        # q001–q250 (shard across machines)
```

Protocol v1.0 (468 headline): [`protocol_v1.0/README.md`](protocol_v1.0/README.md).

`--out` per run, always: the resume logic keys on `answers.jsonl`, so a shared
directory makes one run read another's answers as already-done.

Two ways a run used to produce a plausible but meaningless score are now
refused rather than documented: an arm flag the installed `sodamem` cannot
accept stops the launch before the first billed token, and an answer served by
a model other than the requested one aborts on the question it happened on.
Both used to be fields you were supposed to read afterwards.

## Store of record

`longmemeval_s_500_Hobs_entitysubj` — 500 users, 235,840 facts.

NO re-ingest, migration, repair or flag-changed rebuild on this store: new
store experiments go to a new directory and are paired against it.
`paths.anchor_labels()` defaults to the 3-run consensus anchor.

## Traces

Every run writes two, both per question:

- `answers.jsonl` → `trace`: `(step, tool, query)` for each call. Small enough
  to sit next to the answer and read with `jq`.
- `raw_traces.jsonl` → the full planner trace: `packet` (the model's whole
  decision — state_update, calls, sufficiency, selected ids), `observations`
  (args, `returned_rows`, `new_evidence`, ids, errors), per-step latency, and
  `finalization_rejected` when a finalization was bounced.

Measured, not estimated: **2,071 bytes per step**, ~7.6 KB for a 4-step
question, ~24 KB for one that runs the cap out. A full 500 is about 6.9 MB per
arm.

`planner_output` is dropped when `packet` parsed, since it is the same text;
it is kept when the parse failed, which is the case where the raw string is
the only record of what the model said.

**Not stored: what the planner saw.** `planner_input_chars` is a length. The
user message — evidence cards, search history, feedback — is not kept, so a
trace shows what the planner decided but not the context it decided from.
Storing it would dominate the file.

What the raw trace answers that the compact one cannot:

| question | field |
|---|---|
| Are the steps on one question re-searching synonyms? | `observations[].new_evidence` per step |
| Where do two runs of the same question diverge? | first step whose `args.query` differs |
| What is the MR gate actually blocking? | `finalization_rejected` |
| For a permanent failure, was the right query ever issued? | `observations[].args.query` — never issued is a planning bug, issued and empty is a retrieval bug, and they have nothing in common |
| Do `open_questions` only ever accumulate? | `packet.state_update` — `material_open` is what forces the fallback to insufficient |
