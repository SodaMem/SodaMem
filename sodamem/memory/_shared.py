"""Utilities shared by memory/ingest and memory/retrieval. Lives at the
memory/ package level (not inside either subpackage) because import-linter's
`layers` contract only orders the three TOP-LEVEL packages (answer > context >
memory) — it says nothing about memory's internal submodule graph. Both
ingest (Task 5) and retrieval (Task 6) already depend on `memory` existing; a
shared module one level up costs zero new cross-package edges (R5). Does not
import `sodamem.llm`, so consuming it from `sodamem.memory.retrieval` cannot
violate the `retrieval-no-llm` import-linter contract.

Ported verbatim from the predecessor implementation: `_ts_to_iso`
(:274-293), `_tokenize`/`_canonical_text`/`_date_bucket` (:332-371, in
`_canonical_text`/`_tokenize`/`_date_bucket` order in the source), plus the
supporting `_ROLE_ENTITY_TYPE` constant and `_infer_entity_type`/`_stable_id`
functions from the same span — needed by the predecessor implementation
(Task 5) and the predecessor implementation (Task 7), confirmed by a
repo-wide grep at port time (no other module besides `_helpers.py` itself
references them).

Task 5 addition: `_canonical_entity_id` (`_helpers.py:332-334`, immediately
above `_canonical_text` in the source — the two were adjacent and were split
by an oversight, not a decision). Task 3's grep for this module's original
cut found no consumer beyond `_helpers.py` itself and `storage`'s own inlined
copy (`Store._canonical_entity_id`, kept as-is — not worth touching a landed,
tested file for a 3-line duplicate); Task 5's port of `extractor.py`/
`maintainer.py`/`client.py`'s ingest half needs the SAME function in three
places (entity id derivation for touched-entity tracking, mention/edge
writes, and `EntityResolver`). Centralizing here — rather than three
copy-pasted private functions inside `ingest/`, or an import cycle between
`extractor.py`/`maintainer.py`/`client.py` — is the same reasoning Task 3
already applied to every other helper in this module.

Task 5 addition: `_iso_to_ts` (`_helpers.py:228-271`) — the graceful,
None-on-unparseable forward counterpart of `_ts_to_iso` already here. Needed
by `extractor.py` to convert a (possibly None — most facts have no date)
resolved date STRING into the epoch `FactEvent.occurred_start`/`occurred_end`/
`valid_from`/`valid_until` fields. This is a DIFFERENT function from the new
`parse_session_time` in `ingest/client.py` (which merges this function's
regex cascade with `_parse_benchmark_date` and changes the failure contract
to raise) — `_iso_to_ts` itself keeps its original graceful contract because
extractor.py's per-fact date fields are legitimately absent on most facts
(None means "no date stated", not "parse failure needing recovery"); only
the session-time argument to `ingest_session` (a required anchor) gets the
raise-not-None treatment. Placed beside `_ts_to_iso` for the same symmetry
reason, and to let `client.py`/`extractor.py`/`maintainer.py` all reach it
without a cycle (client.py constructs `FactEventExtractorV2` from
`extractor.py`, so `extractor.py` cannot import from `client.py`).
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)


def _ts_to_iso(ts: float | None, precision: str | None = None) -> str | None:
    """Render an epoch back to an ISO 8601 string at the given precision
    (year->'2023', month->'2023-06', minute->'2023-06-15T19:00'). Defaults to
    day when precision is unknown — so partial precision reaches the reader."""
    if ts is None:
        return None
    try:
        dt = datetime.fromtimestamp(float(ts))
    except Exception as e:
        logger.warning("_ts_to_iso: could not convert epoch %r to a date (%s) — returning None", ts, e)
        return None
    if precision == "year":
        return dt.strftime("%Y")
    if precision == "month":
        return dt.strftime("%Y-%m")
    if precision in ("hour", "minute"):
        return dt.strftime("%Y-%m-%dT%H:%M")
    if precision == "second":
        return dt.strftime("%Y-%m-%dT%H:%M:%S")
    return dt.strftime("%Y-%m-%d")


def _iso_to_ts(value) -> float | None:
    """Parse an ISO 8601 string — incl. partial forms — to an epoch sort key
    WITHOUT losing data. Year/month resolve to the period START ('2019'->Jan 1,
    '2023-06'->the 1st); minute/second are PRESERVED ('2023-06-15T19:00' keeps
    19:00, not midnight). Granularity is recorded separately as time_precision —
    the epoch is just the comparable instant. None only for genuinely
    unparseable input (including None/""/"null") — this function never raises
    and never guesses; a caller needing "unparseable is an error" (the ingest
    session-time argument) wraps this in `sodamem.memory.ingest.client.
    parse_session_time`, it does not change this function's contract."""
    if value in (None, "", "null"):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("/", "-")
    # full datetime: YYYY-MM-DD[T ]HH:MM[:SS] — keep the time component
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})[T ](\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    if m:
        try:
            return datetime(*(int(m.group(i) or 0) for i in range(1, 7))).timestamp()
        except ValueError:
            return None
    # date YYYY-MM-DD
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).timestamp()
        except ValueError:
            return None
    # month YYYY-MM -> first of the month
    m = re.match(r"(\d{4})-(\d{1,2})$", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), 1).timestamp()
        except ValueError:
            return None
    # year YYYY -> Jan 1
    m = re.match(r"(\d{4})$", text)
    if m:
        return datetime(int(m.group(1)), 1, 1).timestamp()
    # embedded full date, legacy fallback
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).timestamp()
        except ValueError:
            return None
    return None


def _canonical_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _canonical_entity_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")
    return "entity_" + (slug or "unknown")


# Deterministic role -> L3 entity type. Generic by design (no benchmark-shaped
# verbs); unknown roles fall back to "concept" and may be upgraded later.
_ROLE_ENTITY_TYPE = {
    "subject": "person", "person": "person", "friend": "person", "doctor": "person",
    "colleague": "person", "family": "person", "partner": "person",
    "airline": "organization", "company": "organization", "employer": "organization",
    "organization": "organization", "brand": "organization", "team": "organization",
    "location": "place", "city": "place", "place": "place", "destination": "place",
    "origin": "place", "country": "place", "venue": "place",
    "medication": "substance", "drug": "substance", "food": "substance",
}


def _infer_entity_type(role: str) -> str:
    return _ROLE_ENTITY_TYPE.get(str(role).lower(), "concept")


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9][a-z0-9'\-]*", str(text or "").lower())
    expanded: list[str] = []
    for tok in tokens:
        expanded.append(tok)
        if "-" in tok:
            expanded.extend(part for part in tok.split("-") if len(part) > 1)
    return expanded


def _stable_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(p) for p in parts)
    return f"{prefix}_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _date_bucket(ts: float | None) -> str:
    if ts is None:
        return ""
    return _ts_to_iso(ts) or ""
