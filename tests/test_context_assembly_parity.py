"""Assembly-equivalence gate: replay real recorded tool-call payloads through
`sodamem.context.store.EvidenceStore` and assert its `compact_cards()` output
matches a frozen oracle byte-for-byte.

This goes beyond the pure pytest/lint-imports gates: it checks the *behavior*
of the assembly path — not just its import graph and unit tests — using real
agent traces rather than hand-written fixtures.

Fixture data: `~/Desktop/LongMemEval-ingest/archived_runs/
ans_S500_live_fixbatch_0706/agent_traces/*.json` — S500 live run trace
files, NOT part of this repo (recorded on a sibling checkout,
`~/Desktop/LongMemEval-ingest`, per the user's own memory notes: "stores
归档 Desktop/LongMemEval-ingest"). This directory is a specific machine's
local artifact, not something `git clone` of this repo reproduces —
`skipif` when absent, per every other external-corpus test in this codebase
(see e.g. `tests/gates/*` `importorskip` patterns).

Frozen oracle: `_OldEvidenceStore`/`_OldEvidenceRecord` below are a literal
snapshot of the evidence-store behavior as it existed on 2026-07-19, together
with their `_candidate_rows`/`_evidence_id`/`_aliases`/`_support_text`/
`_query_terms`/`_identity_key`/`_one_line`/`_parse_stdout` dependencies. It is
a golden reference held inline rather than an import: pulling the original
module in would drag a full LLM-provider stack into a gate that must stay
zero-LLM and instantly runnable. This block is intentionally NEVER updated —
its only job is to freeze the exact behavior `sodamem/context/store.py` is
required to reproduce, so later edits cannot silently invalidate what this
gate proved.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from sodamem.context.store import EvidenceStore as _NewEvidenceStore

FIXTURE_DIR = Path.home() / "Desktop" / "LongMemEval-ingest" / "archived_runs" / "ans_S500_live_fixbatch_0706" / "agent_traces"
TRACE_LIMIT = 20

pytestmark = pytest.mark.skipif(
    not FIXTURE_DIR.is_dir(),
    reason=(
        f"assembly-equivalence fixture directory not found on this machine: {FIXTURE_DIR} "
        "(S500 live-run agent traces live outside this repo on the machine that recorded "
        "them; this gate is a machine-local supplement to the design's zero-fixture Step 4-6 "
        "checks, not a required CI gate)"
    ),
)


# ---------------------------------------------------------------------------
# Frozen oracle — DO NOT "fix" or refactor this to match
# sodamem/context/store.py. Divergence
# between this block and store.py IS the test signal.
# ---------------------------------------------------------------------------

_CARD_TEXT_LIMIT = 220
_MAX_STATE_CARDS = 24


def _old_one_line(value: Any, limit: int = _CARD_TEXT_LIMIT) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _old_parse_stdout(stdout: str) -> Any:
    text = str(stdout or "").strip()
    if not text:
        return None
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


def _old_candidate_rows(payload: Any) -> list[dict[str, Any]]:
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


def _old_evidence_id(row: dict[str, Any]) -> str:
    explicit = row.get("evidence_id")
    if explicit:
        return str(explicit)
    if row.get("fact_id"):
        return f"ev_fact:{row['fact_id']}"
    if row.get("span_id"):
        return f"ev_span:{row['span_id']}"
    raw_id = row.get("id") or row.get("memory_id") or row.get("event_id")
    return str(raw_id or "")


def _old_aliases(row: dict[str, Any], evidence_id: str) -> set[str]:
    values = {
        evidence_id,
        str(row.get("id") or ""),
        str(row.get("memory_id") or ""),
        str(row.get("fact_id") or ""),
        str(row.get("span_id") or ""),
        str(row.get("event_id") or ""),
    }
    return {value for value in values if value}


def _old_support_text(row: dict[str, Any]) -> str:
    return str(
        row.get("extracted_support_text")
        or row.get("support_text")
        or row.get("content")
        or row.get("text")
        or row.get("snippet")
        or row.get("content_preview")
        or ""
    )


def _old_query_terms(value: str) -> set[str]:
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


def _old_identity_key(row: dict[str, Any]) -> str:
    roles = row.get("entity_roles")
    if not isinstance(roles, dict):
        return ""
    parts = []
    for role, raw_value in sorted(roles.items()):
        if role == "subject":
            continue
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        normalized = sorted(_old_one_line(value, 100).lower() for value in values if value)
        if normalized:
            parts.append(f"{role}:{'|'.join(normalized)}")
    return ";".join(parts)


@dataclass
class _OldEvidenceRecord:
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
        self.aliases.update(_old_aliases(row, self.evidence_id))
        self.expanded = self.expanded or tool in {
            "inspect", "result", "inspect_session", "session",
        }

    def compact(self) -> dict[str, Any]:
        row = self.raw
        quantity = row.get("quantity")
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
                    entity_values.append(f"{role}={_old_one_line(value, 70)}")
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
                "support": _old_one_line(_old_support_text(row)),
                "predicate": _old_one_line(row.get("predicate_raw"), 140),
                "entities": "|".join(entity_values)[:180],
                "quantity": quantity,
                "date": (
                    row.get("occurred_start")
                    or row.get("event_date")
                    or row.get("valid_from")
                    or row.get("session_time")
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


class _OldEvidenceStore:
    def __init__(self) -> None:
        self.records: dict[str, _OldEvidenceRecord] = {}
        self.alias_to_id: dict[str, str] = {}
        self.observations: list[dict[str, Any]] = []

    def resolve(self, value: str) -> str:
        return self.alias_to_id.get(str(value), str(value))

    def ingest(self, tool: str, args: dict[str, Any], stdout: str, step: int) -> dict[str, Any]:
        payload = _old_parse_stdout(stdout)
        rows = _old_candidate_rows(payload)
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
            eid = _old_evidence_id(row)
            if not eid:
                continue
            canonical = self.resolve(eid)
            if canonical not in self.records:
                record = _OldEvidenceRecord(
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
        self.observations.append({"step": step, "tool": tool})
        return {"new_ids": new_ids, "seen_ids": seen_ids}

    def compact_cards(
        self,
        *,
        preferred_ids: list[str] | None = None,
        newest_step: int | None = None,
        query: str = "",
        limit: int = _MAX_STATE_CARDS,
    ) -> list[dict[str, Any]]:
        preferred = [self.resolve(value) for value in (preferred_ids or [])]
        terms = _old_query_terms(query)

        def relevance(record: _OldEvidenceRecord) -> int:
            text = (
                _old_support_text(record.raw)
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
                bool(_old_identity_key(record.raw)),
                (record.raw.get("role") or record.raw.get("source_role")) == "user",
                newest_step is not None and record.last_seen_step == newest_step,
                record.expanded,
                record.last_seen_step,
                -record.first_seen_step,
            ),
            reverse=True,
        )
        selected: list[_OldEvidenceRecord] = []
        selected_ids: set[str] = set()

        def add(record: _OldEvidenceRecord) -> None:
            if record.evidence_id not in selected_ids and len(selected) < limit:
                selected.append(record)
                selected_ids.add(record.evidence_id)

        for eid in preferred:
            if eid in self.records:
                add(self.records[eid])

        identity_seen: set[str] = set()
        for record in ranked:
            identity = _old_identity_key(record.raw)
            if identity and identity not in identity_seen:
                add(record)
                identity_seen.add(identity)
        for record in ranked:
            add(record)
        return [record.compact() for record in selected]


# ---------------------------------------------------------------------------
# Replay harness
# ---------------------------------------------------------------------------

def _trace_files() -> list[Path]:
    return sorted(FIXTURE_DIR.glob("*.json"))[:TRACE_LIMIT]


def _replay(trace: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Feed one trace's recorded `cli_tools` calls through both stores in
    the identical (tool, args, step) order, differing only in whether the
    tool payload arrives as raw stdout text (old) or already-parsed (new,
    R12's `payload: Any`). Returns (old_cards, new_cards)."""
    old_store = _OldEvidenceStore()
    new_store = _NewEvidenceStore()
    question = str(trace.get("question") or "")
    for step, call in enumerate(trace.get("cli_tools") or []):
        tool = call.get("tool") or ""
        args = call.get("args") or {}
        stdout = call.get("stdout") or ""
        old_store.ingest(tool, args, stdout, step)
        payload = _old_parse_stdout(stdout)  # the in-process caller's job now, not ingest()'s
        new_store.ingest(tool=tool, args=args, payload=payload, step=step)
    old_cards = old_store.compact_cards(query=question)
    new_cards = new_store.compact_cards(query=question)
    return old_cards, new_cards


@pytest.mark.parametrize("trace_path", _trace_files(), ids=lambda p: p.stem)
def test_compact_cards_matches_rig_oracle(trace_path: Path):
    trace = json.loads(trace_path.read_text())
    old_cards, new_cards = _replay(trace)
    old_json = json.dumps(old_cards, sort_keys=True, ensure_ascii=False)
    new_json = json.dumps(new_cards, sort_keys=True, ensure_ascii=False)
    assert new_json == old_json, (
        f"{trace_path.name}: ported compact_cards() diverged from the frozen rig oracle\n"
        f"old ({len(old_cards)} cards): {old_json[:2000]}\n"
        f"new ({len(new_cards)} cards): {new_json[:2000]}"
    )
