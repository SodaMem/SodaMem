"""多路召回与融合排序核心（graph_v2 0606 PRD 的确定性实现）。

Ported from the predecessor implementation (677L) — Step 1 of this task was
a mandatory full line-by-line audit (R3 + R7 + the FACT_GROUP/R2.9 candidate
check) before any of this file was written; see the R7
section for the complete grep trail. Two config-shape changes fall out of
that audit:

  - `FusionConfig` (now in `.config`, not defined in this file) drops the
    `temporal_hard_filter_enabled` field entirely (R7 winner-goes-flagless):
    a repo-wide grep found this field read NOWHERE — not even inside this
    file — beyond its own declaration. It never gated anything; the only
    branch that resembles a "hard filter" (`retrieve()`'s `plan_active`
    block below) is driven by `QueryPlan.temporal_policy`, which only
    `parse_query_plan` (zero callers, deleted by this task's R2 verdict)
    ever sets away from `"none"`. Production's one call site always builds
    `QueryPlan.default(...)`, so that block is unreachable exactly as it was
    in the source — nothing about its reachability changes here.
  - `fact_group_enabled` (DR-015 same-event merge) IS read (`retrieve()`,
    `if self.config.fact_group_enabled: ...`) and gates real behavior, so
    the R2.9 audit keeps it — as a `RetrievalConfig` field (not
    `FusionConfig`; it's a routing decision, not a fusion-scoring weight).

CQRS split: `self._storage.bm25_search_*`/
`vector_search_*` calls become `self._bm25.search_*` (a `BM25Index` from
`.bm25`, cached per-Store) and `vector.search_*(..., degraded=...)` (`.vector`
module functions) — the read-path lexical/vector search moved out of the
write-side `Store`/`StorageBackend` object entirely.

`GRAPH_V2_DERIVED_CURRENCY` (a source module-level global) becomes
`self.config.derived_currency` (a `RetrievalConfig` field passed to this
class's constructor, not read from an env var) throughout.

刻意不做（后置，对 recall 对照非必需，与源文件一致未实现）:
  * LLM QueryPlan / temporal-plan / frontier best-first 选择。
  * Relation/Role Registry 与 Alias 晋升治理（DR-007/008/011）—— `_registry_key`/
    `_allows_multi_hop`/`_present_roles` below are ported verbatim from the
    source but, exactly as in the source, are dead code (zero call sites):
    this governance layer was never wired up in either repo. Left in place
    (not authorized for deletion by this task's R2/R7/R8/R2.9 decisions,
    which name specific deletions and nothing else) — flagged in the task
    report as a minor R3 finding, not acted on.
  * same-event 模型共指（DR-015 只做确定性 ID/来源合并）。
这些是 PRD 的治理/精排层；本模块先用最笨最清晰的确定性逻辑跑通 A/B/C/D/E。

本模块只负责"算出排序单元及其分数与审计"，evidence 文本物化由 search.py 完成，
避免与 evidence 字典格式耦合。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from sodamem.memory._shared import _date_bucket
from sodamem.memory.storage.store import Store
from sodamem.models import FactEvent, FactStatus

from . import vector
from .bm25 import get_bm25_index
from .config import Degradation, RetrievalConfig
from .query_plan import QueryPlan

logger = logging.getLogger(__name__)


# -------------------------------------------------------------------
# 中间结构
# -------------------------------------------------------------------

@dataclass
class RankingUnit:
    unit_id: str                              # "fact:<id>" 或 "span:<id>"
    kind: str                                 # "fact_event" | "source_span"
    object_id: str
    head_contributions: dict = field(default_factory=dict)  # head_id -> max contribution
    paths: list = field(default_factory=list)               # 审计：命中路径
    connection_density: float = 0.0
    temporal_boost: float = 0.0
    ranking_confidence: float = 0.0
    # DR-015：same-event 合并后挂载的成员事实（canonical 之外的同事件来源）。
    member_fact_ids: list = field(default_factory=list)
    merge_decision: str = ""
    # DR-014 #5/#6：孤立 SourceSpan（无关联 FactEvent 状态）可召回，但必须标记，
    # 不能单独支撑"当前状态已确认"类强结论。
    unstructured_source: bool = False

    def add(self, head_id: str, contribution: float, path: str) -> None:
        # DR-002/016：同一 Search Head 对同一排序单元只取最高一次贡献。
        prev = self.head_contributions.get(head_id, 0.0)
        if contribution > prev:
            self.head_contributions[head_id] = contribution
        self.paths.append(path)


@dataclass
class CalibrationResult:
    multipliers: list[float]
    trace: dict


def calibrate_relevance(scores: list[float | None]) -> CalibrationResult:
    """Identity calibration: route scores are used as-is.

    Score calibration (two-component GMM / percentile) was evaluated and
    removed. Multiple paired 100q A/Bs showed it only reorders
    already-recalled evidence with no net accuracy gain (calibration=none
    was always >= gmm). Production keeps identity multipliers.
    """
    return CalibrationResult([1.0] * len(scores), {"applied_method": "identity"})


# -------------------------------------------------------------------
# 时间窗口（确定性，DR-013 的规则子集）
# -------------------------------------------------------------------

_MONTHS = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}


def query_date_prefixes(query: str) -> list[str]:
    """从原始 query 抽取确定性日期锚点：ISO、'Month YYYY'、裸 4 位年。

    集中在融合层用于 temporal boost。
    """
    prefixes: list[str] = []
    for m in re.findall(r"\b(\d{4}-\d{2}(?:-\d{2})?)\b", query):
        prefixes.append(m)
    for mon, yr in re.findall(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{4})\b", query, re.I):
        prefixes.append(f"{yr}-{_MONTHS[mon.lower()]}")
    if not prefixes:
        for yr in re.findall(r"\b(19\d{2}|20\d{2})\b", query):
            prefixes.append(yr)
    out, seen = [], set()
    for p in prefixes:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _fact_time_keys(fact: FactEvent) -> list[str]:
    """fact 的可比较时间转成 YYYY / YYYY-MM / YYYY-MM-DD 前缀集合。

    Naive (no tzinfo) — matches the source's `_iso_to_ts`, the ENCODER that
    produced fact.occurred_start/valid_from in the first place.
    """
    ts = fact.occurred_start or fact.valid_from  # NOT created_at (ingestion wall-clock)
    if not ts:
        return []
    try:
        dt = datetime.fromtimestamp(float(ts))
    except (ValueError, OSError, OverflowError) as e:
        logger.warning(
            "_fact_time_keys: unconvertible timestamp %r on fact %s (%s) — no "
            "date-prefix boost keys for this fact", ts, fact.fact_id, e,
        )
        return []
    return [dt.strftime("%Y"), dt.strftime("%Y-%m"), dt.strftime("%Y-%m-%d")]


def temporal_boost_for(fact: FactEvent, date_prefixes: list[str], amount: float) -> float:
    if not date_prefixes:
        return 0.0
    keys = _fact_time_keys(fact)
    if not keys:
        return 0.0
    for p in date_prefixes:
        # 前缀匹配：query '2024' 命中 fact '2024-05'；query '2024-05' 命中同月。
        if any(k == p or k.startswith(p) or p.startswith(k) for k in keys):
            return amount
    return 0.0


# -------------------------------------------------------------------
# 融合主体
# -------------------------------------------------------------------

class MultiPathFusion:
    def __init__(self, store: Store, user_id: str, config: RetrievalConfig):
        self._store = store
        self.user_id = user_id
        self.config = config          # for derived_currency / fact_group_enabled
        self.cfg = config.fusion      # weight/limit knobs (source's `self.cfg`)
        self._bm25 = get_bm25_index(store)
        self._degraded: list[Degradation] = []

    def retrieve(self, query: str, raw_query_terms: list[str], plan: QueryPlan | None = None,
                 *, degraded: list[Degradation] | None = None):
        """返回 (ranked_units, excluded, trace)。

        ranked_units: list[RankingUnit]，按 ranking_confidence 降序。
        excluded:     list[dict]，有效性筛选被排除的候选（审计）。
        trace:        dict，搜索/融合审计。
        plan:         QueryPlan（DR-013/014）。None 时退回 default()，等价改造前行为。
        degraded:     由调用方持有的 Degradation 列表；本次 retrieve() 期间任何
                       vector 路由失败都会追加到这个列表（by reference，§6.7）。
        """
        self._degraded = degraded if degraded is not None else []
        units: dict[str, RankingUnit] = {}
        excluded: list[dict] = []
        cfg = self.cfg
        plan = plan or QueryPlan.default("no_plan")
        # query_plan 关闭时沿用旧的日期前缀加分；开启时由 plan 的窗口接管。
        date_prefixes = (
            query_date_prefixes(query)
            if cfg.temporal_enabled and not plan.trace.get("applied")
            else []
        )
        allowed_statuses = plan.allowed_statuses or {FactStatus.ACTIVE}

        def unit_for_fact(fact: FactEvent) -> RankingUnit | None:
            # DR-014：状态资格由 plan.allowed_statuses 驱动（current→仅 ACTIVE；
            # history/as_of→+STALE/SUPERSEDED；correction_history→+CONTRADICTED）。
            # derived_currency ON: do NOT gate on stored status — all facts pass
            # into the pool; currency is derived at the read-side board from
            # valid_until (§E.6 检索不变量: 无时间/失效 fact 也进池, 读时算).
            if not self.config.derived_currency and fact.status not in allowed_statuses:
                excluded.append({"evidence_id": "ev_fact:" + fact.fact_id,
                                 "fact_id": fact.fact_id,
                                 "excluded_reason": f"status={_status_val(fact.status)}",
                                 "fact_time_mode": plan.fact_time_mode})
                return None
            uid = "fact:" + fact.fact_id
            u = units.get(uid)
            if u is None:
                u = RankingUnit(unit_id=uid, kind="fact_event", object_id=fact.fact_id)
                units[uid] = u
            return u

        def unit_for_orphan_span(span) -> RankingUnit:
            uid = "span:" + span.span_id
            u = units.get(uid)
            if u is None:
                u = RankingUnit(unit_id=uid, kind="source_span", object_id=span.span_id,
                                unstructured_source=True)
                units[uid] = u
            return u

        def unit_for_raw_turn(turn) -> RankingUnit:
            # Issue #75 §2.2/§2.3: raw turn as a first-class, fact-independent unit.
            uid = "raw:" + turn.turn_id
            u = units.get(uid)
            if u is None:
                u = RankingUnit(unit_id=uid, kind="raw_turn", object_id=turn.turn_id,
                                unstructured_source=True)
                units[uid] = u
            return u

        # When the raw channel is on it is the single source of unstructured raw
        # units; suppress the orphan-span path so a turn is never represented by
        # both a span unit and a raw unit (dedup, spec §"Dedup with orphan spans").
        span_orphan_factory = None if cfg.raw_recall_enabled else unit_for_orphan_span

        trace = {"heads": {}, "channels": [], "calibration": {}}

        # ---- 强信号直接通道：BM25 fact ----
        self._direct_fact_channel(
            "bm25",
            self._bm25.search_fact_events(query, self.user_id, n=cfg.route_limit),
            cfg.strong_direct, unit_for_fact, trace)

        # ---- 弱信号直接通道：embedding fact ----
        self._direct_fact_channel(
            "vector",
            [(f, None) for f in vector.search_fact_events(
                query, store=self._store, user_id=self.user_id,
                n=min(cfg.route_limit, 60), degraded=self._degraded)],
            cfg.weak_direct, unit_for_fact, trace)

        # ---- 强信号直接通道：BM25 span（归一到 fact，否则孤立 span）----
        self._span_channel(
            "bm25",
            [(s, sc) for s, sc in self._bm25.search_source_spans(
                query, self.user_id, n=cfg.route_limit)],
            cfg.strong_direct, unit_for_fact, span_orphan_factory, trace)

        # ---- 弱信号直接通道：embedding span ----
        self._span_channel(
            "vector",
            [(s, None) for s in vector.search_source_spans(
                query, store=self._store, user_id=self.user_id,
                n=min(cfg.route_limit, 60), degraded=self._degraded)],
            cfg.weak_direct, unit_for_fact, span_orphan_factory, trace)

        # ---- Summary 路由通道（只派生贡献，DR-005）----
        self._summary_channel(query, cfg, unit_for_fact, trace)

        # ---- Entity / graph 通道（含 multi-hop semantic projection）----
        self._entity_channel(raw_query_terms, cfg, unit_for_fact, trace)

        # ---- Issue #75 §2.2/§2.3：原文兜底通道（raw turn，不绕回 fact）----
        if cfg.raw_recall_enabled:
            self._raw_channel(query, cfg, unit_for_raw_turn, trace)

        # ---- DR-015：same-event 确定性合并（Noisy-OR 之前，避免重复占名额）----
        if self.config.fact_group_enabled:
            units = self._merge_same_event(units, trace)

        # ---- Noisy-OR 连接密度 + 时间过滤/加分（DR-013）+ 统一排序 ----
        kept: list[RankingUnit] = []
        for u in units.values():
            u.connection_density = _noisy_or(u.head_contributions.values())
            # The DR-013 hard_filter/soft_boost branch lived here until 0806.
            # It ran when `plan.trace["applied"]` was true and
            # `temporal_policy != "none"` — a combination `QueryPlan.default()`
            # cannot produce, and `default()` is the only constructor any
            # production path reaches. `parse_query_plan`, the one would-be
            # producer of a non-default plan, was never ported. So this was
            # always the taken branch; it is now the only one.
            if cfg.temporal_enabled and u.kind == "fact_event":
                fact = self._store.get_fact_event(u.object_id)
                if fact is not None:
                    u.temporal_boost = temporal_boost_for(fact, date_prefixes, cfg.max_temporal_boost)
            u.ranking_confidence = u.connection_density + u.temporal_boost
            kept.append(u)

        ranked = sorted(kept, key=lambda x: x.ranking_confidence, reverse=True)
        trace["n_units"] = len(ranked)
        trace["date_prefixes"] = date_prefixes
        trace["query_plan"] = plan.trace
        trace["temporal_plan"] = plan.temporal_plan_dict()
        trace["graph_plan"] = plan.graph_plan_dict()
        return ranked, excluded, trace

    # ------------------------------------------------------------------
    # 各通道（每个 ≤ max_search_heads_per_channel 个归一后的 head）
    # ------------------------------------------------------------------

    def _direct_fact_channel(self, channel, hits, contribution, unit_for_fact, trace):
        hits = list(hits)
        calibrated = self._calibrate(
            [score for _fact, score in hits],
            f"{channel}_fact_direct",
            trace,
        )
        n_heads = 0
        for idx, (fact, _score) in enumerate(hits):
            if n_heads >= self.cfg.max_search_heads_per_channel:
                break
            u = unit_for_fact(fact)
            if u is None:
                continue
            head_id = f"{channel}:fact:{fact.fact_id}"
            effective = contribution * calibrated.multipliers[idx]
            u.add(head_id, effective, f"{channel}:direct->{fact.fact_id}")
            n_heads += 1
        trace["heads"][f"{channel}_fact_direct"] = n_heads

    def _span_channel(self, channel, hits, contribution, unit_for_fact, unit_for_orphan_span, trace):
        """BM25/embedding 命中 span：DR-003 模糊点1——span 与其 fact 归一到同一
        排序单元，合并成一个该通道的 Search Head（按归一后的 unit 去重）。"""
        hits = list(hits)
        calibrated = self._calibrate(
            [score for _span, score in hits],
            f"{channel}_span",
            trace,
        )
        n_heads = 0
        seen_unit_heads: set = set()
        for idx, (span, _score) in enumerate(hits):
            if n_heads >= self.cfg.max_search_heads_per_channel:
                break
            effective = contribution * calibrated.multipliers[idx]
            facts = self._store.get_facts_for_span(span.span_id)
            # `Store.get_facts_for_span` already applies the derived_currency
            # gate at the SQL layer (Task 3) — this re-filter is a no-op in
            # the default (derived_currency=False) path where the store
            # already returned active-only rows, and matches the source's
            # own redundant-but-harmless double-check otherwise.
            active = facts if self.config.derived_currency else [
                f for f in facts if f.status == FactStatus.ACTIVE]
            if active:
                for fact in active[:self.cfg.search_head_rerank_top_k]:
                    u = unit_for_fact(fact)
                    if u is None:
                        continue
                    head_id = f"{channel}:fact:{fact.fact_id}"  # 与直接 fact 命中同 head → 合并
                    if head_id not in seen_unit_heads:
                        seen_unit_heads.add(head_id)
                        n_heads += 1
                    u.add(head_id, effective, f"{channel}:span:{span.span_id}->{fact.fact_id}")
            elif unit_for_orphan_span is not None:
                # Issue #75: when the raw channel is on, unit_for_orphan_span is
                # None — orphan spans are covered by the raw-turn channel instead
                # (dedup), so we skip orphan-unit creation here.
                u = unit_for_orphan_span(span)
                head_id = f"{channel}:span:{span.span_id}"
                if head_id not in seen_unit_heads:
                    seen_unit_heads.add(head_id)
                    n_heads += 1
                u.add(head_id, effective, f"{channel}:span:{span.span_id}(orphan)")
        trace["heads"][f"{channel}_span"] = n_heads

    def _raw_channel(self, query, cfg, unit_for_raw_turn, trace):
        """Issue #75 §2.2/§2.3：原文兜底通道。

        直接对不可变 raw_turns 做 BM25 + 向量召回，命中即作为独立排序单元
        （kind="raw_turn", unstructured_source），**不绕回 fact**。相似度驱动
        打分（BM25→strong_direct，向量→weak_direct，经 Noisy-OR 累计），不再像
        旧的孤立 span 那样被固定降权到 tier2/0.2，因此强原文命中能与 fact 同台
        竞争。raw_turns 无 status，永不被 supersede/archive，提供稳定兜底。
        """
        bm25_hits = list(self._bm25.search_raw_turns(
            query, self.user_id, n=cfg.route_limit))
        bm25_cal = self._calibrate([sc for _t, sc in bm25_hits], "raw_bm25", trace)
        n_bm25 = 0
        for idx, (turn, _score) in enumerate(bm25_hits):
            if n_bm25 >= cfg.max_search_heads_per_channel:
                break
            u = unit_for_raw_turn(turn)
            head_id = f"bm25:raw:{turn.turn_id}"
            u.add(head_id, cfg.strong_direct * bm25_cal.multipliers[idx],
                  f"bm25:raw->{turn.turn_id}")
            n_bm25 += 1

        vec_hits = list(vector.search_raw_turns(
            query, store=self._store, user_id=self.user_id,
            n=min(cfg.route_limit, 60), degraded=self._degraded))
        vec_cal = self._calibrate([None for _t in vec_hits], "raw_vector", trace)
        n_vec = 0
        for idx, turn in enumerate(vec_hits):
            if n_vec >= cfg.max_search_heads_per_channel:
                break
            u = unit_for_raw_turn(turn)
            head_id = f"vector:raw:{turn.turn_id}"
            u.add(head_id, cfg.weak_direct * vec_cal.multipliers[idx],
                  f"vector:raw->{turn.turn_id}")
            n_vec += 1
        trace["heads"]["raw_bm25"] = n_bm25
        trace["heads"]["raw_vector"] = n_vec

    def _summary_channel(self, query, cfg, unit_for_fact, trace):
        # Issue #77 §2.4 invariant: a summary is a CLUE, never evidence. This
        # channel must only ever expand a matched summary into its source facts
        # via unit_for_fact — it must not create a standalone summary unit.
        hits = list(self._bm25.search_summary_syntheses(
            query,
            self.user_id,
            n=min(cfg.max_search_heads_per_channel, cfg.route_limit),
        ))
        calibrated = self._calibrate(
            [score for _summary, score in hits],
            "summary",
            trace,
        )
        n_heads = 0
        source_unavailable: list = []
        for idx, (summary, _score) in enumerate(hits):
            if n_heads >= cfg.max_search_heads_per_channel:
                break
            # DR-004 #8 / DR-005 #9：展开前二次校验（覆盖索引检索后发生的并发更新）。
            if not getattr(summary, "eligible_for_routing", True):
                continue
            head_id = f"summary:{summary.summary_id}"
            effective = cfg.weak_derived * calibrated.multipliers[idx]
            hit = 0
            found = 0
            for fid in (summary.source_fact_ids or [])[:cfg.search_head_rerank_top_k]:
                fact = self._store.get_fact_event(fid)
                if fact is None:
                    continue
                found += 1
                u = unit_for_fact(fact)
                if u is None:
                    continue
                u.add(head_id, effective, f"summary:{summary.summary_id}->{fid}")
                hit += 1
            if hit:
                n_heads += 1
            elif found == 0 and (summary.source_fact_ids or []):
                # DR-005 #7/#8：来源完全丢失 = 数据修复问题。记录并标 dirty，
                # 由 refresh 流程（既有修复路径）重建或归档，不降级为答案证据。
                source_unavailable.append(summary.summary_id)
                try:
                    self._store.mark_summary_dirty(
                        summary.user_id, summary.scope_type, summary.scope_id,
                        reason="summary_source_unavailable")
                except Exception as e:  # noqa: BLE001 - 审计记录不应中断检索
                    logger.warning(
                        "fusion: mark_summary_dirty failed for summary %s (%s) — "
                        "the summary's source-unavailable state was NOT recorded, "
                        "retrieval continues regardless",
                        summary.summary_id, e,
                    )
        trace["heads"]["summary"] = n_heads
        if source_unavailable:
            trace["summary_source_unavailable"] = source_unavailable

    def _entity_channel(self, raw_query_terms, cfg, unit_for_fact, trace):
        """Entity Search Head：query terms → mentions → 唯一 entity_id（alias 归一）。
        每个 entity head 经 get_facts_for_entity 派生（= DR-006 semantic hop）。
        multi_hop 开启时，对 1-hop fact 的邻接 entity 再扩展一跳。"""
        mentions = self._store.get_entity_mentions_by_terms(
            self.user_id, raw_query_terms, n=cfg.route_limit)
        # DR-003：多个 alias 命中同一 entity_id 只形成一个 Search Head。
        entity_ids: list[str] = []
        seen = set()
        for m in mentions:
            if m.entity_id and m.entity_id not in seen:
                seen.add(m.entity_id)
                entity_ids.append(m.entity_id)
        entity_ids = entity_ids[:cfg.max_search_heads_per_channel]

        n_heads = 0
        hop2_seen: set = set()
        for eid in entity_ids:
            head_id = f"entity:{eid}"
            facts = self._store.get_facts_for_entity(eid, user_id=self.user_id)[:cfg.search_head_rerank_top_k]
            hit = 0
            hop2_entities: set = set()
            for fact in facts:
                u = unit_for_fact(fact)
                if u is None:
                    continue
                u.add(head_id, cfg.strong_derived, f"entity:{eid}->{fact.fact_id}")
                hit += 1
                if cfg.multi_hop_enabled and cfg.max_semantic_hops >= 2:
                    for oid in ([fact.subject_entity_id] + list(fact.object_entity_ids or [])):
                        if oid and oid != eid:
                            hop2_entities.add(oid)
            if hit:
                n_heads += 1
            # ---- 第二跳：root_search_head_id 始终保持初始 entity head ----
            if cfg.multi_hop_enabled and cfg.max_semantic_hops >= 2:
                for oid in list(hop2_entities)[:cfg.search_head_rerank_top_k]:
                    if oid in hop2_seen:
                        continue
                    hop2_seen.add(oid)
                    for fact in self._store.get_facts_for_entity(
                            oid, user_id=self.user_id)[:cfg.search_head_rerank_top_k]:
                        u = unit_for_fact(fact)
                        if u is None:
                            continue
                        # 多跳中间 entity 不产生新 head，贡献仍归属根 entity head。
                        u.add(head_id, cfg.strong_derived,
                              f"entity:{eid}->hop2:{oid}->{fact.fact_id}")
        trace["heads"]["entity"] = n_heads
        trace["entity_ids"] = entity_ids

    def _merge_same_event(self, units: dict, trace: dict) -> dict:
        """DR-015：把描述同一现实事件的 FactEvent 排序单元合并为一个 Fact group。

        只在确定性证据充分时合并：事件指纹（relation_key + subject + 全部 object
        实体 + occurred 日期桶 + modality + polarity + quantity）完全一致。任何不一致
        都使指纹不同，从而自然区分"同人不同目的地/不同日期/计划 vs 实际/肯定 vs 否定"。
        指纹身份不足（无 object 实体且无 relation）的事实保持独立，宁可重复不误合。
        """
        groups: dict[tuple, list] = {}
        passthrough: dict[str, RankingUnit] = {}
        facts: dict[str, FactEvent] = {}
        for uid, u in units.items():
            if u.kind != "fact_event":
                passthrough[uid] = u
                continue
            fact = self._store.get_fact_event(u.object_id)
            if fact is None:
                passthrough[uid] = u
                continue
            facts[uid] = fact
            fp = _event_fingerprint(fact)
            if fp is None:
                passthrough[uid] = u   # 身份不足，不参与合并
                continue
            groups.setdefault(fp, []).append(uid)

        merged: dict[str, RankingUnit] = dict(passthrough)
        n_merged_groups = 0
        for fp, uids in groups.items():
            if len(uids) == 1:
                merged[units[uids[0]].unit_id] = units[uids[0]]
                continue
            # canonical：head 支持最多者优先，并列取信息最完整（object 实体多 + 有时间）。
            canonical_uid = max(
                uids,
                key=lambda x: (len(units[x].head_contributions), _completeness(facts[x])),
            )
            canon = units[canonical_uid]
            for other_uid in uids:
                if other_uid == canonical_uid:
                    continue
                other = units[other_uid]
                for hid, contrib in other.head_contributions.items():
                    if contrib > canon.head_contributions.get(hid, 0.0):
                        canon.head_contributions[hid] = contrib
                canon.paths.extend(other.paths)
                canon.member_fact_ids.append(other.object_id)
            canon.merge_decision = "deterministic_same_event"
            merged[canon.unit_id] = canon
            n_merged_groups += 1
        trace["fact_group_merged_groups"] = n_merged_groups
        return merged

    def _calibrate(self, scores, trace_key, trace) -> CalibrationResult:
        result = calibrate_relevance(scores)
        trace["calibration"][trace_key] = result.trace
        return result


# -------------------------------------------------------------------
# helpers
# -------------------------------------------------------------------

def _registry_key(fact: FactEvent) -> str:
    """DR-006/007：优先用 registry 解析出的可遍历 key，回退 predicate_canonical。

    Dead code (see this module's docstring): the Relation/Role Registry
    governance layer was never wired up — this function has zero call
    sites."""
    meta = fact.metadata if isinstance(fact.metadata, dict) else {}
    rr = meta.get("relation_resolution") or {}
    return (rr.get("registry_relation_key") or fact.predicate_canonical or "").strip().lower()


def _present_roles(fact: FactEvent) -> set:
    """Dead code, ported verbatim — see `_registry_key`'s docstring."""
    return set((fact.participants or {}).keys())


def _allows_multi_hop(registry, relation_key: str) -> bool:
    """关系存在任一 allow_multi_hop=1 的投影规则才允许向下一跳扩展（DR-006）。
    无规则时保守允许（沿用旧的无差别扩展，避免漏召回）。

    Dead code, ported verbatim — see `_registry_key`'s docstring."""
    if not relation_key:
        return True
    rules = registry.projection_rules(relation_key)
    if not rules:
        return True
    return any(int(r.get("allow_multi_hop", 0)) for r in rules)


def _event_fingerprint(fact: FactEvent):
    """DR-015 事件指纹。身份不足时返回 None（不参与合并）。

    身份字段：relation_key(predicate_canonical) + subject + 全部 object 实体 +
    occurred 日期桶 + modality + polarity + (quantity_value, quantity_unit)。
    """
    rel = (getattr(fact, "relation_key", "") or fact.predicate_canonical or "").strip().lower()
    objs = tuple(sorted(e for e in (fact.object_entity_ids or []) if e))
    if not rel and not objs:
        return None  # 无稳定关系且无客体实体 -> 身份不足
    bucket = _date_bucket(fact.occurred_start)
    modality = getattr(fact.modality, "value", str(fact.modality))
    return (
        rel,
        fact.subject_entity_id or "",
        objs,
        bucket,
        modality,
        (fact.polarity or "positive"),
        (fact.quantity_value, (fact.quantity_unit or "").strip().lower()),
    )


def _completeness(fact: FactEvent) -> int:
    score = len(fact.object_entity_ids or [])
    if fact.occurred_start is not None:
        score += 1
    if fact.quantity_value is not None:
        score += 1
    if (getattr(fact, "relation_key", "") or fact.predicate_canonical):
        score += 1
    return score


def _noisy_or(contributions) -> float:
    prod = 1.0
    for c in contributions:
        prod *= (1.0 - float(c))
    return 1.0 - prod


def _status_val(status) -> str:
    return status.value if isinstance(status, FactStatus) else str(status)
