"""EvidenceStore — the durable, deduplicating store of tool-call evidence a
context-assembly pass accumulates across a single query, plus the id/text
projection helpers built on top of it.

R12 signature change (`EvidenceStore.ingest`): an earlier design took
`stdout: str` and parsed it itself, because a CLI-subprocess harness
transported tool output as JSON text across a process boundary.
`sodamem.tools` calls happen in-process — there is no stdout to parse, only
an already-materialized Python value. `ingest()` therefore takes
`payload: Any` and the key-sniffing normalization (`_candidate_rows`) starts one step
later than it used to. `_parse_stdout` itself is DELETED, not ported: its only caller was this method, and that caller no
longer needs it.

`_one_line` gap note: the port inventory had no row for
`autonomous_runtime.py:201-202` (`_one_line`) even though it is used by
nearly every function this file ports (`EvidenceRecord.compact`/
`reader_evidence`, both organizers, `_identity_key`). Same class of omission
the map's own history already caught twice for this exact source file
(`_aliases`, `_query_centered_excerpt`/`_identity_key` — see map row
comments "原review误判"/"原review完全漏掉此段"). Ported here as a shared
helper, same treatment as `_query_terms`/`_reader_source_text`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

_CARD_TEXT_LIMIT = 220
_READER_TEXT_LIMIT = 3200
_DEFAULT_MAX_STATE_CARDS = 24


def _one_line(value: Any, limit: int = _CARD_TEXT_LIMIT) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _json_object(text: str) -> dict[str, Any] | None:
    """Extract a single JSON object out of LLM-generated text (which may be
    fenced in a ```json block or wrapped in prose). This is for picking apart
    an already-generated LLM response — never for parsing tool output (that
    was `_parse_stdout`'s job, and it is gone; see module docstring)."""
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    start = value.find("{")
    end = value.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(value[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _candidate_rows(payload: Any) -> list[dict[str, Any]]:
    """Normalize an already-parsed tool response into candidate row dicts.

    Ported byte-for-byte. Tool responses are heterogeneous by construction
    (search returns `items`, timeline returns `events`, a single `inspect`
    call returns one bare row, ...) — this key-sniffing cascade is the
    contract every ported organizer and `EvidenceStore.ingest` assume, and
    R12 explicitly keeps it as-is (YAGNI: `sodamem/tools/`'s return shape is
    not being redesigned in this phase)."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    rows: list[dict[str, Any]] = []
    memory = payload.get("memory")
    if isinstance(memory, dict):
        rows.append(memory)
    for key in (
        "items", "results", "hits", "memories", "evidence", "events",
        "raw_evidence", "turns", "messages",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(row for row in value if isinstance(row, dict))
    for group in payload.get("groups") or []:
        if isinstance(group, dict):
            rows.extend(row for row in group.get("items") or [] if isinstance(row, dict))
    if not rows and any(key in payload for key in ("evidence_id", "id", "fact_id", "span_id")):
        rows.append(payload)
    return rows


def _evidence_id(row: dict[str, Any]) -> str:
    explicit = row.get("evidence_id")
    if explicit:
        return str(explicit)
    if row.get("fact_id"):
        return f"ev_fact:{row['fact_id']}"
    if row.get("span_id"):
        return f"ev_span:{row['span_id']}"
    raw_id = row.get("id") or row.get("memory_id") or row.get("event_id")
    return str(raw_id or "")


def _aliases(row: dict[str, Any], evidence_id: str) -> set[str]:
    values = {
        evidence_id,
        str(row.get("id") or ""),
        str(row.get("memory_id") or ""),
        str(row.get("fact_id") or ""),
        str(row.get("span_id") or ""),
        str(row.get("event_id") or ""),
    }
    return {value for value in values if value}


def _support_text(row: dict[str, Any]) -> str:
    return str(
        row.get("extracted_support_text")
        or row.get("support_text")
        or row.get("content")
        or row.get("text")
        or row.get("snippet")
        or row.get("content_preview")
        or ""
    )


def _reader_source_text(row: dict[str, Any]) -> str:
    """Prefer original source text over lossy extraction summaries."""
    return str(
        row.get("content")
        or row.get("text")
        or row.get("support_text")
        or row.get("extracted_support_text")
        or row.get("snippet")
        or row.get("content_preview")
        or ""
    )


def _query_terms(value: str) -> set[str]:
    stop = {
        "the", "and", "for", "that", "this", "with", "from", "what", "when",
        "where", "which", "have", "many", "much", "does", "about", "answer",
        "gather", "sufficient", "evidence",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9'\-]*", value.lower())
        if len(token) >= 3 and token not in stop
    }


def _query_centered_excerpt(text: str, query: str, limit: int) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text

    terms = sorted(_query_terms(query), key=len, reverse=True)
    lowered = text.lower()
    positions: list[int] = []
    for term in terms:
        start = 0
        while len(positions) < 8:
            index = lowered.find(term, start)
            if index < 0:
                break
            if all(abs(index - prior) > 240 for prior in positions):
                positions.append(index)
            start = index + len(term)

    if not positions:
        return text[:limit]

    windows: list[tuple[int, int]] = []
    radius = 420
    for position in sorted(positions):
        start = max(0, position - radius)
        end = min(len(text), position + radius)
        if windows and start <= windows[-1][1] + 80:
            windows[-1] = (windows[-1][0], max(windows[-1][1], end))
        else:
            windows.append((start, end))

    excerpts = []
    used = 0
    for start, end in windows:
        available = limit - used
        if available <= 0:
            break
        piece = text[start:end][:available]
        prefix = "... " if start else ""
        suffix = " ..." if end < len(text) else ""
        excerpts.append(prefix + piece + suffix)
        used += len(piece) + len(prefix) + len(suffix) + 2
    return "\n\n".join(excerpts)[:limit]


def _identity_key(row: dict[str, Any]) -> str:
    roles = row.get("entity_roles")
    if not isinstance(roles, dict):
        return ""
    parts = []
    for role, raw_value in sorted(roles.items()):
        if role == "subject":
            continue
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        normalized = sorted(_one_line(value, 100).lower() for value in values if value)
        if normalized:
            parts.append(f"{role}:{'|'.join(normalized)}")
    return ";".join(parts)


@dataclass
class EvidenceRecord:
    evidence_id: str
    raw: dict[str, Any]
    first_seen_step: int
    last_seen_step: int
    tools: set[str] = field(default_factory=set)
    aliases: set[str] = field(default_factory=set)
    expanded: bool = False

    def merge(self, row: dict[str, Any], tool: str, step: int) -> None:
        for key, value in row.items():
            if value not in (None, "", [], {}):
                current = self.raw.get(key)
                if current in (None, "", [], {}) or len(str(value)) > len(str(current)):
                    self.raw[key] = value
        self.last_seen_step = step
        self.tools.add(tool)
        self.aliases.update(_aliases(row, self.evidence_id))
        self.expanded = self.expanded or tool in {
            "inspect", "result", "inspect_session", "session",
        }

    def compact(self) -> dict[str, Any]:
        row = self.raw
        quantity = row.get("quantity")
        # G3a planner-slim (default since the 0713 6-run verdict): 60% of
        # cards carry the
        # pure-noise placeholder {"value": null, "unit": ""}; drop it from
        # the planner payload. Keep-unit rule: value=None with a NON-empty
        # unit stays — those cards carry real unit information.
        if (
            isinstance(quantity, dict)
            and quantity.get("value") is None
            and not quantity.get("unit")
        ):
            quantity = None
        roles = row.get("entity_roles") if isinstance(row.get("entity_roles"), dict) else {}
        entity_values = []
        for role, raw_value in roles.items():
            if role == "subject":
                continue
            values = raw_value if isinstance(raw_value, list) else [raw_value]
            for value in values:
                if value:
                    entity_values.append(f"{role}={_one_line(value, 70)}")
        session_id = row.get("session_id") or row.get("source_session_id")
        turn_id = row.get("turn_id") or row.get("source_turn_id")
        source = "/".join(str(value) for value in (session_id, turn_id) if value)
        source_role = row.get("role") or row.get("source_role")
        status = row.get("status")
        version_status = row.get("version_status")
        return {
            key: value
            for key, value in {
                "evidence_id": self.evidence_id,
                "support": _one_line(_support_text(row)),
                "predicate": _one_line(row.get("predicate_raw"), 140),
                "entities": "|".join(entity_values)[:180],
                "quantity": quantity,
                "date": (
                    row.get("occurred_start")
                    or row.get("event_date")
                    or row.get("valid_from")
                    or row.get("session_time")  # mention time: last resort so no card is dateless
                ),
                "source": source,
                "role": source_role if source_role not in (None, "", "user") else None,
                "status": status if status not in (None, "", "active") else None,
                "version_status": (
                    version_status
                    if version_status not in (None, "", "current")
                    else None
                ),
                "expanded": self.expanded,
            }.items()
            if value not in (None, "", [], {})
        }

    def reader_evidence(self, query: str) -> dict[str, Any]:
        row = self.raw
        quantity = row.get("quantity") if isinstance(row.get("quantity"), dict) else {}
        session_time = row.get("session_time") or row.get("source_created_at")
        if not session_time and (
            row.get("span_id")
            or row.get("message_unit_id")
            or str(self.evidence_id).startswith(("ev_span:", "ev_turn:", "ev_raw:"))
        ):
            # Span/raw-turn cards historically carried their SESSION time
            # under the created_at / timestamp keys (contract axis-③ value
            # under an axis-④ name) — accept those ONLY for span-shaped
            # rows. Fact rows' created_at is ingest wallclock and must never
            # be read as a time value (§D4).
            session_time = row.get("created_at") or row.get("timestamp")
        return {
            "evidence_id": self.evidence_id,
            # NEW alongside `evidence_id` (not a replacement — every field
            # above is byte-identical to the ported source): `build_context()`
            # (this task's new facade code, §6.2 skeleton) indexes rows by
            # `r["id"]`, not `r["evidence_id"]`. Adding an alias key is
            # strictly additive; nothing that reads `evidence_id` breaks.
            "id": self.evidence_id,
            "fact_id": row.get("fact_id") or row.get("id"),
            "support_text": _query_centered_excerpt(
                _reader_source_text(row),
                query,
                _READER_TEXT_LIMIT,
            ),
            "source_session_id": row.get("session_id") or row.get("source_session_id"),
            "source_turn_id": row.get("turn_id") or row.get("source_turn_id"),
            "source_role": row.get("role") or row.get("source_role") or "user",
            "session_time": session_time,
            "document_time": row.get("document_time"),
            "occurred_start": row.get("occurred_start") or row.get("event_date"),
            "valid_from": row.get("valid_from"),
            "valid_until": row.get("valid_until") or row.get("valid_to"),
            "status": row.get("status"),
            "time_source": row.get("time_source"),
            "source_type": row.get("source_type"),
            "label": row.get("predicate_raw") or row.get("label"),
            "value": quantity.get("value"),
            "unit": quantity.get("unit"),
        }


class EvidenceStore:
    """Accumulates evidence rows ingested from tool calls during a single
    context-assembly pass, deduplicating by canonical id/alias and ranking
    for card projection. `max_state_cards` replaces the source's module
    constant `_MAX_STATE_CARDS` (default 24, unchanged) as a constructor
    default."""

    def __init__(self, *, max_state_cards: int = _DEFAULT_MAX_STATE_CARDS) -> None:
        self.records: dict[str, EvidenceRecord] = {}
        self.alias_to_id: dict[str, str] = {}
        self.observations: list[dict[str, Any]] = []
        self.max_state_cards = max_state_cards

    def resolve(self, value: str) -> str:
        return self.alias_to_id.get(str(value), str(value))

    def ingest(self, tool: str, args: dict[str, Any], payload: Any, step: int) -> dict[str, Any]:
        """R12: `payload` is an already-parsed value (dict/list/scalar), not
        `stdout: str`. See module docstring."""
        rows = _candidate_rows(payload)
        if (
            not rows
            and isinstance(payload, dict)
            and any(
                key in payload
                for key in ("value", "result", "display_value", "days", "operator", "calculation_trace")
            )
        ):
            rows = [{
                **payload,
                "id": f"derived_{tool}_{step}_{len(self.observations)}",
                "evidence_id": f"ev_derived:{tool}:{step}:{len(self.observations)}",
                "support_text": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                "source_type": "derived_runtime",
                "role": "tool",
            }]
        new_ids: list[str] = []
        seen_ids: list[str] = []
        for row in rows:
            eid = _evidence_id(row)
            if not eid:
                continue
            canonical = self.resolve(eid)
            if canonical not in self.records:
                record = EvidenceRecord(
                    evidence_id=canonical,
                    raw=dict(row),
                    first_seen_step=step,
                    last_seen_step=step,
                )
                self.records[canonical] = record
                new_ids.append(canonical)
            else:
                record = self.records[canonical]
            record.merge(row, tool, step)
            for alias in record.aliases:
                self.alias_to_id[alias] = canonical
            seen_ids.append(canonical)

        observation = {
            "step": step,
            "tool": tool,
            "args": args,
            "returned_rows": len(rows),
            "evidence_rows": len(seen_ids),
            "new_evidence": len(new_ids),
            "duplicate_evidence": max(0, len(seen_ids) - len(new_ids)),
            "new_ids": new_ids,
            "seen_ids": seen_ids,
            # Source derived this from the raw stdout text (`_one_line(stdout,
            # 300)`) when parsing failed. There is no stdout here (R12) — a
            # `payload` that is None/empty with zero rows is flagged by a
            # fixed marker instead of a text snippet that no longer exists.
            "error": "" if (payload is not None or rows) else "empty_payload",
        }
        # `evidence_count` returns a deduplicated, date-ordered roster next to
        # its per-label groups. It rides in the observation rather than through
        # `_candidate_rows` on purpose: its entries are the same facts the
        # groups already ingested, so treating them as rows would add nothing
        # but a second pass over the same evidence ids. What the planner cannot
        # reconstruct from evidence records is the roster's *shape* — one line
        # per distinct fact, in date order, with the labels that matched — and
        # that shape is the whole point.
        if isinstance(payload, dict) and isinstance(payload.get("roster"), list):
            observation["roster"] = payload["roster"]
        self.observations.append(observation)
        return observation

    def ranked_records(
        self,
        *,
        preferred_ids: list[str] | None = None,
        newest_step: int | None = None,
        query: str = "",
        limit: int | None = None,
    ) -> list[EvidenceRecord]:
        """The selection/dedup/ranking algorithm, factored out of
        `compact_cards` so `sodamem.context.cards.project_answer_bundle` can
        reuse the exact same ranking without a second, parallel selector. `compact_cards` below is this method plus
        the `.compact()` projection, unchanged from the source method of the
        same name — this split is new structure, not a behavior change."""
        limit = self.max_state_cards if limit is None else limit
        preferred = [self.resolve(value) for value in (preferred_ids or [])]
        terms = _query_terms(query)

        def relevance(record: EvidenceRecord) -> int:
            text = (
                _support_text(record.raw)
                + " "
                + str(record.raw.get("predicate_raw") or "")
                + " "
                + json.dumps(record.raw.get("entity_roles") or {}, ensure_ascii=False)
            ).lower()
            return sum(term in text for term in terms)

        ranked = sorted(
            self.records.values(),
            key=lambda record: (
                record.evidence_id in preferred,
                relevance(record),
                bool(_identity_key(record.raw)),
                (record.raw.get("role") or record.raw.get("source_role")) == "user",
                newest_step is not None and record.last_seen_step == newest_step,
                record.expanded,
                record.last_seen_step,
                -record.first_seen_step,
            ),
            reverse=True,
        )
        selected: list[EvidenceRecord] = []
        selected_ids: set[str] = set()

        def add(record: EvidenceRecord) -> None:
            if record.evidence_id not in selected_ids and len(selected) < limit:
                selected.append(record)
                selected_ids.add(record.evidence_id)

        for eid in preferred:
            if eid in self.records:
                add(self.records[eid])

        identity_seen: set[str] = set()
        for record in ranked:
            identity = _identity_key(record.raw)
            if identity and identity not in identity_seen:
                add(record)
                identity_seen.add(identity)
        for record in ranked:
            add(record)
        return selected

    def compact_cards(
        self,
        *,
        preferred_ids: list[str] | None = None,
        newest_step: int | None = None,
        query: str = "",
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """The card-selection algorithm the assembly parity gate pins (see
        tests/test_context_assembly_parity.py): replaying the same tool
        payloads through `ingest()` and calling this must reproduce a known
        card list exactly."""
        selected = self.ranked_records(
            preferred_ids=preferred_ids, newest_step=newest_step, query=query, limit=limit,
        )
        return [record.compact() for record in selected]

    def reader_rows(self, evidence_ids: list[str] | None = None, query: str = "") -> list[dict[str, Any]]:
        """`evidence_ids=None` (new default; the source required an explicit
        list) returns rows for every currently-known record, in insertion
        order — the shape `build_context()`'s zero-arg `store.reader_rows()`
        call (§6.2 new-code skeleton) needs before any ranking/organizer has
        run."""
        if evidence_ids is None:
            evidence_ids = list(self.records.keys())
        rows: list[dict[str, Any]] = []
        seen = set()
        # Content-level dedup: a fact row and its source span arrive under
        # different ids but render the same text — 0706 audit: 31% of
        # full-pool reader rows / 26% of prompt chars were same-text
        # re-renders. Key on (session, role, normalized text prefix); on
        # collision keep the metadata-richer row (occurred_start / quantity)
        # in the earlier row's position.
        content_slot: dict[tuple, int] = {}
        for raw_id in evidence_ids:
            eid = self.resolve(raw_id)
            if eid in seen or eid not in self.records:
                continue
            seen.add(eid)
            row = self.records[eid].reader_evidence(query)
            if not row.get("support_text"):
                continue
            ckey = (
                str(row.get("source_session_id") or ""),
                str(row.get("source_role") or ""),
                re.sub(r"\W+", " ", str(row["support_text"]).lower()).strip()[:400],
            )
            slot = content_slot.get(ckey)
            if slot is None:
                content_slot[ckey] = len(rows)
                rows.append(row)
                continue
            kept = rows[slot]
            if (row.get("occurred_start") or row.get("value") is not None) and not (
                kept.get("occurred_start") or kept.get("value") is not None
            ):
                rows[slot] = row
        return rows

    def augment_reader_ids(
        self,
        evidence_ids: list[str],
        query: str,
        *,
        supplemental_limit: int = 6,
    ) -> list[str]:
        """Add high-relevance source evidence already retrieved but not selected."""
        selected = list(dict.fromkeys(self.resolve(value) for value in evidence_ids))
        selected_set = set(selected)
        query_terms = _query_terms(query)
        selected_terms: set[str] = set()
        for evidence_id in selected:
            record = self.records.get(evidence_id)
            if record is not None:
                selected_terms.update(_query_terms(
                    _support_text(record.raw)
                    + " "
                    + json.dumps(record.raw.get("entity_roles") or {}, ensure_ascii=False)
                ))

        def score(record: EvidenceRecord) -> tuple[int, int, int, int]:
            text = (
                _reader_source_text(record.raw)
                + " "
                + str(record.raw.get("predicate_raw") or "")
                + " "
                + json.dumps(record.raw.get("entity_roles") or {}, ensure_ascii=False)
            ).lower()
            query_overlap = sum(term in text for term in query_terms)
            selected_overlap = sum(term in text for term in selected_terms)
            user_source = int(
                (record.raw.get("role") or record.raw.get("source_role") or "user")
                == "user"
            )
            exact_quantity = int(
                bool(re.search(r"\b\d[\d,]*(?:\.\d+)?\b", _reader_source_text(record.raw)))
            )
            return query_overlap + selected_overlap, user_source, exact_quantity, query_overlap

        candidates = [
            record
            for record in self.records.values()
            if record.evidence_id not in selected_set and score(record)[0] > 0
        ]
        candidates.sort(key=score, reverse=True)
        selected.extend(record.evidence_id for record in candidates[:supplemental_limit])
        return selected


__all__ = ["EvidenceStore", "EvidenceRecord"]
