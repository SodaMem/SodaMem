"""Deterministic set counting from browser_count_evidence rosters.

Soft path: inject a structured advisory for the reader.
Hard path: override the final answer when confidence is high.

Gated to true set-aggregation questions (how many X / total spent), not
single-slot metrics (points needed, plan speed) or abstention traps.
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

from sodamem_opt.intent import aggregation_labels, classify_intent
from sodamem_opt.timewords import resolve_time_window

_TLS = threading.local()

_PLAN_RE = re.compile(
    r"\b(plan(?:ning)?|thinking of|consider(?:ing)?|want to|going to|"
    r"would like|this weekend|next weekend|tomorrow|soon|i[' ]?ll|"
    r"i will|should add|looking forward)\b",
    re.I,
)
_ADVICE_RE = re.compile(r"\b(recommend|suggest|tips|ideas|options)\b", re.I)
_MONEY_RE = re.compile(
    r"(?:\$|usd\s*)(\d+(?:,\d{3})*(?:\.\d+)?)|(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:dollars|usd)\b",
    re.I,
)
_SET_COUNT_RE = re.compile(r"\bhow many\b", re.I)
_SET_SUM_RE = re.compile(
    r"\b(how much total|total money|total (?:amount|cost)|"
    r"spent on|how much .+ spent|total .+ spent)\b",
    re.I,
)
_NOT_SET_RE = re.compile(
    r"\b(need to earn|points? do i need|do i need|followers|"
    r"will i save|instead of|internet plan|personal best|"
    r"wake up|how much is the|worth in terms)\b",
    re.I,
)


def det_count_enabled() -> bool:
    return os.environ.get("SODAMEM_OPT_DET_COUNT", "1").strip() != "0"


def det_count_hard_enabled() -> bool:
    # Default OFF: hard integer override regressed previously-fixed misses
    # (net 23/46 vs prior 28/46). Soft advisory remains the safe path.
    return os.environ.get("SODAMEM_OPT_DET_COUNT_HARD", "0").strip() == "1"


@dataclass
class DetCountResult:
    mode: str  # count | sum
    value: float | int
    items: list[dict[str, Any]] = field(default_factory=list)
    confidence: str = "low"  # low | medium | high
    reason: str = ""
    advisory: str = ""
    answer_text: str = ""

    @property
    def should_override(self) -> bool:
        return (
            det_count_hard_enabled()
            and self.confidence == "high"
            and self.mode == "count"
            and isinstance(self.value, int)
            and self.value >= 1
        )


def is_set_aggregation(question: str) -> bool:
    """True only for enumerate-then-aggregate questions."""
    q = question or ""
    intent = classify_intent(q)
    if intent.preference or intent.abstention_sensitive:
        return False
    if _NOT_SET_RE.search(q):
        return False
    if _SET_COUNT_RE.search(q):
        return True
    if _SET_SUM_RE.search(q):
        return True
    return False


def aggregation_mode(question: str) -> Optional[str]:
    if not is_set_aggregation(question):
        return None
    if _SET_SUM_RE.search(question or ""):
        return "sum"
    if _SET_COUNT_RE.search(question or ""):
        return "count"
    return None


def collect_roster(evidence: Any) -> list[dict[str, Any]]:
    observations = getattr(evidence, "observations", None) or []
    for obs in reversed(list(observations)):
        tool = str(obs.get("tool") or "")
        if tool in {"browser_count_evidence", "count"} and obs.get("roster"):
            return list(obs["roster"])
    return []


def _parse_date(current_date: str) -> Optional[date]:
    m = re.match(r"(\d{4})[-/](\d{2})[-/](\d{2})", (current_date or "").strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _item_date(item: dict[str, Any]) -> Optional[date]:
    ed = item.get("event_date")
    if isinstance(ed, str) and re.match(r"\d{4}-\d{2}-\d{2}", ed):
        try:
            return date.fromisoformat(ed[:10])
        except ValueError:
            pass
    ep = item.get("occurred_epoch")
    if isinstance(ep, (int, float)) and ep > 0:
        from sodamem.memory._shared import _safe_fromtimestamp

        dt = _safe_fromtimestamp(ep)
        if dt is not None:
            return dt.date()
    return None


def _content(item: dict[str, Any]) -> str:
    return str(item.get("content") or "")


def _label_stems(question: str) -> list[str]:
    stems = []
    for lab in aggregation_labels(question):
        s = lab.lower()
        if len(s) >= 4:
            stems.append(s[: max(4, len(s) - 2)] if len(s) > 5 else s)
        elif len(s) >= 3:
            stems.append(s)
    # Question-type extras
    ql = (question or "").lower()
    for extra in (
        "bake", "wedding", "bike", "project", "tank", "furniture", "workshop",
        "album", "baby", "babies", "delivery", "art", "clothing", "property",
        "session", "jog", "event", "museum", "airline",
    ):
        if extra in ql:
            stems.append(extra)
    # dedupe
    out: list[str] = []
    seen: set[str] = set()
    for s in stems:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def filter_roster(
    roster: list[dict[str, Any]],
    question: str,
    *,
    current_date: str,
) -> list[dict[str, Any]]:
    window = resolve_time_window(question, current_date=current_date)
    stems = _label_stems(question)
    kept: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for item in roster:
        key = str(item.get("fact_id") or item.get("evidence_id") or "")
        text = _content(item)
        tl = text.lower()
        if not text.strip():
            continue
        if _PLAN_RE.search(tl) and not re.search(
            r"\b(yesterday|last |ago|on \d{4}|attended|bought|spent|baked|fixed)\b",
            tl,
        ):
            # Drop pure future-plan mentions.
            if re.search(r"\b(planning|thinking of|want to|going to)\b", tl):
                continue
        if _ADVICE_RE.search(tl) and not stems:
            continue
        if stems and not any(st in tl for st in stems):
            # Keep if any roster label overlaps stems.
            labels = [str(x).lower() for x in (item.get("labels") or [])]
            if not any(st in lab or lab in st for st in stems for lab in labels):
                continue
        if window is not None:
            d = _item_date(item)
            if d is not None:
                lo = date.fromisoformat(window["from_date"])
                hi = date.fromisoformat(window["to_date"])
                if d < lo or d > hi:
                    continue
        # Content-level near-dupe: same first 80 chars
        sig = re.sub(r"\s+", " ", tl)[:80]
        if key:
            if key in seen_keys:
                continue
            seen_keys.add(key)
        elif sig in seen_keys:
            continue
        else:
            seen_keys.add(sig)
        kept.append(item)
    return kept


def _extract_money(text: str) -> list[float]:
    vals: list[float] = []
    for m in _MONEY_RE.finditer(text or ""):
        raw = m.group(1) or m.group(2)
        if not raw:
            continue
        try:
            vals.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return vals


def compute_det_count(
    question: str,
    evidence: Any,
    *,
    current_date: str,
) -> Optional[DetCountResult]:
    if not det_count_enabled():
        return None
    mode = aggregation_mode(question)
    if mode is None:
        return None
    roster = collect_roster(evidence)
    if not roster:
        return DetCountResult(
            mode=mode,
            value=0,
            confidence="low",
            reason="no_count_roster",
            advisory=(
                "deterministic_set_count: no browser_count_evidence roster was "
                "available; do not invent a count."
            ),
        )
    filtered = filter_roster(roster, question, current_date=current_date)
    if mode == "count":
        n = len(filtered)
        conf = "low"
        if n >= 2 and n <= 12 and n >= max(1, len(roster) // 3):
            conf = "high"
        elif n >= 1:
            conf = "medium"
        lines = []
        for i, it in enumerate(filtered[:20], 1):
            d = it.get("event_date") or "?"
            snippet = re.sub(r"\s+", " ", _content(it))[:160]
            lines.append(f"{i}. [{d}] {snippet}")
        advisory = (
            "deterministic_set_count ( mechanized roster filter; prefer this "
            f"integer unless evidence clearly excludes an item):\n"
            f"mode=count value={n} raw_roster={len(roster)} "
            f"filtered={n} confidence={conf}\n"
            "included:\n" + ("\n".join(lines) if lines else "(none)")
        )
        answer = (
            f"{n}"
            if n
            else "0"
        )
        if n and lines:
            answer = f"{n}. Items counted:\n" + "\n".join(lines[:n])
        return DetCountResult(
            mode="count",
            value=n,
            items=filtered,
            confidence=conf,
            reason=f"filtered_{n}_of_{len(roster)}",
            advisory=advisory,
            answer_text=answer,
        )

    # sum mode — soft/medium only; hard override disabled for sums
    amounts: list[tuple[float, dict]] = []
    for it in filtered:
        monies = _extract_money(_content(it))
        if len(monies) == 1:
            amounts.append((monies[0], it))
        elif len(monies) > 1:
            # Prefer the largest mentioned once per fact (common "spent $X")
            amounts.append((max(monies), it))
    total = sum(a for a, _ in amounts)
    conf = "medium" if len(amounts) >= 2 else "low"
    lines = []
    for i, (amt, it) in enumerate(amounts[:20], 1):
        d = it.get("event_date") or "?"
        snippet = re.sub(r"\s+", " ", _content(it))[:120]
        lines.append(f"{i}. [{d}] ${amt:g} — {snippet}")
    advisory = (
        "deterministic_set_count (mechanized money extract; verify before "
        f"finalizing):\nmode=sum value={total:g} components={len(amounts)} "
        f"filtered_facts={len(filtered)} confidence={conf}\n"
        "components:\n" + ("\n".join(lines) if lines else "(none)")
    )
    return DetCountResult(
        mode="sum",
        value=total,
        items=filtered,
        confidence=conf,
        reason=f"sum_{len(amounts)}_components",
        advisory=advisory,
        answer_text=f"${total:g}" if amounts else "",
    )


def remember_det(result: Optional[DetCountResult]) -> None:
    _TLS.result = result


def pop_det() -> Optional[DetCountResult]:
    result = getattr(_TLS, "result", None)
    _TLS.result = None
    return result


def peek_det() -> Optional[DetCountResult]:
    return getattr(_TLS, "result", None)
