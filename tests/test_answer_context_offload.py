from __future__ import annotations

import json

import pytest

from sodamem.answer.context_offload import PlannerContextOffload
from sodamem.answer.loop import (
    PlannerConfig,
    _planner_user_message,
    _translate_ids,
    run_planner_loop,
)
from sodamem.answer.protocol import PlannerState
from sodamem.answer.reader import ReaderConfig, _build_reader_prompt, assemble_reader_context
from sodamem.context.store import EvidenceStore
from sodamem.llm.testing import EchoProvider, ScriptedProvider
from sodamem.prompts.planner import TOOL_GUIDE
from sodamem.tools import ToolError


def _card(evidence_id: str, support: str = "full text") -> dict:
    return {
        "evidence_id": evidence_id,
        "support": support,
        "predicate": "owns_piano",
        "entities": "instrument=piano",
        "date": "2023-04-01",
        "source": "s1/t1",
        "expanded": False,
    }


def test_hot_warm_folded_precedence_and_claim_downgrade():
    lifecycle = PlannerContextOffload()
    cards = [_card("e1"), _card("e2"), _card("e3")]

    first = lifecycle.project(cards, supported_ids={"e1"})
    assert first.cards == cards  # newly visible is Hot even if already cited
    lifecycle.commit(first)

    later = lifecycle.project(
        cards, supported_ids={"e1", "e2"}, unresolved_conflict_ids={"e2"},
        selected_ids={"e3"},
    )
    assert later.cards[0] == {"evidence_id": "e1"}
    assert later.cards[1] == cards[1]  # conflict: Hot overrides Folded
    assert later.cards[2] == cards[2]  # explicit selection stays Hot
    lifecycle.commit(later)

    downgraded = lifecycle.project(cards)
    assert all("support" not in card for card in downgraded.cards)
    assert downgraded.warm_ids == ["e1", "e2", "e3"]


def test_rehydration_waits_for_selection_and_is_consumed_once():
    lifecycle = PlannerContextOffload()
    initial = lifecycle.project([_card("e1")])
    lifecycle.commit(initial)
    lifecycle.queue_rehydration(["e1", "e2"])

    emitted = lifecycle.project([_card("e1")], supported_ids={"e1", "e2"})
    assert emitted.cards == [_card("e1")]
    assert emitted.rehydrated_ids_consumed == ["e1"]
    lifecycle.commit(emitted)
    assert lifecycle.pending_rehydration_ids == {"e2"}

    folded = lifecycle.project([_card("e1")], supported_ids={"e1"})
    assert folded.cards == [{"evidence_id": "e1"}]
    first_e2 = lifecycle.project([_card("e2")], supported_ids={"e2"})
    assert first_e2.cards == [_card("e2")]
    assert first_e2.rehydrated_ids_consumed == ["e2"]


def test_reentry_does_not_reset_seen_and_ids_are_stable():
    lifecycle = PlannerContextOffload()
    first = lifecycle.project([_card("canonical-long-id")])
    lifecycle.commit(first)
    lifecycle.commit(lifecycle.project([]))
    reentry = lifecycle.project([_card("canonical-long-id")])
    assert reentry.warm_ids == ["canonical-long-id"]
    assert reentry.cards[0]["evidence_id"] == "canonical-long-id"


def test_twenty_four_card_eviction_and_reentry_stays_seen():
    store = EvidenceStore()
    for index in range(25):
        store.ingest("browser_search", {}, {"items": [{
            "fact_id": f"f{index}", "evidence_id": f"ev_fact:f{index}",
            "content": f"row {index}",
        }]}, step=0)
    first_cards = store.compact_cards(query="")
    assert len(first_cards) == 24
    lifecycle = PlannerContextOffload()
    lifecycle.commit(lifecycle.project(first_cards))
    first_ids = {card["evidence_id"] for card in first_cards}
    excluded = (set(store.records) - first_ids).pop()

    second_cards = store.compact_cards(preferred_ids=[excluded], query="")
    second = lifecycle.project(second_cards)
    assert excluded in second.hot_ids
    lifecycle.commit(second)
    evicted = (first_ids - {card["evidence_id"] for card in second_cards}).pop()

    reentry_cards = store.compact_cards(preferred_ids=[evicted], query="")
    reentry = lifecycle.project(reentry_cards)
    assert evicted in reentry.warm_ids
    assert evicted not in reentry.hot_ids


class _Tools:
    def dispatch(self, name, **kwargs):
        if name == "memory.tool.result":
            return {"items": [{"fact_id": "f1", "evidence_id": "ev_fact:f1",
                                "content": "expanded full text", "event_date": "2023-04-01"}]}
        return {"items": [{"fact_id": "f1", "evidence_id": "ev_fact:f1",
                            "content": "original text", "event_date": "2023-04-01"}]}


def _packet(tool: str, args: dict) -> str:
    return json.dumps({
        "state_update": {"question_classification": {"type": "ordinary"}},
        "decision": {"action": "tool_calls", "calls": [{"tool": tool, "args": args}]},
    })


def _run(enabled: bool, *, capture: bool = True):
    provider = ScriptedProvider([
        _packet("browser_search", {"query": "q"}),
        _packet("browser_inspect", {"memory_id": "e1"}),
        _packet("browser_search", {"query": "different"}),
    ])
    return run_planner_loop(
        "question", current_date="2023-06-01", tools=_Tools(), provider=provider,
        config=PlannerConfig(max_steps=3, capture_planner_input=capture,
                             context_offload=enabled, short_evidence_ids=True,
                             stall_stop=False),
    )


def test_loop_projects_after_aliasing_boundary_and_traces_without_capture_dependency():
    result = _run(True)
    second = json.loads(result.planner_trace[1]["planner_input"])
    third = json.loads(result.planner_trace[2]["planner_input"])
    # It first entered the selected set after step 0, so this occurrence is Hot.
    assert second["evidence_cards"][0]["support"] == "original text"
    assert second["evidence_cards"][0]["evidence_id"] == "e1"
    assert third["evidence_cards"][0]["support"] == "expanded full text"
    telemetry = result.planner_trace[2]["context_offload"]
    assert telemetry["rehydrated_ids_consumed"] == ["ev_fact:f1"]
    assert telemetry["hot_count"] + telemetry["warm_count"] + telemetry["folded_count"] == 1


def test_default_off_messages_and_store_are_unchanged():
    off = _run(False)
    for row in off.planner_trace:
        cards = json.loads(row["planner_input"])["evidence_cards"]
        assert all("support" in card for card in cards)
        assert row["context_offload"]["enabled"] is False
        assert row["context_offload"]["projected_card_chars"] == row["context_offload"]["full_card_chars"]
    assert off.evidence.records["ev_fact:f1"].raw["content"] == "expanded full text"


def test_telemetry_does_not_force_planner_input_capture():
    result = _run(True, capture=False)
    assert all("planner_input" not in row for row in result.planner_trace)
    assert all(row["context_offload"]["enabled"] for row in result.planner_trace)


def test_reader_context_and_prompt_are_identical_with_fixed_planner_packets():
    off = _run(False)
    on = _run(True)
    config = ReaderConfig()
    contexts = [
        assemble_reader_context(
            result.evidence, result.selected_evidence_ids, "question",
            current_date="2023-06-01", provider=EchoProvider("{}"), config=config,
            insufficient=result.insufficient,
            missing_information=result.missing_information,
            planner_claims=result.planner_claims,
            planner_conflicts=result.planner_conflicts,
        )
        for result in (off, on)
    ]
    assert contexts[0] == contexts[1]
    prompts = [
        _build_reader_prompt(
            question="question", current_date="2023-06-01",
            key_evidence=context.key_evidence,
        )
        for context in contexts
    ]
    assert prompts[0].encode() == prompts[1].encode()


def _cards(payload: dict) -> list[dict]:
    return payload["evidence_cards"] if "evidence_cards" in payload else \
        payload["evidence_state"]["evidence_cards"]


@pytest.mark.parametrize("cache_layout", [False, True])
@pytest.mark.parametrize("short_ids", [False, True])
def test_projection_boundary_covers_both_layouts_and_id_modes(cache_layout, short_ids):
    canonical = "ev_fact:f1"
    model_id = "e1" if short_ids else canonical
    packets = [
        _packet("browser_search", {"query": "q"}),
        json.dumps({
            "state_update": {
                "question_classification": {"type": "ordinary"},
                "upsert_claims": [{"claim_id": "c", "statement": "fact",
                                   "status": "supported", "evidence_ids": [model_id]}],
            },
            "decision": {"action": "tool_calls", "calls": [
                {"tool": "browser_search", "args": {"query": "q2"}}]},
        }),
        _packet("browser_search", {"query": "q3"}),
    ]
    result = run_planner_loop(
        "question", current_date="2023-06-01", tools=_Tools(),
        provider=ScriptedProvider(packets),
        config=PlannerConfig(
            max_steps=3, capture_planner_input=True, context_offload=True,
            prompt_cache_layout=cache_layout, short_evidence_ids=short_ids,
            stall_stop=False,
        ),
    )
    hot = _cards(json.loads(result.planner_trace[1]["planner_input"]))[0]
    folded = _cards(json.loads(result.planner_trace[2]["planner_input"]))[0]
    assert hot["support"] == "original text"
    assert hot["evidence_id"] == model_id
    assert folded == {"evidence_id": model_id}


def _legacy_message(*, state, evidence, step, cache_layout, aliases):
    evidence_state = state.compact(evidence, step, 3)
    if not cache_layout:
        payload = {
            "question": "question", "current_date": "2023-06-01",
            "allowed_tools": {name: TOOL_GUIDE[name] for name in PlannerConfig().allowed_tools},
            "evidence_state": evidence_state,
        }
    else:
        cards = evidence_state.pop("evidence_cards")
        first_seen = {rid: index for index, rid in enumerate(evidence.records)}
        cards = sorted(cards, key=lambda card: first_seen.get(
            str(card.get("evidence_id")), len(first_seen)))
        payload = {
            "protocol": evidence_state.pop("protocol"),
            "question": "question", "current_date": "2023-06-01",
            "evidence_cards": cards,
            "search_history": evidence_state.pop("search_history"),
            "evidence_state": evidence_state,
        }
    if aliases:
        payload = _translate_ids(payload, aliases)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@pytest.mark.parametrize("cache_layout", [False, True])
@pytest.mark.parametrize("short_ids", [False, True])
def test_flag_off_is_byte_identical_to_legacy_message(cache_layout, short_ids):
    evidence = EvidenceStore()
    evidence.ingest("browser_search", {}, {"items": [{
        "fact_id": "f1", "evidence_id": "ev_fact:f1",
        "content": "original text", "event_date": "2023-04-01",
    }]}, step=0)
    state = PlannerState(objective="question")
    aliases = {"ev_fact:f1": "e1"} if short_ids else None
    expected = _legacy_message(
        state=state, evidence=evidence, step=1,
        cache_layout=cache_layout, aliases=aliases,
    )
    actual = _planner_user_message(
        question="question", current_date="2023-06-01", state=state,
        evidence=evidence, step=1, max_steps=3,
        allowed_tools=PlannerConfig().allowed_tools,
        cache_layout=cache_layout, id_aliases=aliases,
    )
    assert actual.encode() == expected.encode()


class _InspectSessionTools(_Tools):
    def dispatch(self, name, **kwargs):
        if name == "memory.tool.search":
            return {"items": [
                {"fact_id": "f1", "evidence_id": "ev_fact:f1", "content": "one"},
                {"fact_id": "f2", "evidence_id": "ev_fact:f2", "content": "two"},
            ]}
        if name == "memory.tool.session":
            return {"items": [
                {"fact_id": "f1", "evidence_id": "ev_fact:f1", "content": "one expanded"},
                {"fact_id": "f2", "evidence_id": "ev_fact:f2", "content": "two expanded"},
            ]}
        return super().dispatch(name, **kwargs)


def test_inspect_session_rehydrates_every_returned_id_once():
    provider = ScriptedProvider([
        _packet("browser_search", {"query": "q"}),
        _packet("browser_inspect_session", {"session_id": "s1"}),
        _packet("browser_search", {"query": "q2"}),
        _packet("browser_search", {"query": "q3"}),
    ])
    result = run_planner_loop(
        "question", current_date="2023-06-01", tools=_InspectSessionTools(),
        provider=provider,
        config=PlannerConfig(max_steps=4, capture_planner_input=True,
                             context_offload=True, short_evidence_ids=False,
                             stall_stop=False),
    )
    telemetry = result.planner_trace[2]["context_offload"]
    assert telemetry["rehydrated_ids_consumed"] == ["ev_fact:f1", "ev_fact:f2"]
    assert all("support" in card for card in _cards(
        json.loads(result.planner_trace[2]["planner_input"])))
    assert result.planner_trace[3]["context_offload"]["warm_count"] == 2


class _FailedInspectTools(_Tools):
    def __init__(self, mode):
        self.mode = mode

    def dispatch(self, name, **kwargs):
        if name == "memory.tool.result":
            if self.mode == "error":
                raise ToolError("inspect_failed", "no result")
            return {}
        return super().dispatch(name, **kwargs)


@pytest.mark.parametrize("mode", ["error", "empty"])
def test_inspect_error_or_empty_result_does_not_rehydrate(mode):
    result = run_planner_loop(
        "question", current_date="2023-06-01", tools=_FailedInspectTools(mode),
        provider=ScriptedProvider([
            _packet("browser_search", {"query": "q"}),
            _packet("browser_inspect", {"memory_id": "e1"}),
            _packet("browser_search", {"query": "q2"}),
        ]),
        config=PlannerConfig(max_steps=3, capture_planner_input=True,
                             context_offload=True, short_evidence_ids=True,
                             stall_stop=False),
    )
    telemetry = result.planner_trace[2]["context_offload"]
    assert telemetry["rehydrated_ids_consumed"] == []
    assert telemetry["warm_count"] == 1
    assert "support" not in _cards(json.loads(
        result.planner_trace[2]["planner_input"]))[0]
