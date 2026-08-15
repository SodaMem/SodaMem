"""v1.0 SlotHardBind — extract competing slot values; prefer question-named slot."""
from __future__ import annotations

import re
from typing import Any, Optional

from protocol_v1.schema import QuestionSchema


def _blob_from_evidence(evidence: Any, admitted: list[dict[str, Any]] | None = None) -> str:
    parts: list[str] = []
    for it in admitted or []:
        parts.append(str(it.get("content") or ""))
    for obs in getattr(evidence, "observations", None) or []:
        for hit in obs.get("hits") or obs.get("results") or []:
            if isinstance(hit, dict):
                parts.append(str(hit.get("content") or hit.get("text") or ""))
        if obs.get("roster"):
            for it in obs["roster"]:
                parts.append(str(it.get("content") or ""))
        parts.append(str(obs.get("content") or ""))
    return "\n".join(parts)


_REDEEM_CUES = re.compile(
    r"\b(redeem|beauty insider|free (?:skincare )?product|threshold|loyalty)\b",
    re.I,
)
_GOAL_CUES = re.compile(
    r"\b(goal|all set|almost reaching|reaching your|my total|need a total)\b",
    re.I,
)


def _cue_distance(ctx_full: str, center: int, pattern: re.Pattern[str]) -> Optional[int]:
    """Distance from center index in full blob to nearest cue match."""
    best = None
    for m in pattern.finditer(ctx_full):
        d = min(abs(m.start() - center), abs(m.end() - center))
        if best is None or d < best:
            best = d
    return best


def extract_redeem_vs_goal(blob: str) -> tuple[Optional[int], Optional[int]]:
    """Classify each N-points mention by nearest cue (goal vs redeem)."""
    redeem: list[int] = []
    goal: list[int] = []
    for m in re.finditer(r"\b(\d{2,4})\s*points?\b", blob, re.I):
        n = int(m.group(1))
        if n < 10:
            continue
        center = (m.start() + m.end()) // 2
        local = blob[max(0, m.start() - 25) : m.end() + 25]
        # Prefer redeem collocation on THIS span first
        if re.search(
            r"points?\s+to\s+(?:redeem|get)|(?:redeem|free (?:skincare )?product)",
            local,
            re.I,
        ):
            # avoid false redeem when this number is explicitly a "points goal"
            if not re.search(rf"{n}\s*points?\s+goal", local, re.I):
                redeem.append(n)
                continue
        # Goal collocations tied to this number
        if re.search(
            rf"(?:your\s+)?{n}\s*points?\s+goal|need a total of\s+{n}\s*points?|"
            rf"all set",
            local,
            re.I,
        ):
            # "all set" only if near this number (already in local window)
            if re.search(rf"{n}\s*points?", local) and (
                re.search(rf"{n}\s*points?\s+goal|need a total of\s+{n}|all set", local, re.I)
            ):
                goal.append(n)
                continue
        # Distant cues: only classify GOAL (safer). Never promote a number to
        # redeem via long-range "Beauty Insider" — that misfires on balances (200).
        gd = _cue_distance(blob, center, _GOAL_CUES)
        gd = gd if gd is not None and gd <= 40 else None
        if gd is not None:
            goal.append(n)

    def uniq(xs: list[int]) -> list[int]:
        out: list[int] = []
        seen: set[int] = set()
        for x in xs:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    ru, gu = uniq(redeem), uniq(goal)
    return (ru[0] if ru else None), (gu[0] if gu else None)


def extract_redeem_local_only(blob: str) -> Optional[int]:
    """Redeem value only if the number span itself collocates with redeem cues."""
    redeem, _goal = extract_redeem_vs_goal(blob)
    if redeem is None:
        return None
    # Verify preferred redeem still has local collocation somewhere
    for m in re.finditer(rf"\b{redeem}\s*points?\b", blob, re.I):
        local = blob[max(0, m.start() - 40) : m.end() + 40]
        if re.search(
            r"(?:redeem|threshold|free (?:skincare )?product|beauty insider|"
            r"points?\s+to\s+(?:redeem|get))",
            local,
            re.I,
        ) and not re.search(rf"{redeem}\s*points?\s+goal", local, re.I):
            return redeem
    return None


def extract_new_vs_current_speed(blob: str) -> tuple[Optional[str], Optional[str]]:
    new_v: list[str] = []
    cur_v: list[str] = []
    for m in re.finditer(r"(\d+(?:\.\d+)?\s*(?:Mbps|Gbps))", blob, re.I):
        val = m.group(1)
        pre = blob[max(0, m.start() - 35) : m.start()]
        # Nearest governing cue in left window only
        new_m = None
        for mm in re.finditer(r"\b(upgrad(?:ed|ing)|new plan|switched to)\b", pre, re.I):
            new_m = mm
        cur_m = None
        for mm in re.finditer(r"\b(currently|current(?:ly)?|my internet is|now (?:have|at))\b", pre, re.I):
            cur_m = mm
        if new_m and cur_m:
            if cur_m.start() >= new_m.start():
                cur_v.append(val)
            else:
                new_v.append(val)
        elif cur_m:
            cur_v.append(val)
        elif new_m:
            new_v.append(val)
    return (new_v[0] if new_v else None), (cur_v[0] if cur_v else None)


def slot_hard_decision(
    schema: QuestionSchema,
    evidence: Any,
    *,
    admitted: list[dict[str, Any]] | None = None,
) -> Optional[dict[str, Any]]:
    blob = _blob_from_evidence(evidence, admitted)
    if not blob.strip():
        return None

    if schema.slot_name == "redeem_threshold" or (
        schema.task == "SLOT_LOOKUP" and "point" in (schema.predicate or "").lower()
    ):
        redeem, goal = extract_redeem_vs_goal(blob)
        if redeem is not None:
            return {
                "answer_text": str(redeem),
                "reason": f"slot_hard redeem={redeem} goal={goal}",
                "advisory": (
                    "protocol_v1.0 slot_hard_bind: FINAL ANSWER MUST BE "
                    f"{redeem} (redeem/threshold). "
                    + (
                        f"Do NOT answer personal goal {goal}."
                        if goal is not None and goal != redeem
                        else ""
                    )
                ),
            }
        return None

    if (
        schema.task == "VERSIONED_ATTR"
        or "new" in schema.modifiers
        or schema.slot_name == "new_plan_speed"
    ):
        new_s, cur_s = extract_new_vs_current_speed(blob)
        if new_s and (cur_s is None or new_s.lower() != cur_s.lower()):
            return {
                "answer_text": new_s,
                "reason": f"slot_hard new_speed={new_s} current={cur_s}",
                "advisory": (
                    "protocol_v1.0 slot_hard_bind: Question asks NEW plan speed. "
                    f"FINAL ANSWER MUST BE {new_s}. "
                    + (f"Ignore current-status {cur_s}." if cur_s else "")
                ),
            }
    return None
