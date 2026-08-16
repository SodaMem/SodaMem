# benchmarking/

The LongMemEval rig: code only. No dataset, no run output.

The published result is **92.8% (464/500)** on LongMemEval-S. Every answer and
every retrieved memory behind it is in [`artifacts/`](artifacts/) — 500 answers
verbatim, 8,427 evidence rows, re-gradable with any judge. This directory holds
the harness that produced them.

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
questions and their gold answers — a third-party corpus with its own license.
Committing them would mean redistributing material this repository does not
own and does not need to ship. Point the scripts at the data instead:

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
python benchmarking/run_s500.py --only /path/to/ids.json   # a subset
```

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

## LoCoMo (Cat 1-4)

**1338/1540 (86.88%)** end-to-end QA accuracy on LoCoMo categories 1-4, graded
by LLM-as-judge. Category 5 (adversarial) is excluded — that is the strict
reading, 1,540 of the 1,986 questions. Every number below is counted off this
run's `answers.jsonl`, which has 1,540 unique eval_ids and no errors.

| category | score | |
|---|---|---|
| single-hop (type 4) | 764/841 | 90.8% |
| temporal (type 2) | 277/321 | 86.3% |
| multi-hop (type 1) | 231/282 | 81.9% |
| open-domain (type 3) | 66/96 | 68.8% |

Per-conversation accuracy spans 82.6%-90.8% across the ten stores. The total is
not carried by one conversation and not sunk by one either.

| | |
|---|---|
| score | **1338/1540 (86.88%)** |
| scope | LoCoMo Cat 1-4, Cat 5 (adversarial) excluded; end-to-end QA accuracy, LLM-as-judge |
| answered | 1540/1540, errors 0 |
| store | `locomo10_Hobs` — 10 user stores, all `built`, 2,905 fact events, from 10 conversations / 272 sessions / 5,918 turns. Ingested 2026-07-07 with `deepseek-chat`; not rebuilt for this run. |
| reader / planner | `deepseek-v4-flash` |
| judge | `deepseek-v4-flash`, LongMemEval's own judge prompts, byte-copied. This question set reaches two of them: `multi-session` on 1,219 questions, `temporal-reasoning` on 321. |
| code | a pre-release build — this repository's published history begins at v0.1.0 |
| cost | 8,169 calls; 17.2M prompt + 2.3M completion = 19.5M tokens, 6.1M of the prompt tokens served from cache |

### What the question set is

The questions come from a LoCoMo-refined variant of the corpus, not from
`locomo10.json` read directly. Its type distribution is `{1: 282, 2: 321,
3: 96, 4: 841, 5: 446}`, so Cat 1-4 sums to 1,540 — count and per-category
shape align with the strict official split. No per-question text diff against
the official file was run, so nothing here claims the two files are identical
item by item.

Cat 5 in that file has been rewritten: all 446 of its questions carry a
non-empty answer and non-empty evidence, i.e. they are answerable rather than
adversarial. Cat 5 is fully excluded from this run, so that rewrite cannot
move this score. Whether Cat 1-4 was also touched is not known — comparing
them needs the official file, and that comparison was not run.

### No per-question artifacts for this run

No `answers.jsonl`, no retrieved context, no run directory is published for
LoCoMo. The artifact policy above applies to it like to any other run, and
nothing was added under `artifacts/`. What is published is this section.

### Reproducing

```bash
export SODAMEM_BENCH_DATA=/path/to/locomo-bench-data   # questions_slim.json, core1540_ids.json
export SODAMEM_BENCH_STORES=/path/to/locomo10_Hobs     # <root>/<user_id>/memory.db
export SODAMEM_BENCH_MODEL=deepseek-v4-flash           # reader/planner and judge both read this variable
export DEEPSEEK_API_KEY=...

python benchmarking/run_s500.py \
  --out benchmarking/results/locomo_core1540 \
  --only /path/to/locomo-bench-data/core1540_ids.json \
  --concurrency 6
```

The Cat 1-4 selection is that id list and nothing else — the harness was not
modified for this benchmark. `--only` takes a *path* to a file holding the
eval_ids, either a JSON list or one id per line; `core1540_ids.json` holds
exactly the 1,540 non-Cat-5 ids, and it equals the id set in the run's
answers. `SODAMEM_BENCH_SRC` is dataset prep only and is not needed here.

Reproducing the number digit for digit would mean checking out the exact
pre-release build it was measured on, and this repository's history starts at
v0.1.0 — that build is not in it. The scoring path has not changed since; what
has is unrelated to it. Treat the number as measured-and-reported rather than
re-runnable from this tree.

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
