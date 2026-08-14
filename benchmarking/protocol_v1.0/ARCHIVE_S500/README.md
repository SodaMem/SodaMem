# ARCHIVE_S500 — historical protocol snapshot

This folder stores a **prior** LongMemEval-S answer dump (500 rows) and
scorecard for provenance.

| Field | Value |
|---|---|
| Snapshot role | Historical archive only |
| Current public method | **Typed Answer Schema (TAS)** in the parent directory |
| Published engine artifact | Prefer [`../../artifacts/`](../../artifacts/) — **464/500** |

**Do not treat this archive’s score as the score of the current TAS code.**
TAS was scrubbed of per-question entity include/exclude packs and brand
query packs; any new headline requires a fresh run.

## Files

| File | Contents |
|------|----------|
| `summary.json` | Machine-readable scorecard from the historical run |
| `answers_all.jsonl` | 500 final answer rows |
| `answers_all.json` / `.csv` | Same answers, alternate formats |
| `by_question.md` | Per-question table |
