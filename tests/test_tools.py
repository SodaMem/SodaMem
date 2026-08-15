"""Guardian tests for sodamem.tools (MemoryTool, ToolError, _TOOL_REGISTRY).

Three sources, kept in separate sections below:

1. The task brief's own Step 2 skeleton, verbatim: `_TOOL_REGISTRY` carries
   no dead `path` field (riding-along fix #3);
   named `as_of`, not `question_date` (riding-along fix #2).
2. The subset of the HTTP/CLI adapter and id-normalization tests that locks
   `MemoryTool`'s OWN response shape — NOT an HTTP adapter shape (most of
   those tests exercise the adapter directly
   and can't even import in this repo). Import paths changed; assertions
   left untouched, per R15's "assertions don't move" rule.
3. New: the T6 handoff (per-call `limit` override on
   `sodamem.memory.retrieval.search.search()`) actually exercised end to end
   through `MemoryTool.search()`'s dynamic per-call limit formula — the
   headline behavior this task restores (see
   tests/test_retrieval_search.py for the lower-level unit test of the
   override mechanism itself).
"""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from sodamem import SodaMem
from sodamem.llm.testing import ScriptedProvider
from sodamem.memory.ingest.client import IngestClient
from sodamem.memory.ingest.extractor import FactEventExtractorV2
from sodamem.memory.retrieval.config import RetrievalConfig
from sodamem.memory.storage.store import open_store
from sodamem.models import SourceSpan
from sodamem.prompts.extraction import DETERMINISM_RULES, EXTRACT_SYSTEM_PROMPT
from sodamem.tools import MemoryTool, ToolError, _TOOL_REGISTRY, list_tools

_PROMPTS = {"extract": EXTRACT_SYSTEM_PROMPT + DETERMINISM_RULES}


class _FakeEmbedder:
    def embed(self, texts):
        return [[0.0] for _ in texts]


# ---------------------------------------------------------------------------
# 1. Task brief Step 2 (literal)
# ---------------------------------------------------------------------------

def test_tool_registry_has_no_dead_path_fields():
    for name, spec in _TOOL_REGISTRY.items():
        assert "path" not in spec, f"{name} still carries a dead http path field"


# ---------------------------------------------------------------------------
# 2. The subset that locks MemoryTool's own shape, not an HTTP adapter's.
# ---------------------------------------------------------------------------

def test_core_tool_registry_remains_graph_v2_canonical():
    names = [tool["name"] for tool in list_tools()["tools"]]
    assert "memory.tool.search" in names
    assert "memory.tool.raw-search" in names
    assert not any(name.startswith("memory.browser.") for name in names)


def test_search_next_tool_is_only_advertised_when_more_results_exist():
    tool = object.__new__(MemoryTool)

    with_more = tool._next_tools_for_search([], has_more=True)
    without_more = tool._next_tools_for_search([], has_more=False)

    assert with_more[0]["name"] == "memory.tool.search-more"
    assert all(item["name"] != "memory.tool.search-more" for item in without_more)


def test_result_expands_source_span_when_search_result_has_no_fact():
    span = SourceSpan(
        span_id="span_session_1_2_0",
        user_id="demo",
        session_id="session_1",
        turn_id="session_1_turn_2",
        role="user",
        text="I like to wake up at 7:30 am on Saturdays.",
    )

    class FakeStorage:
        def get_fact_event(self, result_id):
            assert result_id == span.span_id
            return None

        def get_source_span(self, result_id):
            assert result_id == span.span_id
            return span

    tool = object.__new__(MemoryTool)
    tool._store = FakeStorage()
    tool._user_id = "demo"

    payload = tool.result(span.span_id)

    assert payload["result_kind"] == "source_span"
    assert payload["memory"]["id"] == span.span_id
    assert payload["source_span"]["text"] == span.text
    assert payload["memory"]["actions"] == [
        {"tool": "memory.tool.session", "session_id": "session_1"},
        {
            "tool": "memory.tool.raw-search",
            "query": span.text,
            "session_id": "session_1",
        },
    ]


# ---------------------------------------------------------------------------
# Inspect-id normalization, built on Store/IngestClient/SodaMem.
# ---------------------------------------------------------------------------

_TURNS = [
    {"role": "user", "content": "I drive a 2008 Subaru Outback with 180000 miles on it."},
    {"role": "user", "content": "My cat is named Mochi and she is a tabby."},
]
_TURN_ID = "s1_turn_0"


def _tool_with_raw_turn_only(tmp_path):
    """A store with a RawTurn but ZERO extracted FactEvents (ScriptedProvider
    returns "[]" — matches the source test's `p.call.return_value = "[]"`),
    so MemoryTool.result() must fall through fact -> span -> raw_turn."""
    store = open_store(tmp_path / "s.sqlite3", prompts=_PROMPTS, embedder=_FakeEmbedder())
    extractor = FactEventExtractorV2(ScriptedProvider(["[]"]))
    client = IngestClient(store=store, extractor=extractor)
    client.ingest_session(_TURNS, user_id="u1", session_id="s1", session_time="2023/05/21 (Sun) 10:00")
    assert store.get_raw_turn(_TURN_ID) is not None  # sanity: turn persisted
    memory = SodaMem(store=store)
    return MemoryTool(memory, user_id="u1")


@pytest.mark.parametrize("mid", [
    "s1_turn_0",                     # bare turn_id
    "ev_raw:s1_turn_0",              # ev_raw wrapper
    "ev_turn:s1:s1_turn_0",          # fusion turn evidence (session_id embedded) — the 404 case
    "ev_turn:s1_turn_0",             # runtime turn evidence (no session_id)
])
def test_result_resolves_every_turn_id_form(tmp_path, mid):
    tool = _tool_with_raw_turn_only(tmp_path)
    r = tool.result(mid)
    assert r["result_kind"] == "raw_turn"
    assert r["memory"]["turn_id"] == _TURN_ID


def test_result_still_404s_a_genuinely_absent_id(tmp_path):
    tool = _tool_with_raw_turn_only(tmp_path)
    with pytest.raises(ToolError):
        tool.result("ev_turn:s9:s9_turn_99")


# ---------------------------------------------------------------------------
# 3. New: T6 handoff (search() per-call limit override) exercised end to end
# through MemoryTool.search()'s dynamic per-call limit.
# ---------------------------------------------------------------------------

_MANY_FACTS = [
    {"kind": "fact", "predicate_raw": f"User completed task number {i}", "predicate_canonical": "completed_task",
     "event_type": "task", "modality": "past_event", "occurred_start": "2023-06-10",
     "entity_roles": {"subject": "user", "item": f"task-{i}"},
     "source_span_ids": ["irrelevant"], "support_text": f"I completed task number {i} today."}
    for i in range(1, 13)
]


def _ingest_many_facts(store):
    provider = ScriptedProvider([json.dumps([f]) for f in _MANY_FACTS])
    extractor = FactEventExtractorV2(provider)
    client = IngestClient(store=store, extractor=extractor)
    for i, f in enumerate(_MANY_FACTS):
        client.ingest_session(
            [{"role": "user", "content": f["support_text"]}],
            user_id="u1", session_id=f"s{i}", session_time=f"2023/06/{10 + i} (Sat) 09:00",
        )


def test_search_pagination_reaches_evidence_beyond_tiny_config_default_limit(tmp_path):
    """MemoryTool.search()'s dynamic per-call limit (max(offset+top_k+20,
    top_k*4, 80)) must override a small config.default_limit — otherwise
    offset-based pagination silently truncates candidates below what a
    caller's top_k/offset actually needs (the exact bug the T6 handoff
    flagged: retrieval.search() only read config.default_limit, fixed at
    construction, with no per-call hook)."""
    store = open_store(tmp_path / "pagination.sqlite3", prompts=_PROMPTS, embedder=_FakeEmbedder())
    _ingest_many_facts(store)  # 12 facts
    memory = SodaMem(store=store)
    tool = MemoryTool(memory, user_id="u1", config=RetrievalConfig(default_limit=5, search_route="wide"))

    first_page = tool.search("completed task", top_k=10, offset=0, include_context=False)
    # 12 ingested facts each surface as TWO evidence items on the wide route
    # (a fact-level card via memory_unit_bm25 AND its source-span card via
    # message_unit_bm25/message_unit_linked_memory — legitimate dual
    # surfacing, not a dedup bug), so total_candidates is ~24. The exact
    # count isn't the point: with config.default_limit=5, an unfixed
    # search() would cap total_candidates at 5 — anything clearing the
    # ingested fact count proves the per-call override took effect.
    assert first_page["total_candidates"] >= 12, (
        "MemoryTool.search()'s dynamic limit did not override "
        "config.default_limit=5 — the T6 per-call limit override regressed"
    )
    assert first_page["has_more"] is True
    assert len(first_page["items"]) == 10

    second_page = tool.search(
        "completed task", top_k=10, offset=first_page["next_offset"], include_context=False,
    )
    assert len(second_page["items"]) > 0


# ---------------------------------------------------------------------------

def test_dispatch_drops_unknown_args_like_source_pydantic_boundary(tmp_path):
    """Bug #8 family (0723): source's HTTP boundary used pydantic models that
    silently drop unknown fields; the port's bare **kwargs call rejected them
    (TypeError->ToolError). Two real-world casualties: the forced step-0
    search's include_multimodal (failed on 500/500 S500 questions) and
    planner pagination's search_raw offset. Dispatch must drop-with-log, not
    reject; missing REQUIRED args must still raise."""
    from sodamem import SodaMem
    from sodamem.tools import MemoryTool, ToolError
    (tmp_path / "u1").mkdir(parents=True)
    tool = MemoryTool(SodaMem.open(tmp_path / "u1"), user_id="u1")
    # include_multimodal: the exact forced-search shape from loop.py step 0
    out = tool.dispatch("memory.tool.search", query="anything", top_k=10,
                        include_context=True, include_multimodal=True)
    assert out["query"] == "anything"
    # search_raw offset: anchor traces paginated with it
    out = tool.dispatch("memory.tool.raw-search", query="anything", top_k=5, offset=5)
    assert out["query"] == "anything"
    # required args still enforced
    with pytest.raises(ToolError):
        tool.dispatch("memory.tool.search", top_k=3)  # no query


def test_time_boundaries_parse_epochs_iso_offsets_and_full_upper_day(tmp_path):
    from sodamem import SodaMem
    from sodamem.tools import MemoryTool, _parse_time_boundary
    (tmp_path / "u1").mkdir(parents=True)
    tool = MemoryTool(SodaMem.open(tmp_path / "u1"), user_id="u1")

    assert _parse_time_boundary("1685404800", upper=False) == 1685404800.0
    assert _parse_time_boundary(1685404800, upper=False) == 1685404800.0
    assert _parse_time_boundary("2023-05-30", upper=False) == datetime(
        2023, 5, 30
    ).timestamp()
    assert _parse_time_boundary("2023-05-30", upper=True) == datetime(
        2023, 5, 30, 23, 59, 59, 999999
    ).timestamp()
    assert _parse_time_boundary(
        "2023-05-30T12:30:00Z", upper=False
    ) == datetime.fromisoformat("2023-05-30T12:30:00+00:00").timestamp()
    assert _parse_time_boundary(
        "2023-05-30T20:30:00+08:00", upper=False
    ) == _parse_time_boundary("2023-05-30T12:30:00Z", upper=False)

    out = tool.dispatch("memory.tool.raw-search", query="anything",
                        top_k=5, from_ts="2023-05-30", to_ts="2023-05-30")
    assert out["query"] == "anything"
    out2 = tool.dispatch("memory.tool.evidence-count", query="anything",
                         labels=["x"], from_ts="2023-05-30", to_ts="2023-05-30")
    assert isinstance(out2, dict)


@pytest.mark.parametrize("value", [
    True, False, float("nan"), float("inf"), "-inf", "2023-05-30junk",
    "2023-05", "2023-05-30T12", object(),
])
def test_time_boundaries_reject_invalid_explicit_values(tmp_path, value):
    (tmp_path / "u1").mkdir(parents=True)
    tool = MemoryTool(SodaMem.open(tmp_path / "u1"), user_id="u1")
    with pytest.raises(ToolError) as exc:
        tool.dispatch("memory.tool.raw-search", query="anything", from_ts=value)
    assert exc.value.code == "invalid_request"


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(10**10000, id="huge-int"),
        pytest.param(1e309, id="overflowed-float"),
        pytest.param("1e1000000", id="huge-numeric-string"),
        pytest.param("9" * 10000, id="huge-digit-string"),
    ],
)
def test_time_boundaries_normalize_numeric_overflow_to_tool_error(tmp_path, value):
    (tmp_path / "u1").mkdir(parents=True)
    tool = MemoryTool(SodaMem.open(tmp_path / "u1"), user_id="u1")
    with pytest.raises(ToolError) as exc:
        tool.dispatch(
            "memory.tool.evidence-count",
            query="anything",
            labels=["x"],
            from_ts=value,
        )
    assert exc.value.code == "invalid_request"


def test_time_boundaries_reject_inverted_window(tmp_path):
    (tmp_path / "u1").mkdir(parents=True)
    tool = MemoryTool(SodaMem.open(tmp_path / "u1"), user_id="u1")
    with pytest.raises(ToolError) as exc:
        tool.dispatch(
            "memory.tool.evidence-count",
            query="anything",
            labels=["x"],
            from_ts="2023-05-31",
            to_ts="2023-05-30",
        )
    assert exc.value.code == "invalid_request"


def test_evidence_count_filters_with_unrendered_intraday_epoch_precision():
    exact = datetime.fromisoformat("2023-05-30T12:30:00+00:00").timestamp()
    facts = {
        "exact": SimpleNamespace(occurred_start=exact),
        "fractional": SimpleNamespace(occurred_start=exact + 0.5),
    }

    class FakeStore:
        def get_fact_event(self, fact_id):
            return facts.get(fact_id)

    tool = object.__new__(MemoryTool)
    tool._store = FakeStore()
    tool._user_id = "u1"
    tool.search = lambda *args, **kwargs: {
        "items": [
            {
                "fact_id": fact_id,
                "evidence_id": f"ev_fact:{fact_id}",
                # The planner card is intentionally day-truncated. The filter
                # must use the underlying stored epoch instead.
                "event_date": "2023-05-30",
            }
            for fact_id in facts
        ]
    }

    for boundary in (
        exact,
        str(exact),
        "2023-05-30T12:30:00Z",
        "2023-05-30T20:30:00+08:00",
    ):
        result = tool.dispatch(
            "memory.tool.evidence-count",
            query="event",
            labels=["x"],
            from_ts=boundary,
            to_ts=boundary,
        )
        assert [
            item["fact_id"] for item in result["groups"][0]["items"]
        ] == ["exact"]


def test_evidence_count_date_upper_bound_includes_last_fractional_second():
    last_moment = datetime(2023, 5, 30, 23, 59, 59, 500000).timestamp()
    next_day = datetime(2023, 5, 31).timestamp()
    facts = {
        "last-moment": SimpleNamespace(occurred_start=last_moment),
        "next-day": SimpleNamespace(occurred_start=next_day),
    }

    class FakeStore:
        def get_fact_event(self, fact_id):
            return facts.get(fact_id)

    tool = object.__new__(MemoryTool)
    tool._store = FakeStore()
    tool._user_id = "u1"
    tool.search = lambda *args, **kwargs: {
        "items": [
            {
                "fact_id": fact_id,
                "evidence_id": f"ev_fact:{fact_id}",
                "event_date": "2023-05-30",
            }
            for fact_id in facts
        ]
    }

    result = tool.dispatch(
        "memory.tool.evidence-count",
        query="event",
        labels=["x"],
        to_ts="2023-05-30",
    )
    assert [
        item["fact_id"] for item in result["groups"][0]["items"]
    ] == ["last-moment"]
