"""IngestClient: the ingest facade for `sodamem.memory.ingest`.

Ported from the ingest half of the predecessor implementation
(`GraphMemoryClientV2.__init__`/`close`/`llm_usage_summary`/`ingest_session`/
`flush_window`). The
retrieval half of that source file (lines 304-1082: `dream`/
`retrieve_wide_audit_bundle`/`retrieve_fusion_audit_bundle`/evidence-item
projection) is Task 6's, not this task's — `GraphMemoryClientV2` is not
ported as one class. Its ingest responsibilities land here as `IngestClient`,
constructed from an ALREADY-OPEN `Store` and an already-built extractor
rather than owning storage/LLM-provider construction itself:
`SodaMem.ingest()` (this package's `__init__.py` addition) is the
composition root now, not this class's `__init__`.

Headline fix (spec §6.1, "never fall back to wall-clock time"): source line
108 was `ts = _parse_benchmark_date(session_date) or time.time()` — an
unparseable/empty session_date silently became "now", corrupting every
RawTurn.timestamp / SourceSpan.session_time for that entire session with
zero signal to the caller. `parse_session_time()` below replaces it: it
RAISES `IngestError` on anything unparseable and never guesses a wall-clock
time. It merges the source's two separate, individually-incomplete date
parsers per the design's instruction — `_iso_to_ts` (`sodamem.memory._shared`,
ported by this same task; handles full/partial ISO8601 but chokes on a
"(Weekday)" parenthetical) supplies the regex cascade;
`_parse_benchmark_date`'s ONE capability `_iso_to_ts` lacked — tolerating the
real production "YYYY/MM/DD (Weekday) HH:MM" anchor format — is folded in as
a pre-parse strip (mirroring `calendar_resolve.py`'s own `_WEEKDAY_PAREN`
fix for the identical input shape on the relative-date-resolution side).
`_parse_benchmark_date` itself is not ported — nothing else called it.

Fixes this module carries over its predecessor:
  - `INGEST_SALIENCE_GATE`/`INGEST_CONSOLIDATE` gate calls (source :238,:242)
    are deleted, not ported — both are R2.9 dead flags (see `extractor.py`'s
    module docstring); `FactEventExtractorV2` no longer has
    `salience_filter`/`consolidate_filter` methods to call, and their
    per-session counters (`salience_dropped_in_session` etc.) are gone from
    the returned counts along with them.
  - The four `_cfg.env_int()` extract-window reads (source :76-89) are now
    `IngestConfig.window` fields, read once at `IngestClient` construction
    instead of at every legacy-client construction from a global env lookup.
  - `close()` is not ported: the source's `GraphMemoryClientV2` owned (and
    therefore closed) its own `StorageBackend`; `IngestClient` receives an
    already-open `Store` it does not own, so store lifecycle belongs to
    whoever called `open_store()`, not to this class.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional, Union

from sodamem.errors import ConfigError, ErrorCode, IngestError
from sodamem.memory._shared import _canonical_entity_id, _iso_to_ts, _tokenize, _ts_to_iso
from sodamem.memory.storage.store import Store
from sodamem.models import ExtractionTrace, FactEvent, RawTurn, SourceSpan

from .config import IngestConfig
from .extractor import FactEventExtractorV2
from .maintainer import GraphMaintainer, SummarySynthesizer

logger = logging.getLogger(__name__)


# Session-time anchors carry a weekday parenthetical some callers write for
# readability, e.g. "2023/03/04 (Sat) 16:45" — the real production ingest
# format. Mirrors `calendar_resolve.py`'s own `_WEEKDAY_PAREN` (kept as two
# tiny, independent single-line regexes rather than one shared import: the
# symbol is private to each module and the duplication is one line, not a
# maintenance burden — importing a leading-underscore name across ingest
# submodules for a one-line regex would be the worse trade).
_WEEKDAY_PAREN = re.compile(r"\s*\([A-Za-z]{3,9}\)\s*")


def parse_session_time(value: Union[str, int, float]) -> float:
    """Parse a caller-supplied session time into an epoch float.

    Accepts a bare epoch number, ISO 8601 (full or partial: year / month /
    day / minute / second precision), or the "YYYY/MM/DD (Weekday) HH:MM"
    slash form. RAISES `IngestError` on anything else — this function never
    falls back to wall-clock time (spec §6.1). See this module's docstring
    for why this exists and what it replaces (source client.py:108).
    """
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        raise IngestError(
            f"session_time must be a date string or epoch number, got bool {value!r}",
            code=ErrorCode.INGEST_FAILED,
            details={"value": value},
        )
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        raise IngestError(
            "session_time is empty — cannot ingest without a session time "
            "(no wall-clock fallback; spec §6.1)",
            code=ErrorCode.INGEST_FAILED,
            details={"value": value},
        )
    cleaned = _WEEKDAY_PAREN.sub(" ", text).strip()
    ts = _iso_to_ts(cleaned)
    if ts is None:
        raise IngestError(
            f"session_time {value!r} is not a recognized ISO8601/epoch/slash "
            "value — cannot ingest without a session time (no wall-clock "
            "fallback; spec §6.1)",
            code=ErrorCode.INGEST_FAILED,
            details={"value": value},
        )
    return ts


@dataclass
class IngestResult:
    """Result of one `IngestClient.ingest_session()` call.

    New shape — the source returned a bare `dict` of counts. `usage`
    (cumulative LLM token accounting, `sodamem.llm`'s `usage_summary()`
    shape) and `trace_ids` (every `ExtractionTrace.trace_id` written during
    this call, so a caller can pull the full audit trail for exactly this
    ingest without a separate query) are new fields the design asked for
    explicitly; `counts` carries everything the source dict did (minus the
    salience/consolidate/enrich counters, which no longer exist — see this
    module's docstring)."""
    counts: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
    trace_ids: list = field(default_factory=list)


class IngestClient:
    """Ingest facade: turns raw conversation turns into RawTurn/SourceSpan/
    FactEvent rows in `store`, via `extractor` (structured LLM extraction)
    and an internally-owned `GraphMaintainer`/`SummarySynthesizer` pair
    (dedup/supersession/typed-edges/session-summary maintenance)."""

    def __init__(
        self,
        *,
        store: Store,
        extractor: FactEventExtractorV2,
        config: Optional[IngestConfig] = None,
    ) -> None:
        if extractor is None:
            # SodaMem.extractor defaults to None (it's an `Any | None` facade
            # field, not required by every subsystem) — a caller who
            # constructs `SodaMem(store=store)` with no extractor and then
            # calls `.ingest()` would otherwise hit a bare
            # `AttributeError: 'NoneType' object has no attribute '_config'`
            # two lines below. Fail with a clear, typed error at the actual
            # point of the mistake instead (spec §6.7).
            raise ConfigError(
                "IngestClient requires an extractor (FactEventExtractorV2) — "
                "got None. Construct SodaMem(..., extractor=...) before "
                "calling .ingest().",
                code=ErrorCode.CONFIG_INVALID,
            )
        self._store = store
        self._config = config or IngestConfig()
        self._extractor = extractor
        # `extractor` may be constructed once and reused across many
        # `SodaMem.ingest()` calls (the facade keeps it on `self.extractor`
        # rather than rebuilding the LLM provider every call). Rebinding its
        # config here — instead of threading `config` as an explicit
        # parameter through every extractor method — means confidence
        # weights / LLM temperature+max_tokens / `extract.user_only` always
        # reflect the config passed to THIS `.ingest()` call, not whatever
        # config the extractor happened to be constructed with.
        self._extractor._config = self._config
        self._maintainer = GraphMaintainer(self._store, self._config)
        self._summaries = SummarySynthesizer(self._store, self._config)

    def llm_usage_summary(self) -> dict:
        return self._extractor.usage_summary()

    def ingest_session(
        self,
        turns: list[dict],
        *,
        user_id: str,
        session_id: str,
        session_time: Union[str, int, float],
        agent_id: str = "",
        run_id: str = "",
        project_id: str = "",
        infer: bool = True,
    ) -> IngestResult:
        ts = parse_session_time(session_time)
        # The extractor's calendar-resolution path (calendar_resolve.py) needs
        # a STRING anchor, not an epoch — pass the caller's own string through
        # unchanged (preserving whatever "(Weekday)" formatting it had, which
        # calendar_resolve._to_date already strips internally) when one was
        # given; render one from `ts` only when the caller passed a bare epoch.
        session_date_str = (
            session_time if isinstance(session_time, str)
            else (_ts_to_iso(ts, precision="minute") or "")
        )
        ingest_now = time.time()  # ingestion wall-clock = created_at (≠ session_time=ts)
        spans: list[SourceSpan] = []
        # (turn_text, turn-level source unit, [persisted evidence pieces])
        turn_units: list[tuple[str, SourceSpan, list[SourceSpan]]] = []
        for idx, turn in enumerate(turns):
            role = turn.get("role", "user")
            content = turn.get("content", "")
            if not content or not content.strip():
                continue
            turn_id = f"{session_id}_turn_{idx}"
            turn_ts = ts + idx * 0.001
            self._store.upsert_raw_turn(RawTurn(
                user_id=user_id,
                session_id=session_id,
                turn_id=turn_id,
                role=role,
                content=content,
                timestamp=turn_ts,
            ))
            # One SourceSpan per whole turn (claim-level sub-span splitting was
            # removed upstream — it only changed span granularity, never fact
            # extraction, and flooded the candidate pool when on).
            turn_span = SourceSpan(
                user_id=user_id,
                session_id=session_id,
                turn_id=turn_id,
                role=role,
                text=content,
                char_start=0,
                char_end=len(content),
                # Deterministic span_id (no uuid): re-ingesting the same session
                # upserts the same span rather than spawning a duplicate
                # (idempotency). session_id+idx uniquely identify a turn.
                span_id=f"span_{session_id}_{idx}",
                span_type="turn_chunk",
                created_at=ingest_now + idx * 1e-6,
                session_time=turn_ts,
            )
            self._store.upsert_source_span(turn_span)
            turn_spans = [turn_span]
            spans.append(turn_span)
            # Transient message-level unit used by the extract window. It keeps
            # window accounting at message granularity even when evidence pieces
            # are enabled for precise source tracing.
            message_unit = SourceSpan(
                user_id=user_id, session_id=session_id, turn_id=turn_id,
                role=role, text=content, char_start=0, char_end=len(content),
                span_id=f"message_{turn_id}", created_at=ingest_now + idx * 1e-6,
                session_time=turn_ts,
            )
            turn_units.append((content, message_unit, turn_spans))

        if not infer:
            # Raw storage (R2.6, mem0 parity). Every RawTurn and SourceSpan is
            # already written by the loop above — extraction is what comes
            # next, and this is where we decline it.
            #
            # NOT a cheaper extraction: no FactEvent, so no dual timeline and
            # no evidence chain. That is what the caller asked for, and it is
            # why this must never be silently substituted for infer=True. The
            # turns remain retrievable through the raw-recall path
            # (`RetrievalConfig.raw_recall_enabled`, see retrieval/fusion.py),
            # and the spans are still written so a raw hit is citable.
            return IngestResult(
                counts={
                    "raw_turns": len(turn_units),
                    "extract_window_spans_in_session": len(spans),
                    "fact_events": 0,
                    "extracted_facts_in_session": 0,
                    "inferred": False,
                },
                usage={},
                trace_ids=[],
            )

        extract_spans: list[SourceSpan] = []
        facts: list[FactEvent] = []
        extract_windows = 0
        window: list[tuple[str, SourceSpan, list[SourceSpan]]] = []
        window_tokens = 0
        # Reset per-session extractor split diagnostics (read into counts below).
        self._extractor.split_events = 0
        self._extractor.deepest_split = 0
        self._extractor.split_exhausted = 0
        self._extractor.high_budget_recovered = 0

        trace_ids: list[str] = []

        def _record_trace(trace: ExtractionTrace) -> None:
            self._store.append_extraction_trace(trace)
            trace_ids.append(trace.trace_id)

        def flush_window(reason: str) -> None:
            nonlocal extract_windows, window, window_tokens
            if not window:
                return
            batch_spans = [span for _content, _message_unit, turn_spans in window for span in turn_spans]
            if not batch_spans:
                window = []
                window_tokens = 0
                return
            batch_text = "\n".join(content for content, _message_unit, _turn_spans in window)
            first_unit = window[0][1]
            extract_windows += 1
            _record_trace(ExtractionTrace(
                user_id=user_id,
                session_id=first_unit.session_id,
                turn_id=first_unit.turn_id,
                span_id=batch_spans[0].span_id,
                stage="extract_window",
                action="flush",
                status="ok",
                reason=reason,
                input_hash=self._store._text_hash(batch_text),
                metadata={
                    "window_index": extract_windows,
                    "message_unit_count": len(window),
                    "evidence_piece_count": len(batch_spans),
                    "token_count": window_tokens,
                    "max_tokens": self._config.window.max_tokens,
                    "max_turns": self._config.window.max_turns,
                },
            ))
            extract_spans.extend(batch_spans)
            facts.extend(self._extractor.extract_session(
                batch_spans,
                user_id=user_id,
                session_date=session_date_str,
            ))
            if self._config.window.overlap > 0:
                window = window[-self._config.window.overlap:]
                window_tokens = sum(max(1, len(_tokenize(c))) for c, _u, _s in window)
            else:
                window = []
                window_tokens = 0

        for content, message_unit, turn_spans in turn_units:
            token_count = max(1, len(_tokenize(content)))
            over_tokens = bool(window) and window_tokens + token_count > self._config.window.max_tokens
            over_chars = bool(
                self._config.window.max_chars and window
                and sum(len(c) for c, _u, _s in window) + len(content) > self._config.window.max_chars
            )
            if over_tokens or over_chars:
                flush_window("max_tokens" if over_tokens else "max_chars")
            window.append((content, message_unit, turn_spans))
            window_tokens += token_count
            if len(window) >= self._config.window.max_turns:
                flush_window("max_turns")
        flush_window("session_end")

        # (Dead-flag deletion: source ran a Stage-1 SALIENCE GATE and a
        # CONSOLIDATE pass over `facts` here, gated by INGEST_SALIENCE_GATE /
        # INGEST_CONSOLIDATE. Both flags — and the extractor methods they
        # called — are deleted; see extractor.py's module docstring.)

        facts_by_span: dict[str, list[str]] = {}
        touched_entities: set[str] = set()
        # Scope provenance (R1.2). Stamped BEFORE maintain() so a superseding
        # fact carries the scope of the run that produced it. Empty values are
        # not written at all — an unscoped ingest produces metadata byte-
        # identical to before this feature existed.
        # Built from the single SCOPE_KEYS list the read path filters on, so a
        # key can never be stamped-but-unfilterable (or the reverse).
        from sodamem.memory.retrieval.search import active_scope
        scope = active_scope(agent_id, run_id, project_id)
        # Recorded once for the SESSION as well as on each fact. The fact
        # stamp is what pre-existing stores carry and is still authoritative
        # for a fact; the session row is what makes RAW TURNS scopeable, since
        # they have no metadata of their own — and raw turns are most of what
        # a coding-agent session writes. See schema.py's session_scope.
        self._store.set_session_scope(user_id, session_id, scope)
        if scope:
            for fact in facts:
                if isinstance(fact.metadata, dict):
                    fact.metadata = {**fact.metadata, "scope": scope}
        for fact in facts:
            maintained, actions = self._maintainer.maintain(fact)
            roles = maintained.metadata.get("entity_roles", {}) if isinstance(maintained.metadata, dict) else {}
            for role, value in roles.items():
                values = value if isinstance(value, list) else [value]
                for item in values:
                    if item not in (None, ""):
                        touched_entities.add(_canonical_entity_id(str(item)))
            for span_id in maintained.source_span_ids:
                facts_by_span.setdefault(span_id, []).append(maintained.fact_id)
            for action in actions:
                _record_trace(ExtractionTrace(
                    user_id=user_id,
                    session_id=session_id,
                    span_id=(maintained.source_span_ids or [""])[0],
                    stage="graph_maintainer",
                    action=action.get("action", "maintain"),
                    status="ok",
                    reason=json.dumps(action, ensure_ascii=False),
                    output_fact_ids=[maintained.fact_id],
                    metadata=action,
                ))
        for span in extract_spans:
            _record_trace(ExtractionTrace(
                user_id=user_id,
                session_id=span.session_id,
                turn_id=span.turn_id,
                span_id=span.span_id,
                stage="fact_event_extractor",
                action="extract",
                status="ok" if facts_by_span.get(span.span_id) else "no_fact",
                reason="session structured extraction",
                input_hash=self._store._text_hash(span.text),
                output_fact_ids=facts_by_span.get(span.span_id, []),
                metadata={"extractor": "FactEventExtractorV2"},
            ))
        self._summaries.refresh_session(user_id, session_id)
        # D35 incremental dirty-bit: mark every entity touched this session as
        # stale (O(touched), <1ms each). The expensive profile rebuild is the
        # external Dreaming pass's job (Task 7), not ingest's.
        for entity_id in touched_entities:
            self._store.mark_entity_stale(user_id, entity_id, session_id)
        counts = self._store.get_graph_v2_counts(user_id)
        counts["extracted_facts_in_session"] = len(facts)
        counts["extract_now_spans_in_session"] = len(extract_spans)
        counts["extract_window_spans_in_session"] = len(extract_spans)
        counts["extract_windows_in_session"] = extract_windows
        counts["extract_split_events_in_session"] = self._extractor.split_events
        counts["extract_deepest_split_in_session"] = self._extractor.deepest_split
        counts["extract_split_exhausted_in_session"] = self._extractor.split_exhausted
        counts["extract_high_budget_recovered_in_session"] = self._extractor.high_budget_recovered
        counts["stale_entities"] = self._store.count_stale_entities(user_id)
        return IngestResult(
            counts=counts,
            usage=self._extractor.usage_summary(),
            trace_ids=trace_ids,
        )
