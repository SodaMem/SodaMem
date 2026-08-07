"""GET /v1/usage — PRD R2.10 ("暴露已有的 token 会计；机器已在，运维看不见").

The accounting was never missing: `IngestResult.usage` has carried cumulative
token counts all along and `/v1/answer` already returns its provider's
summary. What was missing is a place for an operator to read the total —
`_run_ingest` computed the usage and dropped it on the floor.

This matters beyond ops hygiene. PRD §1.3 records that NO competitor publishes
write-path cost, and §9.2 measured ours (output is 65% of the bill). "We are
the only memory service that can tell you what an ingest cost you" is a
differentiator we already paid for and were not collecting.

Same shape as `/v1/metrics`: a bounded, in-process accumulator, absent rather
than zero-filled before any traffic.
"""
from __future__ import annotations

import pytest

from server.usage import UsageRegistry


def test_empty_registry_reports_no_traffic_not_zero_cost():
    """Zeros read as 'this deployment spent nothing'; absence reads as 'no
    data yet'. Only one of those is true before the first ingest."""
    assert UsageRegistry().snapshot()["total"] == {}


def test_usage_accumulates_across_calls():
    reg = UsageRegistry()
    reg.record("ingest", {"prompt_tokens": 100, "completion_tokens": 20})
    reg.record("ingest", {"prompt_tokens": 50, "completion_tokens": 10})
    total = reg.snapshot()["total"]
    assert total["prompt_tokens"] == 150
    assert total["completion_tokens"] == 30


def test_operations_are_tracked_separately():
    """Ingest and answer have completely different cost profiles — collapsing
    them into one number hides the only interesting comparison."""
    reg = UsageRegistry()
    reg.record("ingest", {"prompt_tokens": 100})
    reg.record("answer", {"prompt_tokens": 7})
    snap = reg.snapshot()
    assert snap["by_operation"]["ingest"]["prompt_tokens"] == 100
    assert snap["by_operation"]["answer"]["prompt_tokens"] == 7
    assert snap["total"]["prompt_tokens"] == 107


def test_nested_provider_summaries_are_merged_not_dropped():
    """Providers report nested per-model breakdowns; flattening only the top
    level would silently lose everything under it."""
    reg = UsageRegistry()
    reg.record("ingest", {"models": {"deepseek": {"prompt_tokens": 10}}})
    reg.record("ingest", {"models": {"deepseek": {"prompt_tokens": 5}}})
    assert reg.snapshot()["total"]["models"]["deepseek"]["prompt_tokens"] == 15


def test_non_numeric_values_do_not_crash_the_accumulator():
    """A provider that reports a model NAME alongside counts must not take the
    whole endpoint down — usage reporting is never worth a 500."""
    reg = UsageRegistry()
    reg.record("ingest", {"model": "deepseek-v4-flash", "prompt_tokens": 3})
    reg.record("ingest", {"model": "deepseek-v4-flash", "prompt_tokens": 4})
    assert reg.snapshot()["total"]["prompt_tokens"] == 7


def test_empty_usage_is_not_recorded_as_an_operation():
    """A zero-network ingest (no LLM configured) reports {}. Registering it
    would invent an operation with no cost, which reads as 'ingest is free'."""
    reg = UsageRegistry()
    reg.record("ingest", {})
    assert reg.snapshot()["by_operation"] == {}


def test_call_counts_are_tracked():
    reg = UsageRegistry()
    for _ in range(3):
        reg.record("ingest", {"prompt_tokens": 1})
    assert reg.snapshot()["by_operation"]["ingest"]["calls"] == 3


# --- wired into the app -----------------------------------------------------

def test_usage_endpoint_is_behind_auth(tmp_path, monkeypatch):
    """Spend is commercially sensitive — same gate as everything but /health."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from tests.test_server_routes import _configure_env
    from server.app import create_app

    _configure_env(monkeypatch, tmp_path, auth_disabled=False)
    monkeypatch.setattr("sodamem.llm.factory.create_provider", lambda **kw: None)
    assert TestClient(create_app()).get("/v1/usage").status_code == 401
