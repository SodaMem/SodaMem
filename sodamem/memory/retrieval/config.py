"""Typed retrieval configuration. No field is read from a global env at call
time — the composition root parses env/toml once (spec §6.1 point 4).

Also carries `Degradation`/`DegradationCode` — NOT configuration in the
narrow sense, but the typed, zero-dependency envelope `vector.py`, `fusion.py`
and `search.py` all need to construct. `search.py`'s own module docstring
(and the task brief's literal skeleton) originally sketched these two types
as living in `search.py` itself, but `search.py` sits at the TOP of this
package's import graph (it imports `fusion`, which imports `vector` and
`bm25`); `vector.py`'s three ported methods need to build a `Degradation`
at the exact point a chroma query fails (§6.7 — the signal has to be raised
where the failure happens, not bubbled up as a bare exception across a
module boundary), so `vector.py` must be able to import this type without
importing `search.py` back — a cycle. `config.py` is this package's one
module with no internal dependencies (mirroring why `FusionConfig`/
`RetrievalConfig` live here), so it is the only cycle-free home. `search.py`
re-exports both names (`from .config import Degradation, DegradationCode`),
so `sodamem.memory.retrieval.search.Degradation` still resolves exactly as
the design's skeleton names it — only the physical definition moved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DegradationCode(str, Enum):
    VECTOR_ROUTE_FAILED = "vector_route_failed"
    BM25_ROUTE_FAILED = "bm25_route_failed"
    EVIDENCE_TIMESTAMP_UNRESOLVED = "evidence_timestamp_unresolved"
    # Added by Task 10 (T8 handoff item #2 — `sodamem.context.organizers.
    # query_plan.reader_query_plan` used a local plain-dict degraded signal
    # pending this unification; see that module's docstring). Not a
    # retrieval-route failure — the read-side answer-shape classifier is an
    # optional LLM call, not a required route — but the same "caller gets a
    # first-class typed signal, not just a log line" contract applies.
    ORGANIZER_LLM_ERROR = "organizer_llm_error"
    ORGANIZER_UNPARSEABLE = "organizer_unparseable"


@dataclass(frozen=True)
class Degradation:
    code: DegradationCode
    message: str
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FusionConfig:
    """1:1 with config.toml's [retrieve.fusion] table (12 keys, verified)."""
    strong_direct: float = 0.4
    weak_direct: float = 0.2
    strong_derived: float = 0.1
    weak_derived: float = 0.05
    max_temporal_boost: float = 0.3
    temporal_enabled: bool = True
    multi_hop_enabled: bool = True
    max_semantic_hops: int = 2
    max_search_heads_per_channel: int = 10
    search_head_rerank_top_k: int = 10
    route_limit: int = 80
    raw_recall_enabled: bool = True
    # Single-sourced from RetrievalConfig.raw_recall_enabled at construction
    # time (storage.py's `Store.raw_recall_enabled` gets the SAME value from
    # the same composition-root read) — not independently read from env here.
    #
    # NOTE: no temporal_hard_filter_enabled field. R7 verdict = winner-goes-
    # flagless: a repo-wide grep found
    # `temporal_hard_filter_enabled` read NOWHERE in fusion.py or anywhere
    # else — it was dead the moment it was declared (a naming red herring;
    # the only branch that resembles a "hard filter" is gated by
    # `QueryPlan.temporal_policy`, set exclusively by `parse_query_plan`,
    # itself zero-caller dead code deleted by this same task's R2 verdict).
    # The env var read (`GRAPH_V2_TEMPORAL_HARD_FILTER`) is deleted with it.
    #
    # fact_group_enabled lives on RetrievalConfig, not here — see below (it
    # gates a routing decision, not a fusion-scoring weight).


@dataclass(frozen=True)
class RetrievalConfig:
    """1:1 with config.toml's [retrieve]/[retrieve.limits]/[retrieve.scoring]
    tables (verified against source), plus the two GRAPH_V2_* flags that
    live outside those tables."""
    search_route: str = "fusion"                # GRAPH_V2_SEARCH_ROUTE; "wide" | "fusion"
    default_limit: int = 80
    route_limit_floor: int = 80
    vector_route_cap: int = 60
    summary_route_cap: int = 20
    rrf_k: float = 60.0
    max_route_score_cap: float = 5.0
    max_route_score_weight: float = 0.05
    vector_score_decay: float = 0.03
    linked_memory_boost: float = 0.10
    summary_expanded_boost: float = 0.05
    mention_memory_boost: float = 0.05
    derived_currency: bool = False               # GRAPH_V2_DERIVED_CURRENCY
    raw_recall_enabled: bool = True               # GRAPH_V2_RAW_RECALL, single source (see FusionConfig note)
    fact_group_enabled: bool = True               # GRAPH_V2_FACT_GROUP — kept pending Step 1 audit (see R2.9 candidate note above); do not remove without a documented audit finding
    fusion: FusionConfig = field(default_factory=FusionConfig)
