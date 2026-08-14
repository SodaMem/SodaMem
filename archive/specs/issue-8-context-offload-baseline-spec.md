# Spec: Promote Planner context offload to the default baseline
Record: GitHub issue #8

## Problem

GitHub issue #7 introduced a Planner-only Hot/Warm/Folded evidence-card projection behind two default-off controls: `PlannerConfig.context_offload` and `SODAMEM_ANSWER_CONTEXT_OFFLOAD`. The implementation at commit `d6c2bd27320fc0aa1f215e7cc678fb9e78b650a1` has already passed implementation review, the stable69 safety gate, and a fresh paired LongMemEval-S500 validation. Leaving the controls default-off means ordinary `PlannerConfig()` consumers and no-environment S500 runs continue to use the older, more expensive baseline despite that promotion decision.

This ticket is configuration promotion only. It must make the validated arm the default while preserving an explicit false/off override for rollback and paired controls. It must not modify the projection algorithm or any answer behavior outside the choice of default.

## Value

Make the validated lower-context Planner path the baseline without hiding how to reproduce its control. The fresh S500 comparison measured 457/500 with offload OFF versus 461/500 with it ON; the +4 is inside the documented ±8–12/500 noise floor and is not an accuracy claim. The promotion is justified by 1,539,150 fewer Planner-input characters (-12.36%), 293,960 fewer total tokens (-3.15%; 587.92/question), 3.99% fewer tokens per correct answer, complete 500/500 healthy arms, and zero content regressions causally attributable to missing or degraded offloaded context.

## Scope

- `sodamem/answer/loop.py`: change only `PlannerConfig.context_offload`'s default and its adjacent status/evidence comment from experimental/default-off to promoted/default-on.
- `benchmarking/run_s500.py`: change only the unset environment fallback for `SODAMEM_ANSWER_CONTEXT_OFFLOAD` to enabled and update its adjacent comment. Preserve explicit `0` as the off arm and preserve requested/effective/unsupported configuration stamping.
- `tests/test_answer_defaults.py`: pin context offload as part of the promoted default baseline while keeping genuinely unresolved arms pinned off.
- `benchmarking/tests/test_benchmarking_context_offload.py`: pin no-environment requested/effective `true` and explicit environment `0` requested/effective `false`, including arm-validity/stamping behavior.
- `benchmarking/README.md`: replace the issue #7 default-off experiment description with the promoted baseline, explicit rollback/control command, and the fresh full-S500 evidence and interpretation.
- `benchmarking/run_comparison.html`: add or update the comparison artifact so the promoted baseline records commit `d6c2bd2`, 457→461, -12.36% Planner input, -3.15% total tokens, and zero causally attributable regressions without presenting +4 as a score improvement.
- `benchmarking/specs/issue-8-context-offload-baseline-spec.md`: this specification.

Out of scope:

- Any change to `sodamem/answer/context_offload.py` or the Hot/Warm/Folded lifecycle, projection precedence, rehydration, telemetry, or evidence-card shapes.
- Reader code, Reader selection/pool/context/prompt, evidence retention, retrieval, ranking, prompts, claims, finalization, or tool policy.
- Planner budgets, thresholds, allowed tools, or any unrelated configuration default.
- A new LongMemEval-S500 run unless a configuration smoke check shows requested/effective values do not match.
- Push, PR, merge, or modification of repository `main`; delivery ends at a local commit on `codex/issue-8-context-offload-baseline`.

## Implementation Path

1. In `PlannerConfig`, change `context_offload: bool = False` to `True`. Do not change the field name, type, ordering, consumers, constructor wiring, or lifecycle implementation. Update the adjacent comment to cite issue #7's reviewed full-S500 promotion evidence and retain the Planner-only/Reader-unchanged boundary.
2. In `benchmarking/run_s500.py`, change the environment fallback from `os.environ.get("SODAMEM_ANSWER_CONTEXT_OFFLOAD", "0")` to fallback `"1"`. Keep the existing exact `== "1"` parsing so an explicit value of `0` remains false. Do not change subprocess propagation, `_supported`, `_run_arm_status`, answer-row fields, summary fields, banner fields, unsupported-flag handling, or requested/effective mismatch validation.
3. Update default tests so `PlannerConfig().context_offload is True` is asserted with the promoted mechanisms, while `abstention_gate` and `time_window` remain the unresolved default-off arms. Add focused benchmark tests that reload or otherwise evaluate `run_s500` with the environment variable absent and with it explicitly set to `0`. The no-environment case must prove the module-level requested value is true and that a supported `PlannerConfig` produces requested/effective true with a valid arm stamp. The override case must prove requested/effective false with a valid arm stamp. Tests must restore environment/module state and must not invoke providers, Chroma, the Reader, or a real 500-question run.
4. Update `benchmarking/README.md` to state that context offload is now the default baseline. Document `SODAMEM_ANSWER_CONTEXT_OFFLOAD=0` as the paired-control and rollback path and `=1` as an explicit baseline pin. Preserve the description of full evidence staying in `EvidenceStore`, Reader isolation, requested/effective stamping, and fail-closed unsupported/mismatch behavior.
5. Update `benchmarking/run_comparison.html` with the promotion evidence from `/Users/aaron.w/Desktop/LongMemEval-ingest/results/issue7-full500-d6c2bd2-analysis.md`: OFF 457/500, ON 461/500, McNemar p=0.5847, movement inside the noise floor, Planner input -12.36%, prompt tokens -3.33%, total tokens -293,960/-3.15%, tokens per correct -3.99%, elapsed -0.86%, and 0 of 30 official discordances attributable to offload after content/trace review. The artifact must describe the result as cost validation with preserved accuracy, not as a statistically supported score gain.
6. Review the final diff against starting commit `d6c2bd27320fc0aa1f215e7cc678fb9e78b650a1`. Beyond this spec, production-code changes must be limited to the two boolean fallback literals and their adjacent comments. Tests and benchmark documentation/comparison may change only to pin and explain those defaults. No algorithm, Reader, prompt, ranking, threshold, or unrelated-default change is permitted.
7. Run focused configuration/default tests, then the repository's complete local gates (`pytest -q`, `ruff check .`, import contracts, and the documented base-install gate). Run a no-environment configuration smoke and an explicit-off smoke that inspect emitted/requested/effective configuration without provider calls. A new full-S500 run is required only if either smoke exposes a mismatch or unsupported configuration.
8. Create one local commit on `codex/issue-8-context-offload-baseline`. Do not push or open a PR.

## Acceptance Criteria

- [ ] AC1: `PlannerConfig().context_offload is True`, and the only runtime semantic change in `sodamem/answer/loop.py` from `d6c2bd27320fc0aa1f215e7cc678fb9e78b650a1` is that single default literal; the offload algorithm and every unrelated `PlannerConfig` default are unchanged.
- [ ] AC2: With `SODAMEM_ANSWER_CONTEXT_OFFLOAD` absent, the S500 harness records `context_offload_requested=true` and `context_offload=true`, reports no unsupported flags, and marks the arm configuration valid. The unset fallback literal/comment are the only production behavior changes in `benchmarking/run_s500.py`.
- [ ] AC3: With `SODAMEM_ANSWER_CONTEXT_OFFLOAD=0`, the S500 harness records requested/effective false, reports no context-offload mismatch, and marks the arm valid. Explicit `PlannerConfig(context_offload=False)` likewise remains supported, proving rollback and paired-control paths are retained.
- [ ] AC4: Focused tests pin both defaults and both override paths without making provider, retrieval, Reader, judge, or full-S500 calls. Existing requested/effective, mixed-child, unsupported-checkout, and fail-closed arm-status tests continue to pass.
- [ ] AC5: The diff contains no changes to `sodamem/answer/context_offload.py`, Reader modules, prompts, evidence selection/retention, retrieval/ranking, lifecycle thresholds, Planner budgets/tools, or unrelated defaults. Reader behavior remains inherited byte-for-byte from reviewed commit `d6c2bd2`.
- [ ] AC6: `benchmarking/README.md` documents default ON, explicit `=0` control/rollback, explicit `=1` pinning, Planner-only scope, Reader isolation, and fail-closed requested/effective stamping. `benchmarking/run_comparison.html` records the fresh full-S500 cost evidence and labels 457→461 as inside noise rather than an accuracy gain.
- [ ] AC7: The issue #7 full-S500 report remains the acceptance evidence: both arms 500/500 and healthy; total tokens -293,960/-3.15%; Planner input -12.36%; tokens per correct -3.99%; exact McNemar p=0.5847; and zero content-real regressions attributable to offload. No new full-S500 run is required unless AC2 or AC3 exposes a requested/effective configuration mismatch.
- [ ] AC8: Focused tests, full `pytest -q`, `ruff check .`, import contracts, and the documented base-install gate pass. The final branch contains one local implementation commit based on `d6c2bd2`, with no push or PR.

## Open Questions

None. The ticket explicitly decides promotion, preserves an off override, and supplies sufficient fresh full-S500 evidence; implementation should not reopen algorithm or accuracy-policy questions.
