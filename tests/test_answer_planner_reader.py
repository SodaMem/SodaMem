"""Guardian tests for the planner loop and the reader.

EvidenceStore-behavior tests already covered by Task 8's own suite
(`tests/test_context.py`, `tests/test_context_assembly_parity.py` — 20 real
S500 traces replayed byte-for-byte) are NOT re-ported here: those symbols
(`EvidenceStore`/`_query_centered_excerpt`/`compact_cards`) live in
`sodamem.context.store`, not `sodamem.answer`, and porting the same handful
of example-based assertions here would duplicate coverage a stronger gate
already provides. This file covers only the symbols this task actually
owns: `PlannerState`/`_finalization_errors`/`_call_signature`/
`run_planner_loop`/`assemble_reader_context`/`answer`.

Adaptations forced by the API surface changing (task brief Step 1):
- `PlannerState(..., saw_search=True)` -> plain `PlannerState(...)`
  (`saw_search`/`saw_compute` deleted, see `protocol.py`'s docstring).
- `_finalization_errors(..., require_compute=False)` -> the
  `require_compute` parameter is deleted; the source's "at least one
  browser_search is required" check is `rules.check(..., is_final=True)`'s
  job now, exercised separately below.
- `run_autonomous_agent(chat=fake_chat, api_key=..., model=..., ...)` ->
  `run_planner_loop(tools=FakeTools(...), provider=ScriptedProvider(...),
  config=PlannerConfig(...))` — no reader call inside the loop anymore
  (protocol/loop/reader split); reader behavior is tested against
  `assemble_reader_context()`/`answer()` directly.
- The old runtime-override test tested a mechanism `rules.InitToolRule`
  replaces outright (see `loop.py`'s
  module docstring) — `test_loop_issues_init_search_before_consulting_
  planner` below asserts the NEW mechanism's observable equivalent: search
  happens before any planner LLM call, not after one gets discarded.
"""
from __future__ import annotations

import json

import pytest

from sodamem.answer import answer_question
from sodamem.answer.loop import (
    DEFAULT_ALLOWED_TOOLS,
    PlannerConfig,
    _call_signature,
    _finalization_errors,
    run_planner_loop,
)
from sodamem.answer.protocol import PlannerState
from sodamem.answer.reader import ReaderConfig, assemble_reader_context, answer as reader_answer
from sodamem.answer.rules import DEFAULT_RULES, check as rules_check
from sodamem.context.store import EvidenceStore
from sodamem.llm import LLMProvider
from sodamem.llm.testing import ScriptedProvider
from sodamem.prompts.planner import PLANNER_SYSTEM_PROMPT
from sodamem.tools import ToolError


class FakeTools:
    """`MemoryTool`-shaped double: returns already-parsed payloads by call
    order, ignoring the dispatch name (matches `MemoryTool.dispatch()`'s
    `-> dict` contract; R12 means there is no stdout text to parse here)."""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls: list[tuple[str, dict]] = []

    def dispatch(self, name, **kwargs):
        self.calls.append((name, kwargs))
        if not self._payloads:
            raise ToolError("empty_input", f"FakeTools exhausted at call {name}")
        return self._payloads.pop(0)


class _TruncatingThenCompleteProvider(LLMProvider):
    """Local double exposing `_last_finish_reason` the way
    `OpenAICompatibleProvider` does (see `reader.answer`'s `_ask` closure
    docstring) — `ScriptedProvider` doesn't model finish_reason at all, so
    this test needs its own small double."""

    def __init__(self, responses: list[tuple[str, str | None]]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, messages, system="", max_tokens=2048, temperature=None,
                 usage_phase=None, **kwargs):
        self.calls.append({"messages": messages, "system": system})
        text, finish_reason = self._responses.pop(0)
        self._last_finish_reason = finish_reason
        return text

    async def acomplete(self, **kw):
        return self.complete(**kw)


def _tool_output(*rows):
    return {"items": list(rows)}


_ORDINARY_CLASSIFICATION = {
    "question_classification": {
        "type": "ordinary",
        "comparison_requires_count_or_sum": False,
    }
}


# ---------------------------------------------------------------------------
# PlannerState (ported from test_autonomous_agent_runtime.py)
# ---------------------------------------------------------------------------

def test_call_signature_normalizes_query_whitespace_and_case():
    assert _call_signature(
        "browser_search", {"query": " Rare   Items ", "top_k": 10}
    ) == _call_signature(
        "browser_search", {"query": "rare items", "top_k": 10}
    )


def test_supported_claim_without_real_evidence_is_downgraded():
    state = PlannerState(objective="test")
    state.question_classification = {"type": "ordinary", "comparison_requires_count_or_sum": False}
    state.apply_update(
        {
            "upsert_claims": [{
                "claim_id": "c1",
                "statement": "Unsupported statement",
                "evidence_ids": ["invented"],
                "status": "supported",
            }]
        },
        EvidenceStore(),
    )

    assert state.claims["c1"].status == "hypothesis"
    assert state.claims["c1"].evidence_ids == []


def test_planner_payload_strips_signature_and_noise_quantity():
    store = EvidenceStore()
    store.ingest("browser_search", {"query": "q"}, _tool_output(
        {"id": "fact_slim1", "support_text": "user ran 5 km", "quantity": {"value": None, "unit": ""}},
        {"id": "fact_slim2", "support_text": "user has a pace goal", "quantity": {"value": None, "unit": "min/km"}},
        {"id": "fact_slim3", "support_text": "user ran a race", "quantity": {"value": 5, "unit": "km"}},
    ), 0)
    state = PlannerState(objective="test objective")
    state.search_history.append({
        "step": 0, "tool": "browser_search", "args": {"query": "q"},
        "signature": _call_signature("browser_search", {"query": "q"}),
        "new_evidence": 3, "returned_rows": 3,
    })

    payload = state.compact(store, step=1, max_steps=12)

    # G1: signature projected out of the payload, kept in memory for dup checks.
    assert "signature" not in payload["search_history"][0]
    assert state.search_history[0]["signature"]
    assert payload["search_history"][0]["tool"] == "browser_search"
    # Bug #9: source exposed these two keys in the planner payload (:1494-1495);
    # the port dropped them; restored.
    assert payload["saw_search"] is False and payload["saw_compute"] is False
    cards = {c["evidence_id"]: c for c in payload["evidence_cards"]}
    # G3a: pure-noise quantity dropped; keep-unit rule; real values untouched.
    assert "quantity" not in cards["fact_slim1"]
    assert cards["fact_slim2"]["quantity"] == {"value": None, "unit": "min/km"}
    assert cards["fact_slim3"]["quantity"] == {"value": 5, "unit": "km"}


# ---------------------------------------------------------------------------
# _finalization_errors (ported, minus require_compute/saw_search)
# ---------------------------------------------------------------------------

def test_finalization_rejects_material_unknowns_and_unknown_ids():
    evidence = EvidenceStore()
    evidence.ingest("browser_search", {}, _tool_output(
        {"id": "fact_a", "evidence_id": "ev_fact:fact_a", "content": "Known evidence"},
    ), 0)
    state = PlannerState(objective="test")
    state.open_questions = [{"question": "Could another component exist?", "material": True}]

    errors, selected = _finalization_errors(
        decision={
            "sufficiency": "sufficient",
            "selected_evidence_ids": ["ev_fact:fact_a", "invented"],
        },
        state=state, evidence=evidence, max_selected_evidence=24,
    )

    assert selected == ["ev_fact:fact_a"]
    assert any("unknown evidence id" in error for error in errors)
    assert any("material open questions remain" in error for error in errors)


def test_finalization_allows_reader_to_work_without_planner_claim_protocol():
    evidence = EvidenceStore()
    evidence.ingest("browser_search", {}, _tool_output(
        {"id": "fact_a", "evidence_id": "ev_fact:fact_a", "content": "The answer-bearing source text."},
    ), 0)
    state = PlannerState(objective="test")
    state.question_classification = {
        "type": "ordinary",
        "comparison_requires_count_or_sum": False,
    }

    errors, selected = _finalization_errors(
        decision={"sufficiency": "sufficient", "selected_evidence_ids": ["ev_fact:fact_a"]},
        state=state, evidence=evidence, max_selected_evidence=24,
    )

    assert errors == []
    assert selected == ["ev_fact:fact_a"]


def test_finalization_rejects_absence_as_supported_zero_claim():
    evidence = EvidenceStore()
    evidence.ingest("browser_search", {}, _tool_output(
        {"id": "baseball", "evidence_id": "ev_fact:baseball", "content": "The user collected autographed baseballs."},
    ), 0)
    state = PlannerState(objective="count footballs")
    state.apply_update(
        {
            "upsert_claims": [{
                "claim_id": "none",
                "statement": "No evidence was found about autographed footballs.",
                "evidence_ids": ["ev_fact:baseball"],
                "status": "supported",
            }],
        },
        evidence,
    )

    errors, _ = _finalization_errors(
        decision={"sufficiency": "sufficient", "selected_evidence_ids": ["ev_fact:baseball"]},
        state=state, evidence=evidence, max_selected_evidence=24,
    )

    assert any("absence of retrieved evidence" in error for error in errors)


def test_finalization_search_requirement_moved_to_rules_check():
    """Source's `_finalization_errors` used to reject "at least one
    browser_search is required" itself (`state.saw_search`). That check is
    gone from this function (R16) — it lives in `rules.check(...,
    is_final=True)` now, called by the loop BEFORE `_finalization_errors`
    runs."""
    evidence = EvidenceStore()
    state = PlannerState(objective="test")

    errors, _selected = _finalization_errors(
        decision={"sufficiency": "sufficient", "selected_evidence_ids": []},
        state=state, evidence=evidence, max_selected_evidence=24,
    )
    assert not any("browser_search" in e or "search" in e for e in errors)

    violations = rules_check(DEFAULT_RULES, tools_seen=set(), proposed_calls=[], is_final=True)
    assert any("search" in v.detail for v in violations)


# ---------------------------------------------------------------------------
# run_planner_loop (adapted from test_autonomous_loop_uses_compact_fresh_
# planner_messages_and_separate_reader / test_agent_loop_forces_cli_search_
# before_accepting_final_answer)
# ---------------------------------------------------------------------------

def test_loop_issues_init_search_before_consulting_planner():
    tools = FakeTools([_tool_output({
        "id": "fact_a", "fact_id": "fact_a", "evidence_id": "ev_fact:fact_a",
        "content": "The user goes to the gym at 6pm.",
        "session_id": "session_0", "turn_id": "session_0_turn_0", "role": "user",
    })])
    provider = ScriptedProvider(['{"state_update": {}, "decision": {"action": "tool_calls", "calls": [{"tool": "browser_search", "args": {"query": "q"}}]}}', json.dumps({
        "state_update": {
            **_ORDINARY_CLASSIFICATION,
            "upsert_claims": [{
                "claim_id": "gym_time",
                "statement": "The user goes to the gym at 6pm.",
                "evidence_ids": ["ev_fact:fact_a"],
                "status": "supported",
            }],
            "open_questions": [],
        },
        "decision": {
            "action": "final",
            "reason": "The exact time is supported.",
            "selected_evidence_ids": ["ev_fact:fact_a"],
            "sufficiency": "sufficient",
            "missing_information": "",
        },
    })])

    result = run_planner_loop(
        "When do I go to the gym?", current_date="2023-06-01",
        tools=tools, provider=provider,
        # Parity test: pin the historical configuration (allowed_tools in
        # the user payload, not the system prompt).
        config=PlannerConfig(max_steps=4, prompt_cache_layout=False),
    )

    # Forced init search is the first tool dispatched...
    assert tools.calls[0][0] == "memory.tool.search"
    # ...and the planner IS consulted at step 0 (bug-#9 restoration: source
    # consulted it and applied its state_update; only its calls were
    # overridden). Two consults total: step 0 (overridden) + the final.
    assert len(provider.calls) == 2
    assert provider.calls[0]["system"] == PLANNER_SYSTEM_PROMPT
    assert result.termination == "planner_final"
    assert result.selected_evidence_ids == ["ev_fact:fact_a"]
    assert result.planner_claims == [
        {"statement": "The user goes to the gym at 6pm.", "evidence_ids": ["ev_fact:fact_a"]}
    ]
    assert result.insufficient is False


def test_loop_forces_search_args_via_planner_config():
    tools = FakeTools([_tool_output({"id": "a", "evidence_id": "ev_fact:a", "content": "x"})])
    provider = ScriptedProvider(['{"state_update": {}, "decision": {"action": "tool_calls", "calls": [{"tool": "browser_search", "args": {"query": "q"}}]}}', json.dumps({
        "state_update": _ORDINARY_CLASSIFICATION,
        "decision": {
            "action": "final", "selected_evidence_ids": ["ev_fact:a"],
            "sufficiency": "sufficient", "missing_information": "",
        },
    })])

    run_planner_loop(
        "q", current_date="2023-06-01", tools=tools, provider=provider,
        config=PlannerConfig(max_steps=2, fallback_top_k=7),
    )

    _, kwargs = tools.calls[0]
    assert kwargs["query"] == "q"
    assert kwargs["top_k"] == 7
    assert kwargs["include_context"] is True
    assert kwargs["include_multimodal"] is True


def test_loop_rejects_finalization_before_search_and_keeps_looping():
    """If a caller supplies rules with no InitToolRule (so search is not
    pre-seeded) and the planner tries to finalize immediately, the
    TerminalRule blocks it and the loop asks again -- no silent rewrite."""
    tools = FakeTools([_tool_output({"id": "a", "evidence_id": "ev_fact:a", "content": "x"})])
    provider = ScriptedProvider([
        json.dumps({
            "state_update": _ORDINARY_CLASSIFICATION,
            "decision": {"action": "final", "selected_evidence_ids": [], "sufficiency": "insufficient",
                         "missing_information": "no search yet"},
        }),
        json.dumps({
                "state_update": _ORDINARY_CLASSIFICATION,
            "decision": {"action": "tool_calls", "calls": [{"tool": "browser_search", "args": {"query": "x"}}]},
        }),
        json.dumps({
            "state_update": _ORDINARY_CLASSIFICATION,
            "decision": {"action": "final", "selected_evidence_ids": ["ev_fact:a"],
                         "sufficiency": "sufficient", "missing_information": ""},
        }),
    ])
    no_init_rules = tuple(r for r in DEFAULT_RULES if type(r).__name__ != "InitToolRule")

    result = run_planner_loop(
        "q", current_date="2023-06-01", tools=tools, provider=provider,
        config=PlannerConfig(max_steps=5), rules=no_init_rules,
    )

    assert result.termination == "planner_final"
    assert result.selected_evidence_ids == ["ev_fact:a"]
    assert len(provider.calls) == 3


def test_finalization_requires_classification_and_successful_tool_families():
    evidence = EvidenceStore()
    evidence.ingest(
        "browser_search",
        {},
        _tool_output({"id": "a", "evidence_id": "ev_fact:a", "content": "x"}),
        0,
    )
    decision = {
        "sufficiency": "sufficient",
        "selected_evidence_ids": ["ev_fact:a"],
    }
    state = PlannerState(objective="test")

    errors, _ = _finalization_errors(
        decision=decision, state=state, evidence=evidence,
        max_selected_evidence=24,
    )
    assert any("question_classification" in error for error in errors)

    state.question_classification = {
        "type": "count",
        "comparison_requires_count_or_sum": False,
    }
    errors, _ = _finalization_errors(
        decision=decision, state=state, evidence=evidence,
        max_selected_evidence=24,
    )
    assert any("count_family" in error for error in errors)
    state.successful_tool_capabilities.add("count_family")
    errors, _ = _finalization_errors(
        decision=decision, state=state, evidence=evidence,
        max_selected_evidence=24,
    )
    assert errors == []


@pytest.mark.parametrize(
    "question",
    [
        "How many times did I bake?",
        "When did I renew my passport?",
    ],
)
def test_question_without_classification_is_never_inferred(question):
    tools = FakeTools([
        _tool_output({"id": "a", "evidence_id": "ev_fact:a", "content": "x"}),
    ])
    provider = ScriptedProvider([
        json.dumps({
            "state_update": {},
            "decision": {"action": "tool_calls", "calls": [
                {"tool": "browser_search", "args": {"query": "q"}},
            ]},
        }),
        json.dumps({
            "state_update": {},
            "decision": {
                "action": "final",
                "selected_evidence_ids": ["ev_fact:a"],
                "sufficiency": "sufficient",
                "missing_information": "",
            },
        }),
    ])

    result = run_planner_loop(
        question, current_date="2023-06-01",
        tools=tools, provider=provider, config=PlannerConfig(max_steps=2),
    )

    assert result.termination == "max_steps_reader_fallback"
    assert result.insufficient is True
    assert "question_classification" in result.missing_information
    assert result.state["question_classification"] is None


def test_mixed_comparison_requires_timeline_and_count_families():
    state = PlannerState(objective="test")
    state.question_classification = {
        "type": "comparison",
        "comparison_requires_count_or_sum": True,
    }
    errors, _ = _finalization_errors(
        decision={
            "sufficiency": "insufficient",
            "selected_evidence_ids": [],
            "missing_information": "none",
        },
        state=state,
        evidence=EvidenceStore(),
        max_selected_evidence=24,
    )
    assert any("timeline_family" in error for error in errors)
    assert any("count_family" in error for error in errors)


def test_failed_count_call_does_not_satisfy_gate_and_max_step_is_insufficient():
    tools = FakeTools([
        _tool_output({"id": "a", "evidence_id": "ev_fact:a", "content": "x"}),
    ])
    classification = {
        "question_classification": {
            "type": "count",
            "comparison_requires_count_or_sum": False,
        }
    }
    provider = ScriptedProvider([
        json.dumps({
            "state_update": classification,
            "decision": {"action": "tool_calls", "calls": [
                {"tool": "browser_count_evidence", "args": {"query": "q", "labels": ["x"]}},
            ]},
        }),
        json.dumps({
            "state_update": classification,
            "decision": {"action": "tool_calls", "calls": [
                {"tool": "browser_count_evidence", "args": {"query": "q", "labels": ["x"]}},
            ]},
        }),
        json.dumps({
            "state_update": classification,
            "decision": {
                "action": "final",
                "selected_evidence_ids": ["ev_fact:a"],
                "sufficiency": "sufficient",
                "missing_information": "",
            },
        }),
    ])
    result = run_planner_loop(
        "How many x?", current_date="2023-06-01", tools=tools,
        provider=provider, config=PlannerConfig(max_steps=3),
    )
    assert result.termination == "max_steps_reader_fallback"
    assert result.insufficient is True
    assert "count_family" in result.missing_information
    assert "count_family" not in result.state["successful_tool_capabilities"]


def test_successful_empty_count_call_satisfies_gate():
    tools = FakeTools([
        _tool_output({"id": "a", "evidence_id": "ev_fact:a", "content": "x"}),
        {"groups": []},
    ])
    classification = {
        "question_classification": {
            "type": "count",
            "comparison_requires_count_or_sum": False,
        }
    }
    provider = ScriptedProvider([
        json.dumps({
            "state_update": classification,
            "decision": {"action": "tool_calls", "calls": [
                {"tool": "browser_count_evidence", "args": {"query": "q", "labels": ["x"]}},
            ]},
        }),
        json.dumps({
            "state_update": classification,
            "decision": {"action": "tool_calls", "calls": [
                {"tool": "browser_count_evidence", "args": {"query": "q", "labels": ["x"]}},
            ]},
        }),
        json.dumps({
            "state_update": classification,
            "decision": {
                "action": "final",
                "selected_evidence_ids": ["ev_fact:a"],
                "sufficiency": "sufficient",
                "missing_information": "",
            },
        }),
    ])
    result = run_planner_loop(
        "How many x?", current_date="2023-06-01", tools=tools,
        provider=provider, config=PlannerConfig(max_steps=3),
    )
    assert result.termination == "planner_final"
    assert result.insufficient is False
    assert "count_family" in result.state["successful_tool_capabilities"]


def test_loop_allowed_tools_matches_planner_prompt_vocabulary():
    assert "browser_search" in DEFAULT_ALLOWED_TOOLS
    assert "compute" in DEFAULT_ALLOWED_TOOLS
    assert not any(t.startswith("memory.tool.") for t in DEFAULT_ALLOWED_TOOLS)


# ---------------------------------------------------------------------------
# reader.assemble_reader_context / reader.answer
# ---------------------------------------------------------------------------

def _evidence_with_one_fact(**extra):
    store = EvidenceStore()
    store.ingest("search", {}, _tool_output({
        "id": "fact_a", "fact_id": "fact_a", "evidence_id": "ev_fact:fact_a",
        "support_text": "The sunset painting is the flea market find, worth triple what I paid.",
        "session_id": "session_0", "turn_id": "session_0_turn_0", "role": "user",
        "session_time": "2023-05-01",
        **extra,
    }), 0)
    return store


_NO_ORGANIZER_PLAN = json.dumps({"confidence": 0.0})


def test_reader_answer_carries_planner_claims_into_prompt():
    evidence = _evidence_with_one_fact()
    provider = ScriptedProvider([_NO_ORGANIZER_PLAN, "Triple what you paid."])

    context = assemble_reader_context(
        evidence, ["ev_fact:fact_a"], "How much is the sunset painting worth?",
        current_date="2023-06-01", provider=provider, config=ReaderConfig(),
        planner_claims=[{
            "statement": "The sunset painting is the flea market find.",
            "evidence_ids": ["ev_fact:fact_a"],
        }],
    )
    result = reader_answer(
        "How much is the sunset painting worth?", context,
        current_date="2023-06-01", provider=provider, config=ReaderConfig(),
    )

    assert result.text == "Triple what you paid."
    reader_prompt = provider.calls[-1]["messages"][0]["content"]
    assert "planner_supported_claims" in reader_prompt
    assert "flea market find" in reader_prompt
    assert "advisory pre-resolved context" in reader_prompt


def test_reader_answer_is_advisory_not_authoritative_when_insufficient():
    evidence = _evidence_with_one_fact()
    provider = ScriptedProvider([_NO_ORGANIZER_PLAN, "The source evidence still supports an answer."])

    context = assemble_reader_context(
        evidence, ["ev_fact:fact_a"], "How many reports in the missing role?",
        current_date="2023-06-01", provider=provider, config=ReaderConfig(),
        insufficient=True, missing_information="No memory mentions the requested role.",
    )
    result = reader_answer(
        "How many reports in the missing role?", context,
        current_date="2023-06-01", provider=provider, config=ReaderConfig(),
    )

    assert result.text == "The source evidence still supports an answer."
    reader_prompt = provider.calls[-1]["messages"][0]["content"]
    assert "sufficiency judgment is advisory" in reader_prompt


def test_reader_answer_returns_not_enough_information_with_zero_evidence():
    evidence = EvidenceStore()
    provider = ScriptedProvider([_NO_ORGANIZER_PLAN])

    context = assemble_reader_context(
        evidence, [], "anything?", current_date="2023-06-01", provider=provider,
        config=ReaderConfig(), insufficient=True, missing_information="nothing retrieved",
    )
    result = reader_answer("anything?", context, current_date="2023-06-01", provider=provider, config=ReaderConfig())

    assert "Not enough information" in result.text
    assert "nothing retrieved" in result.text
    # No LLM call for the final answer -- only the (always-on) query-plan call.
    assert len(provider.calls) == 1


def test_reader_retries_terse_when_first_answer_is_truncated():
    evidence = _evidence_with_one_fact(
        support_text="Four festivals: AFI Fest, Austin, Seattle, Portland.",
    )
    provider = _TruncatingThenCompleteProvider([
        ("{}", None),  # reader_query_plan: unparseable-ish but valid JSON -> confidence 0.0, no organizer
        ("Step 1... Portland Film Festival -", "length"),
        ("4", None),
    ])

    context = assemble_reader_context(
        evidence, ["ev_fact:fact_a"], "How many film festivals?",
        current_date="2023-06-01", provider=provider, config=ReaderConfig(),
    )
    result = reader_answer(
        "How many film festivals?", context, current_date="2023-06-01",
        provider=provider, config=ReaderConfig(),
    )

    assert result.text == "4"
    assert len(provider.calls) == 3  # query_plan + first attempt + retry
    assert "cut off before finishing" in provider.calls[-1]["messages"][0]["content"]


def test_reader_currency_off_keeps_both_values():
    evidence = _currency_fixture()
    provider = ScriptedProvider([_NO_ORGANIZER_PLAN])

    context = assemble_reader_context(
        evidence, ["ev_fact:f_old", "ev_fact:f_new"], "What time do I wake up on Saturdays?",
        current_date="2023-06-15", provider=provider,
        config=ReaderConfig(currency=False, full_pool=False, drop_external=False),
    )

    texts = " ".join(r.get("support_text", "") for r in context.key_evidence)
    assert "7:30" in texts and "8:30" in texts


def test_reader_currency_on_current_intent_collapses_to_latest():
    evidence = _currency_fixture()
    provider = ScriptedProvider([_NO_ORGANIZER_PLAN])

    context = assemble_reader_context(
        evidence, ["ev_fact:f_old", "ev_fact:f_new"], "What time do I wake up now?",
        current_date="2023-06-15", provider=provider,
        config=ReaderConfig(currency=True, full_pool=False, drop_external=False),
    )

    texts = " ".join(r.get("support_text", "") for r in context.key_evidence)
    assert "7:30" in texts
    assert "8:30" not in texts


def test_reader_currency_on_history_intent_is_skipped():
    evidence = _currency_fixture()
    provider = ScriptedProvider([_NO_ORGANIZER_PLAN])

    context = assemble_reader_context(
        evidence, ["ev_fact:f_old", "ev_fact:f_new"], "What time did I used to wake up before?",
        current_date="2023-06-15", provider=provider,
        config=ReaderConfig(currency=True, full_pool=False, drop_external=False),
    )

    texts = " ".join(r.get("support_text", "") for r in context.key_evidence)
    assert "7:30" in texts and "8:30" in texts


def _currency_fixture() -> EvidenceStore:
    """Currency fixture: two facts share a dynamic-state slot
    (predicate_canonical + subject-only roles) but differ in value/time."""
    def state(fid, value, session_time):
        return {
            "id": fid, "fact_id": fid, "evidence_id": f"ev_fact:{fid}",
            "kind": "state", "modality": "current_state",
            "predicate_canonical": "wake_time", "event_type": "routine",
            "entity_roles": {"subject": "user"},
            "extracted_support_text": f"I wake up at {value}", "session_time": session_time,
        }

    store = EvidenceStore()
    store.ingest("browser_search", {}, _tool_output(
        state("f_old", "8:30 am", "2023-01-01"),
        state("f_new", "7:30 am", "2023-05-01"),
    ), 0)
    return store


# ---------------------------------------------------------------------------
# answer_question(): the __init__.py composition root (new code, not a port
# — see that module's docstring). No brief-given signature to port against;
# this is an integration smoke test that the four submodules actually wire
# together, since nothing else in this file exercises the seam between
# run_planner_loop()'s AutonomousAgentResult and assemble_reader_context()'s
# expected inputs.
# ---------------------------------------------------------------------------

def test_answer_question_composes_loop_and_reader_end_to_end():
    tools = FakeTools([_tool_output({
        "id": "fact_a", "fact_id": "fact_a", "evidence_id": "ev_fact:fact_a",
        "support_text": "The user goes to the gym at 6pm.",
        "session_id": "session_0", "turn_id": "session_0_turn_0", "role": "user",
        "session_time": "2023-05-01",
    })])
    provider = ScriptedProvider([
        '{"state_update": {}, "decision": {"action": "tool_calls", "calls": [{"tool": "browser_search", "args": {"query": "q"}}]}}',
        json.dumps({
            "state_update": _ORDINARY_CLASSIFICATION,
            "decision": {
                "action": "final", "selected_evidence_ids": ["ev_fact:fact_a"],
                "sufficiency": "sufficient", "missing_information": "",
            },
        }),
        _NO_ORGANIZER_PLAN,
        "The gym is at 6pm.",
    ])

    result = answer_question(
        "When do I go to the gym?", current_date="2023-06-01",
        tools=tools, provider=provider,
        planner_config=PlannerConfig(max_steps=3), reader_config=ReaderConfig(),
    )

    assert result.text == "The gym is at 6pm."
    assert result.citations == ["ev_fact:fact_a"]
    assert tools.calls[0][0] == "memory.tool.search"
