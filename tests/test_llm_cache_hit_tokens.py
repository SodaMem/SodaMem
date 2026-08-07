"""DeepSeek's cache hits live at the top of usage, not where OpenAI puts them.

The `prompt_cache_layout` arm's one deliverable is billed-token reduction:
DeepSeek prices a cache-hit prefix at roughly a tenth of a miss and reports
the split as top-level `usage.prompt_cache_hit_tokens`. The existing capture
only read OpenAI's `prompt_tokens_details.cached_tokens`, so against the
provider the arm targets, `cached_input_tokens` was structurally zero and
the arm was unverifiable — a lever without its meter, again.
"""
from __future__ import annotations

from sodamem.llm.base import new_usage_summary, record_response_usage


class _Resp:
    def __init__(self, usage):
        self.model = "deepseek-v4-flash"
        self.usage = usage


def test_deepseek_top_level_cache_hit_tokens_are_captured():
    summary = new_usage_summary()
    record_response_usage(summary, phase="answer_planner", model="deepseek-v4-flash",
                          response=_Resp({"prompt_tokens": 1000,
                                          "completion_tokens": 50,
                                          "prompt_cache_hit_tokens": 800,
                                          "prompt_cache_miss_tokens": 200}))

    assert summary["cached_input_tokens"] == 800
    assert summary["prompt_tokens"] == 1000


def test_openai_style_nested_cached_tokens_still_win():
    summary = new_usage_summary()
    record_response_usage(summary, phase="answer_planner", model="gpt",
                          response=_Resp({"prompt_tokens": 1000,
                                          "prompt_tokens_details": {"cached_tokens": 700}}))

    assert summary["cached_input_tokens"] == 700


def test_no_cache_fields_stays_zero():
    summary = new_usage_summary()
    record_response_usage(summary, phase="answer_planner", model="deepseek-v4-flash",
                          response=_Resp({"prompt_tokens": 10, "completion_tokens": 1}))

    assert summary["cached_input_tokens"] == 0
