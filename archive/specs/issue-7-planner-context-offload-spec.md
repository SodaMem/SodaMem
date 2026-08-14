# Spec: Planner Hot/Warm/Folded context offload experiment
Record: GitHub issue #7

## Problem

The iterative Planner currently receives up to 24 compact evidence cards on every step. The cards are already smaller than Reader evidence, but previously seen cards are serialized and read again even when their facts have been absorbed into `PlannerState.claims`. In the captured c3 run (500 questions, 1,772 Planner steps), Planner user messages contain 10.11M characters and evidence cards account for 7.60M. The issue's offline counterfactuals measured reductions of 7.2% from folding only supported-claim evidence, 16.2% from sending support text once, 23.1% from retaining a structured index after first sight, and 39.4% from handle-only retention after first sight. The last option has materially higher correctness risk.

There is no persistent model scratchpad today. `EvidenceStore` retains complete runtime evidence, `PlannerState.compact()` repeatedly projects compact cards to the Planner, and the final Reader independently receives raw evidence assembled from `EvidenceStore`. The experiment must reduce repeated Planner context without deleting evidence, changing retrieval/ranking, or changing Reader inputs.

## Value

Turn the Planner's accumulated evidence context into a bounded, recoverable working set: show a card's support text when it first becomes visible, retain enough metadata to remember an unabsorbed card, and replace safely absorbed cards with stable handles. This targets the 75% evidence-card share of Planner user-message characters while preserving the raw evidence needed for reinspection and final answering.

The feature remains default-off until measured. Its value is established by deterministic character reduction plus a stable69 regression experiment, not by assuming that fewer characters preserve answer quality.

## Scope

- `sodamem/answer/context_offload.py` (new): Planner-only lifecycle/projection state and pure Hot/Warm/Folded projection rules.
- `sodamem/answer/loop.py`: independent `PlannerConfig` flag, per-question lifecycle ownership, rehydration notifications after inspect tools, prompt serialization, and trace telemetry.
- `sodamem/answer/protocol.py`: pass the already selected compact cards and current claim/conflict protections into the projection boundary without changing claim semantics or the default compact payload.
- `benchmarking/run_s500.py`: default-off environment arm, subprocess propagation, per-row arm stamp, and inclusion in `unsupported_flags`/provenance reporting.
- `benchmarking/census_planner_context_offload.py` (new): zero-LLM replay/census over captured Planner inputs.
- `benchmarking/README.md`: arm definition, census invocation/output contract, stable69 paired-run protocol, and interpretation rules.
- `tests/test_answer_context_offload.py` (new): deterministic lifecycle, precedence, rehydration, stable-ID, and Reader-isolation tests.
- Focused updates where needed in `tests/test_answer_defaults.py`, `tests/test_answer_prompt_cache_layout.py`, `tests/test_answer_short_ids.py`, and `tests/test_answer_capture_input.py`.

Out of scope:

- Changing `EvidenceStore` ingestion, deduplication, ranking, retention, or raw records.
- Reader pool selection, Reader prompt construction, Reader tool loops, answer wording, or judge behavior.
- Provider-persistent sessions or relying on a provider to remember earlier calls.
- Changing Planner step budgets, retrieval policy, prompts, claim schema, or finalization gates.
- Physically deleting evidence that the Planner does not select.

## Implementation Path

1. Add a single independent `PlannerConfig` boolean, `context_offload`, defaulting to `False`. Wire it to the benchmark as `SODAMEM_ANSWER_CONTEXT_OFFLOAD=0|1`; stamp the effective value in every answer row and summary/arm banner. Unsupported checkout detection must report this arm rather than silently running the control path.
2. Keep `EvidenceStore.compact_cards()` as the sole selector. The offload path must first obtain the exact same selected card membership, 24-card cap, metadata values, and canonical ranking as the control path. It changes only the precision of those selected cards. Do not fork or modify Reader selection.
3. Introduce per-question, runtime-owned projection state keyed by canonical evidence ID. It records which selected IDs have already been delivered as full cards and which canonical IDs must be rehydrated once. This state never enters claims, never deletes store records, and is discarded at the end of the question. Projection occurs before short-ID aliasing so lifecycle identity remains canonical; the existing alias translation remains the only model-bound ID transformation.
4. Project each currently selected card using this deterministic precedence:
   - **Hot**: emit the existing compact card byte-for-byte. A card is Hot when it has never previously appeared in a Planner message, when a successful `browser_inspect` or `browser_inspect_session` returned that ID for this next message, when its ID appears in an unresolved conflict, or when it is explicitly protected by `selected_evidence_ids`. A newly visible card must be Hot even if a state update already cites it.
   - **Folded**: emit only `{"evidence_id": <stable id>}` when the ID is cited by any current `supported` claim and no Hot protection applies.
   - **Warm**: otherwise emit the existing compact card with only `support` removed. Preserve `predicate`, `entities`, `quantity`, `date`, `source`, `role`, `status`, `version_status`, and `expanded` when those fields exist.
   Hot overrides Folded, and Folded overrides Warm. Disputed/hypothesis claims do not fold evidence. Retracting or downgrading the last supported claim returns its evidence to Warm, not Hot. Because current open-question rows carry no evidence IDs, the runtime must not guess semantic links from natural-language text; explicit unresolved-conflict IDs are the deterministic unresolved protection in this version.
5. Mark a selected ID as having received its initial full card only after that Planner message is successfully assembled. Records that existed in `EvidenceStore` but were outside the 24 selected cards remain unseen and must receive a full card the first time they later enter the payload. Re-ranking, eviction, and re-entry must not reset the seen bit.
6. Treat successful `browser_inspect` and `browser_inspect_session` observations as rehydration. Canonicalize their returned/seen IDs through `EvidenceStore.resolve()`, queue them for exactly the next Planner message, emit their then-current full compact cards there if selected, and consume only the IDs actually emitted. IDs not selected in that next projection remain queued until emitted or the question ends. An inspect error or empty result does not invent a rehydration. A rehydrated card may fold again on the following message.
7. Preserve both existing message layouts. With `prompt_cache_layout=False`, levels remain inside `evidence_state.evidence_cards`; with it enabled, they remain in top-level `evidence_cards` in first-seen order. No new model-facing lifecycle labels are required: Hot/Warm/Folded are represented solely by card shape. Apply short evidence IDs after projection, and translate Planner responses back exactly as today.
8. Add trace-only lifecycle telemetry per Planner step, outside the model message: enabled flag; Hot/Warm/Folded counts; canonical IDs in each level; rehydrated IDs consumed; full and projected card-character counts. Keep `capture_planner_input=False` behavior unchanged. This telemetry must make a regression attributable without requiring raw evidence to be duplicated in `answers.jsonl`.
9. Add a zero-LLM census that reads captured `planner_input` steps from a specified `raw_traces.jsonl.gz`, groups them by `eval_id` and step, applies the exact production projection rules, and writes machine-readable JSON plus a concise table. Report source path/digest, source run/provenance if present, question/step counts, baseline and projected total Planner user-message characters, baseline and projected evidence-card characters, absolute and percentage reductions, Hot/Warm/Folded counts, rehydration count, and malformed/skipped rows. The census must explicitly record any legacy-trace assumption it uses when rehydration metadata is unavailable. It must make no provider, retrieval, Reader, or judge calls. Run it against the captured c3 500-question artifact referenced by issue #7 and retain the report path in the ticket/PR evidence.
10. Evaluate the arm with two fresh, separate `run_s500.py --only <stable69 ids>` output directories on the same commit, frozen stores, requested/served model, judge, and 69-ID file: control with the flag off and treatment with it on. The set is the repository's documented 19 stable-wrong targets plus 50 stable-correct controls. Enable Planner-input capture for diagnosis, require 69/69 completed rows, zero errors, `chroma_available=true`, empty `unsupported_flags`, one identical served model per arm, and matching effective flags except for context offload. Report official-judge flips/regressions, content-verify every discordant answer against its evidence/gold, compare Planner steps and usage totals, and explain every stable-correct regression from the paired traces. A content-real stable-correct regression attributable to missing/degraded context fails acceptance; judge-format or ordinary Planner nondeterminism must be labeled with evidence rather than silently counted as mechanism-safe. Interpret small aggregate score movement against the documented +/-8-12 per 500 noise floor; stable69 is a safety gate, not evidence to promote the flag by default.
11. Run focused tests first, then the complete local gates: `pytest -q` and `ruff check .`. Reader modules and Reader snapshots must remain unchanged, and an end-to-end scripted test must show byte-identical Reader context/Reader prompt for off versus on when retrieval and Planner outputs are held fixed.

## Acceptance Criteria

- [ ] AC1: `PlannerConfig.context_offload` and `SODAMEM_ANSWER_CONTEXT_OFFLOAD` independently select the experiment, default to off, cross the benchmark subprocess boundary, are stamped in output, and cannot be silently ignored by an older checkout.
- [ ] AC2: With the flag off, captured Planner messages and final `PlannerState`/`EvidenceStore`/selected-evidence results are byte-for-byte identical to the pre-change control for both cache layouts and with short IDs on and off.
- [ ] AC3: With the flag on, the first Planner-visible occurrence of every selected card is the unchanged full compact card; later unabsorbed occurrences omit only `support`; supported-claim occurrences contain only the stable evidence handle. Hot > Folded > Warm precedence, re-entry, claim retraction/downgrade, disputed/hypothesis claims, and the 24-card eviction boundary are pinned by deterministic tests.
- [ ] AC4: Unresolved-conflict and explicitly selected IDs stay Hot. Successful single-result and session inspect calls rehydrate every returned canonical ID for exactly its next emitted Planner occurrence; errors, empty results, aliases, and temporarily unselected rehydration IDs behave as specified.
- [ ] AC5: Full evidence remains present and unchanged in `EvidenceStore`; canonical/short-ID translation, retrieval ranking, claim citations, final selected IDs, and max-step fallback resolve to the same canonical records under both arms.
- [ ] AC6: Holding tool results and Planner packets fixed, off/on runs assemble byte-identical Reader rows, Reader context, and Reader prompt. No Reader configuration, full-pool behavior, evidence truncation, or answer guidance changes in the diff.
- [ ] AC7: Trace telemetry accurately accounts for every selected card as exactly one of Hot/Warm/Folded and reports card-character totals without forcing Planner-input capture on. Existing capture and prompt-cache-layout tests continue to pass.
- [ ] AC8: The offline c3 census completes without LLM/tool/Reader calls, records provenance and assumptions, and reports reproducible baseline/projected totals plus a non-zero reduction in both evidence-card and total Planner user-message characters; re-running it on the same artifact produces byte-identical machine-readable results.
- [ ] AC9: Focused lifecycle tests, the complete `pytest -q` suite, and `ruff check .` pass on the issue worktree.
- [ ] AC10: Both stable69 arms are complete and comparable (69/69, zero errors, healthy Chroma, empty unsupported flags, identical single served model and settings except the arm). All discordant answers are content-verified, and there is no unexplained or content-real stable-correct regression attributable to the offload mechanism; token/character and Planner-step deltas are reported without claiming significance below the documented noise floor.

## Open Questions

None. The feature stays experimental and default-off; promotion, a full-500 run, or a stricter savings threshold requires a separate decision after this ticket's measurements.
