"""Card selection + text rendering, and the answer-evidence-bundle projector.

Also holds the glue for the `build_context()` facade's `compact_cards(...)`
call shape (§6.2 skeleton).

Two jobs live in this one module deliberately. Evidence selection used to
exist as two independently written implementations of the same job — picking
the most useful evidence for a downstream reader. Rather than keep a second
selection algorithm here, `project_answer_bundle` below reuses
`EvidenceStore.ranked_records` (the exact ranking `compact_cards` uses) via a
scratch `EvidenceStore` built from `eligible_evidence`. The second algorithm's
term-coverage/dedup variant is gone on purpose — this is a consolidation, not
an oversight.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sodamem.context.store import EvidenceStore
from sodamem.memory._shared import _tokenize
from sodamem.models import AnswerEvidenceBundle
from sodamem.prompts.reader import DEFAULT_ANSWER_CONSTRAINTS

# ---------------------------------------------------------------------------
# compact_cards(): the build_context() facade's selection + rendering step.
# ---------------------------------------------------------------------------


def compact_cards(
    store: EvidenceStore,
    *,
    preferred_ids: list[str] | None = None,
    newest_step: int | None = None,
    query: str = "",
    token_budget: int | None = None,
) -> tuple[str, list[dict[str, Any]], list[str]]:
    """`build_context()`'s card-selection + text-rendering step.

    Two layers, one selector — not a parallel second one:

    1. Selection: `store.compact_cards(...)`. This is the assembly parity
       gate's target (`tests/test_context_assembly_parity.py`): replaying the
       same tool payloads through `EvidenceStore.ingest()` and calling this
       method must reproduce a known card list exactly.
    2. Rendering: turns the selected card list into a flat prompt string
       outside the planner loop. Renders to newline-delimited text, stopping
       once `token_budget` (approximated as `token_budget * 4` characters —
       the same order-of-magnitude token/char ratio used elsewhere for token
       accounting) is exhausted. `token_budget=None` renders every selected
       card.
    """
    cards = store.compact_cards(preferred_ids=preferred_ids, newest_step=newest_step, query=query)
    text, rendered = _render_cards(cards, token_budget=token_budget)
    # T8-review Critical fix: citations/evidence derive from the RENDERED subset,
    # never the pre-truncation list — a citation for evidence absent from the
    # text is a lie the reader will repeat downstream.
    citations = [str(card["evidence_id"]) for card in rendered if card.get("evidence_id")]
    return text, rendered, citations


def _render_cards(
    cards: list[dict[str, Any]], *, token_budget: int | None
) -> tuple[str, list[dict[str, Any]]]:
    """Returns (text, included_cards): the exact subset that made it into the
    text, so callers can keep citations honest under truncation."""
    char_budget = None if token_budget is None else max(0, int(token_budget) * 4)
    lines: list[str] = []
    included: list[dict[str, Any]] = []
    used = 0
    for card in cards:
        line = "- " + " ".join(f"{key}={value}" for key, value in card.items())
        if char_budget is not None and used + len(line) > char_budget:
            break
        lines.append(line)
        included.append(card)
        used += len(line) + 1
    return "\n".join(lines), included


# ---------------------------------------------------------------------------
# project_answer_bundle(): AnswerEvidenceBundle projection.
# Ported from client.py:823-892 (`_project_answer_bundle`) + client.py:993-1081
# (`_query_coverage`, which rides along per the map: "供 AnswerEvidenceBundle.
# answer_notes，随 _project_answer_bundle 一起搬").
# ---------------------------------------------------------------------------

# Role vocabulary for picking a human-readable `label` out of an evidence
# row's `entity_roles`. Source (client.py:849) hard-coded this as a bare
# tuple inline in `_project_answer_bundle`'s body — a riding-along
# fix: "角色词表元组配置化——本任务新增一个 RoleVocabulary 轻量结构". Same
# default role names, now constructor-injectable instead of a module
# constant baked into the function body.
_DEFAULT_ROLES = (
    "airline", "provider", "merchant", "item", "person", "location",
    "destination", "company",
)


@dataclass(frozen=True)
class RoleVocabulary:
    roles: tuple[str, ...] = _DEFAULT_ROLES


def project_answer_bundle(
    query: str,
    eligible_evidence: list[dict[str, Any]],
    *,
    evidence_limit: int = 14,
    role_vocabulary: RoleVocabulary | None = None,
) -> AnswerEvidenceBundle:
    """Project raw retrieval evidence into the I4 gate bundle
    (`sodamem.models.AnswerEvidenceBundle`) a reader consumes.

    Deletions vs. source:
    - No `answer_task` dict (source :828-837, assigned :887). `models/
      __init__.py`'s `AnswerEvidenceBundle` dropped the field in Task 1;
      this is the paired call-site cleanup — constructing a dict for a
      field the dataclass no longer has would just be dead code.
    - `question_date` (source's only use for it was building `answer_task`)
      is dropped from the signature entirely, not kept as an unused
      parameter — the same "no dead parameter on a public signature"
      standard applied elsewhere in this package (on
      `run_autonomous_agent`'s `oracle_context`).
    - `evidence_limit` (source :841: call-time
      `_cfg.get('retrieve','answer.evidence_limit',14)`) is a plain keyword
      parameter with the source's literal default (14), not a config-file
      read — the composition root (Task 10 / `build_context()` callers)
      supplies it.
    """
    role_vocabulary = role_vocabulary or RoleVocabulary()
    scratch = EvidenceStore(max_state_cards=evidence_limit)
    for step, row in enumerate(eligible_evidence):
        scratch.ingest(tool="eligible_evidence", args={}, payload=row, step=step)
    ranked = scratch.ranked_records(query=query, limit=evidence_limit)

    key_evidence = []
    for record in ranked:
        ev = record.raw
        qty = ev.get("quantity") or {}
        label = None
        roles = ev.get("entity_roles") or {}
        for role in role_vocabulary.roles:
            if roles.get(role):
                label = roles.get(role)
                if isinstance(label, list):
                    label = label[0]
                break
        key_evidence.append({
            "label": label or ev.get("predicate_raw", "")[:80],
            "value": qty.get("value"),
            "unit": qty.get("unit", ""),
            "source_type": ev.get("source_type"),
            "confidence": ev.get("confidence"),
            "confidence_reason": ev.get("confidence_reason"),
            "support_text": ev.get("support_text", ""),
            "extracted_support_text": ev.get("extracted_support_text", ""),
            "fact_id": ev.get("fact_id"),
            "source_span_id": (ev.get("source_span_ids") or [None])[0],
            "source_trace_id": (ev.get("source_trace_ids") or ev.get("source_span_ids") or [None])[0],
            "source_role": ev.get("source_role", "user"),
            "source_session_id": ev.get("source_session_id"),
            "source_turn_id": ev.get("source_turn_id"),
            "session_time": ev.get("session_time"),
            "occurred_start": ev.get("occurred_start"),
        })

    # Insufficient-evidence signal lets the answer agent abstain consistently.
    result: dict[str, Any] = {"insufficient": True} if not key_evidence else {}

    # D26 (source comment, preserved): no category-conditioned abstention.
    # Coverage is computed and surfaced as an audit/answer note; the
    # decision to abstain belongs to the answerer (Task 10), not to the
    # memory core.
    coverage = _query_coverage(query, key_evidence)

    return AnswerEvidenceBundle(
        query=query,
        result=result,
        key_evidence=key_evidence,
        answer_notes=[{"type": "query_coverage", **coverage}] if coverage.get("terms") else [],
        answer_constraints=list(DEFAULT_ANSWER_CONSTRAINTS),
    )


_COVERAGE_STOP = {
    "what", "when", "where", "which", "whose", "whom", "how", "why",
    "many", "much", "have", "been", "with", "from", "into", "about",
    "before", "after", "between", "during", "since", "until", "while",
    "would", "could", "should", "might", "must", "does", "doing",
    "this", "that", "these", "those", "there", "here", "they",
    "their", "them", "your", "yours", "mine", "ours",
    "also", "just", "even", "very", "really", "quite", "still",
    "ever", "never", "always", "often", "sometimes", "today",
    "year", "years", "month", "months", "week", "weeks", "day", "days",
    "total", "first", "last", "most", "recent", "currently", "current",
    "type", "kind", "thing", "things", "stuff",
    "tell", "give", "show", "describe", "explain",
    "name", "names", "list",
    "did", "was", "were", "are", "is", "am",
    "for", "the", "and", "any", "all", "out",
    "based", "looking", "wondering", "thinking", "planning",
    "took", "taken", "take", "taking", "make", "made", "making",
    "currently",
}


def _query_coverage(query: str, key_evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Coverage signal for ABS-style topic mismatches.

    Ported byte-for-byte from `client.py:993-1081`. Two layers:
    - General terms: length >= 4 content words, used as a coarse score.
    - High-info terms: capitalised words (after the first word) AND
      uncommon long words. These are the topic anchors — if the user wrote
      "iPad", "Holiday Market", "Master's degree", "vintage films", they are
      asking about that specific thing. When NONE of them appear in any
      evidence support_text, abstention is the right move.
    """
    text = " ".join(
        (ev.get("support_text") or "") + " " + (ev.get("predicate_raw") or "")
        for ev in key_evidence
    ).lower()
    # General coverage
    terms = []
    seen = set()
    for tok in _tokenize(query):
        if len(tok) < 4 or tok in _COVERAGE_STOP or tok.isdigit():
            continue
        if tok in seen:
            continue
        seen.add(tok)
        terms.append(tok)
    matched = [t for t in terms if t in text]
    missing = [t for t in terms if t not in text]
    # High-info anchor terms: proper-noun-style tokens in the original
    # query. We accept words that have an uppercase letter NOT at the
    # absolute start of the question (so the first word's capital does not
    # count) AND that are not common WH-words. This captures iPad, Holiday
    # Market, Master's, vintage-cased proper nouns, etc., while ignoring
    # ordinary common verbs like "attend" or "bought".
    words = re.findall(r"[A-Za-z][A-Za-z'\-]+", query)
    anchors = []
    seen_anchor = set()
    for i, w in enumerate(words):
        lw = w.lower()
        if lw in _COVERAGE_STOP or len(lw) < 3:
            continue
        has_inner_upper = any(c.isupper() for c in w[1:])
        is_caps_non_first = (i > 0 and w[0].isupper())
        if not (has_inner_upper or is_caps_non_first):
            continue
        if lw in seen_anchor:
            continue
        seen_anchor.add(lw)
        anchors.append(lw)
    for term in terms:
        if len(term) >= 7 and term not in seen_anchor:
            seen_anchor.add(term)
            anchors.append(term)
    anchor_matched = [a for a in anchors if a in text]
    score = len(matched) / len(terms) if terms else 1.0
    anchor_score = len(anchor_matched) / len(anchors) if anchors else 1.0
    return {
        "score": score,
        "anchor_score": anchor_score,
        "terms": terms,
        "matched": matched,
        "missing": missing,
        "anchors": anchors,
        "anchor_matched": anchor_matched,
        "anchor_missing": [a for a in anchors if a not in text],
    }


__all__ = ["compact_cards", "project_answer_bundle", "RoleVocabulary"]
