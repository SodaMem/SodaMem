"""Collect candidate evidence items from planner Evidence (roster-first)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Candidate:
    cid: str
    content: str
    date: str = ""
    source: str = ""  # roster | record | observation
    session_id: str = ""
    fact_id: str = ""
    amount: Optional[float] = None
    labels: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


def _as_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    out: dict[str, Any] = {}
    for k in (
        "content", "date", "event_date", "session_id", "id", "fact_id",
        "evidence_id", "text", "amount", "value", "labels", "occurred_epoch",
    ):
        if hasattr(obj, k):
            out[k] = getattr(obj, k)
    return out


def _content_of(d: dict[str, Any]) -> str:
    for k in ("content", "text", "title", "summary", "observation"):
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _date_of(d: dict[str, Any]) -> str:
    for k in ("event_date", "date", "timestamp", "time", "created_at", "session_date"):
        v = d.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s[:32]
    return ""


def _try_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("$", "").strip()
    try:
        return float(s)
    except Exception:
        return None


def collect_roster(evidence: Any) -> list[dict[str, Any]]:
    observations = getattr(evidence, "observations", None) or []
    if isinstance(evidence, dict):
        observations = evidence.get("observations") or []
    for obs in reversed(list(observations)):
        if not isinstance(obs, dict):
            obs = _as_dict(obs)
        tool = str(obs.get("tool") or "")
        if tool in {"browser_count_evidence", "count"} and obs.get("roster"):
            return list(obs["roster"])
    return []


def collect_candidates(evidence: Any) -> list[Candidate]:
    """Prefer count-roster items; fall back to records / observation text."""
    out: list[Candidate] = []
    seen: set[str] = set()

    def add(
        content: str,
        *,
        date: str = "",
        source: str = "",
        session_id: str = "",
        fact_id: str = "",
        amount=None,
        labels=None,
        meta=None,
    ) -> None:
        content = (content or "").strip()
        if not content:
            return
        key = (fact_id or f"{date}|{content[:180]}").lower()
        if key in seen:
            return
        seen.add(key)
        out.append(
            Candidate(
                cid=f"c{len(out)}",
                content=content,
                date=date or "",
                source=source,
                session_id=session_id or "",
                fact_id=fact_id or "",
                amount=amount,
                labels=list(labels or []),
                meta=meta or {},
            )
        )

    if evidence is None:
        return out

    for item in collect_roster(evidence):
        d = _as_dict(item)
        add(
            _content_of(d),
            date=_date_of(d),
            source="roster",
            session_id=str(d.get("session_id") or ""),
            fact_id=str(d.get("fact_id") or d.get("evidence_id") or ""),
            amount=_try_float(d.get("amount") or d.get("value")),
            labels=[str(x) for x in (d.get("labels") or [])],
            meta={"occurred_epoch": d.get("occurred_epoch")},
        )

    records = getattr(evidence, "records", None) or []
    if isinstance(evidence, dict):
        records = evidence.get("records") or []
    for r in records:
        d = _as_dict(r)
        add(
            _content_of(d),
            date=_date_of(d),
            source="record",
            session_id=str(d.get("session_id") or d.get("sid") or ""),
            fact_id=str(d.get("fact_id") or d.get("evidence_id") or d.get("id") or ""),
            amount=_try_float(d.get("amount") or d.get("value")),
        )

    # Surface search / inspect observation payloads (needed for slot routes
    # that never call browser_count_evidence).
    observations = getattr(evidence, "observations", None) or []
    if isinstance(evidence, dict):
        observations = evidence.get("observations") or []

    def _walk_strings(obj: Any, *, depth: int = 0) -> None:
        if depth > 5:
            return
        if isinstance(obj, str):
            if len(obj.strip()) >= 24:
                add(obj, source="observation_walk")
            return
        if isinstance(obj, dict):
            # Prefer dated content fields when present.
            content = _content_of(obj)
            if content:
                add(
                    content,
                    date=_date_of(obj),
                    source="observation_walk",
                    fact_id=str(obj.get("fact_id") or obj.get("evidence_id") or ""),
                )
            for v in obj.values():
                _walk_strings(v, depth=depth + 1)
            return
        if isinstance(obj, list):
            for v in obj[:40]:
                _walk_strings(v, depth=depth + 1)

    for obs in observations:
        _walk_strings(obs if isinstance(obs, (dict, list)) else _as_dict(obs))

    return out
