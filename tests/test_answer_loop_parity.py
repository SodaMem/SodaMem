"""Task 10 Step 3: orchestration byte-equivalence gate.

Replays 3 real S500 live-run planner decision sequences through
`sodamem.answer.loop.run_planner_loop()` via `sodamem.llm.testing.
ScriptedProvider`, and asserts the resulting `EvidenceStore`/`PlannerState`
final state matches a reference replay built from the SAME recorded tool
payloads. Tool OUTPUT equivalence is already proven by Task 8's gate
(`tests/test_context_assembly_parity.py`, 20 traces) — this gate is
narrower and checks only LOOP ORCHESTRATION: does replaying the same
planner decisions through the new loop produce the same accumulated
evidence and the same final ranked `evidence_cards`, despite the
architecture change documented in `sodamem/answer/loop.py`'s module
docstring (the runtime override at source :2211-2225 -> `rules.
InitToolRule` executing BEFORE the planner's first turn instead of
discarding it AFTER)?

Fixture data: `~/Desktop/LongMemEval-ingest/archived_runs/
ans_S500_live_fixbatch_0706/agent_traces/*.json` — same directory as
Task 8's gate, NOT part of this repo (see that file's docstring for the
full provenance note). `skipif` when absent.

Why NOT compare against `trace["llm_steps"][-1]["state"]["evidence_cards"]`
(the benchmark harness's own recorded final state), unlike a naive first attempt at this
gate: that recording is FROM THE 0706 BATCH, predating the G3a
"planner-slim" quantity-noise-dropping fix
(`sodamem.context.store.EvidenceRecord.compact()`'s own docstring: "default
since the 0713 6-run verdict"). The recorded cards still carry
`quantity={"value": null, "unit": ""}` on every row (verified directly
against the fixture files) — the current `EvidenceRecord.compact()` this
repo ships correctly drops that noise field. Comparing against the
STALE recording would make this gate fail on a two-week-old fixed bug, not
signal an orchestration regression. `_reference_final_state()` below
instead replays the SAME `cli_tools`/`llm_steps` sequence through the
production `sodamem.context.store.EvidenceStore` /
`sodamem.answer.protocol.PlannerState` classes THIS repo actually ships
(not a duplicate frozen copy — Task 8's gate already independently proves
`EvidenceStore` port fidelity against these exact fixtures), but replays it
the way the OLD rig's runtime actually worked: applying the DISCARDED
step-0 planner proposal's `state_update` (source :2186 ran this
unconditionally, before overriding the proposal's calls) before ingesting
the forced first search. Comparing THAT against `run_planner_loop()`'s own
result — which never applies step-0's `state_update` at all, because it
never asks the planner at step 0 — isolates exactly the orchestration
question this gate exists to answer, using the SAME evidence-store/
planner-state machinery on both sides.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sodamem.answer.loop import PlannerConfig, run_planner_loop
from sodamem.answer.protocol import PlannerState
from sodamem.context.store import EvidenceStore
from sodamem.llm.testing import ScriptedProvider
from sodamem.tools import ToolError

FIXTURE_DIR = Path.home() / "Desktop" / "LongMemEval-ingest" / "archived_runs" / "ans_S500_live_fixbatch_0706" / "agent_traces"
TRACE_NAMES = ["q001", "q006", "q008"]

pytestmark = pytest.mark.skipif(
    not FIXTURE_DIR.is_dir(),
    reason=(
        f"assembly-equivalence fixture directory not found on this machine: {FIXTURE_DIR} "
        "(S500 live-run agent traces live outside this repo on the machine that recorded "
        "them; same fixture Task 8's parity gate uses — see tests/test_context_assembly_"
        "parity.py's docstring)."
    ),
)


class _FakeTools:
    """Returns each recorded `cli_tools[i]`'s stdout, parsed, in call order —
    already-parsed payloads are `MemoryTool.dispatch()`'s real contract
    (R12), so this is not a simplification, it's the actual shape."""

    def __init__(self, cli_tools: list[dict[str, Any]]):
        self._queue = list(cli_tools)
        self.dispatched: list[str] = []

    def dispatch(self, name: str, **kwargs: Any) -> dict:
        self.dispatched.append(name)
        if not self._queue:
            raise ToolError("empty_input", f"fixture exhausted at dispatch #{len(self.dispatched)} ({name})")
        row = self._queue.pop(0)
        return json.loads(row["stdout"])


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


def _replayable_planner_outputs(trace: dict[str, Any]) -> list[str]:
    """`llm_steps[:-1]`: every real planner turn INCLUDING step 0 (the loop
    consults the planner at step 0 again since the bug-#9 restoration — its
    calls get overridden by the forced search but its state_update applies,
    exactly like source), skipping only the post-hoc `autonomous_final_state`
    summary row."""
    steps = trace["llm_steps"]
    return [
        json.dumps(
            _ordinary_legacy_packet(json.loads(row["planner_output"])),
            ensure_ascii=False,
        )
        for row in steps[:-1]
    ]


def _ordinary_legacy_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """Normalize the pre-classification ordinary-trace fixture protocol.

    These three historical fixture traces predate the required
    ``question_classification`` field. Their expected final decisions are
    ordinary-query decisions, so the replay adapter supplies that missing
    protocol field uniformly. It deliberately does not inspect question text
    or fixture IDs; production classification gates remain unchanged.
    """
    normalized = dict(packet)
    normalized["question_mode"] = "ordinary"
    update = dict(normalized.get("state_update") or {})
    update.setdefault(
        "question_classification",
        {
            "type": "ordinary",
            "comparison_requires_count_or_sum": False,
        },
    )
    normalized["state_update"] = update
    return normalized


def _reference_final_state(trace: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Old-runtime-semantics replay over THIS repo's real `EvidenceStore`/
    `PlannerState` (module docstring). Returns (compacted_state,
    selected_evidence_ids).

    Scope limit (deliberate, not hidden): this helper takes the FIRST
    `action == "final"` decision at face value — it does not replay
    `_finalization_errors`/rejected-finalization retries, and it assumes
    every proposed call in a `tool_calls` decision consumes exactly one
    `cli_tools` entry (never a `skipped: exact_duplicate` no-op). Verified
    true for all 3 `TRACE_NAMES` (no rejected finalizations, no duplicate-
    call skips in any of their recorded steps) — this is NOT a general
    reference replayer for arbitrary S500 traces; extending `TRACE_NAMES`
    to a trace with either pattern requires extending this helper first
    (it will fail loudly — IndexError or the `cli_cursor` assertion below —
    rather than silently compute a wrong reference)."""
    llm_steps = trace["llm_steps"]
    cli_tools = trace["cli_tools"]
    question = trace["question"]

    evidence = EvidenceStore()
    state = PlannerState(objective=f"Gather sufficient evidence to answer: {question}")

    # Step 0: the discarded planner proposal's state_update WAS applied by
    # the old runtime (source :2186, unconditional) before its calls got
    # overridden by the forced search (source :2216-2224) -- cli_tools[0].
    step0_packet = _ordinary_legacy_packet(llm_steps[0].get("packet") or {})
    state.apply_update(step0_packet.get("state_update"), evidence)
    evidence.ingest(
        cli_tools[0]["tool"], cli_tools[0]["args"], json.loads(cli_tools[0]["stdout"]), 0,
    )

    cli_cursor = 1
    final_decision: dict[str, Any] = {}
    real_steps = llm_steps[1:-1]
    for step_index, row in enumerate(real_steps, start=1):
        packet = _ordinary_legacy_packet(row.get("packet") or {})
        state.apply_update(packet.get("state_update"), evidence)
        decision = packet.get("decision") or {}
        if decision.get("action") == "final":
            final_decision = decision
            break
        for _call in decision.get("calls") or []:
            ct = cli_tools[cli_cursor]
            evidence.ingest(ct["tool"], ct["args"], json.loads(ct["stdout"]), step_index)
            cli_cursor += 1

    assert cli_cursor == len(cli_tools), (
        f"reference replay consumed {cli_cursor} cli_tools entries, fixture has {len(cli_tools)}"
    )

    selected: list[str] = []
    for raw_id in final_decision.get("selected_evidence_ids") or []:
        eid = evidence.resolve(str(raw_id))
        if eid in evidence.records and eid not in selected:
            selected.append(eid)
    state.selected_evidence_ids = selected

    final_step = 1 + len(real_steps)  # step0 + one entry per real planner turn
    return state.compact(evidence, final_step, final_step), selected


@pytest.mark.parametrize("name", TRACE_NAMES)
def test_replayed_loop_matches_reference_final_state(name: str):
    trace = _load(name)
    cli_tools = trace["cli_tools"]

    replayed = _replayable_planner_outputs(trace)
    provider = ScriptedProvider(replayed)
    tools = _FakeTools(cli_tools)

    result = run_planner_loop(
        trace["question"], current_date="2023-06-15",
        tools=tools, provider=provider,
        config=PlannerConfig(max_steps=len(replayed), fallback_top_k=10),
    )

    # Every recorded tool call was replayed exactly once, in order.
    assert len(tools.dispatched) == len(cli_tools), (
        f"{name}: replayed {len(tools.dispatched)} tool call(s), rig recorded {len(cli_tools)}"
    )
    assert result.termination == "planner_final"

    reference_state, reference_selected = _reference_final_state(trace)
    assert result.selected_evidence_ids == reference_selected

    old_json = json.dumps(reference_state["evidence_cards"], sort_keys=True, ensure_ascii=False)
    new_json = json.dumps(result.state["evidence_cards"], sort_keys=True, ensure_ascii=False)
    assert new_json == old_json, (
        f"{name}: replayed loop's evidence_cards diverged from the reference (step0-inclusive) replay\n"
        f"reference ({len(reference_state['evidence_cards'])} cards): {old_json[:2000]}\n"
        f"loop ({len(result.state['evidence_cards'])} cards): {new_json[:2000]}"
    )


def _loop_fixture(tmp_path, planner_outputs):
    """run_planner_loop against the REAL MemoryTool on a tmp store, planner
    scripted. Real tool surface is the point: _FakeTools.dispatch(**kwargs)
    accepting anything is exactly the double-blindness that hid bug #8."""
    from sodamem import SodaMem
    from sodamem.answer.loop import PlannerConfig, run_planner_loop
    from sodamem.llm.testing import ScriptedProvider
    from sodamem.tools import MemoryTool

    (tmp_path / "u1").mkdir(parents=True, exist_ok=True)
    tool = MemoryTool(SodaMem.open(tmp_path / "u1"), user_id="u1")
    provider = ScriptedProvider([
        json.dumps(_ordinary_legacy_packet(json.loads(output)))
        for output in planner_outputs
    ])
    result = run_planner_loop(
        "When did I adopt my cat?", current_date="2023-05-30",
        tools=tool, provider=provider, config=PlannerConfig(),
    )
    return result, provider


def test_step0_consults_planner_applies_state_update_and_forces_search(tmp_path):
    """Bug #9 (audit 0724): source ran the planner at step 0 and DISCARDED ONLY
    ITS CALLS — the step-0 state_update (objective rewrite, open_questions)
    was applied before the forced-search override (source :2185 apply_update
    precedes :2215's override). The port's skip-step-0 'optimization' threw
    away that state shaping and shrank 12 planner turns to 11. Real casualty
    profile: anchor q268's step-0 rewrote the objective to 'find birth date +
    wedding date' with two material open_questions; the ported run hedged
    '32 or 33' on exactly that question. This pins the restored mechanism."""
    import json as _json

    step0 = _json.dumps({
        "state_update": {
            "objective": "REWRITTEN: find adoption date",
            "upsert_claims": [], "retract_claim_ids": [],
            "open_questions": [{"question": "When was the cat adopted?", "material": True}],
            "resolved_questions": [], "conflicts": [],
        },
        "decision": {"action": "tool_calls",
                     "calls": [{"tool": "browser_inspect", "args": {"memory_id": "x"}}]},
    })
    step1 = _json.dumps({
        "state_update": {}, "decision": {
            "action": "final", "selected_evidence_ids": [],
            "sufficiency": "insufficient", "missing_information": "no evidence",
        },
    })
    result, provider = _loop_fixture(tmp_path, [step0, step1])

    # planner consulted at step 0 (not skipped), exactly 2 consults total
    assert result.planner_trace[0].get("planner_output"), "step 0 must consult the planner"
    assert len(provider.calls) == 2, "exactly two planner consults (step 0 + final)"
    # its non-search proposal was overridden by the forced search...
    assert result.planner_trace[0].get("runtime_first_search_enforced") is True
    obs = result.planner_trace[0]["observations"]
    assert obs and obs[0]["tool"] == "browser_search"
    assert not obs[0].get("error"), f"forced search must not error on the real tool: {obs[0].get('error')}"
    # ...but its state_update SURVIVED (the thing the port used to discard)
    assert result.state["objective"] == "REWRITTEN: find adoption date"
    assert result.state["open_questions"], "step-0 open_questions must survive"
    # payload parity: saw_search/saw_compute restored to the planner-visible state
    assert result.state.get("saw_search") is True
    assert result.state.get("saw_compute") is False
