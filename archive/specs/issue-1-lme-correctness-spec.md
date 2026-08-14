# Spec: LongMemEval retrieval correctness and Hobs audit cleanup
Record: GitHub issue #1

## Problem
Time-window tool arguments currently parse ISO dates as numeric prefixes, MR questions can finalize from non-exhaustive similarity top-k results, and ordinary retrieval unconditionally writes unbounded audit bundles whose failures can break reads. The fail-closed real dry-run and an independent read-only verification established that the replay created 1,871 audit rows across 325 Hobs stores. This corrects the older 1,864-row/324-store survey, which skipped `lme_q292` because of its journal state; that database contributes exactly seven authorized-window rows.

## Value
Restore correct temporal filtering and MR answers, keep retrieval read-only and resilient by default, bound opted-in diagnostics, and safely remove the authorized replay artifacts without touching unrelated data.

## Scope
- `sodamem/tools/__init__.py`
- `sodamem/prompts/planner.py`
- `sodamem/answer/protocol.py`
- `sodamem/answer/loop.py`
- `sodamem/answer/rules.py` only if the successful-tool-family gate is expressed declaratively
- `sodamem/memory/retrieval/config.py`
- `sodamem/memory/retrieval/search.py`
- `sodamem/memory/storage/store.py`
- `benchmarking/scripts/cleanup_hobs_audit_bundles.py` (new)
- Focused tests in `tests/test_tools.py`, `tests/test_prompts.py`, `tests/test_answer_planner_reader.py` or `tests/test_answer_rules.py`, `tests/test_retrieval_search.py`, `tests/test_storage.py`, plus cleanup-script tests
- Cleanup target only: `/Users/aaron.w/Desktop/LongMemEval-ingest/longmemeval_s_500_Hobs/**/memory.db`, table `audit_bundles`, rows created from `2026-07-26 19:44:46` through `20:00:47` Asia/Singapore, inclusive

## Implementation Path
1. Replace parseFloat-style coercion with one strict boundary parser. Accept finite numeric epochs and complete numeric strings; parse ISO dates/datetimes into real epoch seconds; honor explicit offsets/`Z` and use the store’s existing local-time convention for naive values. A date-only lower bound means local midnight; a date-only upper bound means the end of that local day. Reject booleans, non-finite numbers, malformed explicit values, and `from_ts > to_ts` with `ToolError(code="invalid_request")`; never silently drop them.
2. Extend the planner protocol/prompt so every question is classified as ordinary, enumeration, count, sum, or comparison. Enumeration/count/sum require a successful count-family call (`browser_count_evidence`); comparison requires a successful timeline-family call (`browser_timeline_events`). If a comparison also asks for counts/sums, require both families. State explicitly that ranked similarity top-k results are candidates and never exhaustive.
3. Track successful tool capabilities separately from attempted calls. A dispatch satisfies a family only when it returns without an error; empty successful results count as executed, failed calls do not. Reject planner finalization when classification is missing or its required family has not succeeded. Apply the same rule to max-step fallback: it must become insufficient with explicit missing information, never silently return a sufficient MR answer from similarity search alone.
4. Add explicit audit configuration with ordinary search defaulting off and a finite per-user retention cap. Only opted-in searches persist bundles. Insert/upsert and pruning occur in one locked transaction with deterministic newest-first retention. Catch audit write/prune failures at the retrieval boundary, log and surface a typed degradation, and still return the already-computed retrieval result.
5. Implement the cleanup as a two-phase utility. Dry-run is the default and emits a deterministic manifest containing the exact database paths and target `bundle_id`, `user_id`, `created_at`, query/payload digests, per-database counts, logical sizes, and SQLite page/freelist metrics. Apply requires that exact manifest and its digest, creates checksummed SQLite-consistent backups for every affected database, then deletes only the manifested IDs with the authorized inclusive epoch predicate.
6. Process affected databases sequentially. Preflight free space, checkpoint WAL safely, reclaim only each affected `memory.db` with SQLite-supported vacuuming, and never touch Chroma databases. Abort a database on manifest drift, backup failure, row-count mismatch, integrity failure, or insufficient reclaim space.
7. Record before/after evidence: expected 1,871 rows in 325 databases before apply; zero target rows afterward; unchanged non-target audit counts/digests; unchanged row counts for every non-audit table; exact delete row counts; `foreign_key_check` clean; `integrity_check` equal to `ok`; backup checksums; and per-database/file aggregate space reclaimed.

## Acceptance Criteria
- [ ] AC1: Numeric epochs, complete numeric strings, ISO datetimes, offsets, and `Z` produce correct epoch boundaries in raw-search and evidence-count; a date-only `to_ts` includes events through 23:59:59.999999 local time and excludes the next day.
- [ ] AC2: Invalid explicit boundaries, booleans, NaN/infinity, partial numeric garbage, and inverted windows fail with `invalid_request` instead of becoming `None`, a year number, or a backend error.
- [ ] AC3: The planner prompt/protocol classifies ordinary, enumeration, count, sum, and comparison questions and states that similarity top-k is non-exhaustive; focused tests pin the classification/tool-family guidance.
- [ ] AC4: Enumeration/count/sum finalization is rejected until count-family success; comparison is rejected until timeline-family success; mixed comparison aggregates require both. Failed calls do not satisfy the gate, and successful empty calls do.
- [ ] AC5: Max-step fallback cannot bypass the MR gate and reports insufficient with the missing required tool family when the gate remains unmet.
- [ ] AC6: Ordinary search writes no audit row. Explicit audit opt-in writes and deterministically prunes to the configured finite cap.
- [ ] AC7: Injected audit insert, commit, or prune failure does not fail retrieval; evidence is returned and the audit degradation is observable.
- [ ] AC8: Cleanup dry-run is non-mutating and its manifest resolves exactly 1,871 authorized rows across 325 Hobs `memory.db` files; apply refuses any manifest/database drift.
- [ ] AC9: Before deletion, every affected database has a verified backup artifact. Apply deletes only manifested `audit_bundles` rows in the inclusive authorized window and does not modify Chroma or non-audit tables.
- [ ] AC10: Post-cleanup verification reports zero target rows, unchanged non-target data, clean SQLite integrity/foreign-key checks, successful safe per-database reclaim, and before/after size metrics.
