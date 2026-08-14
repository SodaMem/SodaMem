"""Heuristic question intents for answer-side tool forcing (no LLM)."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class QuestionIntent:
    temporal_order: bool = False
    temporal_recent: bool = False
    temporal_relative: bool = False
    value_lookup: bool = False
    comparison: bool = False
    current_attribute: bool = False
    aggregation: bool = False  # how many / how much / total across sessions
    preference: bool = False
    abstention_sensitive: bool = False
    previous_value: bool = False
    new_attribute: bool = False

    @property
    def needs_timeline(self) -> bool:
        return self.temporal_order or self.temporal_recent or self.temporal_relative

    @property
    def needs_enumeration(self) -> bool:
        return (
            self.temporal_order
            or self.comparison
            or self.aggregation
        )

    @property
    def needs_wide_recall(self) -> bool:
        """Extra search / raw-search passes beyond the default query."""
        return (
            self.aggregation
            or self.needs_timeline
            or self.value_lookup
            or self.preference
            or self.temporal_relative
        )


_ORDER = re.compile(
    r"\b(order of|in what order|sequence|chronolog|from earliest|"
    r"earliest to latest|from oldest|oldest to newest|before today)\b",
    re.I,
)
_RECENT = re.compile(
    r"\b(most recently|most recent|newest|last (?:one|time)|"
    r"started? using most recently)\b|"
    r"(?<!to )(?<!earliest to )\blatest\b",
    re.I,
)
_RELATIVE = re.compile(
    r"\b(\d+\s+(day|week|month|year)s?\s+ago|a\s+week\s+ago|"
    r"(two|three|four|five|six)\s+months?\s+ago|"
    r"yesterday|(last|past)\s+(weekend|saturday|sunday|week|month)|"
    r"two months ago|past (few )?(months|weeks|days)|"
    r"in the (past|last)\s+\w+|this year|since the start)\b",
    re.I,
)
_VALUE = re.compile(
    r"\b(worth|price|cost|paid|discount|percent|%|speed|mbps|gbps|"
    r"how much|painting|sunset)\b",
    re.I,
)
_COMPARE = re.compile(
    r"\b(compared to|higher|lower|more than|less than|vs\.?|versus)\b",
    re.I,
)
_CURRENT = re.compile(
    r"\b(current|now|these days|currently)\b",
    re.I,
)
_NEW = re.compile(
    r"\b(new (internet )?plan|my new |upgraded? to|switched to|just (got|bought))\b",
    re.I,
)
_PREVIOUS = re.compile(
    r"\b(previous|prior|old |used to|before (i |the |my )|"
    r"personal best|former)\b",
    re.I,
)
_AGG = re.compile(
    r"\b(how many|how much|total (money|amount|cost|spent)?|"
    r"in total|altogether|combined|sum of|"
    r"number of|count of|spent on|earn(ed)? to redeem)\b",
    re.I,
)
_PREF = re.compile(
    r"\b(recommend|recommendation|suggest|suggestion|any tips|"
    r"what should i|can you recommend|for me to watch|"
    r"interesting .+ around me|tips\b)\b",
    re.I,
)
_ABSTAIN = re.compile(
    r"\b(how much will i save|instead of|which university|"
    r"poster|not enough|did i receive)\b",
    re.I,
)


def classify_intent(question: str) -> QuestionIntent:
    q = question or ""
    return QuestionIntent(
        temporal_order=bool(_ORDER.search(q)),
        temporal_recent=bool(_RECENT.search(q)),
        temporal_relative=bool(_RELATIVE.search(q)),
        value_lookup=bool(_VALUE.search(q)),
        comparison=bool(_COMPARE.search(q)),
        current_attribute=bool(_CURRENT.search(q)),
        aggregation=bool(_AGG.search(q)),
        preference=bool(_PREF.search(q)),
        abstention_sensitive=bool(_ABSTAIN.search(q)),
        previous_value=bool(_PREVIOUS.search(q)),
        new_attribute=bool(_NEW.search(q)),
    )


def question_from_objective(objective: str) -> str:
    prefix = "Gather sufficient evidence to answer: "
    if (objective or "").startswith(prefix):
        return objective[len(prefix) :]
    return objective or ""


def aggregation_labels(question: str) -> list[str]:
    """Noun-ish labels for browser_count_evidence."""
    stop = {
        "how", "many", "much", "the", "a", "an", "i", "my", "me", "do", "did",
        "have", "has", "had", "been", "was", "were", "is", "are", "to", "of",
        "in", "on", "at", "for", "from", "with", "and", "or", "total", "money",
        "past", "last", "few", "this", "year", "month", "months", "weeks",
        "days", "currently", "including", "one", "that", "which", "what",
        "need", "needs", "earned", "earn", "since", "start", "before",
        "making", "different", "types", "type", "something",
    }
    words = re.findall(r"[A-Za-z][A-Za-z0-9+\-]{2,}", question or "")
    labels = [w for w in words if w.lower() not in stop][:8]
    return labels or [question.strip()[:60] or "items"]
