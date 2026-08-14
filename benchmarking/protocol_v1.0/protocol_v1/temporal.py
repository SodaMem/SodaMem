"""Temporal grounding + axis advisories (v1.1 day-pin)."""
from __future__ import annotations

from protocol_v1.schema import QuestionSchema


def temporal_advisory(
    schema: QuestionSchema,
    *,
    current_date: str = "",
    absolute_window: dict | None = None,
) -> str:
    lines = [
        "protocol_v1.1 temporal_grounding:",
        f"- task={schema.task} axis={schema.axis}",
    ]
    if schema.time_window_raw:
        lines.append(
            f"- Resolve relative phrase {schema.time_window_raw!r} against "
            f"as-of date {current_date or '(unknown)'} into an absolute window; "
            "only answer who/what/where from events whose occurrence falls in that window."
        )
    if absolute_window:
        lines.append(
            f"- PINNED WINDOW: {absolute_window.get('from_date')} .. "
            f"{absolute_window.get('to_date')}. Discard candidates outside this range."
        )
    if schema.task == "TEMPORAL_EVENT" and (
        schema.predicate.startswith("who") or "who " in f" {schema.predicate} "
        or "which bike" in (schema.predicate or "")
        or "which " in (schema.notes and "" or "")
    ):
        lines.append(
            "- WHO/WHICH binding: companions or objects must come from a session/"
            "fact dated inside the pinned window — not from an earlier retelling "
            "of a similar event (e.g. same venue type on another day)."
        )
    # broader who/which from question notes
    lines.append(
        "- If multiple similar events exist, choose the one whose event/session date "
        "matches the pinned window day, then read companions/objects from THAT evidence."
    )
    if schema.axis == "got":
        lines.append(
            "- Axis=got/arrived/received: compare possession/arrival dates, "
            "NOT preorder/order dates."
        )
    elif schema.axis == "ordered":
        lines.append(
            "- Axis=ordered/booked: if the question asks 'how many months ago did I book', "
            "compute elapsed time from booking date to as-of date — do NOT answer with "
            "'booked N months in advance of the stay'."
        )
    if schema.task == "ORDERED_LIST":
        lines.append(
            "- Enumerate ALL matching dated events, sort earliest→latest, exclude plans. "
            "If the question states a count (e.g. six museums), keep searching until that "
            "many distinct dated visits are found or evidence is exhausted."
        )
    # duration weeks
    if "week" in (schema.predicate or "") or "weeks" in (schema.time_window_raw or ""):
        lines.append(
            "- For week gaps: search for explicit recovery and target event dates; "
            "prefer an explicit 'N weeks' user statement if present; else use "
            "floor(days/7) and also consider round(days/7) only if evidence supports."
        )
    return "\n".join(lines)
