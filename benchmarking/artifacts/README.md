# Published run artifacts

## `sodamem_lme_judged.json`

Every answer SodaMem produced on LongMemEval-S (500 questions), verbatim, with
the verdict our judge gave it.

| field | meaning |
|---|---|
| `question_id` | positional id in the LongMemEval-S question file |
| `question` / `golden_answer` | from the public dataset, reproduced for alignment |
| `hypothesis` | **our system's answer, verbatim** |
| `llm_judge` | `{model, correct}` — the verdict and which model gave it |
| `planner_steps`, `tools_used`, `evidence_ids` | what the agent did to get there |
| `elapsed_s`, `usage_totals` | wall clock and token cost per question |

**Why `hypothesis` is the point.** A score alone cannot be checked by anyone.
The answers can: take this file, run any judge you like against
`golden_answer`, and see what you get. numbers.

### What this run is

| | |
|---|---|
| score | **464/500 (92.8%)** |
| judge | **`deepseek-v4-flash`** with the LongMemEval benchmark's own judge prompts, byte-identical (5/5 task templates + abstention) |
| reader / planner | **`deepseek-v4-flash`** — **the same model as the judge** |
| store | `longmemeval_s_500_Hobs_entitysubj`, 500 users / 235,840 facts |
| code | a pre-release build — this repository's published history begins at v0.1.0 |

### Reading the number

**Reader and judge are the same model.** That is a self-grading loop and it
runs loose; how loose depends on the answer's shape, so no
"official-protocol equivalent" is derived here. Score these answers with your
own judge instead — that is why they are published.

**The reader is a budget model.** Systems reporting 94-96% on this benchmark
generally use frontier readers, and reader tier moves the score more than most
architectural differences do.

## `sodamem_lme_retrieved_context.json`

What our retrieval returned for each question: the planner's actual queries
and the union of the evidence they retrieved. 8,427 rows, 16.9 per question,
none empty.

| field | meaning |
|---|---|
| `planner_queries` | the queries that run really issued, from its trace |
| `retrieved_evidence[].content` | the memory text |
| `retrieved_evidence[].source_trace_ids` | **which source span this fact came from** — the evidence chain, and the field a key-value memory cannot produce |
| `retrieved_evidence[].kind` / `modality` / `status` | fact vs preference, past event vs current state, active vs superseded |

**This is the retrieval, not the reader's prompt.** That run had
`SODAMEM_BENCH_CAPTURE_INPUT` off, so the assembled prompt was never recorded
and cannot be reconstructed. What could be reconstructed exactly is the
retrieval, and both legs of that claim were measured rather than assumed:

1. **The store has not changed since the run** — its content fingerprint,
   computed after the run finished, still matches.
2. **Retrieval is deterministic** — the same query through two freshly opened
   stores returns the same evidence ids in the same order.

Not reproduced: ordering across planner steps, per-step truncation, and the
planner's own selection and context offloading.

## Reproducing

The judge half needs nothing from us: `hypothesis` and `golden_answer` are
both in the file — point any judge at them.

The answer half needs no access to our service either: take
`retrieved_evidence` for a question, feed it to any reader, and judge the
result against `golden_answer`. Regenerating the retrieval itself needs the
frozen store, which is 12 GB and not distributed — so the script that produced
this file lives with the store rather than here.
