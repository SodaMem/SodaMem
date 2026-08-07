"""value_board 的三处修复 — 由 q193/q053 逐步定位的机制级错误 (0731)。

q193 "How many musical instruments do I currently own?" (gold 4, 12/13 次
run 答错): planner 第 3 步 final、选中 7 条证据、reason 里点名了全部四件乐器。
错在 read 侧:

1. `_value_from_record` 让 `count` 和 `duration` 共用一张 unit 白名单——
   "弹了 3 年钢琴" (duration=3) 被当成计数候选收下, 而 "一台钢琴"
   (item_count=1) 因 `item_count` 不在名单里被扔掉。方向正好相反。
   证据卡全量统计: 1224 条 `*_count` 被挡, 1516 条 duration 被放进计数题。
2. strong 缩池直接**替换** planner 的选择 (`narrow_reader_ids` 整体顶掉
   `reader_ids`) — 一个正则组件抹掉了 planner 四步选出的证据, reader 于是
   "看不到" 鼓和 Yamaha, 答 3。
3. advisory 的 rule 写着 "Do not recompute a different value" — q053 (gold
   100 = 需要总分 300 − 已有 200) 里 reader 两条证据都在手, 被这句话禁止做
   减法, 只能复读 resolved_value=300。

四臂离线重放 (b5 全部 21 道 strong 题, control 复现原判 18/21) 实测:
unit 修复翻对 q193, rule 软化再翻对 q053, 16 道原本答对的有效题 (含全部
KU) 零反向翻转。KU 上 runtime_resolved_value 本来就是 14/14 —— 组件干它
该干的事时完美, 这三处只删它不该有的权力。
"""
from __future__ import annotations

import json

from sodamem.answer.reader import ReaderConfig, assemble_reader_context
from sodamem.context.organizers.value_board import _value_from_record, build_value_board
from sodamem.context.store import EvidenceStore
from sodamem.llm.testing import ScriptedProvider
from sodamem.prompts.reader import READER_GUIDANCE_RUNTIME_RESOLVED_ADDENDUM


# ---------------------------------------------------------------------------
# 1. unit 家族: count 收 count/item_count/*_count, 拒 duration; duration 反之
# ---------------------------------------------------------------------------

def test_count_question_accepts_item_count_units():
    raw = {"quantity": {"value": 1, "unit": "item_count"}}
    assert _value_from_record(raw, "count") == (1, "item_count")


def test_count_question_accepts_any_count_suffix_unit():
    raw = {"quantity": {"value": 3, "unit": "trip_count"}}
    assert _value_from_record(raw, "count") == (3, "trip_count")


def test_count_question_rejects_duration_values():
    """"弹了 3 年钢琴" 不是 "3 件乐器" — q193 的第一刀。"""
    for unit in ("duration", "hours", "days", "weeks"):
        assert _value_from_record({"quantity": {"value": 3, "unit": unit}}, "count") is None


def test_duration_question_rejects_count_values():
    assert _value_from_record({"quantity": {"value": 5, "unit": "item_count"}}, "duration") is None


def test_duration_question_still_accepts_time_units():
    assert _value_from_record({"quantity": {"value": 8, "unit": "hours"}}, "duration") == (8, "hours")


def test_unitless_quantity_still_passes_for_both():
    # 抽取器没标 unit 的数值不因这次收紧而丢失。
    assert _value_from_record({"quantity": {"value": 4, "unit": ""}}, "count") == (4, "count")
    assert _value_from_record({"quantity": {"value": 4}}, "duration") == (4, "duration")


# ---------------------------------------------------------------------------
# 2+3. strong 缩池只前置不替换; rule 允许对证据做算术
# ---------------------------------------------------------------------------

_STRONG_COUNT_PLAN = json.dumps({
    "question_classification": "ordinary",
    "answer_shape": "single_slot_current_value",
    "temporal_intent": "current",
    "requires_value_board": True,
    "slot_hint": {
        "metric": "sephora points needed",
        "value_type": "count",
        "total_vs_delta": "total",
        "entities": [],
    },
    "confidence": 0.9,
})


def _points_evidence():
    """q053 的形状: 两个互相竞争的 count 值, 外加一条无数值的上下文行。"""
    store = EvidenceStore()
    rows = [
        {"id": "f_total", "fact_id": "f_total", "evidence_id": "ev_fact:f_total",
         "support_text": "I just need a total of 300 sephora points and I'm all set",
         "predicate_canonical": "user needs 300 sephora points total",
         "quantity": {"value": 300, "unit": "count"},
         "session_id": "s1", "turn_id": "s1_t0", "role": "user", "session_time": "2023-05-29"},
        {"id": "f_sofar", "fact_id": "f_sofar", "evidence_id": "ev_fact:f_sofar",
         "support_text": "bringing my total to 200 sephora points so far",
         "predicate_canonical": "user has 200 sephora points",
         "quantity": {"value": 200, "unit": "count"},
         "session_id": "s2", "turn_id": "s2_t0", "role": "user", "session_time": "2023-05-21"},
        {"id": "f_plain", "fact_id": "f_plain", "evidence_id": "ev_fact:f_plain",
         "support_text": "I'm planning to buy a moisturizer from Sephora soon",
         "predicate_canonical": "user plans to buy a moisturizer",
         "session_id": "s3", "turn_id": "s3_t0", "role": "user", "session_time": "2023-05-29"},
    ]
    for i, row in enumerate(rows):
        store.ingest("search", {}, {"items": [row]}, i)
    return store


def test_strong_narrow_front_loads_but_keeps_the_full_pool():
    """planner 选中的 f_plain 不再被 top-4 替换掉 — q193 的第二刀。"""
    evidence = _points_evidence()
    provider = ScriptedProvider([_STRONG_COUNT_PLAN])

    context = assemble_reader_context(
        evidence, ["ev_fact:f_plain"], "How many points do I need at Sephora?",
        current_date="2023-06-01", provider=provider, config=ReaderConfig(),
    )

    ids = [row["evidence_id"] for row in context.key_evidence]
    assert "ev_fact:f_plain" in ids, "planner 的选择被缩池抹掉了"
    assert {"ev_fact:f_total", "ev_fact:f_sofar"} <= set(ids)
    # 缩池仍有排序权: resolved 候选排在无数值行前面。
    assert ids.index("ev_fact:f_total") < ids.index("ev_fact:f_plain")
    assert any(a.startswith("runtime_resolved_value") for a in context.semantic_advisories)


def test_resolved_rule_permits_arithmetic_over_the_evidence():
    """q053 的第三刀: rule 不得禁止 reader 做 300−200。"""
    evidence = _points_evidence()
    plan = json.loads(_STRONG_COUNT_PLAN)
    block, _keep, narrow = build_value_board(
        plan=plan, evidence=evidence,
        reader_ids=list(evidence.records.keys()),
        strong=True, question="How many points do I need at Sephora?",
    )

    assert narrow, "fixture 应触发 strong 缩池"
    assert "Do not recompute" not in block
    assert "arithmetic" in block
    assert "Do not recompute" not in READER_GUIDANCE_RUNTIME_RESOLVED_ADDENDUM
    assert "arithmetic" in READER_GUIDANCE_RUNTIME_RESOLVED_ADDENDUM
