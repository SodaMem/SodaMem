# Protocol v1.0 (LongMemEval-S answer protocol)

**Headline score: 468/500 (93.6%)** on LongMemEval-S with store
`longmemeval_s_500_Hobs_entitysubj`, model `deepseek-v4-flash`.

> Formerly internal code name **Protocol v1.5**. Soft / earlier protocol
> experiments are treated as **0.x** and are not shipped here.

This directory is **not** a separate product release. It is the **answer-side
protocol** that stacks on SodaMem + `sodamem_opt` (Plan B+) for benchmark
runs. The memory engine lives in `sodamem/`; this tree only patches planner /
reader discipline (question schema, keep-count cardinality, TR/MR skills, etc.).

See `METHOD.md` for design notes and `RESULTS_S500.md` for the scorecard.
Archived summary: `ARCHIVE_S500/summary.json`.

## Run (from repo root)

```bash
export SODAMEM_REPO="$(pwd)"
export SODAMEM_BENCH_DATA=/path/to/bench-data
export SODAMEM_BENCH_STORES=/path/to/longmemeval_s_500_Hobs_entitysubj
export DEEPSEEK_API_KEY=...

cd benchmarking/protocol_v1.0
python run_protocol_s500.py \
  --only ../eval_s500_all.json \
  --range 1-250 \
  --out ./results_shard \
  --concurrency 6 \
  --question-timeout 600 \
  --heartbeat-stale 300
```

`--range START-END` slices by question number (`q001`…`q500`, inclusive).

## Layout

| path | role |
|------|------|
| `protocol_v1/` | patches applied via `SODAMEM_PROTOCOL_V1_ROOT` |
| `skill/set_enumeration/` | MR enumeration skill |
| `run_protocol_s500.py` | runner (sets env + calls `sodamem_opt.run_frozen`) |
