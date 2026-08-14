# Typed Answer Schema (TAS)

LongMemEval **answer-side** discipline on top of SodaMem + Plan B+
(`sodamem_opt`). Package directory: `benchmarking/protocol_v1.0/`.

| | |
|---|---|
| **Public name** | **Typed Answer Schema (TAS)** |
| **Headline** | **468/500 (93.6%)** |
| **Prior published artifact** | 464/500 (92.8%) in [`../artifacts/`](../artifacts/) |
| **Layer** | Answer path only — does **not** replace the SodaMem memory engine |

TAS classifies each question into a task type, then applies structured
answering constraints (enumerate-before-count, temporal pin, named-slot
resolution) so the planner/reader follow evidence discipline instead of
jumping to a bare integer.

---

## What TAS adds vs Soft (Plan B+)

Soft already fixed many temporal / count misses. TAS adds **question-schema
routing** and task-specific advisories.

| Strength | What it does |
|---|---|
| **Question schema** | Classify into tasks (`COUNT_DISTINCT`, `ORDERED_LIST`, `SUM`, `SLOT_LOOKUP`, `TEMPORAL_EVENT`, `VERSIONED_ATTR`, …) |
| **Keep-count cardinality** | `have` = kept admitted items; **no HARD_STOP** when `have < N` |
| **SetEnumeration (MR)** | Force `item_list` (date + quote) before count/sum; mark plan-only rows |
| **Saturation queries** | Schema-driven follow-up searches |
| **OrderedTimeline** | Build `(entity, date)` rows; sort ascending before order answers |
| **EventAnchor + day pin** | Pin absolute day; fill who/which/from-whom only in-window |
| **Conflict / slot board** | Dual candidates + local cue for named slots (redeem vs goal, new vs current) |
| **Sum money-role gate** | Role-tag amounts (raise/donate vs housing/other-spend) for fundraising totals |
| **Entity dedup** | Merge repeated mentions for distinct counts |

**Score delta (same store / model family):** published artifact **464** → TAS **468** (+4 vs artifact; Soft ≈461). Category card: [`RESULTS_S500.md`](RESULTS_S500.md).

---

## Layout

```
benchmarking/protocol_v1.0/
├── README.md
├── METHOD.md
├── RESULTS_S500.md
├── ARCHIVE_S500/
├── protocol_v1/
└── run_protocol_s500.py
```

Agent integration guides live at the repo root:

- [`HERMES_INTEGRATION.md`](../../HERMES_INTEGRATION.md)
- [`DEEPSEEK_HARNESS_INTEGRATION.md`](../../DEEPSEEK_HARNESS_INTEGRATION.md)

---

## Prerequisites

1. SodaMem product install (this repository)
2. `sodamem_opt` (Plan B+) — `apply()` stacks Soft first, then TAS
3. Bench store + `DEEPSEEK_API_KEY` (or `SODAMEM_LLM_API_KEY`) for a full run

TAS does not ingest or store memory; it patches the **answer** path.

---

## Run LongMemEval-S

```bash
export SODAMEM_REPO="$(pwd)"
export SODAMEM_BENCH_DATA=/path/to/bench-data
export SODAMEM_BENCH_STORES=/path/to/longmemeval_s_500_Hobs_entitysubj
export DEEPSEEK_API_KEY=...

cd benchmarking/protocol_v1.0
python run_protocol_s500.py \
  --only /path/to/eval_ids.json \
  --out ./results_s500 \
  --concurrency 6
```

```python
from protocol_v1 import apply
apply()  # Soft (sodamem_opt) then TAS
```

---

## Reproducibility

| Artifact | Path |
|---|---|
| Scorecard | [`RESULTS_S500.md`](RESULTS_S500.md) |
| Machine-readable summary | [`ARCHIVE_S500/summary.json`](ARCHIVE_S500/summary.json) |
| 500 final answers | [`ARCHIVE_S500/answers_all.jsonl`](ARCHIVE_S500/answers_all.jsonl) |
| Earlier published artifact | [`../artifacts/`](../artifacts/) (464/500) |

### Environment

```bash
export SODAMEM_REPO="$(pwd)"
export SODAMEM_BENCH_DATA=/path/to/bench-data
export SODAMEM_BENCH_STORES=/path/to/longmemeval_s_500_Hobs_entitysubj
export DEEPSEEK_API_KEY=...
```

Frozen stores are **not** shipped in git (size / third-party corpus).

### ID list for `--only`

```bash
python - <<'PY'
import json
from pathlib import Path
import os
q = json.loads(Path(os.environ['SODAMEM_BENCH_DATA'], 'questions_slim.json').read_text())
ids = [row['eval_id'] for row in q]
Path('eval_s500_all.json').write_text(json.dumps(ids, indent=2))
print(len(ids))
PY
```

### Re-grade published answers

`ARCHIVE_S500/answers_all.jsonl` contains one JSON object per question with the
final hypothesis and judge label. Re-run LongMemEval's official
`evaluate_qa.py` prompts against those hypotheses if you want an independent
score; the published summary records **468/500**.

## License

Apache-2.0 (same as this repository).
