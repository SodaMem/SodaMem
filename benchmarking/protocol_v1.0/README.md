# SodaMem Protocol v1.0

LongMemEval-S **answer-side protocol** on top of SodaMem + Plan B+ (`sodamem_opt`).

| | |
|---|---|
| **Headline** | **468/500 (93.6%)** |
| **Store** | `longmemeval_s_500_Hobs_entitysubj` |
| **Model** | `deepseek-v4-flash` |
| **Layer** | Protocol only — does **not** replace the SodaMem memory engine |

Soft / earlier stacks are treated as **0.x (~461/500)**.

---

## What v1.0 improves vs Soft (Plan B+)

Soft already fixed many temporal / count misses. Protocol v1.0 adds **question-schema routing** and **task-specific advisories** so the planner/reader follow structured discipline instead of jumping to a bare integer.

| Strength | What it does | Typical win |
|---|---|---|
| **Question schema** | Classify IE / TR / MR / KU / ABS into tasks (`COUNT_DISTINCT`, `ORDERED_LIST`, `SUM`, `SLOT_LOOKUP`, …) | Routes the right tools and advisories |
| **Keep-count cardinality** | `have` = kept admitted items; **no HARD_STOP** when `have < N` | Soft gate only — avoids Soft-era over-abstention while still pushing enumeration |
| **SetEnumeration (MR)** | Force `item_list` (date + quote) before count/sum; mark plan-only rows | Cuts “guess N” and plan-only pollution on set questions |
| **Saturation queries** | Schema-driven extra searches at step-0 / capability calls | Pulls missing evidence for undercount / incomplete lists |
| **OrderedTimeline (TR/MR)** | Build `(entity, date)` rows; sort ascending before order answers | Trip / event order |
| **EventAnchor + day pin (TR)** | Pin absolute day; fill who/which/from-whom only in-window | “Last Saturday / two weeks ago” companion & place |
| **Conflict / slot board** | Dual candidates + local cue for versioned attributes | Points redeem vs balance, latest vs “new plan” |
| **Sum money-role gate** | Charity/raise vs housing/other-spend | Totals without mortgage/workshop noise |
| **Entity dedup** | Merge repeated mentions for distinct counts | Entity-level how-many |

**Score delta (same store / model family):** Soft ~**461** → Protocol v1.0 **468** (+7). Category card: see [`RESULTS_S500.md`](RESULTS_S500.md).

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
2. `sodamem_opt` (Plan B+) — Protocol `apply()` stacks Soft first, then Protocol
3. Bench store + `DEEPSEEK_API_KEY` (or `SODAMEM_LLM_API_KEY`) for the 500-run

Protocol code alone does not ingest or store memory; it patches the **answer** path.

---

## Run LongMemEval-S (headline path)

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

`SODAMEM_PROTOCOL_V1_ROOT` is set to this directory by the runner. You can also call:

```python
from protocol_v1 import apply
apply()  # Soft (sodamem_opt) then Protocol v1.0
```

---

## License

Apache-2.0 (same as this repository).
