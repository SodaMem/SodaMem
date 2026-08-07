"""A claim's own evidence gets added, not demanded back from the model.

The check this replaces exists for citation integrity: every material claim
the reader is shown must have its backing evidence in the reader's context. It
enforces that by rejecting the finalization and making the planner try again —
which cost 254 rejections across 500 questions on the 0731 traced run, 70% of
them followed by a step that only re-submits the same `final` with no
retrieval at all. One LLM call each, to supply ids the loop is already holding
in `claim.evidence_ids`.

Worse, it does not achieve its own goal where it matters most. Of 30 questions
that were rejected this way and still ran out of steps, 22 (73%) reached the
reader with claim evidence missing anyway: bounce the model enough times and
it hits `max_steps`, and the fallback hands over the unbacked claims. The
check works when the planner was nearly right and fails when it was not.

Measured before writing this, on 254 real rejections: claim evidence ids are
median 3, p90 7, max 14 against a cap of 24 (never overflows here, but the cap
is still honoured with claim evidence taking priority), and exactly 1 of the
254 co-occurred with a hallucinated id — which is why autofill resolves every
id through the evidence store rather than trusting the claim.
"""
from __future__ import annotations

from sodamem.answer.loop import _finalization_errors
from sodamem.answer.protocol import Claim, PlannerState
from sodamem.context.store import EvidenceStore


def _store(*fact_ids):
    ev = EvidenceStore()
    ev.ingest("browser_search", {}, {"items": [
        {"id": f, "evidence_id": f"ev_fact:{f}", "content": f"content {f}"}
        for f in fact_ids
    ]}, 0)
    return ev


def _state_with_claim(*evidence_ids):
    state = PlannerState(objective="t")
    state.question_classification = {"type": "ordinary",
                                     "comparison_requires_count_or_sum": False}
    state.claims["c1"] = Claim(claim_id="c1", statement="the user bought a lamp",
                               evidence_ids=list(evidence_ids),
                               status="supported", material=True)
    return state


def test_claim_evidence_is_added_instead_of_rejected():
    ev = _store("a", "b")
    errors, selected = _finalization_errors(
        decision={"sufficiency": "sufficient",
                  "selected_evidence_ids": ["ev_fact:a"]},
        state=_state_with_claim("ev_fact:a", "ev_fact:b"),
        evidence=ev, max_selected_evidence=24, claim_evidence_autofill=True,
    )
    assert errors == []
    assert set(selected) == {"ev_fact:a", "ev_fact:b"}


def test_a_hallucinated_claim_id_is_not_smuggled_in():
    """1 of 254 real rejections cited an id that did not exist. Autofill must
    resolve through the store, or it becomes a way to put a fabricated
    evidence id in front of the reader."""
    ev = _store("a")
    errors, selected = _finalization_errors(
        decision={"sufficiency": "sufficient",
                  "selected_evidence_ids": ["ev_fact:a"]},
        state=_state_with_claim("ev_fact:a", "ev_fact:does-not-exist"),
        evidence=ev, max_selected_evidence=24, claim_evidence_autofill=True,
    )
    assert selected == ["ev_fact:a"]
    # The claim is still unbacked, so the original error must still fire —
    # silence here would be worse than the rejection loop.
    assert any("omits material supported claims" in e for e in errors)


def test_the_cap_is_honoured_with_claim_evidence_first():
    ev = _store(*[f"f{i}" for i in range(6)])
    errors, selected = _finalization_errors(
        decision={"sufficiency": "sufficient",
                  "selected_evidence_ids": ["ev_fact:f4", "ev_fact:f5"]},
        state=_state_with_claim("ev_fact:f0", "ev_fact:f1", "ev_fact:f2"),
        evidence=ev, max_selected_evidence=3, claim_evidence_autofill=True,
    )
    assert selected == ["ev_fact:f0", "ev_fact:f1", "ev_fact:f2"]
    assert not any("select at most" in e for e in errors)


def test_autofill_is_off_by_default():
    ev = _store("a", "b")
    errors, selected = _finalization_errors(
        decision={"sufficiency": "sufficient",
                  "selected_evidence_ids": ["ev_fact:a"]},
        state=_state_with_claim("ev_fact:a", "ev_fact:b"),
        evidence=ev, max_selected_evidence=24,
    )
    assert selected == ["ev_fact:a"]
    assert any("omits material supported claims" in e for e in errors)


# ---------------------------------------------------------------------------
# The max-steps fallback does NOT have the hole the abstention gate had.
#
# It never calls `_finalization_errors`, which looked like the same structural
# gap — and an earlier commit message, this page's notes and the arm legend all
# said so. Reading the branch settles it: the fallback seeds `selected` from
# every supported claim's `evidence_ids` before anything else, and only falls
# back to `compact_cards` when that comes out empty. It reaches autofill's goal
# by another route.
#
# The "31 surviving integrity violations" that motivated the claim were a
# measurement artifact: the metric read `packet.decision.selected_evidence_ids`
# — what the MODEL proposed — and on a question that ran out of steps the model
# never successfully finalized, so that field only ever holds rejected
# attempts. What actually reaches the reader is built by the code below.
#
# These two tests are regression guards for behaviour that already exists.
# ---------------------------------------------------------------------------

import json  # noqa: E402

from sodamem.answer.loop import PlannerConfig, run_planner_loop  # noqa: E402
from sodamem.llm.testing import ScriptedProvider  # noqa: E402
from sodamem.tools import ToolError  # noqa: E402


class _Tools:
    def __init__(self, n=8):
        self._n = n

    def dispatch(self, name, **kwargs):
        if self._n <= 0:
            raise ToolError("empty_input", "exhausted")
        self._n -= 1
        return {"items": [
            {"id": "a", "evidence_id": "ev_fact:a", "content": "backs the claim"},
            {"id": "b", "evidence_id": "ev_fact:b", "content": "also backs it"},
        ]}


def _run_to_max_steps(*, autofill: bool):
    """A planner that claims support, never finalizes, and runs out of steps.

    The claim is upserted on the SECOND step: `apply_update` drops evidence ids
    the store cannot resolve yet, and on step 0 the search has not run.
    """
    def step(with_claim):
        su = {"question_classification": {"type": "ordinary",
                                          "comparison_requires_count_or_sum": False}}
        if with_claim:
            su["upsert_claims"] = [{
                "claim_id": "c1", "statement": "the user bought a lamp",
                "evidence_ids": ["ev_fact:a", "ev_fact:b"],
                "status": "supported", "material": True,
            }]
        return json.dumps({"state_update": su, "decision": {
            "action": "tool_calls",
            "calls": [{"tool": "browser_search", "args": {"query": "lamp"}}]}})

    return run_planner_loop(
        "What did they buy?", current_date="2023-06-01", tools=_Tools(),
        provider=ScriptedProvider([step(False), step(True), step(True), step(True)]),
        # stall_stop pinned off: this test exercises the MAX-STEPS fallback
        # path, and the scripted rejected-finalize steps would otherwise trip
        # the zero-novelty stall first (c2-default behavior).
        config=PlannerConfig(max_steps=3, claim_evidence_autofill=autofill,
                             stall_stop=False),
    )


def test_the_fallback_carries_claim_evidence_to_the_reader():
    """Citation integrity at max_steps, with autofill off — this is the
    pre-existing guarantee, not something autofill added."""
    result = _run_to_max_steps(autofill=False)
    assert result.termination == "max_steps_reader_fallback"
    assert {"ev_fact:a", "ev_fact:b"} <= set(result.selected_evidence_ids)


def test_autofill_does_not_change_the_fallback():
    """Autofill lives in `_finalization_errors`, which the fallback skips, so
    the two arms must produce the same selection here."""
    off = _run_to_max_steps(autofill=False)
    on = _run_to_max_steps(autofill=True)
    assert set(off.selected_evidence_ids) == set(on.selected_evidence_ids)
