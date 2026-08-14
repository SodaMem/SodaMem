# S500 Store of Record — FROZEN (2026-08-04)

## Declaration

**Benchmark store of record = `longmemeval_s_500_Hobs_entitysubj`**
(the H+obs build with `entity_subject` applied — 40,690 facts carry the
subject the LLM actually named instead of the hardcoded `entity_user`).

Point `SODAMEM_BENCH_STORES` at it. The previous store of record,
`longmemeval_s_500_Hobs`, is retained unmodified as a historical artifact
under its own freeze policy.

## Score

**Best 92.8% (464/500) · band 90.6%–92.8% (453–464) · median 90.6% · sigma 6.35 · N=3**

| run | score | output dir |
|---|---|---|
| rep1 | **464/500 (92.8%)** ← best | `entitysubj_arm_treatment` |
| rep2 | 453/500 (90.6%) | `entitysubj_rep2` |
| rep3 | 453/500 (90.6%) | `entitysubj_rep3` |

**Quote the band, or quote the best WITH the band — never the best alone.**
Two of three runs landed at 453; 464 is the top of an 11-question spread, not
a typical result. A single figure from this configuration is a coin flip
between 90.6 and 92.8, and reporting only the high side is the same
self-deception this project has called out elsewhere.

**91.4%/92.2% (457/461) belong to neither this store nor this code** — they
are the July `d6c2bd2` OFF/ON arms measured on the parent star-schema store.
461 also happens to be the consensus-anchor's reference count (below), which
is a comparison baseline, NOT a score. Neither number may be quoted for this
configuration.

Config: HEAD `5a9c4c2`, **`deepseek-v4-flash`** as reader/planner/**and judge**
(requested as `deepseek-chat`; `served_models` records v4-flash on 500/500),
`SODAMEM_ANSWER_CONTEXT_OFFLOAD=1` (default), concurrency 4-6.

## Judge

`deepseek-v4-flash` for reader, planner AND judge, with the LongMemEval
benchmark's own `evaluate_qa.py` prompts byte-identical — five task templates
plus abstention, no rewording.

Reader and judge being the same model is a self-grading loop. No
"official-protocol equivalent" is derived anywhere: every answer is published
verbatim in `artifacts/sodamem_lme_judged.json`, so anyone can score it with
any judge and reach their own number.

## Identity (logical fingerprint)

- users: 500 · fact_events total: **235,840** (identical to the parent store —
  the flip rewrites one column, it adds and removes nothing)
- global_digest: `ce990f641e7eb12aa79e1c12a100f10575fdd33dacc1d5cb6addbea85e45e353`
- per-user manifest: `$SODAMEM_BENCH_DATA/entitysubj_store_fingerprint.json`
- the digest covers `(fact_id, subject_entity_id, predicate_canonical, status)`
  per fact — content-level, so any re-ingest, migration or supersession
  mutation flips it, while read-only benchmark runs do not.

## Paired-comparison anchor

Use `$SODAMEM_BENCH_DATA/entitysubj_consensus_anchor.json` — 3-run majority
vote, 461 consensus-correct.

**Why a consensus and not one run's labels:** two runs of the IDENTICAL
configuration measured net −11 with McNemar p=0.052. A single run's labels
therefore manufacture FIXED/BROKE out of reference jitter alone. The 461
consensus-correct count is a REFERENCE, not a score — the score is 453.

## How this store was built

Not by re-ingesting. The LLM's subject answer was already persisted in
`fact_entity_roles`; the hardcoded literal only discarded it when writing
`fact_events.subject_entity_id`. So the store is an APFS clone of the parent
with one column rewritten by the SQL in
`benchmarking/subject_flip_retrieval_diff.py` — the flag's exact logic,
`''`/`'user'`/`'assistant'` excluded. Cost: seconds and no LLM calls, versus
$22.74 and 22 hours for a rebuild.

Reproduce:

```bash
cp -Rc longmemeval_s_500_Hobs longmemeval_s_500_Hobs_entitysubj
# then the UPDATE in subject_flip_retrieval_diff.py::flip_subjects per store
```

## Freeze policy

1. NO re-ingest, migration, repair, or flag-changed rebuild on this store.
   New store experiments go to a NEW directory and are treated as a formal
   experiment axis, paired against this one.
2. Any arm claiming an effect must run **N≥3**. Measured sigma is 6.35, so a
   single run per arm cannot resolve anything below roughly ±13 questions —
   and single-run McNemar on this harness returns p≈0.05 for no change at all.
3. Opening pre-R2.7 stores needs `run_s500.py`'s `_open_frozen()` fingerprint
   echo: R2.7 removed the LongMemEval-tuned extraction prompt, so their
   fingerprints can no longer be recomputed. Read-only rigs only — the product
   path stays strict.
