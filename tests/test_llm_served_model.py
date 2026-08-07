"""The served model must be recorded, not just the requested one.

An OpenAI-compatible endpoint may answer a request for model X with model Y
and say so only in the response body's `model` field. That already happened
here: `deepseek-chat` was retired 2026-07-24 and the string began routing
server-side to `deepseek-v4-flash`. Every benchmark run kept reporting
"deepseek-chat" because the usage record stamped the *requested* name, so a
7-9 question score drop looked like a code regression for days.

Recording what actually answered is the whole fix — a run can then assert one
model end to end instead of trusting a string it sent.
"""
from __future__ import annotations

from sodamem.llm.base import (
    merge_usage_summaries,
    new_usage_summary,
    record_response_usage,
)


class _Resp:
    """Minimal OpenAI-compatible response: `model` is what actually served."""

    def __init__(self, model=None, usage=None):
        self.model = model
        self.usage = usage


def test_records_the_model_the_server_actually_served():
    summary = new_usage_summary()
    detail = record_response_usage(
        summary, phase="reader", model="deepseek-chat",
        response=_Resp(model="deepseek-v4-flash"),
    )
    assert detail["model"] == "deepseek-chat"
    assert detail["served_model"] == "deepseek-v4-flash"


def test_summary_collects_every_distinct_served_model():
    """One run, one model — the summary has to make a swap visible.

    Per-call detail alone does not answer "did this run use one model?"
    without scanning thousands of rows. The set is what a benchmark asserts.
    """
    summary = new_usage_summary()
    record_response_usage(summary, phase="planner", model="deepseek-chat",
                          response=_Resp(model="deepseek-chat"))
    record_response_usage(summary, phase="reader", model="deepseek-chat",
                          response=_Resp(model="deepseek-v4-flash"))
    record_response_usage(summary, phase="reader", model="deepseek-chat",
                          response=_Resp(model="deepseek-v4-flash"))
    assert sorted(summary["served_models"]) == ["deepseek-chat", "deepseek-v4-flash"]


def test_merging_two_phases_does_not_duplicate_the_same_served_model():
    """`served_models` stays a set-by-meaning across a merge.

    The answer loop merges the planner's summary with the reader's. Plain list
    concatenation would turn one model used twice into a two-element list,
    which reads exactly like a mid-run model swap — the precise failure this
    field exists to detect.
    """
    planner = new_usage_summary()
    record_response_usage(planner, phase="planner", model="deepseek-chat",
                          response=_Resp(model="deepseek-chat"))
    reader = new_usage_summary()
    record_response_usage(reader, phase="reader", model="deepseek-chat",
                          response=_Resp(model="deepseek-chat"))

    merged = merge_usage_summaries(planner, reader)

    assert merged["served_models"] == ["deepseek-chat"]
