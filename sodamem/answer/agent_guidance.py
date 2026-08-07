"""Deterministic steering for the planner loop — the runtime's hands, not its brain.

Everything in this module shares one shape: a rule the loop can evaluate
WITHOUT an LLM, attached to a mechanism that would otherwise burn a planner
step. The 0731 c1/c2 traced runs are the ledger:

  stall accounting   the planner re-proposes a dead query, the runtime skips
                     it, the planner reads the skip feedback and proposes it
                     again — q094/q268 did this 13 times each. Cutting the
                     loop on provable spin removed 38% of planner steps and
                     46.5% of tokens at zero measured score cost, and the 51
                     stalled questions scored 46/51 against 45/51 for the
                     same questions running to exhaustion.

  truncation retry   96 of 99 unparseable planner outputs were hard
                     truncation at the 1200-token cap; the old handling fed
                     back "not valid JSON" and regenerated the SAME
                     truncation at the same cap and temperature. A retry
                     must raise the cap or it is theater.

  capability calls   a planner that classifies a question count/comparison
                     owes a count/timeline-family call before finalizing
                     (spec I1 AC4). On c2, 151 questions were bounced for
                     that debt — each exactly once, and 151/151 obediently
                     issued the required call on the next step. A rule the
                     model follows 100% of the time when told is a rule the
                     runtime should execute the first time: synthesize the
                     call, run it, and let the finalization stand. The
                     gate's substance survives — 112/157 of the make-up
                     calls retrieved new evidence, and the full-pool reader
                     sees everything retrieved whether or not the model
                     "selected" it.

Why one module (0731, user direction): these mechanisms began as loose
counters and branches inside `run_planner_loop`, which made every threshold
change a loop-surgery. The loop keeps orchestration (what happens when);
this module owns the arithmetic and the thresholds (when is it time). Both
sides are pinned by tests that survive either being refactored.
"""
from __future__ import annotations

from typing import Any

from sodamem.context.store import _query_terms

# Planner-facing tool per capability family, mirrored from
# `_missing_capability_families` in loop.py (spec I1 AC4).
_FAMILY_TOOLS: dict[str, str] = {
    "count_family": "browser_count_evidence",
    "timeline_family": "browser_timeline_events",
}


class AgentGuidance:
    """Per-question steering state. One instance per `run_planner_loop` call.

    The loop reports events (`note_*`); this object answers questions
    (`stall_verdict`, `truncation_retry_max_tokens`, `capability_calls`).
    It never touches the LLM, the evidence store, or the trace — the loop
    stays the only writer of those.
    """

    def __init__(self, config: Any) -> None:
        self._config = config
        self._dup_proposals = 0
        self._zero_rows = 0

    # -- stall accounting ---------------------------------------------------

    def note_duplicate_proposal(self) -> None:
        self._dup_proposals += 1

    def note_observation(self, observation: dict[str, Any]) -> None:
        """Zero-row accounting. Skipped and errored calls stay out: a skip
        retrieved nothing by design, and a failed call proves nothing about
        what exists (same rule as the capability gate)."""
        if observation.get("skipped") or observation.get("error"):
            return
        if int(observation.get("returned_rows") or 0) == 0:
            self._zero_rows += 1

    def stall_verdict(self, consecutive_zero_novelty: int) -> str | None:
        """Non-None ends the loop. String format is trace-stable — analyses
        and tests key on `reason=count`."""
        if not self._config.stall_stop:
            return None
        if self._dup_proposals >= self._config.stall_dup_threshold:
            return f"dup_proposals={self._dup_proposals}"
        if self._zero_rows >= self._config.stall_zero_rows_threshold:
            return f"zero_row_calls={self._zero_rows}"
        if consecutive_zero_novelty >= self._config.stall_zero_novelty_threshold:
            return f"zero_novelty_run={consecutive_zero_novelty}"
        return None

    # -- truncation retry ---------------------------------------------------

    def truncation_retry_max_tokens(self) -> int | None:
        """Cap for the single parse-failure retry, or None to not retry.

        At temperature 0 an identical retry reproduces the identical
        truncation; the doubled cap is what makes the retry real.
        """
        if not self._config.truncation_retry:
            return None
        return self._config.planner_max_tokens * 2

    # -- capability auto-calls ----------------------------------------------

    def capability_calls(
        self, missing_families: list[tuple[str, str]], question: str
    ) -> list[dict[str, Any]]:
        """Planner-vocabulary calls that settle a finalization's tool debt.

        Args are synthesized from the question's content terms — cruder than
        the label lists the model crafts on its bounce-recovery step, but
        the debt is coverage, not craft: the call retrieves a deduplicated
        roster for the reader's full pool either way, and a run that wants
        the model's craft can turn the flag off and pay the two steps.
        """
        if not self._config.capability_autocall:
            return []
        terms = sorted(_query_terms(question))[:8] or [question]
        calls: list[dict[str, Any]] = []
        for family, tool in missing_families:
            if tool == "browser_count_evidence":
                calls.append({"tool": tool, "args": {"query": question, "labels": terms}})
            elif tool == "browser_timeline_events":
                calls.append({"tool": tool, "args": {"events": terms}})
        return calls


__all__ = ["AgentGuidance"]
