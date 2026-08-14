"""Deterministic temporal window + status eligibility for fusion retrieval
(DR-013).

Ported from the predecessor implementation ONLY — `QueryPlan`
(the dataclass), `_fact_interval`, and `temporal_match`. Per the migration
map's R2 verdict, everything else in the source file is deleted, not ported:

  - `classify_order_intent`/`classify_order_intent_llm`/`_INTENT_LLM_PROMPT`
    (source :140-198): zero production callers (grep-confirmed at port
    time) — `fusion.py`/`client.py` only ever imported `QueryPlan`/
    `temporal_match` from this module, never these.
  - `TimeConstraint`/`parse_time_constraint`/`apply_evidence_board` (source
    :201-477, the §C/§D EvidenceBoard machinery) and `parse_query_plan`
    (source :480-505): R2 deletion — EvidenceBoard is gated by
    `GRAPH_V2_BOARD`, production default OFF, and `parse_query_plan` is
    `QueryPlan`'s only would-be producer of a non-default plan
    (`temporal_policy="hard_filter"`) and itself has zero callers anywhere
    in the source repo. `client.py`'s one production call site
    (`retrieve_fusion_audit_bundle`) always constructs
    `QueryPlan.default("query_plan_disabled")` — the zero-regression
    fallback — never `parse_query_plan(...)`.
  - The `_LATEST_RE`/`_OLDEST_RE`/`_HISTORY_RE` regexes (source :129-137)
    and the `logging`/`calendar_resolve` imports that supported the deleted
    functions: with every consumer of these three regexes gone
    (`classify_order_intent` and `parse_time_constraint` were the only two),
    porting them forward would just be new dead code — the source's own
    "只搬 QueryPlan/temporal_match" instruction already implies dropping
    everything a live consumer doesn't reach.

`QueryPlan.default()` — the sole production-reachable constructor — is
unaffected by any of these deletions: it never touches the deleted code.
`temporal_match`'s `hard_filter`/`soft_boost` policy branches in
`fusion.py` remain structurally present (this dataclass still carries
`temporal_policy`) but are unreachable from `QueryPlan.default()`
(`temporal_policy="none"`, `trace={"applied": False, ...}`) — this is
exactly the pre-existing, byte-identical production behavior; nothing new
is introduced or removed by this port.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sodamem.models import FactEvent, FactKind, FactStatus


@dataclass
class QueryPlan:
    # ---- temporal (DR-013) ----
    signal_type: str = "none"              # explicit | fuzzy | none
    expression_text: str = ""
    target_time_field: str = "occurred_time"
    temporal_policy: str = "none"          # hard_filter | soft_boost | none
    range_start: Optional[float] = None    # 确定性代码填
    range_end: Optional[float] = None
    order_intent: str = "default"          # default | latest_first | chronological
    # ---- eligibility (DR-014) ----
    fact_time_mode: str = "current"
    allowed_statuses: set = field(default_factory=lambda: {FactStatus.ACTIVE})
    # ---- graph plan (DR-009/012) ----
    relation_allowlist: list = field(default_factory=list)   # relation_key 白名单
    target_roles: list = field(default_factory=list)         # 期望命中的 participant role
    target_entity_types: list = field(default_factory=list)
    answer_shape: str = ""                                   # single_fact|entity_list|count|...
    path_patterns: list = field(default_factory=list)        # [["works_with","travel_by_airline"]]
    max_semantic_hops: int = 1
    # ---- 审计 ----
    trace: dict = field(default_factory=dict)

    @classmethod
    def default(cls, reason: str = "disabled") -> "QueryPlan":
        """零回归 fallback：none 时间信号 + current 资格（只 ACTIVE）。"""
        return cls(trace={"applied": False, "reason": reason})

    def temporal_plan_dict(self) -> dict:
        return {
            "signal_type": self.signal_type,
            "expression_text": self.expression_text,
            "target_time_field": self.target_time_field,
            "policy": self.temporal_policy,
            "range_start": self.range_start,
            "range_end": self.range_end,
            "fact_time_mode": self.fact_time_mode,
            "allowed_statuses": sorted(s.value for s in self.allowed_statuses),
        }

    def graph_plan_dict(self) -> dict:
        return {
            "relation_allowlist": self.relation_allowlist,
            "target_roles": self.target_roles,
            "target_entity_types": self.target_entity_types,
            "answer_shape": self.answer_shape,
            "path_patterns": self.path_patterns,
            "max_semantic_hops": self.max_semantic_hops,
        }


def _fact_interval(fact: FactEvent, target_field: str) -> Optional[tuple[float, Optional[float]]]:
    """按目标字段 + fact.kind 取可比较时间区间 [start, end]。

    DR-014：Event(EVENT/FACT) 用 occurred_*，State(STATE/PREFERENCE/PROFILE) 用 valid_*。
    target_time_field 显式指定 valid/source 时优先尊重之。
    """
    is_state = fact.kind in (FactKind.STATE, FactKind.PREFERENCE, FactKind.PROFILE)
    if target_field == "valid_time" or (target_field == "occurred_time" and is_state):
        start = fact.valid_from
        if start is None:
            return None
        return start, fact.valid_until  # end=None 表示仍然有效
    # occurred_time（事件）
    start = fact.occurred_start or fact.valid_from
    if start is None:
        return None
    return start, (fact.occurred_end or start)


def temporal_match(fact: FactEvent, plan: QueryPlan) -> Optional[bool]:
    """fact 是否落在 plan 的时间窗口内。

    返回 True/False；无可比较时间返回 None（hard_filter 下视为不匹配并排除）。
    采用区间相交判定（覆盖 as_of 时点、history 区间两类）。
    """
    if plan.range_start is None and plan.range_end is None:
        return None
    interval = _fact_interval(fact, plan.target_time_field)
    if interval is None:
        return None
    fs, fe = interval
    qs = plan.range_start if plan.range_start is not None else 0.0
    qe = plan.range_end if plan.range_end is not None else float("inf")
    fe_eff = fe if fe is not None else float("inf")
    return fs <= qe and fe_eff >= qs
