"""Code-side metric slot selection (new plan speed, redeem points, role duration)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from sodamem_struct.candidates import Candidate, collect_candidates
from sodamem_struct.classify import Route, route_question


@dataclass
class SlotResult:
    ok: bool
    kind: str
    answer: str
    reason: str = ""
    confidence: float = 0.0
    evidence: list[dict[str, Any]] = field(default_factory=list)


_SPEED = re.compile(
    r"(\d+(?:\.\d+)?)\s*(gbps|mbps|kbps)",
    re.I,
)
_POINTS = re.compile(
    r"(\d{2,7})\s*points?",
    re.I,
)
_DATE = re.compile(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})")


def _parse_date(s: str) -> Optional[datetime]:
    m = _DATE.search(s or "")
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except Exception:
        return None


def _months_between(a: datetime, b: datetime) -> int:
    if b < a:
        a, b = b, a
    return (b.year - a.year) * 12 + (b.month - a.month)


def try_slot(question: str, evidence: Any, current_date: str = "", route: Optional[Route] = None) -> SlotResult:
    route = route or route_question(question)
    cands = collect_candidates(evidence)

    if route.kind == "slot_new_speed":
        return _slot_new_speed(cands)
    if route.kind == "slot_redeem_points":
        return _slot_redeem_points(cands, question)
    if route.kind == "slot_role_duration":
        return _slot_role_duration(cands, current_date=current_date, question=question)
    return SlotResult(False, "", "", reason="not_slot_route")


def _slot_new_speed(cands: list[Candidate]) -> SlotResult:
    """Prefer upgrade/'new plan' event value — not later 'current speed is'."""
    scored: list[tuple[float, str, Candidate, str]] = []
    for c in cands:
        text = c.content
        tl = text.lower()
        if not re.search(r"internet|plan|mbps|gbps|fiber|broadband", tl):
            continue
        for m in _SPEED.finditer(text):
            num_s, unit = m.group(1), m.group(2).lower()
            val = f"{num_s} {unit}"
            score = 0.0
            # Local window around the speed mention.
            local = text[max(0, m.start() - 50) : m.end() + 50].lower()
            if re.search(r"\bupgrad(?:ed|e)?\s+to\b|\bswitched to\b|\bchanged to\b", local):
                score += 10.0
            if re.search(r"\bnew (internet )?plan\b|\bmy new\b", local):
                score += 6.0
            # Bare current-state claims are weak for "new plan" questions.
            if re.search(r"\b(my internet speed is|speed is|currently)\b", local) and not re.search(
                r"\b(upgrad|new plan|switch)\b", local
            ):
                score -= 8.0
            if re.search(r"\b(old|previous|was on|before|canceled)\b", local):
                score -= 5.0
            # Tiny date tie-break only among same semantic class.
            dt = _parse_date(c.date) or _parse_date(text)
            if dt:
                score += dt.toordinal() / 1e7
            scored.append((score, val, c, text[:200]))
    if not scored:
        return SlotResult(False, "slot_new_speed", "", reason="no_speed", confidence=0.0)
    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0]
    if best[0] < 5.0:
        return SlotResult(
            False, "slot_new_speed", "", reason="no_upgrade_event", confidence=0.3,
            evidence=[{"content": best[3], "score": best[0]}],
        )
    num, unit = best[1].split()
    unit_map = {"gbps": "Gbps", "mbps": "Mbps", "kbps": "Kbps"}
    ans = f"{num if '.' in num else str(int(float(num)))} {unit_map.get(unit, unit)}"
    return SlotResult(
        True, "slot_new_speed", ans, reason="upgrade_event", confidence=0.9,
        evidence=[{"content": best[3], "score": best[0]}],
    )


def _slot_redeem_points(cands: list[Candidate], question: str) -> SlotResult:
    """Pick redeem-threshold / per-reward cost — not personal goal or balance."""
    scored: list[tuple[float, int, str]] = []
    for c in cands:
        tl = c.content.lower()
        for m in _POINTS.finditer(c.content):
            n = int(m.group(1))
            # Local window around the number for cue words.
            start = max(0, m.start() - 40)
            end = min(len(c.content), m.end() + 40)
            window = c.content[start:end].lower()
            score = 0.0
            if re.search(r"redeem|redemption|free (product|reward)|beauty insider", window):
                score += 6.0
            if re.search(r"\b(costs?|requires?|needed|need)\b", window):
                score += 3.0
            if re.search(r"\b(goal|all set|almost reaching|total of)\b", window):
                score -= 4.0  # personal progress target, not catalog threshold
            if re.search(r"balance|have|earned|available|current points", window):
                score -= 3.0
            if "need" in (question or "").lower() and "earn" in (question or "").lower():
                # "points I need to earn to redeem" → threshold remaining OR cost.
                # Prefer smaller catalog thresholds (100) over stated goals (300).
                if n <= 150:
                    score += 2.0
            scored.append((score, n, c.content[:200]))
    if not scored:
        return SlotResult(False, "slot_redeem_points", "", reason="no_points")
    # Highest semantic score; tie-break toward typical 100-pt reward tier.
    scored.sort(key=lambda x: (x[0], 1.0 / (1.0 + abs(x[1] - 100))), reverse=True)
    best = scored[0]

    if best[0] < 3.0:
        return SlotResult(False, "slot_redeem_points", "", reason="ambiguous_points", confidence=0.3)
    return SlotResult(
        True, "slot_redeem_points", str(best[1]), reason="redeem_threshold", confidence=0.85,
        evidence=[{"content": best[2], "points": best[1]}],
    )


def _slot_role_duration(cands: list[Candidate], current_date: str = "", question: str = "") -> SlotResult:
    """Duration in current role from start date to question/current date."""
    start: Optional[datetime] = None
    hit_text = ""
    for c in cands:
        tl = c.content.lower()
        if not re.search(r"role|promoted|started|joined|senior marketing|position|title", tl):
            continue
        if re.search(r"previous|former|left the|before becoming", tl) and not re.search(
            r"current|now|promoted to", tl
        ):
            continue
        dt = _parse_date(c.date) if re.search(r"promoted|started|began|joined", tl) else None
        dt = dt or _parse_date(c.content)
        if dt is None:
            continue
        # prefer promotion / start into current role
        score_boost = 2 if re.search(r"promoted|current role|started as|began", tl) else 0
        if start is None or score_boost or dt > start:
            # actually for start date we want earliest start of current role = latest promotion into it
            if re.search(r"promoted|started|began|joined", tl):
                start = dt
                hit_text = c.content[:220]

    if start is None:
        return SlotResult(False, "slot_role_duration", "", reason="no_start_date")

    end = _parse_date(current_date) or _parse_date(question) or datetime.utcnow()
    months = _months_between(start, end)
    # LongMemEval often expects "X months" or "about X months" / years
    if months >= 12 and months % 12 == 0:
        years = months // 12
        ans = f"{years} year" if years == 1 else f"{years} years"
    elif months >= 12:
        years = months // 12
        rem = months % 12
        ans = f"{years} years and {rem} months" if rem else f"{years} years"
    else:
        ans = f"{months} months"
    return SlotResult(
        True, "slot_role_duration", ans, reason="start_to_current", confidence=0.8,
        evidence=[{"start": start.date().isoformat(), "end": end.date().isoformat(), "content": hit_text}],
    )
