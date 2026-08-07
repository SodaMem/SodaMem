"""Planner-only Hot/Warm/Folded evidence-card projection.

The store remains the source of truth.  This module only controls how much of
an already-selected compact card is repeated in successive Planner messages.
Lifecycle identity is always the canonical evidence id; model-facing aliases
are applied later by :mod:`sodamem.answer.loop`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable


def cards_chars(cards: list[dict[str, Any]]) -> int:
    """Return the exact compact-JSON character count for a card list."""
    return len(json.dumps(cards, ensure_ascii=False, separators=(",", ":")))


@dataclass(frozen=True)
class ProjectionResult:
    cards: list[dict[str, Any]]
    hot_ids: list[str]
    warm_ids: list[str]
    folded_ids: list[str]
    rehydrated_ids_consumed: list[str]
    full_card_chars: int
    projected_card_chars: int

    def telemetry(self, *, enabled: bool) -> dict[str, Any]:
        return {
            "enabled": enabled,
            "hot_count": len(self.hot_ids),
            "warm_count": len(self.warm_ids),
            "folded_count": len(self.folded_ids),
            "hot_ids": self.hot_ids,
            "warm_ids": self.warm_ids,
            "folded_ids": self.folded_ids,
            "rehydrated_ids_consumed": self.rehydrated_ids_consumed,
            "full_card_chars": self.full_card_chars,
            "projected_card_chars": self.projected_card_chars,
        }


@dataclass
class PlannerContextOffload:
    """Per-question delivery state for Planner evidence cards."""

    seen_ids: set[str] = field(default_factory=set)
    pending_rehydration_ids: set[str] = field(default_factory=set)

    def queue_rehydration(self, ids: Iterable[str]) -> None:
        self.pending_rehydration_ids.update(str(value) for value in ids if value)

    def project(
        self,
        cards: list[dict[str, Any]],
        *,
        supported_ids: Iterable[str] = (),
        unresolved_conflict_ids: Iterable[str] = (),
        selected_ids: Iterable[str] = (),
    ) -> ProjectionResult:
        supported = set(supported_ids)
        protected = set(unresolved_conflict_ids) | set(selected_ids)
        output: list[dict[str, Any]] = []
        hot: list[str] = []
        warm: list[str] = []
        folded: list[str] = []
        rehydrated: list[str] = []

        for card in cards:
            evidence_id = str(card.get("evidence_id") or "")
            is_rehydrated = evidence_id in self.pending_rehydration_ids
            if evidence_id not in self.seen_ids or is_rehydrated or evidence_id in protected:
                output.append(dict(card))
                hot.append(evidence_id)
                if is_rehydrated:
                    rehydrated.append(evidence_id)
            elif evidence_id in supported:
                output.append({"evidence_id": evidence_id})
                folded.append(evidence_id)
            else:
                output.append({key: value for key, value in card.items() if key != "support"})
                warm.append(evidence_id)

        return ProjectionResult(
            cards=output,
            hot_ids=hot,
            warm_ids=warm,
            folded_ids=folded,
            rehydrated_ids_consumed=rehydrated,
            full_card_chars=cards_chars(cards),
            projected_card_chars=cards_chars(output),
        )

    def commit(self, result: ProjectionResult) -> None:
        """Commit only after the corresponding Planner message was assembled."""
        self.seen_ids.update(result.hot_ids)
        self.seen_ids.update(result.warm_ids)
        self.seen_ids.update(result.folded_ids)
        self.pending_rehydration_ids.difference_update(result.rehydrated_ids_consumed)


def projection_protections(state: Any) -> tuple[set[str], set[str], set[str]]:
    """Extract deterministic protections without interpreting natural language."""
    supported = {
        evidence_id
        for claim in state.claims.values()
        if claim.status == "supported"
        for evidence_id in claim.evidence_ids
    }
    unresolved = {
        evidence_id
        for conflict in state.conflicts
        if not conflict.get("resolved")
        for evidence_id in conflict.get("evidence_ids") or []
    }
    return supported, unresolved, set(state.selected_evidence_ids)


__all__ = [
    "PlannerContextOffload",
    "ProjectionResult",
    "cards_chars",
    "projection_protections",
]
