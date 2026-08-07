"""Planner protocol types: `Claim`/`PlannerState` — the structured state a
`run_planner_loop` iteration accumulates and serializes back to the planner
LLM each step.

`PLANNER_PROTOCOL_VERSION` is the `"protocol"` key `PlannerState.compact()`
stamps into every payload the planner sees.

Two deliberate departures from the earlier design:

- `saw_search`/`saw_compute` booleans, and the two serialized keys they added
  to `compact()`, are DELETED,
  not replaced. They were a two-tool special case — only `browser_search`/
  `compute` ever set them — duplicating what `sodamem.answer.rules`'s
  generic `tools_seen: set[str]` (owned by `loop.py`, not this module) now
  tracks for ANY tool the loop dispatches. `rules.check()`'s `TerminalRule`
  supersedes both roles the booleans played: the planner system prompt's
  "unless the runtime says it already ran" instruction is satisfied because
  the init search already populated `evidence_cards`/`search_history`
  before the planner's first real turn (see `loop.py`'s module docstring),
  and `_finalization_errors`'s old `if not state.saw_search: errors.append(
  ...)` check is replaced by `rules.check(..., is_final=True)`, called by
  the loop BEFORE `_finalization_errors` runs (R16: finalization validation
  stays in the loop; only the search-was-seen check moves to rules).
- `_MAX_CLAIMS`/`_MAX_OPEN_QUESTIONS`/`_MAX_CONFLICTS` (source module
  constants, 24/12/12) become `PlannerState` constructor fields of the same
  default values — the same "call-time global read -> caller-supplied
  parameter" cleanup spec §6.1 point 4 applies project-wide.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sodamem.context.store import EvidenceStore, _one_line

logger = logging.getLogger(__name__)

PLANNER_PROTOCOL_VERSION = "autonomous_compact_state_v1"
QUESTION_CLASSIFICATIONS = {
    "ordinary", "enumeration", "count", "sum", "comparison",
}


@dataclass
class Claim:
    claim_id: str
    statement: str
    evidence_ids: list[str]
    status: str = "supported"
    material: bool = True

    def compact(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "statement": self.statement,
            "evidence_ids": self.evidence_ids,
            "status": self.status,
            "material": self.material,
        }


@dataclass
class PlannerState:
    objective: str
    max_claims: int = 24
    max_open_questions: int = 12
    max_conflicts: int = 12
    claims: dict[str, Claim] = field(default_factory=dict)
    open_questions: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    search_history: list[dict[str, Any]] = field(default_factory=list)
    feedback: list[str] = field(default_factory=list)
    selected_evidence_ids: list[str] = field(default_factory=list)
    consecutive_zero_novelty: int = 0
    question_classification: dict[str, Any] | None = None
    attempted_tools: set[str] = field(default_factory=set)
    successful_tool_capabilities: set[str] = field(default_factory=set)
    # Bug #9 (0724): source PlannerState carried these and compact() exposed
    # them to the planner payload (:1494-1495); the port dropped both keys.
    saw_search: bool = False
    saw_compute: bool = False

    def apply_update(self, update: Any, evidence: EvidenceStore) -> None:
        if not isinstance(update, dict):
            return
        objective = _one_line(update.get("objective"), 500)
        if objective:
            self.objective = objective
        classification = update.get("question_classification")
        if isinstance(classification, dict):
            classification_type = str(classification.get("type") or "")
            if classification_type in QUESTION_CLASSIFICATIONS:
                self.question_classification = {
                    "type": classification_type,
                    "comparison_requires_count_or_sum": bool(
                        classification.get("comparison_requires_count_or_sum", False)
                    ) if classification_type == "comparison" else False,
                }
        for claim_id in update.get("retract_claim_ids") or []:
            self.claims.pop(str(claim_id), None)
        for row in update.get("upsert_claims") or []:
            if not isinstance(row, dict):
                continue
            claim_id = _one_line(row.get("claim_id"), 80)
            statement = _one_line(row.get("statement"), 500)
            if not claim_id or not statement:
                continue
            evidence_ids = []
            unresolved_ids = []
            for raw_id in row.get("evidence_ids") or []:
                eid = evidence.resolve(str(raw_id))
                if eid in evidence.records:
                    if eid not in evidence_ids:
                        evidence_ids.append(eid)
                else:
                    unresolved_ids.append(str(raw_id))
            if unresolved_ids:
                # No-silent-failures: a dropped id can silently downgrade a
                # supported claim to hypothesis, which removes it from the
                # reader's planner_supported_claims block entirely.
                logger.warning(
                    "claim %s: dropped %d unresolvable evidence id(s): %s",
                    claim_id, len(unresolved_ids), unresolved_ids[:3],
                )
            status = str(row.get("status") or "supported")
            if status not in {"supported", "disputed", "hypothesis"}:
                status = "hypothesis"
            if status == "supported" and not evidence_ids:
                status = "hypothesis"
            self.claims[claim_id] = Claim(
                claim_id=claim_id,
                statement=statement,
                evidence_ids=evidence_ids,
                status=status,
                material=bool(row.get("material", True)),
            )
        resolved = {
            _one_line(value.get("question") if isinstance(value, dict) else value, 500)
            for value in update.get("resolved_questions") or []
        }
        current = [
            row for row in self.open_questions
            if _one_line(row.get("question"), 500) not in resolved
        ]
        supplied = update.get("open_questions")
        if isinstance(supplied, list):
            current = []
            for row in supplied[: self.max_open_questions]:
                if isinstance(row, dict):
                    question = _one_line(row.get("question"), 500)
                    material = bool(row.get("material", True))
                else:
                    question = _one_line(row, 500)
                    material = True
                if question:
                    current.append({"question": question, "material": material})
        self.open_questions = current[: self.max_open_questions]
        conflicts = update.get("conflicts")
        if isinstance(conflicts, list):
            normalized = []
            for row in conflicts[: self.max_conflicts]:
                if not isinstance(row, dict):
                    continue
                ids = [
                    evidence.resolve(str(value))
                    for value in row.get("evidence_ids") or []
                    if evidence.resolve(str(value)) in evidence.records
                ]
                normalized.append({
                    "description": _one_line(row.get("description"), 500),
                    "evidence_ids": ids,
                    "resolved": bool(row.get("resolved")),
                    "resolution": _one_line(row.get("resolution"), 500),
                })
            self.conflicts = normalized

    def compact(
        self,
        evidence: EvidenceStore,
        step: int,
        max_steps: int,
        *,
        evidence_cards: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        preferred = []
        for claim in self.claims.values():
            preferred.extend(claim.evidence_ids)
        preferred.extend(self.selected_evidence_ids)
        return {
            "protocol": PLANNER_PROTOCOL_VERSION,
            "objective": self.objective,
            "question_classification": self.question_classification,
            "claims": [
                claim.compact()
                for claim in list(self.claims.values())[-self.max_claims :]
            ],
            "open_questions": self.open_questions,
            "conflicts": self.conflicts,
            "evidence_cards": (
                evidence.compact_cards(
                    preferred_ids=preferred,
                    newest_step=step - 1 if step else None,
                    query=self.objective,
                )
                if evidence_cards is None else evidence_cards
            ),
            # G1 planner-slim (default since the 0713 6-run verdict):
            # `signature` is a deterministic re-serialization of
            # tool+args kept ONLY for the in-memory duplicate-call check —
            # project it out of the planner-visible payload. In-memory rows
            # keep it (dup check unchanged).
            "search_history": [
                {k: v for k, v in row.items() if k != "signature"}
                for row in self.search_history[-10:]
            ],
            "runtime_feedback": self.feedback[-5:],
            "budget": {
                "step": step,
                "max_steps": max_steps,
                "remaining": max(0, max_steps - step),
                "evidence_count": len(evidence.records),
                "consecutive_zero_novelty": self.consecutive_zero_novelty,
            },
            "saw_search": self.saw_search,
            "saw_compute": self.saw_compute,
            "attempted_tools": sorted(self.attempted_tools),
            "successful_tool_capabilities": sorted(
                self.successful_tool_capabilities
            ),
        }


__all__ = [
    "PLANNER_PROTOCOL_VERSION",
    "QUESTION_CLASSIFICATIONS",
    "Claim",
    "PlannerState",
]
