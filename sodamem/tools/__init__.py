"""MemoryTool — agent-facing tool dispatch over a SodaMem-backed Store.

Ported from the predecessor implementation. The HTTP server (Phase 4) and any
in-process agent loop both call `MemoryTool.dispatch(name, **kwargs)` and
receive identical dict responses. MemoryTool has no HTTP awareness, no
benchmark awareness, no agent-loop awareness. Step counters, audit trails,
blind contracts, and JSON serialization are the caller's responsibility.

Three riding-along fixes applied on port:

1. `GRAPH_V2_SEARCH_ROUTE` call-time `_cfg.env_str()` read (source :400) is a
   textbook call-time global-config violation — replaced by a constructor
   parameter. `MemoryTool.__init__` takes `memory: SodaMem` (Task 3/5/6/7's
   accumulated facade) plus an optional `config: RetrievalConfig` (Task 6) —
   `config.search_route` is what used to be read from the env var at call
   time, now fixed at construction.
2. `_TOOL_REGISTRY`'s `path` field (e.g. `"POST /memory/tool/search"`) never
   matched `http_server.py`'s real routes (mounted under `/memory/browser/*`)
   — a pre-existing dead field, deleted here rather than "corrected" to a
   Phase-4 route scheme that doesn't exist yet.

Structural note: `_TOOL_REGISTRY` is a `dict[str, dict]` keyed by tool name
rather than a `list[dict]`. Iterating a list the same way would try to use an
unhashable dict as a membership-test key and crash. `list_tools()` reassembles
the list-of-dicts-with-name shape its callers actually consume, so nothing
downstream sees a shape change.

R15 (explicit, not casual, decision — see this module's `search()` and
`_format_context`): `MemoryTool.search()`'s response dict shape is NOT
changed by this port. `_format_context` (source :1151-1157) is the THIRD
overlapping context-assembly implementation in the pre-port codebase
(alongside `autonomous_runtime.py`'s `compact_cards` and `run_benchmark.py`'s
benchmark-only `_oracle_context`); `sodamem.context.build_context()` (Task 8)
is the real one going forward, but wiring `search()`'s `include_context`
field to call it would change `search()`'s response shape byte-for-byte, and
`tests/test_http_cli_adapter.py`/`tests/test_inspect_id_normalization.py`
assert against that exact shape. Those two files are themselves mostly
`http_server.py` adapter tests (Phase 4, not yet ported) — the three
assertions in them that lock `MemoryTool`'s OWN shape (not `http_server`'s)
are ported into `tests/test_tools.py` instead, unchanged, per R15's
"assertions don't move" rule. Swapping `_format_context` for `build_context()`
is deliberately left as a TODO, not implemented, exactly as R15 decided.

"""

from __future__ import annotations

import dataclasses
import inspect
import logging
import math
import os
import re
import threading
from datetime import datetime, time as datetime_time, timezone
from typing import Any, Optional

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # type-hint-only; runtime import of the root facade from
    from sodamem import SodaMem  # this lower layer is a cycle (import-linter)
from sodamem.memory.retrieval import vector
from sodamem.memory.retrieval.bm25 import get_bm25_index
from sodamem.memory.retrieval.config import Degradation, RetrievalConfig
from sodamem.memory.retrieval.search import search as _search_evidence
from sodamem.memory._shared import _ts_to_iso
from sodamem.models import (
    EdgeType,
    FactEdge,
    FactEvent,
    FactStatus,
    SourceSpan,
)

from .compute import (
    ComputeError,
    compute as compute_evidence,
    derived_evidence_id,
    list_operators,
)

logger = logging.getLogger(__name__)

_CONTENT_PREVIEW_CHARS = 700
_PREVIEW_SOURCE_NAMES = {
    "raw_turn": "RawTurn",
    "source_span": "SourceSpan",
    "support_text": "SourceTrace",
    "extracted_support_text": "SourceTrace",
    "predicate_raw": "FactEvent",
    "derived_runtime": "DerivedRuntime",
}


class ToolError(Exception):
    """Raised by MemoryTool when a tool call fails for a known reason.

    The HTTP layer maps `code` to HTTP status; the in-process caller can read
    `code` to decide whether to retry, abstain, or surface to the agent.
    """

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ---------------------------------------------------------------------------
# Static agent-visible tool registry — single source of truth for both CLI
# tools-list and agentic LLM tool definitions. Legacy callable endpoints may
# remain outside this advertised surface during migration.
#
# dict[str, dict] keyed by tool name (see module docstring "structural
# note") — the list[dict] shape is reassembled by `list_tools()` below for
# callers. `path` field dropped: it never matched any real route.
# ---------------------------------------------------------------------------

_TOOL_REGISTRY: dict[str, dict] = {
    "memory.tool.search": {
        "description": "Initial wide evidence search. Returns ranked candidate evidence cards with provenance and suggested next tools.",
        "method": "POST",
        "params": ["query", "top_k?", "offset?", "session_id?", "include_context?"],
    },
    "memory.tool.search-more": {
        "description": "Continue a prior wide search from an offset while preserving memory-core ordering.",
        "method": "POST",
        "params": ["query", "top_k?", "offset?", "session_id?", "include_context?"],
    },
    "memory.tool.refine": {
        "description": "Deterministically filter wide search results by metadata fields; no LLM or semantic gate.",
        "method": "POST",
        "params": [
            "query", "top_k?", "offset?", "channels?", "types?", "entity?",
            "source_types?", "event_types?", "modalities?", "kinds?", "source_roles?",
            "session_id?", "entity_roles?", "status?", "quantity_units?",
            "occurred_from?", "occurred_to?", "created_from?", "created_to?",
            "valid_from?", "valid_to?", "min_confidence?", "include_terms?", "exclude_terms?",
        ],
    },
    "memory.tool.result": {
        "description": "Expand one search result by fact_id or source span_id, preserving source-level provenance and follow-up actions.",
        "method": "GET",
        "params": ["memory_id"],
    },
    "memory.tool.raw-search": {
        "description": "Search raw conversation turns directly. Use when compressed memories miss exact entities, dates, numbers, or wording.",
        "method": "POST",
        "params": ["query", "top_k?", "session_id?", "from_ts?", "to_ts?"],
    },
    "memory.tool.session": {
        "description": "Return the full ordered turn list of one session.",
        "method": "GET",
        "params": ["session_id"],
    },
    "memory.tool.entity-timeline": {
        "description": "Timeline of fact events mentioning a given entity, oldest first.",
        "method": "GET",
        "params": ["entity_id"],
    },
    "memory.tool.explore": {
        "description": "Graph BFS over FactEdges from a starting node (memory or entity).",
        "method": "POST",
        "params": ["start_id", "start_type?", "depth?", "edge_types?", "limit?"],
    },
    "memory.tool.event-timeline": {
        "description": "For each event phrase, return matching evidence grouped by event. Use for ordering / comparison.",
        "method": "POST",
        "params": ["events", "top_k_per_event?"],
    },
    "memory.tool.evidence-count": {
        "description": "Collect candidate evidence per label without aggregating. Use for 'most', 'how many times', label comparisons.",
        "method": "POST",
        "params": ["query", "labels", "from_ts?", "to_ts?", "top_k_per_label?"],
    },
    "memory.tool.tools-list": {
        "description": "List all available memory tools and their parameters.",
        "method": "GET",
        "params": [],
    },
}


def list_tools() -> dict:
    """User-agnostic advertised tool registry. Both MemoryTool.tools_list and
    the (Phase 4) HTTP /memory/tool/tools endpoint call this so they cannot
    drift.
    """
    return {"tools": [{"name": name, **spec} for name, spec in _TOOL_REGISTRY.items()]}


def date_calc(
    mode: str,
    *,
    from_: Any = None,
    to: Any = None,
    from_evidence_id: Any = None,
    to_evidence_id: Any = None,
    **extra: Any,
) -> dict:
    """Deterministic date arithmetic, emitted as a derived_runtime card (D49.4).

    Stateless — no client required. The two dates are supplied by the agent
    (already read off retrieved evidence). Pass `from_evidence_id` /
    `to_evidence_id` to record provenance: the card then carries the source
    evidence ids in `inputs` / `derived_from_fact_ids` and is provenance
    `verified`. Without them the result is `unsourced` — usable as a scratch
    computation but not citable as final_evidence (D49.7 invariant #1).

    Legacy numeric fields (days/abs_days/weeks/months_approx/years_approx/
    direction) are retained verbatim so existing callers keep working.
    """
    # CLI passes `from` (reserved word); accept both.
    if from_ is None:
        from_ = extra.get("from")
    if not from_ or not to:
        raise ToolError("invalid_request", "from and to are required")
    try:
        d1 = _parse_iso_date(from_)
        d2 = _parse_iso_date(to)
    except ValueError as e:
        raise ToolError("invalid_request", f"date parse failed: {e}")
    diff = d2 - d1
    days = diff.days
    months_approx = round(days / 30.4375, 2)
    years_approx = round(days / 365.25, 2)
    direction = "after" if days >= 0 else "before"

    inputs = [
        _strip_evidence_prefix(str(eid))
        for eid in (from_evidence_id, to_evidence_id)
        if eid
    ]
    operator = "date.diff"
    params = {"mode": mode, "from": str(from_), "to": str(to)}
    evidence_id = derived_evidence_id(operator, inputs, params)
    from_iso = _parse_iso_date(from_).date().isoformat()
    to_iso = _parse_iso_date(to).date().isoformat()
    calculation_trace = (
        f"{to_iso} - {from_iso} = {abs(days)} days "
        f"(≈ {abs(months_approx)} months, ≈ {abs(years_approx)} years); "
        f"{to_iso} is {direction} {from_iso}"
    )
    content = f"Verified date calculation: {calculation_trace}."
    return {
        # --- derived_runtime card (D49.4) ---
        "evidence_id": evidence_id,
        "kind": "derived",
        "source_type": "derived_runtime",
        "operator": operator,
        "inputs": inputs,
        "params": params,
        "derived_from_fact_ids": list(inputs),
        "value": days,
        "unit": "day",
        "calculation_trace": calculation_trace,
        "content": content,
        "content_preview": content,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "recompute_policy": "idempotent_from_inputs",
        "provenance_status": "verified" if inputs else "unsourced",
        "confidence": 1.0,
        # --- legacy numeric fields (retained; do not remove) ---
        "mode": mode,
        "from": from_,
        "to": to,
        "days": days,
        "abs_days": abs(days),
        "weeks": round(days / 7, 2),
        "months_approx": months_approx,
        "years_approx": years_approx,
        "direction": direction,
    }


_DISPATCH_TABLE = {
    "memory.tool.search": "search",
    "memory.tool.refine": "refine",
    "memory.tool.result": "result",
    "memory.tool.raw-search": "raw_search",
    "memory.tool.session": "session",
    "memory.tool.entity-timeline": "entity_timeline",
    "memory.tool.explore": "explore",
    "memory.tool.event-timeline": "event_timeline",
    "memory.tool.date-calc": "date_calc",
    "memory.tool.compute": "compute",
    "memory.tool.operators-list": "operators_list",
    "memory.tool.evidence-count": "evidence_count",
    # Backward-compatible aliases for pre-rename callers.
    "memory.browser.search": "search",
    "memory.browser.refine": "refine",
    "memory.browser.result": "result",
    "memory.browser.raw-search": "raw_search",
    "memory.browser.session": "session",
    "memory.browser.entity-timeline": "entity_timeline",
    "memory.browser.explore": "explore",
    "memory.browser.event-timeline": "event_timeline",
    "memory.browser.date-calc": "date_calc",
    "memory.browser.evidence-count": "evidence_count",
}


# Per-tool canonical positional / id argument. Anything the LLM might shorthand
# (`id`, `fact_id`, `evidence_id`, `span_id`, `turn_id`) is folded into the
# canonical name. Prefix-stripping is applied so the agent can pass either the
# raw fact uuid or the full `ev_fact:<uuid>` evidence_id.
_ID_ALIASES: dict[str, str] = {
    "result":           "memory_id",
    "session":          "session_id",
    "entity_timeline":  "entity_id",
    "explore":          "start_id",
}

# `from` is a Python keyword — accept both `from`/`from_`.
_FROM_ALIASES = {"from": "from_"}


def _strip_evidence_prefix(value: str) -> str:
    """`ev_fact:abc` / `ev_span:abc` / `ev_raw:abc` -> `abc`. Leave unprefixed alone."""
    for prefix in ("ev_fact:", "ev_span:", "ev_turn:", "ev_raw:"):
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


_COMPLETE_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_ISO_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}"
    r"(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?$"
)


def _time_boundary_label(value: Any) -> str:
    """Bound error rendering even for enormous numeric inputs."""
    if isinstance(value, int) and not isinstance(value, bool):
        return f"<int:{value.bit_length()} bits>"
    if isinstance(value, str) and len(value) > 160:
        return repr(value[:157] + "...")
    try:
        return repr(value)
    except (OverflowError, TypeError, ValueError):
        return f"<{type(value).__name__}>"


def _parse_time_boundary(value: Any, *, upper: bool) -> float:
    """Strictly decode one raw-search/count time boundary to epoch seconds.

    Numeric values and complete numeric strings are epochs. ISO datetimes
    preserve explicit offsets; naive values use the same host-local convention
    as ``memory._shared._iso_to_ts``. A date-only upper bound covers the whole
    local calendar day.
    """
    if isinstance(value, bool):
        raise ToolError("invalid_request", "time boundaries cannot be booleans")
    if isinstance(value, (int, float)):
        try:
            parsed = float(value)
        except (OverflowError, TypeError, ValueError) as exc:
            raise ToolError(
                "invalid_request",
                f"invalid time boundary {_time_boundary_label(value)}: {exc}",
            ) from exc
    elif isinstance(value, str):
        text = value.strip()
        if _COMPLETE_NUMBER.fullmatch(text):
            try:
                parsed = float(text)
            except (OverflowError, TypeError, ValueError) as exc:
                raise ToolError(
                    "invalid_request",
                    f"invalid time boundary {_time_boundary_label(value)}: {exc}",
                ) from exc
        elif _ISO_DATE_ONLY.fullmatch(text):
            try:
                day = datetime.fromisoformat(text).date()
                boundary = datetime.combine(
                    day, datetime_time.max if upper else datetime_time.min
                )
                parsed = boundary.timestamp()
            except (OverflowError, ValueError) as exc:
                raise ToolError(
                    "invalid_request",
                    f"invalid time boundary {_time_boundary_label(value)}: {exc}",
                ) from exc
        elif _ISO_DATETIME.fullmatch(text):
            try:
                parsed = datetime.fromisoformat(
                    text[:-1] + "+00:00" if text.endswith("Z") else text
                ).timestamp()
            except (OverflowError, ValueError) as exc:
                raise ToolError(
                    "invalid_request",
                    f"invalid time boundary {_time_boundary_label(value)}: {exc}",
                ) from exc
        else:
            raise ToolError(
                "invalid_request",
                f"invalid time boundary: {_time_boundary_label(value)}",
            )
    else:
        raise ToolError(
            "invalid_request",
            f"invalid time boundary: {_time_boundary_label(value)}",
        )
    if not math.isfinite(parsed):
        raise ToolError(
            "invalid_request",
            f"time boundary must be finite: {_time_boundary_label(value)}",
        )
    return parsed


def _js_int_num(value):
    """Whole floats -> int, mirroring the source CLI's JavaScript JSON
    round-trip (JSON.stringify(10.0) === "10"). See the quantity card
    comment; keeps planner/reader-visible number rendering byte-compatible
    with the CLI-era traces."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _normalize_args(tool_name: str, kwargs: dict) -> dict:
    """Best-effort argument normalization so agent shorthand works.

    - Maps id/fact_id/evidence_id/span_id/turn_id to the tool's canonical id
      argument (e.g. result expects `memory_id`).
    - Strips `ev_fact:` / `ev_span:` / `ev_turn:` prefixes from id values.
    - Translates the reserved `from` keyword to `from_`.
    """
    if not isinstance(kwargs, dict):
        return {}
    out = dict(kwargs)

    # `from` reserved-word alias for date_calc
    for src, dst in _FROM_ALIASES.items():
        if src in out and dst not in out:
            out[dst] = out.pop(src)

    method_name = _DISPATCH_TABLE.get(tool_name, "")
    canonical = _ID_ALIASES.get(method_name)
    if canonical and canonical not in out:
        # Promote the first hit from a list of common aliases.
        for alias in ("id", "memory_id", "fact_id", "evidence_id", "span_id", "turn_id",
                      "session_id", "entity_id", "start_id"):
            if alias == canonical:
                continue
            if alias in out and isinstance(out[alias], str) and out[alias]:
                out[canonical] = out.pop(alias)
                break

    # Strip evidence_id prefix from any *_id arg so agents can paste either the
    # raw fact_id or the `ev_fact:<id>` they saw in tool output.
    for key, value in list(out.items()):
        if key.endswith("_id") and isinstance(value, str):
            out[key] = _strip_evidence_prefix(value)

    for tkey, upper in (("from_ts", False), ("to_ts", True)):
        if tkey in out and out[tkey] is not None:
            try:
                out[tkey] = _parse_time_boundary(out[tkey], upper=upper)
            except ToolError:
                raise
            except (OverflowError, TypeError, ValueError) as exc:
                raise ToolError(
                    "invalid_request",
                    "invalid time boundary "
                    f"{_time_boundary_label(out[tkey])}: {exc}",
                ) from exc
    if (
        out.get("from_ts") is not None
        and out.get("to_ts") is not None
        and out["from_ts"] > out["to_ts"]
    ):
        raise ToolError("invalid_request", "from_ts must be less than or equal to to_ts")

    return out


def _degradation_to_dict(d: Degradation) -> dict:
    return {"code": d.code.value, "message": d.message, "details": dict(d.details)}


def _log_degraded(where: str, degraded: list[Degradation]) -> None:
    if degraded:
        logger.warning(
            "MemoryTool.%s: %d degraded route(s): %s",
            where, len(degraded), "; ".join(d.message for d in degraded),
        )


class MemoryTool:
    """Tool dispatch over a single-user view of a SodaMem-backed Store.

    Each MemoryTool instance is bound to one `user_id`. The caller is
    responsible for managing instance lifecycles (per-user registry in HTTP,
    per-question in benchmark).
    """

    def __init__(self, memory: SodaMem, *, user_id: str, config: RetrievalConfig | None = None):
        self._memory = memory
        self._store = memory.store
        self._user_id = user_id
        self._config = config or RetrievalConfig()

    # ---------------- public entrypoint ----------------

    def dispatch(self, name: str, **kwargs: Any) -> dict:
        method_name = _DISPATCH_TABLE.get(name)
        if method_name is None:
            raise ToolError("unknown_tool", f"Unknown tool: {name}", status=404)
        method = getattr(self, method_name)
        normalized = _normalize_args(name, kwargs)
        # Bug #8 (0723): source's tool boundary was pydantic request models,
        # which silently DROP unknown fields — browser_search's forced
        # include_multimodal and search_raw's offset both rode on that
        # tolerance. The port's bare **kwargs call turned unknown args into
        # TypeError -> ToolError, which made the forced step-0 search fail on
        # 100% of questions (planner started blind). Restore source semantics:
        # filter to the method's signature (methods with **kwargs keep
        # everything), and log what was dropped — the drop itself is
        # source-faithful, but our no-silent-failures policy means it must
        # leave a trace. Missing REQUIRED args still raise (pydantic 422'd
        # those too).
        params = inspect.signature(method).parameters
        if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
            dropped = sorted(k for k in normalized if k not in params)
            if dropped:
                logger.info("tool %s: dropping unsupported args %s (source pydantic models ignored unknown fields)", name, dropped)
                normalized = {k: v for k, v in normalized.items() if k in params}
        # Tool-level timeout: a stuck backend call (e.g. a pathological
        # timeline/count query against a huge store) must not hang the whole
        # question. We cannot forcibly kill a running Python thread, so once
        # the timeout fires we simply stop waiting and return control to the
        # caller — the worker thread is abandoned, not stopped, and keeps
        # running `method` to completion (or forever) in the background.
        # That abandoned thread is spawned here as `daemon=True` rather than
        # via `ThreadPoolExecutor` on purpose: ThreadPoolExecutor's worker
        # threads are non-daemon and get joined by its `atexit` handler, so a
        # single stuck call would keep the whole process alive past the
        # timeout. A daemon thread carries no such join-on-exit obligation,
        # so an abandoned worker can leak until the call finally returns, but
        # it can never block interpreter shutdown.
        tool_timeout_s = float(os.environ.get("SODAMEM_TOOL_TIMEOUT_S", "45") or 45)

        def _invoke() -> dict:
            return method(**normalized)

        try:
            if tool_timeout_s > 0:
                outcome: dict[str, Any] = {}

                def _run() -> None:
                    try:
                        outcome["value"] = _invoke()
                    except BaseException as exc:  # re-raised on the caller thread below
                        outcome["error"] = exc

                worker = threading.Thread(target=_run, daemon=True)
                worker.start()
                worker.join(tool_timeout_s)
                if worker.is_alive():
                    raise ToolError(
                        "backend_timeout",
                        f"{name} exceeded {tool_timeout_s:.0f}s — try a narrower query",
                        status=504,
                    )
                if "error" in outcome:
                    raise outcome["error"]
                return outcome["value"]
            return _invoke()
        except ToolError:
            raise
        except TypeError as exc:
            # Wrong arg names / missing required args / extra args. Convert to
            # ToolError so the agent loop can recover and retry instead of
            # crashing the whole question.
            raise ToolError(
                "invalid_request",
                f"{name} rejected args {sorted(normalized.keys())}: {exc}",
            )
        except Exception as exc:
            logger.warning("tool %s raised: %s", name, exc, exc_info=True)
            raise ToolError("backend_error", f"{name} raised {type(exc).__name__}: {exc}", status=500)

    # ---------------- tools ----------------

    def operators_list(self) -> dict:
        return list_operators()

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        offset: int = 0,
        session_id: str = "",
        include_context: bool = True,
    ) -> dict:
        if not query:
            raise ToolError("invalid_request", "query is required")
        top_k = max(1, min(int(top_k or 10), 50))
        offset = max(0, int(offset or 0))
        limit = max(offset + top_k + 20, top_k * 4, 80)
        # 检索内核路由：wide=RRF baseline；fusion=多路融合(确定性排序，默认，
        # 对齐 run_base.sh canonical baseline 口径)。riding-along fix #1: this
        # used to be a call-time env read (GRAPH_V2_SEARCH_ROUTE); now fixed
        # at construction via self._config.search_route.
        result = _search_evidence(
            query, user_id=self._user_id, store=self._store,
            config=self._config, limit=limit,
        )
        # R15: search()'s response shape is frozen (see module docstring) —
        # `degraded` is deliberately NOT added as a new response key here.
        # Not swallowed either: logged, so a degraded route is still visible
        # to whoever owns this process's logs, per the zero-silent-
        # degradation policy (spec §6.7). A future shape revision (R15's
        # explicit TODO) should surface this in the response proper.
        _log_degraded("search", result.degraded)
        evidence = result.evidence
        if session_id:
            evidence = [e for e in evidence if e.get("source_session_id") == session_id]
        total_candidates = len(evidence)
        page = evidence[offset:offset + top_k]
        items = [self._evidence_to_card(e) for e in page]
        next_offset = offset + len(items)
        response: dict = {
            "query": query,
            "user_id": self._user_id,
            "items": items,
            "offset": offset,
            "next_offset": next_offset,
            "has_more": next_offset < total_candidates,
            "total_candidates": total_candidates,
            "ranking": {
                "mode": "wide_raw_query_rrf",
                "semantic_planner": "disabled",
                "semantic_hard_filters": "disabled",
            },
            "available_next_tools": self._next_tools_for_search(
                page,
                has_more=next_offset < total_candidates,
            ),
        }
        if include_context:
            response["context"] = self._format_context(page)
        return response

    def refine(
        self,
        query: str,
        *,
        top_k: int = 10,
        offset: int = 0,
        channels: Optional[list[str]] = None,
        types: Optional[list[str]] = None,
        entity: str = "",
        source_types: Optional[list[str]] = None,
        event_types: Optional[list[str]] = None,
        modalities: Optional[list[str]] = None,
        kinds: Optional[list[str]] = None,
        source_roles: Optional[list[str]] = None,
        session_id: str = "",
        entity_roles: Optional[dict] = None,
        status: Optional[list[str] | str] = None,
        quantity_units: Optional[list[str]] = None,
        occurred_from: Any = None,
        occurred_to: Any = None,
        created_from: Any = None,
        created_to: Any = None,
        valid_from: Any = None,
        valid_to: Any = None,
        min_confidence: Optional[float] = None,
        include_terms: Optional[list[str]] = None,
        exclude_terms: Optional[list[str]] = None,
    ) -> dict:
        top_k = max(1, min(int(top_k or 10), 50))
        offset = max(0, int(offset or 0))
        # refine() always uses the wide (RRF baseline) route regardless of
        # self._config.search_route — matching the source, which called
        # retrieve_wide_audit_bundle directly (never fusion) for
        # deterministic-filter semantics. Forcing search_route="wide" via
        # dataclasses.replace composes cleanly with retrieval.search()'s own
        # wide/fusion branch without needing a second entrypoint.
        refine_config = dataclasses.replace(self._config, search_route="wide")
        result = _search_evidence(
            query, user_id=self._user_id, store=self._store,
            config=refine_config, limit=max(top_k * 10 + offset, 120),
        )
        _log_degraded("refine", result.degraded)
        evidence = result.evidence
        if session_id:
            evidence = [e for e in evidence if e.get("source_session_id") == session_id]
        items = [self._evidence_to_card(e) for e in evidence]

        source_types = _merge_filter_values(source_types, channels)
        event_types = _merge_filter_values(event_types, types)
        status_values = _as_list(status)
        occurred_range = (_parse_filter_date(occurred_from), _parse_filter_date(occurred_to))
        created_range = (_parse_filter_date(created_from), _parse_filter_date(created_to))
        valid_range = (_parse_filter_date(valid_from), _parse_filter_date(valid_to))

        def keep(card: dict) -> bool:
            if source_types and card.get("source_type") not in set(source_types):
                return False
            if event_types and card.get("type") not in set(event_types) and card.get("kind") not in set(event_types):
                return False
            if modalities and card.get("modality") not in set(modalities):
                return False
            if kinds and card.get("kind") not in set(kinds):
                return False
            if source_roles and card.get("role") not in set(source_roles):
                return False
            if session_id and card.get("session_id") != session_id:
                return False
            if status_values and card.get("status") not in set(status_values):
                return False
            if quantity_units:
                quantity = card.get("quantity") or {}
                if quantity.get("unit") not in set(quantity_units):
                    return False
            if entity:
                roles = card.get("entity_roles") or {}
                hay = " ".join(
                    str(v) for v in (
                        [card.get("content", ""), card.get("content_preview", "")]
                        + list(roles.values() if isinstance(roles, dict) else [])
                    )
                ).lower()
                if entity.lower() not in hay:
                    return False
            if entity_roles and not _matches_entity_roles(card.get("entity_roles") or {}, entity_roles):
                return False
            if min_confidence is not None and float(card.get("confidence") or 0.0) < float(min_confidence):
                return False
            if not _date_in_range(card.get("event_date") or card.get("occurred_start"), *occurred_range, missing_ok=False):
                return False
            if not _date_in_range(card.get("session_time"), *created_range, missing_ok=False):
                return False
            if not _date_in_range(card.get("valid_from"), valid_range[0], None, missing_ok=True):
                return False
            if not _date_in_range(card.get("valid_until"), None, valid_range[1], missing_ok=True):
                return False
            hay = _filter_text(card)
            if include_terms and not all(str(term).lower() in hay for term in include_terms):
                return False
            if exclude_terms and any(str(term).lower() in hay for term in exclude_terms):
                return False
            return True

        filtered_all = [c for c in items if keep(c)]
        filtered = filtered_all[offset:offset + top_k]
        next_offset = offset + len(filtered)
        return {
            "query": query,
            "user_id": self._user_id,
            "items": filtered,
            "offset": offset,
            "next_offset": next_offset,
            "has_more": next_offset < len(filtered_all),
            "total_candidates": len(filtered_all),
            "filters": {
                "source_types": source_types or [],
                "event_types": event_types or [],
                "modalities": modalities or [],
                "kinds": kinds or [],
                "source_roles": source_roles or [],
                "session_id": session_id,
                "entity": entity,
                "entity_roles": entity_roles or {},
                "status": status_values or [],
                "quantity_units": quantity_units or [],
                "occurred_from": occurred_from,
                "occurred_to": occurred_to,
                "created_from": created_from,
                "created_to": created_to,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "min_confidence": min_confidence,
                "include_terms": include_terms or [],
                "exclude_terms": exclude_terms or [],
            },
            "available_next_tools": self._next_tools_for_search(evidence),
            "degraded": [_degradation_to_dict(d) for d in result.degraded],
        }

    def result(self, memory_id: str) -> dict:
        if not memory_id:
            raise ToolError("invalid_request", "memory_id is required")
        # Normalize the id the agent pasted back from an evidence card. Search/
        # fusion hand out `ev_fact:<fact_id>` / `ev_raw:<turn_id>` and, for turn
        # evidence, `ev_turn:<session_id>:<turn_id>` — but storage is keyed by the
        # bare id. Stripping only the `ev_*:` prefix leaves the embedded
        # `<session_id>:` on turn ids, so the lookup 404s (0705 audit: browser_
        # inspect 37% "memory_not_found"). fact/span/turn ids are colon-free, so
        # the bare id is always the final colon segment after prefix-stripping.
        memory_id = _strip_evidence_prefix(memory_id.strip())
        if ":" in memory_id:
            memory_id = memory_id.rsplit(":", 1)[-1]
        fact = self._store.get_fact_event(memory_id)
        if fact is not None:
            spans = self._store.get_source_spans_by_ids(fact.source_span_ids)
            edges = self._store.get_fact_edges(self._user_id, fact_id=fact.fact_id)
            return {
                "user_id": self._user_id,
                "result_kind": "fact",
                "memory": self._fact_to_full(fact, spans, edges),
            }

        # Fusion search can surface source-level evidence that has no extracted
        # FactEvent. Those result ids must remain inspectable; otherwise a valid
        # raw span produces a misleading 404 and agents may treat it as absent.
        span = self._store.get_source_span(memory_id)
        if span is not None:
            return {
                "user_id": self._user_id,
                "result_kind": "source_span",
                "memory": self._span_to_card(span),
                "source_span": span.to_dict(),
            }

        # Issue #75: the raw-turns recall path surfaces ev_raw:<turn_id> evidence
        # (no FactEvent, no SourceSpan). Keep it inspectable for the same reason
        # spans are — a valid raw turn must not 404 and read as absent.
        turn = self._store.get_raw_turn(memory_id)
        if turn is not None:
            return {
                "user_id": self._user_id,
                "result_kind": "raw_turn",
                "memory": {
                    "id": turn.turn_id,
                    "turn_id": turn.turn_id,
                    "evidence_id": f"ev_raw:{turn.turn_id}",
                    "session_id": turn.session_id,
                    "role": turn.role,
                    "content": turn.content,
                    **_preview_fields(turn.content, "raw_turn", is_source_excerpt=True),
                    "source_type": "raw_message",
                    "kind": "raw_turn",
                    "status": "active",
                    # Contract §D4 axis ③: this is the MENTION time (session time),
                    # not ingest wallclock. session_time is the canonical key;
                    # created_at kept as a deprecated alias (readers predating 0706
                    # parse it) — do not add new consumers of created_at.
                    "session_time": _ts_to_iso(turn.timestamp),
                    "created_at": _ts_to_iso(turn.timestamp),
                },
            }

        raise ToolError(
            "memory_not_found",
            f"Search result {memory_id} was not found as a FactEvent, SourceSpan, or RawTurn",
            status=404,
        )

    def raw_search(
        self,
        query: str,
        *,
        top_k: int = 20,
        session_id: str = "",
        from_ts: Optional[float] = None,
        to_ts: Optional[float] = None,
    ) -> dict:
        if not query:
            raise ToolError("invalid_request", "query is required")
        bm25_idx = get_bm25_index(self._store)
        bm25 = bm25_idx.search_source_spans(query, self._user_id, n=max(top_k * 2, 40))
        spans: dict[str, SourceSpan] = {span.span_id: span for span, _ in bm25}
        degraded: list[Degradation] = []
        for span in vector.search_source_spans(
            query, store=self._store, user_id=self._user_id,
            n=max(top_k, 20), degraded=degraded,
        ):
            spans.setdefault(span.span_id, span)
        candidates = list(spans.values())

        def in_window(span: SourceSpan) -> bool:
            if session_id and span.session_id != session_id:
                return False
            # RED LINE: a span with no session_time is NEVER filtered out.
            if span.session_time is None:
                return True
            if from_ts is not None and span.session_time < from_ts:
                return False
            if to_ts is not None and span.session_time > to_ts:
                return False
            return True

        candidates = [s for s in candidates if in_window(s)][:top_k]
        _log_degraded("raw_search", degraded)
        return {
            "query": query,
            "user_id": self._user_id,
            "items": [self._span_to_card(s) for s in candidates],
            "degraded": [_degradation_to_dict(d) for d in degraded],
        }

    def session(self, session_id: str) -> dict:
        if not session_id:
            raise ToolError("invalid_request", "session_id is required")
        rows = self._store._conn.execute(
            "SELECT turn_id, role, content, timestamp FROM raw_turns "
            "WHERE user_id=? AND session_id=? ORDER BY timestamp ASC, turn_id ASC",
            (self._user_id, session_id),
        ).fetchall()
        turns = [
            {
                "turn_id": row["turn_id"],
                "evidence_id": f"ev_turn:{session_id}:{row['turn_id']}",
                "role": row["role"],
                "content": row["content"],
                **_preview_fields(row["content"], "raw_turn", is_source_excerpt=True),
                # session_time = canonical mention-time key (contract §D4 axis ③);
                # timestamp kept as deprecated alias for pre-0706 consumers.
                "session_time": _ts_to_iso(row["timestamp"]),
                "timestamp": _ts_to_iso(row["timestamp"]),
            }
            for row in rows
        ]
        return {
            "user_id": self._user_id,
            "session_id": session_id,
            "items": turns,
            "count": len(turns),
        }

    def entity_timeline(self, entity_id: str) -> dict:
        if not entity_id:
            raise ToolError("invalid_request", "entity_id is required")
        mentions = self._store.get_entity_mentions_by_terms(
            self._user_id, [entity_id], n=200
        )
        fact_ids = [m.fact_id for m in mentions if m.fact_id]
        seen: set[str] = set()
        facts: list[FactEvent] = []
        for fid in fact_ids:
            if fid in seen:
                continue
            seen.add(fid)
            fact = self._store.get_fact_event(fid)
            if fact is not None:
                facts.append(fact)
        facts.sort(key=lambda f: f.occurred_start or 0.0)
        items = []
        for fact in facts:
            spans = self._store.get_source_spans_by_ids(fact.source_span_ids)
            items.append(self._fact_to_card(fact, spans))
        return {
            "user_id": self._user_id,
            "entity_id": entity_id,
            "items": items,
            "count": len(items),
        }

    def explore(
        self,
        start_id: str,
        *,
        start_type: str = "memory",
        depth: int = 1,
        edge_types: Optional[list[str]] = None,
        limit: int = 25,
    ) -> dict:
        if not start_id:
            raise ToolError("invalid_request", "start_id is required")
        depth = max(1, min(depth, 3))
        wanted_edges = set(edge_types or [])
        visited: set[str] = {start_id}
        frontier: list[str] = [start_id]
        edges_out: list[dict] = []
        nodes_out: dict[str, dict] = {}

        for _ in range(depth):
            next_frontier: list[str] = []
            for node_id in frontier:
                edges = self._store.get_fact_edges(self._user_id, fact_id=node_id)
                for edge in edges:
                    if wanted_edges and str(edge.edge_type.value if hasattr(edge.edge_type, "value") else edge.edge_type) not in wanted_edges:
                        continue
                    edges_out.append(edge.to_dict())
                    other = edge.dst_id if edge.src_fact_id == node_id else edge.src_fact_id
                    if other and other not in visited:
                        visited.add(other)
                        next_frontier.append(other)
                if len(edges_out) >= limit:
                    break
            if len(edges_out) >= limit:
                break
            frontier = next_frontier

        for node_id in visited:
            fact = self._store.get_fact_event(node_id)
            if fact is not None:
                nodes_out[node_id] = self._fact_to_card(fact, [])

        return {
            "user_id": self._user_id,
            "start_id": start_id,
            "start_type": start_type,
            "count": {"nodes": len(nodes_out), "edges": len(edges_out)},
            "nodes": list(nodes_out.values()),
            "edges": edges_out[:limit],
        }

    def event_timeline(
        self,
        events: list[str],
        *,
        top_k_per_event: int = 5,
    ) -> dict:
        if not events or not isinstance(events, list):
            raise ToolError("invalid_request", "events must be a non-empty list of strings")
        groups = []
        for event in events:
            event_str = str(event)
            sub = self.search(event_str, top_k=top_k_per_event, include_context=False)
            groups.append({
                "event": event_str,
                "items": sub["items"],
            })
        return {
            "user_id": self._user_id,
            "groups": groups,
        }

    def date_calc(self, mode: str, **kwargs: Any) -> dict:
        return date_calc(mode, **kwargs)

    def compute(
        self,
        operator: str,
        inputs: list[str],
        *,
        params: Optional[dict[str, Any]] = None,
    ) -> dict:
        if not operator:
            raise ToolError("invalid_request", "operator is required")
        if not isinstance(inputs, list) or not inputs:
            raise ToolError("empty_input", "inputs must be a non-empty list of evidence ids")
        input_ids = [str(evidence_id) for evidence_id in inputs]
        cards: list[dict] = []
        for evidence_id in input_ids:
            if not evidence_id.startswith("ev_fact:"):
                raise ToolError("incompatible_input_kind", f"Unsupported compute input: {evidence_id}")
            fact_id = _strip_evidence_prefix(evidence_id)
            fact = self._store.get_fact_event(fact_id)
            if fact is None:
                raise ToolError("memory_not_found", f"FactEvent {fact_id} not found", status=404)
            spans = self._store.get_source_spans_by_ids(fact.source_span_ids)
            cards.append(self._fact_to_card(fact, spans))
        try:
            derived = compute_evidence(operator, input_ids, cards, params=params)
        except ComputeError as exc:
            raise ToolError(exc.code, exc.message)
        return {
            "user_id": self._user_id,
            "operator": operator,
            "inputs": input_ids,
            "items": [derived],
            "derived": derived,
        }

    def evidence_count(
        self,
        query: str,
        labels: list[str],
        *,
        from_ts: Optional[float] = None,
        to_ts: Optional[float] = None,
        top_k_per_label: int = 20,
    ) -> dict:
        if not query:
            raise ToolError("invalid_request", "query is required")
        if not labels:
            raise ToolError("invalid_request", "labels must be a non-empty list")
        groups = []
        for label in labels:
            label_str = str(label)
            search_query = f"{query} {label_str}".strip()
            sub = self.search(search_query, top_k=top_k_per_label, include_context=False)
            items = sub["items"]
            if from_ts is not None or to_ts is not None:
                def in_window(card: dict) -> bool:
                    ts = self._card_time_epoch(card)
                    if ts is None:
                        return True
                    if from_ts is not None and ts < from_ts:
                        return False
                    if to_ts is not None and ts > to_ts:
                        return False
                    return True
                items = [c for c in items if in_window(c)]
            groups.append({
                "label": label_str,
                "items": items,
                "candidate_count": len(items),
            })
        return {
            "query": query,
            "user_id": self._user_id,
            "labels": list(labels),
            "groups": groups,
            "roster": self._count_roster(groups),
        }

    def _count_roster(self, groups: list[dict]) -> list[dict]:
        """One entry per distinct fact, carrying its date and matching labels.

        `groups` is per-label and labels are synonyms, so their hits overlap by
        construction — summing `candidate_count` double-counts every fact that
        matched more than one. Deduping, dating and ordering are mechanical and
        belong here, where they are deterministic; what is left for the reader
        is the one step that needs judgment, applying the question's literal
        qualifiers, and then the count is just the length of what survives.
        """
        by_fact: dict[str, dict] = {}
        for group in groups:
            label = group["label"]
            for card in group["items"]:
                key = str(card.get("fact_id") or card.get("evidence_id") or "")
                if not key:
                    continue
                entry = by_fact.get(key)
                if entry is None:
                    entry = {
                        "fact_id": card.get("fact_id"),
                        "evidence_id": card.get("evidence_id"),
                        "event_date": card.get("event_date"),
                        "occurred_epoch": self._card_time_epoch(card),
                        "content": card.get("content"),
                        "labels": [],
                    }
                    by_fact[key] = entry
                if label not in entry["labels"]:
                    entry["labels"].append(label)
        # Undated entries sort last rather than crashing the comparison: a
        # missing date is a reason for the reader to look, not to drop the row.
        return sorted(
            by_fact.values(),
            key=lambda e: (e["occurred_epoch"] is None, e["occurred_epoch"] or 0.0),
        )

    def _card_time_epoch(self, card: dict) -> float | None:
        """Resolve a search card's unrendered event/session epoch when possible."""
        fact_id = card.get("fact_id")
        if fact_id:
            fact = self._store.get_fact_event(str(fact_id))
            if fact is not None and fact.occurred_start is not None:
                return float(fact.occurred_start)
        for span_id in card.get("source_span_ids") or []:
            span = self._store.get_source_span(str(span_id))
            if span is not None and span.session_time is not None:
                return float(span.session_time)
        evidence_id = str(card.get("evidence_id") or "")
        if evidence_id.startswith("ev_span:"):
            span = self._store.get_source_span(_strip_evidence_prefix(evidence_id))
            if span is not None and span.session_time is not None:
                return float(span.session_time)
        turn_id = card.get("turn_id")
        if turn_id:
            turn = self._store.get_raw_turn(str(turn_id))
            if turn is not None:
                return float(turn.timestamp)
        rendered = card.get("event_date") or card.get("session_time")
        if rendered in (None, ""):
            return None
        try:
            return _parse_time_boundary(rendered, upper=False)
        except ToolError:
            return None

    def _evidence_to_card(self, ev: dict) -> dict:
        support = ev.get("support_text") or ""
        return {
            "id": ev.get("fact_id") or ev.get("evidence_id"),
            "fact_id": ev.get("fact_id"),
            "evidence_id": ev.get("evidence_id"),
            "source_span_ids": ev.get("source_span_ids") or [],
            "source_trace_ids": ev.get("source_trace_ids") or ev.get("source_span_ids") or [],
            "content": support,
            **_preview_fields(
                support,
                "support_text",
                is_source_excerpt=bool(ev.get("source_span_ids")),
            ),
            "extracted_support_text": ev.get("extracted_support_text") or "",
            "type": ev.get("event_type") or ev.get("kind"),
            "kind": ev.get("kind"),
            "source_type": ev.get("source_type"),
            "modality": ev.get("modality"),
            "status": ev.get("status"),
            "version_status": ev.get("version_status"),
            "superseded_by": ev.get("superseded_by"),
            "current_head_id": ev.get("current_head_id"),
            "superseded_fact_ids": ev.get("superseded_fact_ids") or [],
            "update_chain_summary": ev.get("update_chain_summary") or "",
            "session_id": ev.get("source_session_id"),
            "turn_id": ev.get("source_turn_id"),
            "role": ev.get("source_role") or "user",
            "event_date": ev.get("occurred_start"),
            "occurred_start": ev.get("occurred_start"),
            "occurred_end": ev.get("occurred_end"),
            "valid_from": ev.get("valid_from"),
            "valid_to": ev.get("valid_to") or ev.get("valid_until"),
            "valid_until": ev.get("valid_until"),
            "session_time": ev.get("session_time"),
            "document_time": ev.get("document_time"),
            "predicate_raw": ev.get("predicate_raw"),
            "predicate_canonical": ev.get("predicate_canonical"),
            "entity_roles": ev.get("entity_roles") or {},
            # _js_int_num: same CLI-JSON number emulation as the fact card
            # below — this is the search-item path, the one the forced step-0
            # search (and therefore every planner evidence card) flows through.
            "quantity": {**(ev.get("quantity") or {}),
                         "value": _js_int_num((ev.get("quantity") or {}).get("value"))}
                        if ev.get("quantity") else {},
            "confidence": ev.get("confidence"),
            "confidence_reason": ev.get("confidence_reason") or "",
            "rank": ev.get("rank") or {},
            "routes": ev.get("routes") or [],
            "actions": self._actions_for_evidence(ev),
        }

    def _fact_to_card(self, fact: FactEvent, spans: list[SourceSpan]) -> dict:
        text = "\n".join(s.text for s in spans if s.text).strip() or fact.predicate_raw
        extracted_support_text = fact.metadata.get("support_text", "") if isinstance(fact.metadata, dict) else ""
        preview_source = "source_span" if spans else "predicate_raw"
        _time_prec = fact.metadata.get("time_precision") if isinstance(fact.metadata, dict) else None
        _valid_prec = _time_prec if fact.occurred_start is None else None
        return {
            "id": fact.fact_id,
            "fact_id": fact.fact_id,
            "evidence_id": f"ev_fact:{fact.fact_id}",
            "source_trace_ids": fact.source_span_ids,
            "content": text,
            **_preview_fields(text, preview_source, is_source_excerpt=bool(spans)),
            "extracted_support_text": extracted_support_text,
            "type": fact.event_type,
            "kind": fact.kind.value if hasattr(fact.kind, "value") else fact.kind,
            "modality": fact.modality.value if hasattr(fact.modality, "value") else fact.modality,
            "source_type": fact.source_type.value if hasattr(fact.source_type, "value") else fact.source_type,
            "status": fact.status.value if hasattr(fact.status, "value") else fact.status,
            **self._lineage_fields(fact),
            "session_id": spans[0].session_id if spans else "",
            "turn_id": spans[0].turn_id if spans else "",
            # Precision round-trip (metadata time_precision → renderer): the epoch
            # storage loses the resolved granularity, so a year fact ("2019") was
            # rendered "2019-01-01" (fabricated day). Pass the stored precision so the
            # reader sees honest partial-ISO. time_precision is occurred-first derived,
            # so only trust it for valid_* when there is no occurred date to own it.
            "event_date": _ts_to_iso(fact.occurred_start, _time_prec),
            "occurred_start": _ts_to_iso(fact.occurred_start, _time_prec),
            "occurred_end": _ts_to_iso(fact.occurred_end, _time_prec),
            "valid_from": _ts_to_iso(fact.valid_from, _valid_prec),
            "valid_to": _ts_to_iso(fact.valid_until, _valid_prec),
            "valid_until": _ts_to_iso(fact.valid_until, _valid_prec),
            "time_precision": _time_prec,
            "predicate_raw": fact.predicate_raw,
            "predicate_canonical": fact.predicate_canonical,
            "entity_roles": fact.metadata.get("entity_roles", {}),
            # _js_int_num: source served this card through a Node CLI whose
            # JSON round-trip prints whole floats as integers (JS has one
            # number type) — the planner always saw 10, never 10.0, even
            # though sqlite's REAL column and the Python layer both held
            # 10.0. In-process dispatch skips that round-trip, so emulate it
            # here or planner-visible bytes drift (audit 0724).
            "quantity": {"value": _js_int_num(fact.quantity_value), "unit": fact.quantity_unit},
            "confidence": fact.confidence,
            "actions": [
                {"tool": "memory.tool.result", "memory_id": fact.fact_id},
            ],
        }

    def _fact_to_full(
        self,
        fact: FactEvent,
        spans: list[SourceSpan],
        edges: list[FactEdge],
    ) -> dict:
        return {
            **self._fact_to_card(fact, spans),
            "source_span_ids": fact.source_span_ids,
            "source_trace_ids": fact.source_span_ids,
            "source_spans": [s.to_dict() for s in spans],
            "source_traces": [s.to_dict() for s in spans],
            "edges": [e.to_dict() for e in edges],
            "provenance": fact.provenance,
            "predicate_raw": fact.predicate_raw,
            "predicate_canonical": fact.predicate_canonical,
            "occurred_start": _ts_to_iso(fact.occurred_start),
            "occurred_end": _ts_to_iso(fact.occurred_end),
            "actions": [
                {"tool": "memory.tool.session", "session_id": spans[0].session_id if spans else ""},
                {"tool": "memory.tool.explore", "start_id": fact.fact_id, "start_type": "memory"},
            ],
        }

    def _span_to_card(self, span: SourceSpan) -> dict:
        return {
            "id": span.span_id,
            "span_id": span.span_id,
            "message_unit_id": span.span_id,
            "evidence_id": f"ev_span:{span.span_id}",
            "session_id": span.session_id,
            "turn_id": span.turn_id,
            "role": span.role,
            "content": span.text,
            **_preview_fields(span.text, "source_span", is_source_excerpt=True),
            "source_type": "explicit_text",
            "type": "other",
            "kind": "fact",
            "modality": "assistant_advice" if span.role == "assistant" else "past_event",
            "status": "active",
            "version_status": "current",
            # session_time = canonical mention-time key (contract §D4 axis ③).
            # created_at here has ALWAYS held span.session_time (a mention time
            # under an ingest-wallclock name) — kept as deprecated alias only.
            "session_time": _ts_to_iso(span.session_time),
            "created_at": _ts_to_iso(span.session_time),
            "valid_from": None,
            "valid_to": None,
            "valid_until": None,
            "entity_roles": {"subject": "user"},
            "confidence": span.alignment_confidence,
            "rank": {},
            "actions": [
                {"tool": "memory.tool.session", "session_id": span.session_id},
                {
                    "tool": "memory.tool.raw-search",
                    "query": span.text,
                    "session_id": span.session_id,
                },
            ],
        }

    def _key_evidence_to_retrieve_item(self, ev: dict) -> dict:
        return {
            "id": ev.get("fact_id") or "",
            "content": ev.get("support_text") or "",
            "type": ev.get("source_role") or "fact",
            "tags": [],
            "entities": [],
            "keywords": [],
            "importance": int(round(float(ev.get("confidence") or 0.5) * 10)),
            "recency_score": 0.0,
            "access_count": 0,
            "session_time": ev.get("session_time"),
            "event_date": ev.get("occurred_start"),
            "session_id": ev.get("source_session_id") or "",
            "user_id": self._user_id,
            "status": "active",
            "context_desc": ev.get("confidence_reason") or "",
        }

    def _actions_for_evidence(self, ev: dict) -> list[dict]:
        actions = []
        if ev.get("fact_id"):
            actions.append({"tool": "memory.tool.result", "memory_id": ev["fact_id"]})
        if ev.get("source_session_id"):
            actions.append({"tool": "memory.tool.session", "session_id": ev["source_session_id"]})
        if ev.get("fact_id"):
            actions.append({"tool": "memory.tool.explore", "start_id": ev["fact_id"], "start_type": "memory"})
        return actions

    def _lineage_fields(self, fact: FactEvent) -> dict:
        status = fact.status.value if hasattr(fact.status, "value") else str(fact.status)
        raw_provenance = getattr(fact, "provenance", {})
        provenance = raw_provenance if isinstance(raw_provenance, dict) else {}
        superseded_by = provenance.get("superseded_by_fact_id")
        outgoing = self._store.get_fact_edges(
            self._user_id,
            fact_id=fact.fact_id,
            edge_type=EdgeType.SUPERSEDES.value,
        )
        superseded_fact_ids = [edge.dst_id for edge in outgoing if edge.dst_id]
        if superseded_by:
            version_status = "superseded"
            current_head_id = superseded_by
        else:
            version_status = "current" if status == FactStatus.ACTIVE.value else status
            current_head_id = fact.fact_id
        summary = ""
        if superseded_fact_ids:
            summary = f"current version; supersedes {len(superseded_fact_ids)} older fact(s)"
        elif superseded_by:
            summary = f"superseded by {superseded_by}"
        return {
            "version_status": version_status,
            "superseded_by": superseded_by,
            "current_head_id": current_head_id,
            "superseded_fact_ids": superseded_fact_ids,
            "update_chain_summary": summary,
        }

    def _next_tools_for_search(self, evidence: list[dict], *, has_more: bool = True) -> list[dict]:
        # Advertise evidence-gathering tools only. The answer model owns
        # arithmetic and date reasoning over selected base evidence.
        tools = [
            {"name": "memory.tool.refine", "description": "Deterministically filter current search space by metadata fields."},
            {"name": "memory.tool.raw-search", "description": "Drop to raw turns when entities/dates/numbers are missing."},
            {"name": "memory.tool.event-timeline", "description": "Group evidence per event phrase."},
            {"name": "memory.tool.evidence-count", "description": "Per-label candidate collection for most/how-many."},
            {"name": "memory.tool.entity-timeline", "description": "Timeline of mentions for one entity."},
        ]
        if has_more:
            tools.insert(
                0,
                {"name": "memory.tool.search-more", "description": "Continue the same ranked search using next_offset."},
            )
        return tools

    def _format_context(self, evidence: list[dict]) -> str:
        # R15 (see module docstring): NOT swapped for sodamem.context.build_context()
        # — TODO for whoever revisits search()'s response shape (Phase 4, when
        # tests/test_http_cli_adapter.py's full shape-locking assertions land
        # in this repo alongside http_server.py and can be safely rewritten).
        lines = []
        for ev in evidence:
            support = (ev.get("support_text") or "")[:300]
            if support:
                lines.append(f"- {support}")
        return "\n".join(lines)


# ---------------- module-level helpers ----------------
#
# _ts_to_iso is imported from sodamem.memory._shared, not defined here. This
# module used to have its OWN separate implementation that rendered via
# datetime.fromtimestamp(ts, tz=timezone.utc) — a naive-local vs. UTC
# divergence from the shared version used on the exact same FactEvent
# fields, found by adversarial audit (2026-07-01): the
# same fact could show a different calendar date depending which of this
# module's tools (result/explore/entity-timeline) vs. client.py's evidence
# path (search) rendered it, for any date-only fact (no time-of-day — the
# common case, since _iso_to_ts anchors those at local midnight). Unifying
# on _helpers.py's naive-local convention (rather than switching everything
# to UTC) requires zero data migration, since that's the convention the
# ENCODE side (_iso_to_ts, used by extractor.py to write every FactEvent
# time field) already uses — flipping to UTC would silently shift every
# already-ingested date-only timestamp by up to 8 hours' worth of calendar
# date on read, depending on the host's local timezone.


def _preview_fields(text: Any, source: str, *, is_source_excerpt: bool) -> dict:
    raw = "" if text is None else str(text)
    return {
        "content_preview": raw[:_CONTENT_PREVIEW_CHARS],
        "content_preview_source": _PREVIEW_SOURCE_NAMES.get(source, source),
        "content_preview_truncated": len(raw) > _CONTENT_PREVIEW_CHARS,
        "content_preview_is_source_excerpt": bool(is_source_excerpt),
    }


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _parse_iso_date(value) -> datetime:
    # Naive (no tzinfo), matching _helpers.py's _iso_to_ts/_ts_to_iso — this
    # function decodes strings that mostly ORIGINATE from those (e.g. a
    # card's event_date), and must round-trip against the same convention:
    # an int/float ts is a raw epoch produced by the naive-local encoder, and
    # a string like "2023-06-15" already IS local-midnight in that encoding.
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value))
    s = str(value).strip()
    if _ISO_DATE.match(s):
        return datetime.fromisoformat(s[:10])
    raise ValueError(f"unrecognized date: {value!r}")


def _as_list(value: Any) -> list:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, list):
        return value
    return [value]


def _merge_filter_values(primary: Optional[list[str]], legacy: Optional[list[str]]) -> list[str]:
    merged: list[str] = []
    for value in _as_list(primary) + _as_list(legacy):
        if not isinstance(value, str):
            continue
        if value and value not in merged:
            merged.append(value)
    return merged


def _parse_filter_date(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        return _parse_iso_date(value)
    except ValueError:
        return None


def _date_in_range(value: Any, start: Optional[datetime], end: Optional[datetime], *, missing_ok: bool) -> bool:
    if start is None and end is None:
        return True
    if value in (None, ""):
        return missing_ok
    try:
        dt = _parse_iso_date(value)
    except ValueError:
        return missing_ok
    if start is not None and dt < start:
        return False
    if end is not None and dt > end:
        return False
    return True


def _matches_entity_roles(card_roles: dict, wanted_roles: dict) -> bool:
    if not isinstance(card_roles, dict) or not isinstance(wanted_roles, dict):
        return True
    for role, wanted in wanted_roles.items():
        values = card_roles.get(role)
        if values is None:
            return False
        actual_values = values if isinstance(values, list) else [values]
        wanted_values = wanted if isinstance(wanted, list) else [wanted]
        actual_text = " ".join(str(v).lower() for v in actual_values)
        if not any(str(w).lower() in actual_text for w in wanted_values):
            return False
    return True


def _filter_text(card: dict) -> str:
    roles = card.get("entity_roles") or {}
    return " ".join(
        str(v)
        for v in [
            card.get("content", ""),
            card.get("content_preview", ""),
            card.get("predicate_raw", ""),
            card.get("predicate_canonical", ""),
            card.get("type", ""),
            card.get("kind", ""),
            card.get("modality", ""),
            roles,
        ]
    ).lower()
