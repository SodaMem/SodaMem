"""The retrieval plan object: status eligibility (DR-014) + the trace fields
fusion emits per query.

`QueryPlan.default()` is the only constructor any production path reaches.
`parse_query_plan` — the one would-be producer of a non-default plan
(`temporal_policy="hard_filter"`) — was never ported, and neither were the
intent classifiers or the EvidenceBoard machinery around it.

What that meant in practice: `temporal_match` and `_fact_interval` sat here
being unreachable, because the `plan_active` branch in `fusion.py` that
called them required `trace["applied"]` true AND `temporal_policy != "none"`,
and `default()` produces neither. Both were deleted 0806, along with that
branch.

What stays is live: `allowed_statuses` drives the DR-014 status filter in
`fusion.retrieve()`, and `temporal_plan_dict()`/`graph_plan_dict()` are
serialized into the retrieval trace on every query. The unused plan FIELDS
are kept with them — they are what a real planner would fill in, and the
trace shape is observable output that costs nothing to keep stable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sodamem.models import FactStatus


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


