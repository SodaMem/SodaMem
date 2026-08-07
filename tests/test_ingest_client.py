"""Guardian tests for sodamem.memory.ingest.client (IngestClient,
parse_session_time, IngestResult) and the SodaMem.ingest() facade method.
Zero network — EchoProvider/ScriptedProvider feed pre-set extraction JSON.

Covers the headline fix (spec §6.1 "no wall-clock fallback" — the T5 brief's
"头号静默失败"), the extract-window flush/overlap behaviour, and
IngestResult's new usage/trace_ids fields.
"""
from __future__ import annotations

import json

import pytest

from sodamem import SodaMem
from sodamem.errors import ConfigError, IngestError
from sodamem.llm.testing import EchoProvider, ScriptedProvider
from sodamem.memory.ingest.client import IngestClient, IngestResult, parse_session_time
from sodamem.memory.ingest.config import ExtractWindowConfig, IngestConfig
from sodamem.memory.ingest.extractor import FactEventExtractorV2
from sodamem.memory.storage.store import open_store
from sodamem.prompts.extraction import DETERMINISM_RULES, EXTRACT_SYSTEM_PROMPT


class _FakeEmbedder:
    def embed(self, texts):
        return [[0.0] for _ in texts]


_PROMPTS = {"extract": EXTRACT_SYSTEM_PROMPT + DETERMINISM_RULES}


@pytest.fixture
def store(tmp_path):
    s = open_store(tmp_path / "s.sqlite3", prompts=_PROMPTS, embedder=_FakeEmbedder())
    yield s
    s.close()


# ---------------------------------------------------------------------------
# parse_session_time: the headline §6.1 fix — never falls back to wall-clock
# ---------------------------------------------------------------------------

class TestParseSessionTime:
    def test_epoch_passthrough(self):
        assert parse_session_time(1700000000) == 1700000000.0
        assert parse_session_time(1700000000.5) == 1700000000.5

    def test_iso_full_datetime(self):
        assert parse_session_time("2023-06-15T10:30") > 0

    def test_iso_partial_year_and_month(self):
        assert parse_session_time("2019") > 0
        assert parse_session_time("2023-06") > 0

    def test_slash_format_with_weekday_parenthetical(self):
        # The real production anchor format (client.py used to pass this
        # straight through and silently fall back to time.time() on parse
        # failure — this is the exact input shape that triggered it).
        ts = parse_session_time("2023/03/04 (Sat) 16:45")
        import datetime
        assert datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") == "2023-03-04 16:45"

    def test_unparseable_raises_ingest_error_not_wallclock(self):
        with pytest.raises(IngestError):
            parse_session_time("not a date at all")

    def test_empty_string_raises(self):
        with pytest.raises(IngestError):
            parse_session_time("")

    def test_none_raises(self):
        with pytest.raises(IngestError):
            parse_session_time(None)

    def test_bool_raises_not_treated_as_epoch(self):
        with pytest.raises(IngestError):
            parse_session_time(True)


# ---------------------------------------------------------------------------
# IngestClient.ingest_session: end-to-end, zero network
# ---------------------------------------------------------------------------

def _flight_json(span_id: str) -> str:
    return json.dumps([{
        "kind": "fact",
        "predicate_raw": "User flew United to Boston",
        "predicate_canonical": "travel_by_airline",
        "event_type": "flight",
        "modality": "past_event",
        "occurred_start": "2023-06-10",
        "entity_roles": {"subject": "user", "airline": "United Airlines"},
        "source_span_ids": [span_id],
        "support_text": "I flew United to Boston last week.",
    }])


class TestIngestSessionEndToEnd:
    def test_ingest_session_returns_ingest_result_with_usage_and_traces(self, store):
        provider = EchoProvider(json.dumps([{
            "kind": "fact", "predicate_raw": "User flew United to Boston",
            "predicate_canonical": "travel_by_airline", "event_type": "flight",
            "modality": "past_event", "occurred_start": "2023-06-10",
            "entity_roles": {"subject": "user", "airline": "United Airlines"},
            "source_span_ids": ["irrelevant"],  # recovered by quote match
            "support_text": "I flew United to Boston last week.",
        }]))
        extractor = FactEventExtractorV2(provider)
        client = IngestClient(store=store, extractor=extractor)
        result = client.ingest_session(
            [{"role": "user", "content": "I flew United to Boston last week."}],
            user_id="u1", session_id="s1", session_time="2023/06/15 (Thu) 10:00",
        )
        assert isinstance(result, IngestResult)
        assert result.counts["fact_events"] == 1
        assert result.counts["raw_turns"] == 1
        assert result.trace_ids and all(isinstance(t, str) for t in result.trace_ids)
        facts = store.get_all_fact_events("u1")
        assert len(facts) == 1
        assert facts[0].predicate_canonical == "travel_by_airline"

    def test_session_time_epoch_input_still_lets_extractor_resolve_relative_dates(self, store):
        # session_time given as a bare epoch: the client must still hand the
        # extractor a STRING anchor (rendered from the epoch) so
        # calendar_resolve's relative-date resolution keeps working.
        provider = ScriptedProvider([json.dumps([{
            "kind": "state", "predicate_raw": "User moved to Chicago",
            "predicate_canonical": "lives_in", "event_type": "state",
            "modality": "current_state",
            "occurred_date": {"expr": "yesterday", "anchor": "session_date"},
            "entity_roles": {"subject": "user", "location": "Chicago"},
            "source_span_ids": ["irrelevant"],
            "support_text": "I moved to Chicago yesterday.",
        }])])
        extractor = FactEventExtractorV2(provider)
        client = IngestClient(store=store, extractor=extractor)
        import datetime
        epoch = datetime.datetime(2023, 6, 15, 10, 0).timestamp()
        client.ingest_session(
            [{"role": "user", "content": "I moved to Chicago yesterday."}],
            user_id="u1", session_id="s2", session_time=epoch,
        )
        facts = store.get_all_fact_events("u1")
        assert len(facts) == 1
        expected = datetime.datetime(2023, 6, 14).timestamp()
        assert facts[0].occurred_start == pytest.approx(expected)

    def test_unparseable_session_time_raises_before_any_storage_write(self, store):
        extractor = FactEventExtractorV2(EchoProvider("[]"))
        client = IngestClient(store=store, extractor=extractor)
        with pytest.raises(IngestError):
            client.ingest_session(
                [{"role": "user", "content": "hello"}],
                user_id="u1", session_id="s_bad", session_time="garbage",
            )
        assert store.get_all_raw_turns("u1") == []  # nothing written

    def test_empty_content_turns_are_skipped(self, store):
        extractor = FactEventExtractorV2(EchoProvider("[]"))
        client = IngestClient(store=store, extractor=extractor)
        result = client.ingest_session(
            [{"role": "user", "content": "  "}, {"role": "user", "content": ""}],
            user_id="u1", session_id="s3", session_time="2023-06-15",
        )
        assert result.counts["raw_turns"] == 0

    def test_dead_flag_counters_are_gone_from_counts(self, store):
        extractor = FactEventExtractorV2(EchoProvider("[]"))
        client = IngestClient(store=store, extractor=extractor)
        result = client.ingest_session(
            [{"role": "user", "content": "hello there"}],
            user_id="u1", session_id="s4", session_time="2023-06-15",
        )
        for dead_key in (
            "salience_dropped_in_session", "salience_kept_in_session",
            "enrich_calls_in_session", "consolidate_merged_in_session",
        ):
            assert dead_key not in result.counts


class TestExtractWindow:
    """Window flush accounting."""

    def _client(self, store, provider):
        extractor = FactEventExtractorV2(provider)
        return IngestClient(store=store, extractor=extractor)

    def test_low_signal_messages_still_enter_extract_window(self, store):
        provider = EchoProvider("[]")
        client = self._client(store, provider)
        result = client.ingest_session(
            [
                {"role": "user", "content": "thanks!"},
                {"role": "user", "content": "What is the capital of France?"},
            ],
            user_id="u_window", session_id="s_window", session_time="2026-03-10",
        )
        assert result.counts["extract_window_spans_in_session"] == 2
        assert result.counts["extract_windows_in_session"] == 1
        traces = store.get_recent_extraction_traces("u_window", n=10)
        assert any(t.stage == "extract_window" and t.reason == "session_end" for t in traces)

    def test_window_flushes_by_max_turns(self, store):
        provider = ScriptedProvider(["[]", "[]"])
        extractor = FactEventExtractorV2(provider)
        cfg = IngestConfig(window=ExtractWindowConfig(max_turns=2))
        client = IngestClient(store=store, extractor=extractor, config=cfg)
        result = client.ingest_session(
            [
                {"role": "user", "content": "I am going to Oahu next week."},
                {"role": "assistant", "content": "You mean Oahu for the trip?"},
                {"role": "user", "content": "Yes, that place."},
            ],
            user_id="u_flush", session_id="s_flush", session_time="2026-03-10",
        )
        assert result.counts["extract_windows_in_session"] == 2
        assert len(provider.calls) == 2
        traces = store.get_recent_extraction_traces("u_flush", n=20)
        reasons = {t.reason for t in traces if t.stage == "extract_window"}
        assert {"max_turns", "session_end"} <= reasons


# ---------------------------------------------------------------------------
# SodaMem.ingest() facade
# ---------------------------------------------------------------------------

class TestSodaMemFacade:
    def test_ingest_facade_delegates_to_ingest_client(self, store):
        provider = EchoProvider(json.dumps([{
            "kind": "fact", "predicate_raw": "User likes tea",
            "predicate_canonical": "beverage_preference", "modality": "preference",
            "entity_roles": {"subject": "user"},
            "source_span_ids": ["irrelevant"],
            "support_text": "I really like tea.",
        }]))
        extractor = FactEventExtractorV2(provider)
        sm = SodaMem(store=store, extractor=extractor)
        result = sm.ingest(
            [{"role": "user", "content": "I really like tea."}],
            user_id="u1", session_id="s1", session_time="2023-06-15",
        )
        assert isinstance(result, IngestResult)
        assert result.counts["fact_events"] == 1

    def test_ingest_facade_session_time_raises_not_silently_wallclock(self, store):
        extractor = FactEventExtractorV2(EchoProvider("[]"))
        sm = SodaMem(store=store, extractor=extractor)
        with pytest.raises(IngestError):
            sm.ingest(
                [{"role": "user", "content": "hi"}],
                user_id="u1", session_id="s1", session_time="not a date",
            )

    def test_ingest_facade_with_no_extractor_raises_config_error_not_attributeerror(self, store):
        sm = SodaMem(store=store)  # extractor defaults to None
        with pytest.raises(ConfigError):
            sm.ingest(
                [{"role": "user", "content": "hi"}],
                user_id="u1", session_id="s1", session_time="2023-06-15",
            )

    def test_ingest_facade_default_config_when_none_passed(self, store):
        extractor = FactEventExtractorV2(EchoProvider("[]"))
        sm = SodaMem(store=store, extractor=extractor)
        # Must not raise for lack of an explicit config (facade defaults to
        # IngestConfig()).
        result = sm.ingest(
            [{"role": "user", "content": "hi"}],
            user_id="u1", session_id="s1", session_time="2023-06-15",
        )
        assert isinstance(result, IngestResult)


class TestInferFalseRawStorage:
    """R2.6 — raw storage (mem0 parity).

    The HTTP route returned 501 with an honest reason: "the core ingest path
    always runs structured LLM extraction; there is no raw-turn-only storage
    mode". That reason stopped being true. `ingest_session` writes the RawTurn
    and the SourceSpan for every turn BEFORE extraction starts, and fusion.py
    already retrieves raw turns under `raw_recall_enabled` — the storage path
    and the read path both existed. Only the switch between them was missing.

    This is not "extraction but cheaper": nothing is extracted, so there is no
    FactEvent, no dual timeline and no evidence chain. That is precisely what
    a caller passing infer=false is choosing, and precisely why it must never
    be silently upgraded to infer=true.
    """

    def test_stores_the_turn_and_extracts_nothing(self, store):
        provider = EchoProvider(json.dumps([{"kind": "fact",
                                             "predicate_raw": "should never run"}]))
        client = IngestClient(store=store, extractor=FactEventExtractorV2(provider))
        result = client.ingest_session(
            [{"role": "user", "content": "I flew United to Boston last week."}],
            user_id="u_raw", session_id="s_raw",
            session_time="2023/06/15 (Thu) 10:00", infer=False,
        )
        assert result.counts["raw_turns"] == 1
        assert result.counts.get("fact_events", 0) == 0
        assert store.get_all_fact_events("u_raw") == []
        turns = store.get_all_raw_turns("u_raw")
        assert len(turns) == 1 and "United" in turns[0].content

    def test_makes_no_llm_call(self, store):
        """Raw storage is usually chosen to avoid the token bill; charging for
        it anyway would defeat the option. An exploding provider states that
        as a hard assertion rather than a counter nobody reads."""
        class _NeverCall(EchoProvider):
            def complete(self, **kw):
                raise AssertionError("infer=False must not reach the LLM")

            async def acomplete(self, **kw):
                raise AssertionError("infer=False must not reach the LLM")

        client = IngestClient(store=store,
                              extractor=FactEventExtractorV2(_NeverCall("")))
        client.ingest_session(
            [{"role": "user", "content": "I flew United to Boston last week."}],
            user_id="u_raw2", session_id="s_raw2",
            session_time="2023/06/15 (Thu) 10:00", infer=False,
        )

    def test_spans_are_still_written_so_citations_resolve(self, store):
        """Spans are what a citation points at. Skipping them would make every
        raw-recall hit uncitable."""
        provider = EchoProvider(json.dumps([]))
        client = IngestClient(store=store, extractor=FactEventExtractorV2(provider))
        client.ingest_session(
            [{"role": "user", "content": "I flew United to Boston last week."}],
            user_id="u_raw3", session_id="s_raw3",
            session_time="2023/06/15 (Thu) 10:00", infer=False,
        )
        assert store.get_source_spans_by_ids(["span_s_raw3_0"])

    def test_infer_true_is_the_default_and_is_unchanged(self, store):
        provider = EchoProvider(json.dumps([{
            "kind": "fact", "predicate_raw": "User flew United to Boston",
            "predicate_canonical": "travel_by_airline", "event_type": "flight",
            "modality": "past_event", "occurred_start": "2023-06-10",
            "entity_roles": {"subject": "user", "airline": "United Airlines"},
            "source_span_ids": ["irrelevant"],
            "support_text": "I flew United to Boston last week.",
        }]))
        client = IngestClient(store=store, extractor=FactEventExtractorV2(provider))
        client.ingest_session(
            [{"role": "user", "content": "I flew United to Boston last week."}],
            user_id="u_inf", session_id="s_inf",
            session_time="2023/06/15 (Thu) 10:00",
        )
        assert store.get_all_fact_events("u_inf") != []
