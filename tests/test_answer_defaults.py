"""The c2 configuration IS the default — pin it so a revert is loud.

Flipped 0731 after two full-500 arms measured on deterministic counters:
c1 (stall_stop + truncation_retry): tokens −46.5%, planner steps
3445→2125, parse-fail steps 99→0, the 51 stalled questions scored 46/51
against 45/51 for the same questions un-stalled; c2 (+ prompt_cache_layout
+ short_evidence_ids): input chars a further −12.2%, 36% of prompt tokens
billed at DeepSeek's cache-hit rate. Scores 445 and 452 against
zero-change baselines of 450 and 441 — inside the noise floor, which is
the requirement: these flags buy cost, not score.

The measured-null arms stay off: their runs could not resolve any effect,
and default-on would ship an unmeasured behavior change.
"""
from __future__ import annotations

from sodamem.answer.loop import PlannerConfig


def test_the_c2_configuration_is_the_default():
    config = PlannerConfig()
    assert config.stall_stop is True
    assert config.truncation_retry is True
    assert config.prompt_cache_layout is True
    assert config.short_evidence_ids is True


def test_the_c3_mechanisms_are_the_default():
    """Promoted 0731 after the c3 full-500 run: 453/500 —
    the series' best — with finalization bounces 221 -> 27 (family 151 -> 0,
    claim-omission 61 -> 0), tokens a further −10.5% on c2, and the 143
    autocalled questions scoring 137 against 136 for the same questions
    bouncing on c2. Cumulative vs the b5 baseline: tokens −52.8%, score
    450 -> 453 (p=0.77, inside the noise floor — the requirement)."""
    config = PlannerConfig()
    assert config.capability_autocall is True
    assert config.claim_evidence_autofill is True
    assert config.stall_dup_threshold == 1
    assert config.stall_zero_rows_threshold == 3
    assert config.context_offload is True


def test_the_unresolved_arms_stay_off_by_default():
    config = PlannerConfig()
    assert config.abstention_gate is False


def test_count_roster_promoted_by_the_stable_set_measurement():
    """0731 稳定集评法 (19 必错 + 50 稳定对回归样本, --only) 的裁决:
    roster 翻对 6 回归 1 (官方 judge), 内容核实净 +2~3, 机制归因的
    内容级回归为零。timewin (+2, 其中 2 题与全臂共享) 与 absgate
    (+3/-3, 翻对全是判官格式伪影) 维持默认关。"""
    assert PlannerConfig().count_roster is True
