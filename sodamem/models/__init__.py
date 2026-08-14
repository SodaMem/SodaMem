"""Core data models for SodaMem.

This is the lowest layer in the package graph: no other `sodamem.*` package
is imported here, so `models` can be safely consumed by `storage`,
`retrieval`, `context`, and `answer` without creating cycles.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import json
import uuid
import time


class EdgeType(str, Enum):
    CONTRADICTS = "CONTRADICTS"
    UPDATES = "UPDATES"
    SUPPORTS = "SUPPORTS"
    CO_OCCURS = "CO_OCCURS"
    CAUSES = "CAUSES"
    EVIDENCES = "EVIDENCES"
    MENTIONS = "MENTIONS"
    MENTIONED_WITH = "MENTIONED_WITH"
    SUBJECT_OF = "SUBJECT_OF"
    OBJECT_OF = "OBJECT_OF"
    OCCURRED_DURING = "OCCURRED_DURING"
    HAS_QUANTITY = "HAS_QUANTITY"
    DERIVED_FROM = "DERIVED_FROM"
    SUPERSEDES = "SUPERSEDES"


class FactKind(str, Enum):
    FACT = "fact"
    EVENT = "event"
    STATE = "state"
    PREFERENCE = "preference"
    PROFILE = "profile"   # cross-session consolidated entity profile


class FactStatus(str, Enum):
    ACTIVE = "active"
    STALE = "stale"
    SUPERSEDED = "superseded"
    CONTRADICTED = "contradicted"
    ARCHIVED = "archived"


class SourceType(str, Enum):
    EXPLICIT_TEXT = "explicit_text"
    USER_CORRECTION = "user_correction"
    EXPLICIT_TOOL = "explicit_tool"
    DERIVED_RUNTIME = "derived_runtime"
    DERIVED_MATERIALIZED = "derived_materialized"
    INFERRED_LLM = "inferred_llm"
    SUMMARY_SYNTHESIS = "summary_synthesis"


class Modality(str, Enum):
    PAST_EVENT = "past_event"
    FUTURE_PLAN = "future_plan"
    CURRENT_STATE = "current_state"
    PREFERENCE = "preference"
    QUESTION = "question"
    INTENT = "intent"
    ASSISTANT_ADVICE = "assistant_advice"
    EXTERNAL_INFO = "external_info"


@dataclass
class RawTurn:
    user_id: str
    role: str
    content: str
    session_id: str = ""
    turn_id: str = field(default_factory=lambda: "turn_" + str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "turn_id": self.turn_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RawTurn":
        return cls(
            turn_id=d["turn_id"],
            user_id=d["user_id"],
            session_id=d.get("session_id", ""),
            role=d.get("role", "user"),
            content=d.get("content", ""),
            timestamp=d.get("timestamp", time.time()),
        )


@dataclass
class SourceSpan:
    user_id: str
    turn_id: str
    session_id: str
    role: str
    text: str
    char_start: int = 0
    char_end: int = 0
    span_id: str = field(default_factory=lambda: "span_" + str(uuid.uuid4()))
    text_hash: str = ""
    span_type: str = "turn_chunk"
    extractor_version: str = "graph_v2_rule_v1"
    alignment_method: str = "source_chunk"
    alignment_confidence: float = 1.0
    created_at: float = field(default_factory=time.time)
    # When the user actually SAID this — session/turn time, derived from the
    # session date at ingest. created_at is the ingestion wall-clock (AUDIT ONLY,
    # never read in flow). None = unknown -> treated as "no time" downstream.
    session_time: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "span_id": self.span_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "role": self.role,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "text": self.text,
            "text_hash": self.text_hash,
            "span_type": self.span_type,
            "extractor_version": self.extractor_version,
            "alignment_method": self.alignment_method,
            "alignment_confidence": self.alignment_confidence,
            "created_at": self.created_at,
            "session_time": self.session_time,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SourceSpan":
        return cls(
            span_id=d["span_id"],
            user_id=d["user_id"],
            session_id=d.get("session_id", ""),
            turn_id=d.get("turn_id", ""),
            role=d.get("role", "user"),
            char_start=d.get("char_start", 0),
            char_end=d.get("char_end", 0),
            text=d.get("text", ""),
            text_hash=d.get("text_hash", ""),
            span_type=d.get("span_type", "turn_chunk"),
            extractor_version=d.get("extractor_version", "graph_v2_rule_v1"),
            alignment_method=d.get("alignment_method", "source_chunk"),
            alignment_confidence=d.get("alignment_confidence", 1.0),
            created_at=d.get("created_at", time.time()),
            session_time=d.get("session_time"),
        )


@dataclass
class ExtractionTrace:
    user_id: str
    stage: str
    action: str
    status: str
    session_id: str = ""
    turn_id: str = ""
    span_id: str = ""
    reason: str = ""
    input_hash: str = ""
    output_fact_ids: list = field(default_factory=list)
    error: str = ""
    metadata: dict = field(default_factory=dict)
    trace_id: str = field(default_factory=lambda: "trace_" + str(uuid.uuid4()))
    created_at: float = field(default_factory=time.time)
    retain_until: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "span_id": self.span_id,
            "stage": self.stage,
            "action": self.action,
            "status": self.status,
            "reason": self.reason,
            "input_hash": self.input_hash,
            "output_fact_ids": self.output_fact_ids,
            "error": self.error,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "retain_until": self.retain_until,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ExtractionTrace":
        return cls(
            trace_id=d["trace_id"],
            user_id=d["user_id"],
            session_id=d.get("session_id", ""),
            turn_id=d.get("turn_id", ""),
            span_id=d.get("span_id", ""),
            stage=d.get("stage", ""),
            action=d.get("action", ""),
            status=d.get("status", ""),
            reason=d.get("reason", ""),
            input_hash=d.get("input_hash", ""),
            output_fact_ids=d.get("output_fact_ids", []),
            error=d.get("error", ""),
            metadata=d.get("metadata", {}),
            created_at=d.get("created_at", time.time()),
            retain_until=d.get("retain_until"),
        )


@dataclass
class EntityMention:
    user_id: str
    entity_id: str
    entity_name: str
    source_span_id: str
    role: str = ""
    fact_id: str = ""
    link_confidence: float = 0.85
    mention_id: str = field(default_factory=lambda: "mention_" + str(uuid.uuid4()))
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "mention_id": self.mention_id,
            "user_id": self.user_id,
            "entity_id": self.entity_id,
            "entity_name": self.entity_name,
            "source_span_id": self.source_span_id,
            "fact_id": self.fact_id,
            "role": self.role,
            "link_confidence": self.link_confidence,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EntityMention":
        return cls(
            mention_id=d["mention_id"],
            user_id=d["user_id"],
            entity_id=d["entity_id"],
            entity_name=d.get("entity_name", ""),
            source_span_id=d.get("source_span_id", ""),
            fact_id=d.get("fact_id", ""),
            role=d.get("role", ""),
            link_confidence=d.get("link_confidence", 0.85),
            created_at=d.get("created_at", time.time()),
        )


@dataclass
class FactEdge:
    user_id: str
    src_fact_id: str
    dst_id: str
    edge_type: EdgeType
    predicate_raw: str = ""
    predicate_canonical: str = ""
    confidence: float = 0.85
    metadata: dict = field(default_factory=dict)
    edge_id: str = field(default_factory=lambda: "fact_edge_" + str(uuid.uuid4()))
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "edge_id": self.edge_id,
            "user_id": self.user_id,
            "src_fact_id": self.src_fact_id,
            "dst_id": self.dst_id,
            "edge_type": self.edge_type.value if isinstance(self.edge_type, EdgeType) else self.edge_type,
            "predicate_raw": self.predicate_raw,
            "predicate_canonical": self.predicate_canonical,
            "confidence": self.confidence,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FactEdge":
        return cls(
            edge_id=d["edge_id"],
            user_id=d["user_id"],
            src_fact_id=d.get("src_fact_id", ""),
            dst_id=d.get("dst_id", ""),
            edge_type=EdgeType(d.get("edge_type", "SUPPORTS")),
            predicate_raw=d.get("predicate_raw", ""),
            predicate_canonical=d.get("predicate_canonical", ""),
            confidence=d.get("confidence", 0.85),
            metadata=d.get("metadata", {}),
            created_at=d.get("created_at", time.time()),
        )


@dataclass
class SummarySynthesis:
    user_id: str
    scope_type: str
    scope_id: str
    summary_text: str
    source_fact_ids: list = field(default_factory=list)
    source_span_ids: list = field(default_factory=list)
    status: str = "active"
    dirty: bool = False
    # Dependency revision vs. build revision: scope_revision increments on every
    # dependency change; built_from_revision records which revision this Summary
    # was generated from. Eligible for routing iff:
    #   status == active AND dirty == False AND built_from_revision == scope_revision
    scope_revision: int = 0
    built_from_revision: int = 0
    dirty_reason: list = field(default_factory=list)
    summary_id: str = field(default_factory=lambda: "summary_" + str(uuid.uuid4()))
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "summary_id": self.summary_id,
            "user_id": self.user_id,
            "scope_type": self.scope_type,
            "scope_id": self.scope_id,
            "summary_text": self.summary_text,
            "source_fact_ids": self.source_fact_ids,
            "source_span_ids": self.source_span_ids,
            "status": self.status,
            "dirty": self.dirty,
            "scope_revision": self.scope_revision,
            "built_from_revision": self.built_from_revision,
            "dirty_reason": self.dirty_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SummarySynthesis":
        return cls(
            summary_id=d["summary_id"],
            user_id=d["user_id"],
            scope_type=d.get("scope_type", ""),
            scope_id=d.get("scope_id", ""),
            summary_text=d.get("summary_text", ""),
            source_fact_ids=d.get("source_fact_ids", []),
            source_span_ids=d.get("source_span_ids", []),
            status=d.get("status", "active"),
            dirty=bool(d.get("dirty", False)),
            scope_revision=int(d.get("scope_revision", 0) or 0),
            built_from_revision=int(d.get("built_from_revision", 0) or 0),
            dirty_reason=d.get("dirty_reason", []) or [],
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
        )

    @property
    def eligible_for_routing(self) -> bool:
        return (
            self.status == "active"
            and not self.dirty
            and self.built_from_revision == self.scope_revision
        )


@dataclass
class FactEvent:
    user_id: str
    kind: FactKind
    source_span_ids: list
    fact_id: str = field(default_factory=lambda: "fact_" + str(uuid.uuid4()))
    status: FactStatus = FactStatus.ACTIVE
    subject_entity_id: str = "entity_user"
    predicate_raw: str = ""
    predicate_canonical: str = ""
    event_type: str = ""
    object_entity_ids: list = field(default_factory=list)
    modality: Modality = Modality.PAST_EVENT
    polarity: str = "positive"
    occurred_start: Optional[float] = None
    occurred_end: Optional[float] = None
    valid_from: Optional[float] = None
    valid_until: Optional[float] = None
    # document_time = when the user STATED the fact (mention/observed axis):
    # deterministic (= latest source-span session_time), 100% available. Distinct
    # from created_at (ingest wallclock). Supersession tie-break + retrieval/reader
    # time axis for facts with no explicit event/valid time.
    document_time: Optional[float] = None
    quantity_value: Optional[float] = None
    quantity_unit: str = ""
    source_type: SourceType = SourceType.EXPLICIT_TEXT
    provenance: dict = field(default_factory=dict)
    confidence: float = 0.75
    confidence_reason: str = "rule_extracted"
    metadata: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id,
            "user_id": self.user_id,
            "kind": self.kind.value if isinstance(self.kind, FactKind) else self.kind,
            "status": self.status.value if isinstance(self.status, FactStatus) else self.status,
            "subject_entity_id": self.subject_entity_id,
            "predicate_raw": self.predicate_raw,
            "predicate_canonical": self.predicate_canonical,
            # Dual-write under the newer, plainer names (audit/external readers;
            # same source as above, never diverges).
            "relation_text": self.predicate_raw,
            "relation_key": self.predicate_canonical,
            "event_type": self.event_type,
            "object_entity_ids": self.object_entity_ids,
            "modality": self.modality.value if isinstance(self.modality, Modality) else self.modality,
            "polarity": self.polarity,
            "occurred_start": self.occurred_start,
            "occurred_end": self.occurred_end,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "document_time": self.document_time,
            "quantity_value": self.quantity_value,
            "quantity_unit": self.quantity_unit,
            "source_type": self.source_type.value if isinstance(self.source_type, SourceType) else self.source_type,
            "source_span_ids": self.source_span_ids,
            "provenance": self.provenance,
            "confidence": self.confidence,
            "confidence_reason": self.confidence_reason,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    # Plainer relation/participant field names. To avoid the migration risk of
    # a physical column rename and the divergence risk of a dual-write, these
    # are implemented as property views over the original fields (single
    # source of truth, never diverges).
    #   relation_text  <-> predicate_raw (raw natural-language relation text)
    #   relation_key   <-> predicate_canonical (stable relation identifier)
    #   participants   <-> metadata['entity_roles'] (participating entities + roles)
    @property
    def relation_text(self) -> str:
        return self.predicate_raw

    @relation_text.setter
    def relation_text(self, value: str) -> None:
        self.predicate_raw = value

    @property
    def relation_key(self) -> str:
        return self.predicate_canonical

    @relation_key.setter
    def relation_key(self, value: str) -> None:
        self.predicate_canonical = value

    @property
    def participants(self) -> dict:
        if not isinstance(self.metadata, dict):
            return {}
        return self.metadata.get("entity_roles", {}) or {}

    @participants.setter
    def participants(self, value: dict) -> None:
        if not isinstance(self.metadata, dict):
            self.metadata = {}
        self.metadata["entity_roles"] = value or {}

    @classmethod
    def from_dict(cls, d: dict) -> "FactEvent":
        return cls(
            fact_id=d["fact_id"],
            user_id=d["user_id"],
            kind=FactKind(d.get("kind", "fact")),
            status=FactStatus(d.get("status", "active")),
            subject_entity_id=d.get("subject_entity_id", "entity_user"),
            # Read the newer name first, fall back to the legacy name
            # (compatible with historical/external writes).
            predicate_raw=d.get("relation_text", d.get("predicate_raw", "")),
            predicate_canonical=d.get("relation_key", d.get("predicate_canonical", "")),
            event_type=d.get("event_type", ""),
            object_entity_ids=d.get("object_entity_ids", []),
            modality=Modality(d.get("modality", "past_event")),
            polarity=d.get("polarity", "positive"),
            occurred_start=d.get("occurred_start"),
            occurred_end=d.get("occurred_end"),
            valid_from=d.get("valid_from"),
            valid_until=d.get("valid_until"),
            document_time=d.get("document_time"),
            quantity_value=d.get("quantity_value"),
            quantity_unit=d.get("quantity_unit", ""),
            source_type=SourceType(d.get("source_type", "explicit_text")),
            source_span_ids=d.get("source_span_ids", []),
            provenance=d.get("provenance", {}),
            confidence=d.get("confidence", 0.75),
            confidence_reason=d.get("confidence_reason", "rule_extracted"),
            metadata=d.get("metadata", {}),
            created_at=d.get("created_at", time.time()),
        )


@dataclass
class AnswerEvidenceBundle:
    """Evidence handed from the memory layer to a reader.

    I4 gate symbol (frozen name — see `tests/gates/test_i4_bundle_fields.py`):
    this bundle carries evidence only, never reading/answering instructions.
    An `answer_task` field was deliberately dropped: it let the memory layer
    dictate to the reader how to answer, which belongs to the answer layer,
    not here.
    """
    query: str
    result: dict = field(default_factory=dict)
    key_evidence: list = field(default_factory=list)
    key_derived: list = field(default_factory=list)
    answer_notes: list = field(default_factory=list)
    answer_constraints: list = field(default_factory=list)
    bundle_id: str = field(default_factory=lambda: "answer_bundle_" + str(uuid.uuid4()))

    def to_dict(self) -> dict:
        return {
            "bundle_id": self.bundle_id,
            "query": self.query,
            "result": self.result,
            "key_evidence": self.key_evidence,
            "key_derived": self.key_derived,
            "answer_notes": self.answer_notes,
            "answer_constraints": self.answer_constraints,
        }


@dataclass
class AuditBundle:
    recall_plan: dict = field(default_factory=dict)
    retrieval_policy: dict = field(default_factory=dict)
    retrieval_routes: list = field(default_factory=list)
    eligible_evidence: list = field(default_factory=list)
    excluded_candidates: list = field(default_factory=list)
    ranking_explanation: list = field(default_factory=list)
    computation_trace: dict = field(default_factory=dict)
    invariants: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)
    fallbacks: list = field(default_factory=list)
    bundle_id: str = field(default_factory=lambda: "audit_bundle_" + str(uuid.uuid4()))

    def to_dict(self) -> dict:
        return {
            "bundle_id": self.bundle_id,
            "recall_plan": self.recall_plan,
            "retrieval_policy": self.retrieval_policy,
            "retrieval_routes": self.retrieval_routes,
            "eligible_evidence": self.eligible_evidence,
            "excluded_candidates": self.excluded_candidates,
            "ranking_explanation": self.ranking_explanation,
            "computation_trace": self.computation_trace,
            "invariants": self.invariants,
            "tool_calls": self.tool_calls,
            "fallbacks": self.fallbacks,
        }


@dataclass
class GraphEntity:
    """L3 canonical entity node for the v2 graph.

    Keyed by the deterministic ``entity_id`` slug. Accumulates the surface forms
    seen across mentions so the entity layer is a real, queryable node rather
    than an id scattered across fact_entity_roles. Cross-form alias merging
    (e.g. ``UA`` -> ``United Airlines``) is proposed by Dreaming via SAME_AS
    edges, not invented here.
    """
    user_id: str
    entity_id: str
    # DEDUP KEY ONLY — normalized identity for merge/lookup. Never use for
    # display or retrieval; surface strings live in ``surface_forms``.
    dedup_key: str
    entity_type: str = "concept"
    surface_forms: list = field(default_factory=list)
    mention_count: int = 0
    summary: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "entity_id": self.entity_id,
            "dedup_key": self.dedup_key,
            "entity_type": self.entity_type,
            "surface_forms": list(self.surface_forms),
            "mention_count": self.mention_count,
            "summary": self.summary,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GraphEntity":
        forms = d.get("surface_forms", [])
        if isinstance(forms, str):
            try:
                forms = json.loads(forms)
            except (json.JSONDecodeError, TypeError):
                forms = []
        return cls(
            user_id=d["user_id"],
            entity_id=d["entity_id"],
            dedup_key=d.get("dedup_key", d.get("canonical_name", "")),
            entity_type=d.get("entity_type", "concept"),
            surface_forms=forms or [],
            mention_count=int(d.get("mention_count", 0) or 0),
            summary=d.get("summary", ""),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
        )


def fact_search_document(fact: FactEvent) -> str:
    """The single serialization shared by BM25 and vector (chroma) indexing.

    Ported from the predecessor implementation (`_fact_document`,
    a `@staticmethod`) and renamed. Pure function, zero I/O: both the storage
    write path (chroma upsert) and the retrieval read path (bm25 index) must
    call this and only this to build the text a fact is indexed under.
    """
    d = fact.to_dict()
    parts = [
        d.get("predicate_raw", ""),
        d.get("predicate_canonical", ""),
        d.get("event_type", ""),
        d.get("modality", ""),
        d.get("quantity_unit", ""),
    ]
    roles = d.get("metadata", {}).get("entity_roles", {}) if isinstance(d.get("metadata"), dict) else {}
    for role, value in roles.items():
        if isinstance(value, list):
            parts.append(role + " " + " ".join(str(v) for v in value))
        else:
            parts.append(role + " " + str(value))
    return " | ".join(p for p in parts if p)
