"""Graph maintenance: dedup, supersession, typed edges, summary synthesis.

Ported from the predecessor implementation (680L) + the maintainer-only
half of the predecessor implementation.

R3 audit: `_EDGE_CONF` (8 keys) and the two
`SummarySynthesizer` size caps were module-load-time `_cfg.get()` reads not
yet in the design's `IngestConfig` skeleton — added as `EdgeConfidenceWeights`/
`SummaryConfig` and threaded in via constructor injection instead of reading
a global at class-body-evaluation time (spec §6.1 point 4). `INGEST_CORRECTNESS`
is folded to unconditional True (see `extractor.py`'s module docstring for the
winner-goes-flagless rationale — the same flag, same ruling, applies here).

R3 audit ALSO found one genuinely silent degradation the design's global
"three known violations" list didn't name (those three are storage's chroma
init, client.py:108's wall-clock fallback, and llm_provider's
create_provider_for_model — none of which is this): `_fact_role`'s
`except Exception: logger.warning(...); return "user"`. A storage read
failure here silently takes the PERMISSIVE branch (an assistant-authored
fact using the fallback "user" role can then supersede a real user fact) —
exactly the "logged but the caller has no way to tell something went wrong"
shape spec §6.7 prohibits. Every OTHER `self._storage.*` call in this class
is unguarded; this was the only one wrapped in a swallow. Fixed by deleting
the try/except — a storage read failure now propagates like every sibling
call in this file, instead of being the one special case that keeps going
with a trust-affecting default.

Hand-off from the prompt layer, executed here: the two
`storage.summaries_depending_on(...)`/`storage.replace_summary_dependencies(
...)` call sites are rewritten to `storage.entity_rules.summaries_depending_on
(...)`/`storage.entity_rules.replace_summary_dependencies(...)` — Task 3 split
DR-011/DR-004#6 into `EntityRulesStore`, reached via composition
(`store.entity_rules`), not a flattened method on `Store` itself.
"""
from __future__ import annotations

import re
from typing import Optional

from sodamem.memory._shared import (
    _canonical_entity_id,
    _canonical_text,
    _date_bucket,
    _infer_entity_type,
    _stable_id,
    _tokenize,
)
from sodamem.memory.storage.store import Store
from sodamem.models import (
    EdgeType,
    EntityMention,
    FactEdge,
    FactEvent,
    FactKind,
    FactStatus,
    Modality,
    SourceType,
    SummarySynthesis,
)

from .config import IngestConfig

import logging

logger = logging.getLogger(__name__)


def _role_signature(roles: dict) -> tuple:
    pairs = []
    for role, value in sorted((roles or {}).items()):
        if role == "subject":
            continue
        values = value if isinstance(value, list) else [value]
        normalized = tuple(sorted(_canonical_text(v) for v in values if v))
        if normalized:
            pairs.append((role, normalized))
    return tuple(pairs)


def _document_time(fact) -> float:
    """Supersession tie-break key for facts with no real event/valid time: the
    time the user STATED the fact (document_time = latest source-span session_time).
    Prefers the first-class field (5a), then legacy metadata, then created_at
    (ingest wallclock) — the last only for old stores / synthesized profiles."""
    dt = getattr(fact, "document_time", None)
    if dt is None:
        dt = (getattr(fact, "metadata", None) or {}).get("document_time")
    return dt if dt is not None else fact.created_at


class EntityResolver:
    """Resolve entity surface forms against the live graph entity registry."""

    def __init__(self, store: Store):
        self._store = store

    def resolve(
        self,
        user_id: str,
        surface: str,
        entity_type: str = "concept",
    ) -> tuple[str, str, float, str]:
        name = re.sub(r"\s+", " ", str(surface or "").strip())
        if not name:
            return "", "", 0.0, "empty"
        if name.lower() == "user":
            return "entity_user", "user", 1.0, "user_subject"

        normalized = self._normalize(name)
        existing = self._match_existing(user_id, normalized, entity_type)
        if existing is not None:
            return existing

        entity_id = _canonical_entity_id(name)
        self._store.register_entity(
            user_id=user_id,
            entity_id=entity_id,
            dedup_key=_canonical_text(name),
            surface_form=surface,
            entity_type=entity_type or "concept",
        )
        return entity_id, name, 1.0, "new"

    def _match_existing(
        self,
        user_id: str,
        normalized: str,
        entity_type: str,
    ) -> Optional[tuple[str, str, float, str]]:
        query_tokens = normalized.split()
        for entity in self._store.get_all_graph_entities(user_id):
            key = getattr(entity, "dedup_key", "") or ""
            candidate_norm = self._normalize(key)
            if candidate_norm == normalized:
                return entity.entity_id, key, 1.0, "exact"
            candidate_tokens = candidate_norm.split()
            candidate_acronym = "".join(token[0] for token in candidate_tokens if token)
            if normalized and normalized == candidate_acronym and len(normalized) > 1:
                return entity.entity_id, key, 0.95, "acronym"
            if (
                len(query_tokens) == 1
                and len(candidate_tokens) > 1
                and query_tokens[0] == candidate_tokens[0]
                and self._compatible_type(entity_type, getattr(entity, "entity_type", "concept"))
            ):
                return entity.entity_id, key, 0.85, "prefix_contained"
            if (
                len(candidate_tokens) == 1
                and len(query_tokens) > 1
                and candidate_tokens[0] == query_tokens[0]
                and self._compatible_type(entity_type, getattr(entity, "entity_type", "concept"))
            ):
                return entity.entity_id, key, 0.85, "prefix_contained"
        return None

    @staticmethod
    def _normalize(value: str) -> str:
        value = re.sub(r"^\s*the\s+", "", str(value or "").strip(), flags=re.I)
        value = re.sub(r"[^a-z0-9]+", " ", value.lower())
        return " ".join(value.split())

    @staticmethod
    def _compatible_type(left: str, right: str) -> bool:
        return "concept" in (left, right) or not left or not right or left == right


class GraphMaintainer:
    """Deterministic maintenance for Fact/Event dedup, supersession, and edges."""

    _UPDATE_LIKE_RE = re.compile(
        r"\b("
        r"current|currently|now|still|actually|correction|correcting|rather|instead|"
        r"personal best|record|best time|total|count|collection|own|have|has|"
        r"live|living|located|based|moved|changed|started|stopped|"
        r"in the last|last \d+|past \d+|recent(?:ly)?"
        r")\b",
        re.I,
    )
    _UPDATE_SLOT_STOP = {
        "actually", "already", "recent", "recently", "current", "currently",
        "include", "including", "last", "past", "month", "months", "week",
        "weeks", "year", "years", "count", "total", "watched", "watch",
        "films", "film", "movies", "movie", "time", "times", "have", "has",
        "with", "from", "this", "that", "around",
    }

    def __init__(self, store: Store, config: Optional[IngestConfig] = None):
        self._store = store
        self._config = config or IngestConfig()
        self._edge_conf = self._config.edge_confidence

    def maintain(self, fact: FactEvent) -> tuple[FactEvent, list[dict]]:
        actions: list[dict] = []
        self._materialize_update_validity(fact)
        existing = self._find_existing(fact)
        if existing is not None:
            merged = self._merge(existing, fact)
            actions.append({"action": "duplicate_merge", "target_fact_id": existing.fact_id})
            fact = merged
        else:
            extended = self._find_same_event_extend(fact)
            if extended is not None:
                merged = self._merge(extended, fact)
                actions.append({"action": "same_event_extend", "target_fact_id": extended.fact_id})
                fact = merged
            else:
                actions.extend(self._contradict_prior_facts(fact))
            actions.extend(self._supersede_prior_facts(fact))
        self._store.upsert_fact_event(fact)
        self._write_mentions_and_edges(fact)
        self._mark_related_summaries_dirty(fact)
        return fact, actions

    def _signature(self, fact: FactEvent) -> tuple:
        roles = fact.metadata.get("entity_roles", {}) if isinstance(fact.metadata, dict) else {}
        return (
            fact.kind.value if isinstance(fact.kind, FactKind) else fact.kind,
            fact.event_type,
            fact.modality.value if isinstance(fact.modality, Modality) else fact.modality,
            fact.predicate_canonical,
            _date_bucket(fact.occurred_start),
            _date_bucket(fact.valid_from),
            fact.quantity_unit,
            _role_signature(roles),
            tuple(sorted(fact.source_span_ids)),
        )

    def _loose_signature(self, fact: FactEvent) -> tuple:
        roles = fact.metadata.get("entity_roles", {}) if isinstance(fact.metadata, dict) else {}
        return (
            fact.kind.value if isinstance(fact.kind, FactKind) else fact.kind,
            fact.event_type,
            fact.modality.value if isinstance(fact.modality, Modality) else fact.modality,
            fact.predicate_canonical,
            _date_bucket(fact.occurred_start),
            _date_bucket(fact.valid_from),
            fact.quantity_unit,
            _role_signature(roles),
        )

    def _find_existing(self, fact: FactEvent) -> Optional[FactEvent]:
        exact = self._signature(fact)
        loose = self._loose_signature(fact)
        for existing in self._store.get_all_fact_events(fact.user_id, active_only=False):
            if existing.fact_id == fact.fact_id:
                continue
            if self._signature(existing) == exact:
                return existing
            if self._loose_signature(existing) == loose and set(existing.source_span_ids) & set(fact.source_span_ids):
                return existing
        return None

    def _merge(self, existing: FactEvent, incoming: FactEvent) -> FactEvent:
        existing.source_span_ids = sorted(set(existing.source_span_ids) | set(incoming.source_span_ids))
        existing.object_entity_ids = sorted(set(existing.object_entity_ids) | set(incoming.object_entity_ids))
        existing.confidence = max(existing.confidence, incoming.confidence)
        existing.confidence_reason = existing.confidence_reason or incoming.confidence_reason
        existing.occurred_start = existing.occurred_start or incoming.occurred_start
        existing.occurred_end = existing.occurred_end or incoming.occurred_end
        existing.valid_from = existing.valid_from or incoming.valid_from
        existing.valid_until = existing.valid_until or incoming.valid_until
        if existing.quantity_value is None:
            existing.quantity_value = incoming.quantity_value
            existing.quantity_unit = incoming.quantity_unit
        existing.provenance.setdefault("session_ids", [])
        existing.provenance.setdefault("turn_ids", [])
        for key in ("session_ids", "turn_ids"):
            existing.provenance[key] = sorted(set(existing.provenance.get(key, [])) | set(incoming.provenance.get(key, [])))
        existing.provenance.setdefault("merged_fact_ids", []).append(incoming.fact_id)
        existing.metadata.setdefault("merged_support_texts", [])
        support = incoming.metadata.get("support_text") if isinstance(incoming.metadata, dict) else ""
        if support and support not in existing.metadata["merged_support_texts"]:
            existing.metadata["merged_support_texts"].append(support)
        return existing

    def _materialize_update_validity(self, fact: FactEvent) -> None:
        if fact.valid_from is not None or not self._is_update_like(fact):
            return
        spans = self._store.get_source_spans_by_ids(fact.source_span_ids)
        mentions = [s.session_time for s in spans if s.session_time is not None]
        if mentions:
            fact.valid_from = min(mentions)

    def _is_update_like(self, fact: FactEvent) -> bool:
        kind = fact.kind.value if isinstance(fact.kind, FactKind) else fact.kind
        modality = fact.modality.value if isinstance(fact.modality, Modality) else fact.modality
        source_type = fact.source_type.value if isinstance(fact.source_type, SourceType) else fact.source_type
        support = fact.metadata.get("support_text", "") if isinstance(fact.metadata, dict) else ""
        text = " ".join([fact.predicate_raw or "", fact.predicate_canonical or "", support])
        explicit_update = bool(self._UPDATE_LIKE_RE.search(text))
        if source_type == SourceType.USER_CORRECTION.value:
            return True
        if kind == FactKind.PROFILE.value:
            return True  # D35: a fresh profile always supersedes the prior one
        if kind == FactKind.PREFERENCE.value or modality == Modality.PREFERENCE.value:
            return explicit_update
        if kind == FactKind.STATE.value or modality == Modality.CURRENT_STATE.value:
            return True
        return explicit_update

    def _find_same_event_extend(self, fact: FactEvent) -> Optional[FactEvent]:
        if (fact.kind.value if isinstance(fact.kind, FactKind) else fact.kind) != FactKind.EVENT.value:
            return None
        modality = fact.modality.value if isinstance(fact.modality, Modality) else fact.modality
        for existing in self._store.get_all_fact_events(fact.user_id):
            if existing.fact_id == fact.fact_id:
                continue
            if (existing.kind.value if isinstance(existing.kind, FactKind) else existing.kind) != FactKind.EVENT.value:
                continue
            if existing.event_type != fact.event_type:
                continue
            if existing.predicate_canonical != fact.predicate_canonical:
                continue
            existing_modality = existing.modality.value if isinstance(existing.modality, Modality) else existing.modality
            if existing_modality != modality:
                continue
            if _date_bucket(existing.occurred_start) != _date_bucket(fact.occurred_start):
                continue
            if not (set(existing.object_entity_ids or []) & set(fact.object_entity_ids or [])):
                # PR-2.2 (unconditional — INGEST_CORRECTNESS folded to always-on):
                # shared lexical tokens are not identity evidence — require a real
                # shared object entity. The source's INGEST_CORRECTNESS=False
                # token-overlap fallback below this check is deleted, not kept.
                continue
            return existing
        return None

    def _contradict_prior_facts(self, fact: FactEvent) -> list[dict]:
        source_type = fact.source_type.value if isinstance(fact.source_type, SourceType) else fact.source_type
        if source_type != SourceType.USER_CORRECTION.value:
            return []
        actions = []
        for existing in self._store.get_all_fact_events(fact.user_id):
            if existing.fact_id == fact.fact_id:
                continue
            if not self._same_update_slot(existing, fact):
                continue
            existing.status = FactStatus.CONTRADICTED
            existing.provenance.setdefault("contradicted_by_fact_id", fact.fact_id)
            self._store.upsert_fact_event(existing)
            self._store.upsert_fact_edge(FactEdge(
                edge_id=_stable_id("fact_edge", fact.fact_id, existing.fact_id, "CONTRADICTS"),
                user_id=fact.user_id,
                src_fact_id=fact.fact_id,
                dst_id=existing.fact_id,
                edge_type=EdgeType.CONTRADICTS,
                predicate_raw="user correction contradicts prior assertion",
                predicate_canonical="contradicts",
                confidence=self._edge_conf.contradicts,
            ))
            actions.append({"action": "contradict", "target_fact_id": existing.fact_id})
        return actions

    # Generic buckets that carry NO slot identity. A fact whose predicate could
    # not be resolved (fallback path) or whose event_type is the catch-all must
    # never supersede an unrelated fact just because they share this placeholder.
    _NON_SLOT_CANONICALS = {"message_unit_statement", ""}

    def _same_update_slot(self, existing: FactEvent, incoming: FactEvent) -> bool:
        # Unknown predicate => no slot identity => never the same update slot.
        # Without this, every fallback fact (all 'message_unit_statement') would
        # supersede every earlier one, collapsing a whole session to one fact.
        if (existing.predicate_canonical in self._NON_SLOT_CANONICALS
                or incoming.predicate_canonical in self._NON_SLOT_CANONICALS):
            return False
        same_predicate = existing.predicate_canonical == incoming.predicate_canonical
        same_type = (existing.event_type or "") == (incoming.event_type or "")
        # PR-2.3 unknown != equal (unconditional — INGEST_CORRECTNESS folded to
        # always-on): a generic/empty event_type shared by both sides is a
        # catch-all, not positive identity evidence.
        both_generic = (existing.event_type or "") in ("", "other") and (incoming.event_type or "") in ("", "other")
        same_type = same_type and not both_generic
        # PROFILE identity lives entirely in predicate_canonical (entity_profile_
        # <entity_id>). Different entities share event_type="profile" and overlap
        # on the underlying facts' tokens, so the same_type / token fallbacks
        # would wrongly merge distinct entities' profiles. Demand an exact match.
        e_profile = (existing.kind.value if isinstance(existing.kind, FactKind) else existing.kind) == FactKind.PROFILE.value
        i_profile = (incoming.kind.value if isinstance(incoming.kind, FactKind) else incoming.kind) == FactKind.PROFILE.value
        if e_profile or i_profile:
            return same_predicate
        if not same_predicate and not same_type:
            return False
        if (existing.quantity_unit or "") != (incoming.quantity_unit or ""):
            return False
        existing_roles = existing.metadata.get("entity_roles", {}) if isinstance(existing.metadata, dict) else {}
        incoming_roles = incoming.metadata.get("entity_roles", {}) if isinstance(incoming.metadata, dict) else {}
        existing_keys = {k for k, v in existing_roles.items() if k != "subject" and v not in (None, "", [], {})}
        incoming_keys = {k for k, v in incoming_roles.items() if k != "subject" and v not in (None, "", [], {})}
        # Value-bearing roles such as "time" or "count" may legitimately change
        # between versions. Require role compatibility only when both sides expose
        # non-value slots such as item/company/location.
        value_roles = {"value", "time", "duration", "count", "quantity", "amount", "number", "score"}
        comparable_existing = existing_keys - value_roles
        comparable_incoming = incoming_keys - value_roles
        if comparable_existing and comparable_incoming and comparable_existing != comparable_incoming:
            return False
        if not same_predicate:
            # PR-2.2 (unconditional — INGEST_CORRECTNESS folded to always-on):
            # lexical token overlap is not slot identity. Require a concrete
            # shared identity: same non-generic event_type AND a matching
            # non-value identity role. The source's INGEST_CORRECTNESS=False
            # token-overlap fallback is deleted, not kept.
            if not (same_type and comparable_existing and comparable_incoming
                    and comparable_existing == comparable_incoming):
                return False
            return True
        return True

    def _fact_role(self, fact: FactEvent) -> str:
        """Authoring role: 'user' if any source span is user-authored, else
        'assistant'. Defaults to 'user' when unknown (conservative — only an
        assistant-only fact is blocked from superseding a user fact).

        R3 audit fix: the source wrapped `get_source_spans_by_ids` in a bare
        `except Exception: logger.warning(...); return "user"` — a storage
        failure silently took the PERMISSIVE branch (an authority-affecting
        default that lets an assistant-only fact through as if it were
        user-authored) with only a log line for anyone to notice. No other
        `self._store.*` call in this class is guarded this way; removing the
        guard makes this call consistent with its siblings and turns a
        storage read failure into a real, visible exception instead of a
        quietly-degraded trust decision (spec §6.7)."""
        spans = self._store.get_source_spans_by_ids(fact.source_span_ids)
        if not spans:
            return "user"
        for s in spans:
            if getattr(s, "role", "user") == "user":
                return "user"
        return "assistant"

    def _supersede_order_key(self, fact: FactEvent) -> tuple:
        """Deterministic, ingest-order-INDEPENDENT supersession ordering key.
        Real valid/event time dominates; then said-time (document_time, falling
        back to created_at only for legacy/synthesized facts). The final
        tie-break is a CONTENT hash — NOT the random uuid `fact_id` (models.py
        default_factory=uuid4), which made the "current" value non-reproducible
        across re-ingests and ingest order."""
        support = fact.metadata.get("support_text", "") if isinstance(fact.metadata, dict) else ""
        content_sig = _stable_id("sig", fact.predicate_canonical, fact.quantity_value, support)
        return (fact.valid_from or fact.occurred_start or 0.0, _document_time(fact), content_sig)

    def _mark_superseded(self, loser: FactEvent, winner: FactEvent, actions: list, *, persist_loser: bool) -> None:
        """`loser` is superseded by `winner`. persist_loser=True when the loser is an
        already-stored EXISTING fact (must be re-upserted); False when it is the
        INCOMING fact (maintain() upserts it after supersession runs).

        Unconditional since 0806. This used to sit behind an IngestConfig
        flag that defaulted to observe-only, so the library would match the
        frozen benchmark stores (the "obs" in "H+obs"). The cost was that no
        deployment ever superseded anything: HTTP and MCP never construct an
        IngestConfig, so the flag was unreachable, and the ADD-only
        correction the README promises happened only when a caller hit
        PATCH /v1/memories/{id} by hand. A knob whose only reachable setting
        was "do nothing" is not a choice; the flag is gone."""
        loser.status = FactStatus.SUPERSEDED
        loser.valid_until = winner.valid_from or winner.occurred_start
        loser.provenance.setdefault("superseded_by_fact_id", winner.fact_id)
        if persist_loser:
            self._store.upsert_fact_event(loser)
        self._store.upsert_fact_edge(FactEdge(
            edge_id=_stable_id("fact_edge", winner.fact_id, loser.fact_id, "SUPERSEDES"),
            user_id=winner.user_id,
            src_fact_id=winner.fact_id,
            dst_id=loser.fact_id,
            edge_type=EdgeType.SUPERSEDES,
            predicate_raw="newer update-like assertion supersedes older assertion",
            predicate_canonical="supersedes",
            confidence=self._edge_conf.supersedes,
            metadata={
                "reason": "same predicate/quantity update slot",
                "current_head_fact_id": winner.fact_id,
            },
        ))
        actions.append({"action": "supersede", "target_fact_id": loser.fact_id})

    def _supersede_prior_facts(self, fact: FactEvent) -> list[dict]:
        incoming_update_like = self._is_update_like(fact)
        if not incoming_update_like and fact.quantity_value is None:
            return []
        actions = []
        fact_key = self._supersede_order_key(fact)
        superseded_by = None          # newest same-slot existing that outranks the incoming
        superseded_by_key = None
        for existing in self._store.get_all_fact_events(fact.user_id):
            if existing.fact_id == fact.fact_id:
                continue
            existing_update_like = self._is_update_like(existing)
            if not incoming_update_like and not existing_update_like:
                continue
            if not self._same_update_slot(existing, fact):
                continue
            # D32 range vs point: a point that falls inside an existing range is not
            # a contradiction — the range already subsumes it. Keep both (symmetric).
            if self._range_subsumes_point(existing, fact):
                continue
            # Order by VALID/EVENT time: real time DOMINATES, so a back-dated fact
            # can't mis-supersede a later-asserted one; tie-break = said-time then
            # a CONTENT hash (order-independent, see _supersede_order_key).
            existing_key = self._supersede_order_key(existing)
            if existing_key >= fact_key:
                # The existing outranks the incoming → the INCOMING is stale on
                # arrival (out-of-order / repair ingest). Mark it superseded so
                # the store is INGEST-ORDER-INDEPENDENT. PR-2.4 source authority
                # (unconditional — INGEST_CORRECTNESS folded to always-on): an
                # assistant existing must not supersede a user incoming.
                if self._fact_role(existing) == "assistant" and self._fact_role(fact) == "user":
                    continue
                if superseded_by_key is None or existing_key > superseded_by_key:
                    superseded_by, superseded_by_key = existing, existing_key
                continue
            # The incoming outranks the existing → supersede the existing
            # (forward). PR-2.4 (unconditional): an assistant incoming must not
            # supersede a user existing (q030).
            if self._fact_role(fact) == "assistant" and self._fact_role(existing) == "user":
                continue
            self._mark_superseded(existing, fact, actions, persist_loser=True)
        # Order-independence: a newer same-slot fact already existed → the incoming is
        # stale on arrival. maintain() upserts `fact` after this → persist_loser=False.
        if superseded_by is not None:
            self._mark_superseded(fact, superseded_by, actions, persist_loser=False)
        return actions

    @staticmethod
    def _quantity_range(fact: FactEvent) -> Optional[tuple[float, float]]:
        meta = fact.metadata if isinstance(fact.metadata, dict) else {}
        rng = meta.get("quantity_range")
        if isinstance(rng, (list, tuple)) and len(rng) == 2:
            try:
                return float(rng[0]), float(rng[1])
            except (TypeError, ValueError):
                logger.warning(
                    "maintainer._quantity_range: malformed quantity_range=%r on "
                    "fact %s — treating as no range", rng, fact.fact_id,
                )
                return None
        return None

    def _range_subsumes_point(self, existing: FactEvent, incoming: FactEvent) -> bool:
        """True when one side is a range and the other's point falls inside it."""
        e_rng, i_rng = self._quantity_range(existing), self._quantity_range(incoming)
        if e_rng is not None and incoming.quantity_value is not None and i_rng is None:
            return e_rng[0] <= incoming.quantity_value <= e_rng[1]
        if i_rng is not None and existing.quantity_value is not None and e_rng is None:
            return i_rng[0] <= existing.quantity_value <= i_rng[1]
        return False

    def _update_slot_tokens(self, fact: FactEvent) -> set[str]:
        support = fact.metadata.get("support_text", "") if isinstance(fact.metadata, dict) else ""
        roles = fact.metadata.get("entity_roles", {}) if isinstance(fact.metadata, dict) else {}
        role_text = " ".join(
            " ".join(str(x) for x in value) if isinstance(value, list) else str(value)
            for key, value in roles.items()
            if key != "subject" and value not in (None, "", [], {})
        )
        text = " ".join([fact.predicate_raw or "", support, fact.predicate_canonical or "", role_text])
        return {
            token for token in _tokenize(text)
            if len(token) >= 3 and not token.isdigit() and token not in self._UPDATE_SLOT_STOP
        }

    def _write_mentions_and_edges(self, fact: FactEvent) -> None:
        roles = fact.metadata.get("entity_roles", {}) if isinstance(fact.metadata, dict) else {}
        for span_id in fact.source_span_ids:
            self._store.upsert_fact_edge(FactEdge(
                edge_id=_stable_id("fact_edge", fact.fact_id, span_id, "EVIDENCES"),
                user_id=fact.user_id,
                src_fact_id=fact.fact_id,
                dst_id=span_id,
                edge_type=EdgeType.EVIDENCES,
                predicate_raw="source trace evidences memory unit",
                predicate_canonical="evidenced_by_source_trace",
                confidence=self._edge_conf.evidences,
            ))
        for role, value in roles.items():
            values = value if isinstance(value, list) else [value]
            for item in values:
                if item in (None, ""):
                    continue
                entity_name = str(item)
                entity_id = _canonical_entity_id(entity_name)
                # L3: register/merge the canonical entity node so the entity
                # layer is a live, queryable object, not a scattered id.
                self._store.register_entity(
                    user_id=fact.user_id,
                    entity_id=entity_id,
                    dedup_key=_canonical_text(entity_name),
                    surface_form=entity_name,
                    entity_type=_infer_entity_type(role),
                )
                edge_type = EdgeType.SUBJECT_OF if role == "subject" else EdgeType.OBJECT_OF
                self._store.upsert_fact_edge(FactEdge(
                    edge_id=_stable_id("fact_edge", fact.fact_id, entity_id, role),
                    user_id=fact.user_id,
                    src_fact_id=fact.fact_id,
                    dst_id=entity_id,
                    edge_type=edge_type,
                    predicate_raw=f"{role}: {entity_name}",
                    predicate_canonical=role,
                    confidence=self._edge_conf.subject_object_of,
                ))
                self._store.upsert_fact_edge(FactEdge(
                    edge_id=_stable_id("fact_edge", fact.fact_id, entity_id, role, "MENTIONS"),
                    user_id=fact.user_id,
                    src_fact_id=fact.fact_id,
                    dst_id=entity_id,
                    edge_type=EdgeType.MENTIONS,
                    predicate_raw=f"mentions {entity_name}",
                    predicate_canonical="mentions",
                    confidence=self._edge_conf.mentions,
                ))
                for span_id in fact.source_span_ids:
                    self._store.upsert_entity_mention(EntityMention(
                        mention_id=_stable_id("mention", span_id, fact.fact_id, role, entity_id),
                        user_id=fact.user_id,
                        entity_id=entity_id,
                        entity_name=entity_name,
                        source_span_id=span_id,
                        fact_id=fact.fact_id,
                        role=role,
                        link_confidence=self._edge_conf.mention_link,
                    ))
        if fact.occurred_start is not None:
            self._store.upsert_fact_edge(FactEdge(
                edge_id=_stable_id("fact_edge", fact.fact_id, _date_bucket(fact.occurred_start), "OCCURRED_DURING"),
                user_id=fact.user_id,
                src_fact_id=fact.fact_id,
                dst_id=_date_bucket(fact.occurred_start),
                edge_type=EdgeType.OCCURRED_DURING,
                predicate_raw="fact occurred during date",
                predicate_canonical="occurred_during",
                confidence=self._edge_conf.occurred_during,
            ))
        if fact.quantity_value is not None or fact.quantity_unit:
            self._store.upsert_fact_edge(FactEdge(
                edge_id=_stable_id("fact_edge", fact.fact_id, fact.quantity_value, fact.quantity_unit, "HAS_QUANTITY"),
                user_id=fact.user_id,
                src_fact_id=fact.fact_id,
                dst_id=f"{fact.quantity_value}:{fact.quantity_unit}",
                edge_type=EdgeType.HAS_QUANTITY,
                predicate_raw="fact has quantity",
                predicate_canonical="has_quantity",
                confidence=self._edge_conf.has_quantity,
            ))

    def _mark_related_summaries_dirty(self, fact: FactEvent) -> None:
        """DR-004 #6: centralized invalidation entry point. The explicit
        dependency index reverse-looks-up "pre-change" dependents (a fact
        that left scope, whose old Summary still depends on it); session
        provenance is the fallback for "post-change" dependents (a fact newly
        entering a scope that may not be indexed yet). Both paths are
        idempotent — re-marking is harmless."""
        reason = f"fact_{getattr(fact.status, 'value', str(fact.status))}"
        for summary_id in self._store.entity_rules.summaries_depending_on("fact", fact.fact_id):
            self._store.mark_summary_dirty_by_id(summary_id, reason=reason)
        for session_id in fact.provenance.get("session_ids") or []:
            self._store.mark_summary_dirty(fact.user_id, "session", session_id, reason=reason)


class SummarySynthesizer:
    """Deterministic summary materialized view over Fact/Event + SourceSpan."""

    def __init__(self, store: Store, config: Optional[IngestConfig] = None):
        self._store = store
        self._config = config or IngestConfig()

    def refresh_session(self, user_id: str, session_id: str) -> Optional[SummarySynthesis]:
        # DR-004: records the build's starting revision; if the scope has
        # already been advanced past it by commit time, storage keeps it dirty.
        target_revision = self._store.get_scope_revision(user_id, "session", session_id)
        facts = [
            f for f in self._store.get_all_fact_events(user_id)
            if session_id in (f.provenance.get("session_ids") or [])
            and f.source_type != SourceType.SUMMARY_SYNTHESIS
        ]
        if not facts:
            # DR-004 #5: a scope with no valid facts left must be archived, not
            # left active.
            self._store.archive_summary(user_id, "session", session_id)
            return None
        parts = []
        source_span_ids = []
        max_facts = self._config.summary.max_facts
        max_chars = self._config.summary.max_chars
        for fact in facts[:max_facts]:
            roles = fact.metadata.get("entity_roles", {}) if isinstance(fact.metadata, dict) else {}
            role_text = " ".join(
                str(v) if not isinstance(v, list) else " ".join(str(x) for x in v)
                for k, v in roles.items() if k != "subject"
            )
            parts.append(" ".join(p for p in [fact.predicate_raw, role_text, fact.event_type, fact.modality.value] if p))
            source_span_ids.extend(fact.source_span_ids)
        summary_text = " ".join(parts)[:max_chars]
        summary = SummarySynthesis(
            summary_id=_stable_id("summary", user_id, "session", session_id),
            user_id=user_id,
            scope_type="session",
            scope_id=session_id,
            summary_text=summary_text,
            source_fact_ids=[f.fact_id for f in facts],
            source_span_ids=sorted(set(source_span_ids)),
            dirty=False,
            scope_revision=target_revision,
            built_from_revision=target_revision,
        )
        self._store.upsert_summary_synthesis(summary)
        # DR-004 #6: rebuild the explicit dependency index (fact / source_span /
        # session) used to reverse-look-up "pre-change" dependents when a fact's
        # status changes.
        deps = ([("session", session_id)]
                + [("fact", fid) for fid in summary.source_fact_ids]
                + [("source_span", sid) for sid in summary.source_span_ids])
        self._store.entity_rules.replace_summary_dependencies(summary.summary_id, deps)
        return summary
