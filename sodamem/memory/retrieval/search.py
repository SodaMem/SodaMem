"""search(): the I5 gate symbol. Composes the wide-RRF and fusion retrieval
routes (ported from `client.py`'s `retrieve_wide_audit_bundle`/
`retrieve_fusion_audit_bundle`) behind a single typed signature. Zero LLM
calls by construction — this module has no import of `sodamem.llm` anywhere
in its transitive closure (enforced by the import-linter `retrieval-no-llm`
contract, Phase 0 B3). Zero network egress beyond the store's own in-process
embedder.

NOTE on signature: earlier scaffolding (Phase 0 B6's tests/gates/
test_i5_zero_llm_zero_egress.py) called `retrieval.search(query=, user_id=,
provider=)`. That signature predates this task and does not match the
authoritative one below (spec §6.1: `search(query, *, user_id, store,
config=None, as_of=None) -> SearchResult`, no provider param — retrieval
cannot accept an LLM provider without contradicting its own zero-LLM
structural guarantee). This task's Step 4 REWRITES that gate test to match
reality instead of bending this signature to match a stale scaffold.

`Degradation`/`DegradationCode` are defined in `.config`, not here, and
re-exported below — see that module's docstring for why (a circular-import
constraint: `vector.py`, which `fusion.py` depends on, must be able to
construct a `Degradation` at the point a chroma query fails, and `vector.py`
cannot import from this module without creating a cycle). `SearchResult`
stays defined here — it IS this module's own top-level composition result.

T9 handoff (per-call `limit` override): the source's `tool.py` (MemoryTool)
computes a DYNAMIC per-call limit from `top_k`/`offset` (e.g.
`max(offset + top_k + 20, top_k * 4, 80)`) and passed it straight into
`retrieve_wide_audit_bundle(query, limit=...)` /
`retrieve_fusion_audit_bundle(query, limit=...)` — both used to take an
explicit per-call `limit: int = 80` parameter. This
module's `search()` had no equivalent hook (only `config.default_limit`,
fixed at construction time), which silently breaks MemoryTool's offset-based
pagination the moment a caller asks for `offset + top_k` beyond 80. The
optional `limit` parameter below restores that call-time override —
`None` (the default) changes nothing (falls through to
`config.default_limit`, byte-identical to pre-T9 behaviour); a caller-passed
int overrides ONLY `default_limit` for this call via `dataclasses.replace`
(both `_retrieve_wide_audit_bundle`/`_retrieve_fusion_audit_bundle` already
read `config.default_limit` exclusively, so no other function needed to
change).
"""
from __future__ import annotations

import dataclasses
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from sodamem.memory._shared import _canonical_text, _ts_to_iso, _tokenize
from sodamem.memory.storage.store import Store
from sodamem.models import (
    AuditBundle,
    EdgeType,
    FactEvent,
    FactStatus,
    RawTurn,
    SourceSpan,
    SourceType,
)

from . import vector
from .bm25 import get_bm25_index
from .config import Degradation, DegradationCode, RetrievalConfig
from .fusion import MultiPathFusion
from .query_plan import QueryPlan

logger = logging.getLogger(__name__)

__all__ = [
    "search", "SearchResult", "Degradation", "DegradationCode", "RetrievalConfig",
]


@dataclass(frozen=True)
class SearchResult:
    evidence: list[dict]
    degraded: list[Degradation] = field(default_factory=list)
    routes: dict[str, int] = field(default_factory=dict)


#: Every narrowing key, in one place. The read path used to carry one
#: `if <key> and stamp.get(<key>) not in (...)` pair per key, duplicated across
#: the fact filter and the row filter — four branches for two keys, six for
#: three. A tuple plus a dict comprehension is the same rule with no per-key
#: code, so `project_id` (R1.2b, the repo dimension coding agents need) cost
#: one entry here rather than four new branches.
SCOPE_KEYS: tuple[str, ...] = ("agent_id", "run_id", "project_id")


def active_scope(agent_id: str = "", run_id: str = "",
                 project_id: str = "") -> dict[str, str]:
    """The non-empty narrowing keys for one query. Empty dict = no narrowing."""
    values = (agent_id, run_id, project_id)
    return {k: v for k, v in zip(SCOPE_KEYS, values) if v}


def _stamp_matches(stamped: dict | None, wanted: dict[str, str]) -> bool:
    """Scope narrowing (R1.2). The scope keys record PROVENANCE — which
    agent / run / project contributed a fact — not ownership: the store is
    already one physical file per user, so a fact is never another user's.

    A fact with NO stamp for a key therefore matches every query narrowing on
    that key. That is a deliberate divergence from mem0's strict-AND filters:
    under strict AND, every memory written before this feature existed (and
    every one the user stated directly rather than via an agent) becomes
    invisible the moment a caller passes agent_id — indistinguishable, from
    the outside, from the memory having been lost. Narrowing beats
    partitioning here, and it is what makes cross-project recall ("how did I
    fix this in the other repo?") possible at all: drop `project_id` from the
    query and the project-stamped facts are all still there.
    """
    stamp = stamped or {}
    return all(stamp.get(k) in (None, "", v) for k, v in wanted.items())


def _scope_row_matches(row: dict, wanted: dict[str, str],
                       session_scopes: dict[str, dict[str, str]]) -> bool:
    """`_stamp_matches` against a rendered evidence card.

    A row's scope is its OWN stamp when it has one, else its session's. Two
    sources, one rule, in this order for a reason:

      * fact cards carry `scope` in metadata, and stores written before
        `session_scope` existed have only that — reading it first is what
        keeps their agent_id/run_id filtering working unchanged;
      * raw-turn cards have no metadata at all, so without the session
        fallback they passed every scoped query. That was not a rounding
        error: raw turns are the bulk of a coding-agent session, so narrowing
        to one repo still surfaced another repo's conversation text.

    (There used to be a `_scope_matches` twin taking a `FactEvent`. It had no
    callers — retrieval filters rendered rows, never raw facts — so it was two
    branches per scope key that nothing executed. Deleted rather than
    extended.)
    """
    stamp = row.get("scope") or session_scopes.get(row.get("source_session_id") or "")
    return _stamp_matches(stamp, wanted)


def search(query: str, *, user_id: str, store: Store,
           config: RetrievalConfig | None = None,
           as_of: datetime | None = None,
           limit: int | None = None,
           agent_id: str = "", run_id: str = "",
           project_id: str = "") -> SearchResult:
    if as_of is not None:
        # §6.7: a caller-supplied as_of that silently does nothing is worse
        # than an explicit error. The source machinery that would have
        # consumed a point-in-time constraint (query_plan.py §C/§D's
        # TimeConstraint/EvidenceBoard) was deleted by this task's R2
        # verdict (production-default-off, zero-regression to delete) — no
        # replacement point-in-time mechanism has been built yet. Raising
        # here documents the gap instead of hiding it; a future task wiring
        # real as_of semantics replaces this guard, not the silence it
        # prevents.
        raise NotImplementedError(
            "search(as_of=...) is accepted for forward signature "
            "compatibility (spec §6.1) but is not implemented: the "
            "source's point-in-time filtering (query_plan.py §C/§D) was "
            "deleted as dead-by-default code in this port. Pass "
            "as_of=None until a real implementation lands."
        )
    config = config or RetrievalConfig()
    if limit is not None:
        config = dataclasses.replace(config, default_limit=limit)
    degraded: list[Degradation] = []
    if config.search_route == "wide":
        bundle = _retrieve_wide_audit_bundle(query, user_id=user_id, store=store,
                                              config=config, degraded=degraded)
    else:
        bundle = _retrieve_fusion_audit_bundle(query, user_id=user_id, store=store,
                                                config=config, degraded=degraded)
    evidence = bundle.eligible_evidence
    wanted = active_scope(agent_id, run_id, project_id)
    if wanted:
        # Filtered at the single OUTPUT point rather than inside each retrieval
        # engine: `wide` and `fusion` gate candidates in different modules, and
        # duplicating the rule in both is how the two drift apart. Cost: the
        # route limits are applied before this, so a narrowly-scoped query can
        # return fewer rows than `limit` — stated here rather than hidden.
        #
        # One indexed read for the whole result set, and only when the query
        # actually narrows — an unscoped search touches session_scope zero
        # times and costs exactly what it did before this table existed.
        session_scopes = store.get_session_scopes(user_id)
        evidence = [e for e in evidence
                    if _scope_row_matches(e, wanted, session_scopes)]
    return SearchResult(
        evidence=evidence,
        degraded=degraded,
        routes=_route_counts(bundle),
    )


def _route_counts(bundle: AuditBundle) -> dict[str, int]:
    """`AuditBundle` (ported in Task 1) carries no `route_counts` field of its
    own — derive one from whichever trace data the bundle actually has.
    Fusion bundles carry a clean per-channel head count in
    `recall_plan['search_trace']['heads']`; wide bundles instead populate
    `retrieval_routes` (a flat per-candidate list), so fall back to counting
    route-name occurrences there."""
    search_trace = (bundle.recall_plan or {}).get("search_trace") or {}
    heads = search_trace.get("heads")
    if heads:
        return dict(heads)
    counts: dict[str, int] = {}
    for r in bundle.retrieval_routes or []:
        route = r.get("route")
        if route:
            counts[route] = counts.get(route, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Wide RRF-baseline route. Ported from client.py:348-488
# (`retrieve_wide_audit_bundle`).
# ---------------------------------------------------------------------------

def _retrieve_wide_audit_bundle(
    query: str, *, user_id: str, store: Store, config: RetrievalConfig,
    degraded: list[Degradation],
) -> AuditBundle:
    """Wide raw-query recall without semantic planning or eligibility gates.

    This is the canonical retrieval surface for agentic tools. It fuses
    lexical/vector/entity/summary routes and ranks candidates, but it does
    not apply semantic hard filters that could suppress relevant evidence.
    """
    limit = config.default_limit
    route_items: list[tuple[str, str, object, float, int]] = []
    route_floor = config.route_limit_floor
    vector_cap = config.vector_route_cap
    summary_cap = config.summary_route_cap
    vector_decay = config.vector_score_decay
    linked_boost = config.linked_memory_boost
    summary_boost = config.summary_expanded_boost
    mention_boost = config.mention_memory_boost

    def add_route(route: str, object_id: str, obj, score: float, rank: int) -> None:
        route_items.append((route, object_id, obj, float(score or 0.0), rank))

    route_limit = max(limit, route_floor)
    bm25_idx = get_bm25_index(store)

    for rank, (fact, score) in enumerate(bm25_idx.search_fact_events(query, user_id, n=route_limit), 1):
        add_route("memory_unit_bm25", fact.fact_id, fact, score, rank)
    for idx, fact in enumerate(vector.search_fact_events(
            query, store=store, user_id=user_id, n=min(route_limit, vector_cap), degraded=degraded)):
        add_route("memory_unit_vector", fact.fact_id, fact, max(0.0, 1.0 - idx * vector_decay), idx + 1)
    for rank, (span, score) in enumerate(bm25_idx.search_source_spans(query, user_id, n=route_limit), 1):
        add_route("message_unit_bm25", span.span_id, span, score, rank)
        for fact in store.get_facts_for_span(span.span_id):
            add_route("message_unit_linked_memory", fact.fact_id, fact, score + linked_boost, rank)
    for idx, span in enumerate(vector.search_source_spans(
            query, store=store, user_id=user_id, n=min(route_limit, vector_cap), degraded=degraded)):
        add_route("message_unit_vector", span.span_id, span, max(0.0, 1.0 - idx * vector_decay), idx + 1)
        for fact in store.get_facts_for_span(span.span_id):
            add_route("message_unit_vector_memory", fact.fact_id, fact, 0.9 - idx * 0.02, idx + 1)

    for rank, (summary, score) in enumerate(
            bm25_idx.search_summary_syntheses(query, user_id, n=min(summary_cap, route_limit)), 1):
        add_route("summary_route", summary.summary_id, summary, score, rank)
        for fact_id in summary.source_fact_ids[:30]:
            fact = store.get_fact_event(fact_id)
            if fact is not None:
                add_route("summary_expanded_fact", fact.fact_id, fact, score + summary_boost, rank)

    raw_query_terms = _raw_query_terms(query)
    for rank, mention in enumerate(store.get_entity_mentions_by_terms(user_id, raw_query_terms, n=route_limit), 1):
        span = store.get_source_span(mention.source_span_id)
        if span is not None:
            add_route("mention_message_unit", span.span_id, span, mention.link_confidence, rank)
        if mention.fact_id:
            fact = store.get_fact_event(mention.fact_id)
            if fact is not None:
                add_route("mention_memory_unit", fact.fact_id, fact, mention.link_confidence + mention_boost, rank)

    facts_by_id: dict[str, dict] = {}
    spans_by_id: dict[str, dict] = {}
    retrieval_routes = []
    excluded = []
    for global_rank, (route, object_id, obj, score, route_rank) in enumerate(route_items, 1):
        retrieval_routes.append({
            "route": route,
            "object_id": object_id,
            "rank": route_rank,
            "global_rank": global_rank,
            "score": round(float(score or 0), 4),
        })
        if isinstance(obj, FactEvent):
            # derived_currency ON: do not gate on stored status — superseded/
            # contradicted facts pass; currency is derived at the board.
            if not config.derived_currency and obj.status != FactStatus.ACTIVE:
                excluded.append({
                    "evidence_id": "ev_fact:" + obj.fact_id,
                    "fact_id": obj.fact_id,
                    "excluded_reason": f"status={obj.status.value}",
                })
                continue
            entry = facts_by_id.setdefault(obj.fact_id, {"fact": obj, "routes": []})
            entry["routes"].append({"route": route, "rank": route_rank, "score": float(score or 0)})
        elif isinstance(obj, SourceSpan):
            entry = spans_by_id.setdefault(obj.span_id, {"span": obj, "routes": []})
            entry["routes"].append({"route": route, "rank": route_rank, "score": float(score or 0)})
        # SummarySynthesis hits contribute only via their expanded facts
        # (summary_expanded_fact, above) — a summary itself never becomes
        # standalone evidence (Issue #77 §2.4 invariant).

    evidence = []
    for payload in facts_by_id.values():
        _apply_rrf_rank(payload, config)
        item = _evidence_item(payload["fact"], payload["routes"], store=store, user_id=user_id)
        item["rank"].update(payload.get("rank") or {})
        evidence.append(item)
    for payload in spans_by_id.values():
        _apply_rrf_rank(payload, config)
        item = _span_evidence_item(payload["span"])
        item["routes"] = payload["routes"]
        item["rank"].update(payload.get("rank") or {})
        evidence.append(item)

    evidence.sort(key=_rank_key, reverse=True)

    return AuditBundle(
        recall_plan={
            "mode": "wide_raw_query",
            "query": query,
            "raw_query_terms": raw_query_terms,
            "routes": [
                "memory_unit_bm25",
                "memory_unit_vector",
                "message_unit_bm25",
                "message_unit_vector",
                "summary_route",
                "mention_lookup",
            ],
        },
        retrieval_policy={
            "mode": "ranked_candidates_only",
            "semantic_hard_filters": [],
            "validity_filters": ["fact_status_active"],
            "note": "Semantic planning and heuristic gates are not used for agentic retrieval.",
        },
        retrieval_routes=retrieval_routes[:120],
        eligible_evidence=evidence[:limit],
        excluded_candidates=excluded[:30],
        ranking_explanation=[
            "wide_raw_query_recall",
            "RRF-style route fusion for prior ordering",
            "no semantic text expansion",
            "no semantic eligibility gate",
        ],
        tool_calls=[],
        fallbacks=[],
        computation_trace={
            "mode": "wide_retrieval_only",
            "computed_in_core": False,
            "note": "Memory core returns ranked candidate evidence only; filtering and answer generation are outside the core.",
        },
    )


# ---------------------------------------------------------------------------
# Multi-path fusion route. Ported from client.py:502-609
# (`retrieve_fusion_audit_bundle`).
# ---------------------------------------------------------------------------

def _retrieve_fusion_audit_bundle(
    query: str, *, user_id: str, store: Store, config: RetrievalConfig,
    degraded: list[Degradation],
) -> AuditBundle:
    """多路融合检索（graph_v2 0606 PRD）。

    与 _retrieve_wide_audit_bundle（RRF baseline）同构输出，但排序用 Search
    Head 归一 + Noisy-OR 连接密度 + 规则时间加分。
    """
    limit = config.default_limit
    raw_query_terms = _raw_query_terms(query)
    # DR-014 LLM query planning removed; always use the zero-regression default
    # plan (no temporal window, ACTIVE-only). DR-013 temporal_hard_filter still
    # runs deterministically over date prefixes extracted in fusion.
    plan = QueryPlan.default("query_plan_disabled")
    fusion = MultiPathFusion(store, user_id, config)
    ranked_units, excluded, search_trace = fusion.retrieve(
        query, raw_query_terms, plan, degraded=degraded)
    # NOTE: read-side temporal ordering (GRAPH_V2_BOARD) is applied at the READER
    # assembly stage (Task 8+), NOT here. Reordering the agent's search results
    # by time is VERIFIED CATASTROPHIC (47%->5%): a time-sort feeds the newest K
    # instead of the most relevant K and buries relevant-but-old gold out of
    # view. Retrieval stays relevance-ordered.

    evidence = []
    for u in ranked_units[: max(limit, 80)]:
        routes = [
            {"route": hid, "rank": 1, "score": round(float(c), 4)}
            for hid, c in u.head_contributions.items()
        ]
        if u.kind == "fact_event":
            fact = store.get_fact_event(u.object_id)
            if fact is None:
                continue
            item = _evidence_item(fact, routes, store=store, user_id=user_id)
        elif u.kind == "raw_turn":
            # Issue #75 §2.2/§2.3：原文兜底单元，直接来自不可变 raw_turn，不绕回 fact。
            turn = store.get_raw_turn(u.object_id)
            if turn is None:
                continue
            item = _raw_turn_evidence_item(turn, store=store, user_id=user_id)
            item["routes"] = routes
            item["unstructured_source"] = bool(u.unstructured_source)
        else:
            span = store.get_source_span(u.object_id)
            if span is None:
                continue
            item = _span_evidence_item(span)
            item["routes"] = routes
            # DR-014 #5/#6：孤立 span 无关联 FactEvent 状态，可召回但必须标记；
            # 不能单独支撑"当前状态已确认"类需要结构化有效性判断的强结论。
            item["unstructured_source"] = bool(u.unstructured_source)
        item["rank"] = {
            "tier": (item.get("rank") or {}).get("tier", 1),
            "score": round(u.ranking_confidence, 4),
        }
        item["fusion"] = {
            "connection_density": round(u.connection_density, 4),
            "temporal_boost": round(u.temporal_boost, 4),
            "ranking_confidence": round(u.ranking_confidence, 4),
            "head_contributions": {k: round(v, 4) for k, v in u.head_contributions.items()},
            "n_heads": len(u.head_contributions),
            "paths": u.paths[:8],
            "member_fact_ids": u.member_fact_ids,
            "merge_decision": u.merge_decision,
        }
        evidence.append(item)

    # ranked_units 已按 ranking_confidence 降序，evidence 保持该序。
    return AuditBundle(
        recall_plan={
            "mode": "multipath_fusion",
            "query": query,
            "raw_query_terms": raw_query_terms,
            "config": dataclasses.asdict(config.fusion),
            "search_trace": search_trace,
        },
        retrieval_policy={
            "mode": "fuse_before_channel_cutoff",
            "fusion_strategy": "fuse_before_channel_cutoff",
            "validity_filters": ["fact_status_active"],
        },
        retrieval_routes=[],
        eligible_evidence=evidence[:limit],
        excluded_candidates=excluded[:30],
        ranking_explanation=[
            "multipath_recall",
            "search_head_normalization",
            "noisy_or_connection_density",
            "temporal_boost" if config.fusion.temporal_enabled else "temporal_off",
            "multi_hop" if config.fusion.multi_hop_enabled else "one_hop_only",
        ],
        tool_calls=[],
        fallbacks=[],
        computation_trace={
            "mode": "multipath_fusion",
            "computed_in_core": True,
        },
    )


# ---------------------------------------------------------------------------
# Shared evidence-projection helpers. Ported from client.py:611-821
# (`_raw_query_terms`/`_apply_rrf_rank`/`_evidence_item`/`_lineage_fields`/
# `_span_evidence_item`/`_raw_turn_evidence_item`/`_rank_key`) plus
# `_fact_mention_ts` (client.py:490-500).
# ---------------------------------------------------------------------------

def _raw_query_terms(query: str) -> list[str]:
    terms = []
    for w in re.findall(r"[A-Z][A-Za-z0-9'&.-]+(?:\s+[A-Z][A-Za-z0-9'&.-]+)*", query):
        if len(w) > 2:
            terms.append(w)
    terms.extend(t for t in _tokenize(query) if len(t) >= 4)
    seen = set()
    clean = []
    for term in terms:
        key = _canonical_text(term)
        if key and key not in seen:
            seen.add(key)
            clean.append(key)
    return clean[:30]


def _apply_rrf_rank(payload: dict, config: RetrievalConfig) -> None:
    rrf_k = config.rrf_k
    max_cap = config.max_route_score_cap
    max_weight = config.max_route_score_weight
    routes = payload.get("routes") or []
    rrf = sum(1.0 / (rrf_k + max(1, int(r.get("rank") or 1))) for r in routes)
    max_route = max((float(r.get("score") or 0.0) for r in routes), default=0.0)
    payload["routes"] = routes
    payload["rank"] = {
        "rrf": rrf,
        "max_route_score": max_route,
        "score": round(rrf + min(max_route, max_cap) * max_weight, 6),
    }


def _fact_mention_ts(fact: FactEvent, *, store: Store, degraded: list[Degradation]) -> float:
    """A fact's latest assertion time = max session_time over its source spans
    (NEVER created_at); falls back to valid_from/occurred_start.

    §6.7 fix (violation #2): the
    source was a bare `except Exception: spans = []` — a lookup failure was
    silently indistinguishable from "this fact genuinely has no spans."
    Failure now appends a typed Degradation and still returns a usable
    (possibly 0.0) timestamp — the empty/fallback result is fine; it must
    just arrive with a signal attached.

    NOTE: carried over deliberately (client.py
    :490-500) but — same as the source — has zero callers within the ported
    line range; kept as public API, not invoked internally by this module.
    """
    try:
        spans = store.get_source_spans_by_ids(fact.source_span_ids)
    except Exception as e:  # noqa: BLE001 - converted to a typed Degradation, not swallowed
        degraded.append(Degradation(
            DegradationCode.EVIDENCE_TIMESTAMP_UNRESOLVED,
            f"could not resolve source spans for fact {fact.fact_id}: {e}",
            {"fact_id": fact.fact_id},
        ))
        spans = []
    times = [s.session_time for s in spans if s.session_time is not None]
    if times:
        return max(times)
    return fact.valid_from or fact.occurred_start or 0.0


def _evidence_item(fact: FactEvent, routes: list, *, store: Store, user_id: str) -> dict:
    spans = store.get_source_spans_by_ids(fact.source_span_ids)
    source_span = spans[0] if spans else None
    extracted_support_text = fact.metadata.get("support_text") or ""
    raw_source_text = "\n".join(s.text for s in spans if s.text).strip()
    support_text = raw_source_text or extracted_support_text or fact.predicate_raw
    roles = fact.metadata.get("entity_roles", {})
    quantity_value = fact.quantity_value
    quantity_unit = fact.quantity_unit
    return {
        "evidence_id": "ev_fact:" + fact.fact_id,
        "fact_id": fact.fact_id,
        "source_span_ids": fact.source_span_ids,
        "source_trace_ids": fact.source_span_ids,
        "source_role": source_span.role if source_span else "user",
        "source_session_id": source_span.session_id if source_span else None,
        "source_turn_id": source_span.turn_id if source_span else None,
        "session_time": _ts_to_iso(source_span.session_time) if source_span else None,
        # document_time (§D4 axis ③, "when the user said it") = stored column
        # when populated, else derived read-side as max(provenance span session
        # time) — the store predates the document_time column backfill, so
        # without this fallback the field is NULL for facts written before it.
        "document_time": _ts_to_iso(
            fact.document_time
            or max((s.session_time for s in spans if s.session_time), default=None)
        ) if (fact.document_time
              or any(s.session_time for s in spans)) else None,
        "source_type": fact.source_type.value,
        "kind": fact.kind.value,
        "event_type": fact.event_type,
        "modality": fact.modality.value,
        "status": fact.status.value,
        "predicate_raw": fact.predicate_raw,
        "predicate_canonical": fact.predicate_canonical,
        "entity_roles": roles,
        # Scope provenance rides on the card so a caller can SEE which
        # agent/run contributed a fact, not just filter on it.
        "scope": (fact.metadata or {}).get("scope") or {},
        "confidence": fact.confidence,
        "confidence_reason": fact.confidence_reason,
        "provenance": fact.provenance,
        "occurred_start": _ts_to_iso(fact.occurred_start),
        "occurred_end": _ts_to_iso(fact.occurred_end),
        "valid_from": _ts_to_iso(fact.valid_from),
        "valid_to": _ts_to_iso(fact.valid_until),
        "valid_until": _ts_to_iso(fact.valid_until),
        "time_source": (fact.metadata.get("time_source")
                        if isinstance(fact.metadata, dict) else None),
        "quantity": {
            "value": quantity_value,
            "unit": quantity_unit,
        },
        "support_text": support_text,
        "extracted_support_text": extracted_support_text,
        **_lineage_fields(fact, store=store, user_id=user_id),
        "routes": routes,
        "rank": {
            "tier": 1 if fact.source_type == SourceType.EXPLICIT_TEXT else 3,
            "score": max((r.get("score", 0) for r in routes), default=0.0),
        },
    }


def _lineage_fields(fact: FactEvent, *, store: Store, user_id: str) -> dict:
    status = fact.status.value if isinstance(fact.status, FactStatus) else str(fact.status)
    superseded_by = fact.provenance.get("superseded_by_fact_id") if isinstance(fact.provenance, dict) else None
    outgoing = store.get_fact_edges(
        user_id,
        fact_id=fact.fact_id,
        edge_type=EdgeType.SUPERSEDES.value,
    )
    superseded_fact_ids = [edge.dst_id for edge in outgoing if edge.dst_id]
    if superseded_by:
        version_status = "superseded"
        current_head_id = superseded_by
    else:
        version_status = "current" if status == FactStatus.ACTIVE.value else status
        current_head_id = fact.fact_id
    summary = ""
    if superseded_fact_ids:
        summary = f"current version; supersedes {len(superseded_fact_ids)} older fact(s)"
    elif superseded_by:
        summary = f"superseded by {superseded_by}"
    return {
        "version_status": version_status,
        "superseded_by": superseded_by,
        "current_head_id": current_head_id,
        "superseded_fact_ids": superseded_fact_ids,
        "update_chain_summary": summary,
    }


def _span_evidence_item(span: SourceSpan) -> dict:
    return {
        "evidence_id": "ev_span:" + span.span_id,
        "fact_id": None,
        "source_span_ids": [span.span_id],
        "source_trace_ids": [span.span_id],
        "message_unit_id": span.span_id,
        "source_role": span.role,
        "source_session_id": span.session_id,
        "source_turn_id": span.turn_id,
        "session_time": _ts_to_iso(span.session_time),
        "source_type": "explicit_text",
        "kind": "fact",
        "event_type": "other",
        "modality": "assistant_advice" if span.role == "assistant" else "past_event",
        "status": "active",
        "predicate_raw": span.text[:500],
        "predicate_canonical": "message_unit_fallback",
        "entity_roles": {"subject": "user"},
        "confidence": span.alignment_confidence,
        "confidence_reason": "message_unit_fallback_after_backfill",
        "provenance": {"session_ids": [span.session_id], "turn_ids": [span.turn_id]},
        "occurred_start": None,
        "occurred_end": None,
        "valid_from": None,
        "valid_to": None,
        "valid_until": None,
        "quantity": {"value": None, "unit": ""},
        "support_text": span.text[:1800],
        "routes": [{"route": "message_unit_fallback", "rank": 1, "score": 0.2}],
        "rank": {"tier": 2, "score": 0.2},
    }


def _raw_turn_evidence_item(turn: RawTurn, *, store: Store, user_id: str) -> dict:
    """Issue #75 §2.2/§2.3: materialize an immutable raw turn as standalone,
    fact-independent evidence (the user's verbatim words + context).

    Marked unstructured (DR-014 #5/#6): it cannot ALONE support a 'current
    state confirmed' structured conclusion, but it is no longer score-penalized
    — fusion ranks it on similarity, so a strong raw match competes with facts.
    The placeholder rank/routes here are overwritten by the caller with the
    unit's real head contributions and ranking_confidence.
    """
    text = turn.content or ""
    span_ids = [s.span_id for s in store.get_source_spans_by_turn(user_id, turn.turn_id)]
    return {
        "evidence_id": "ev_raw:" + turn.turn_id,
        "fact_id": None,
        "source_span_ids": span_ids,
        "source_trace_ids": span_ids or [turn.turn_id],
        "message_unit_id": (span_ids[0] if span_ids else turn.turn_id),
        "source_role": turn.role,
        "source_session_id": turn.session_id,
        "source_turn_id": turn.turn_id,
        "session_time": _ts_to_iso(turn.timestamp),
        "source_type": "raw_message",
        "kind": "raw_turn",
        "event_type": "other",
        "modality": "assistant_advice" if turn.role == "assistant" else "past_event",
        "status": "active",
        "predicate_raw": text[:500],
        "predicate_canonical": "raw_turn_recall",
        "entity_roles": {"subject": "user"},
        "confidence": 1.0,
        "confidence_reason": "raw_turn_recall",
        "provenance": {"session_ids": [turn.session_id], "turn_ids": [turn.turn_id]},
        "occurred_start": None,
        "occurred_end": None,
        "valid_from": None,
        "valid_to": None,
        "valid_until": None,
        "quantity": {"value": None, "unit": ""},
        "support_text": text[:1800],
        "content": text,
        "routes": [{"route": "raw_turn_recall", "rank": 1, "score": 0.0}],
        "rank": {"tier": 2, "score": 0.0},
    }


def _rank_key(item: dict) -> tuple:
    rank = item.get("rank") or {}
    qty = item.get("quantity") or {}
    return (
        -int(rank.get("tier", 5)),
        1 if qty.get("value") is not None else 0,
        float(rank.get("score") or 0.0),
        len(item.get("support_text", "")),
    )
