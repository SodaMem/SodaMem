"""GET /v1/metrics — the latency percentiles the PRD's G5 goal wants published.

Deliberately an INSTRUMENT, not a number in a README. The percentiles the PRD
records (warm p50 322ms / p95 399ms) were measured on one machine against
an earlier codebase; copying them into this repo's docs would be the
same unverifiable self-report the PRD criticises competitors for. Shipping the
recorder means anyone — us, a user, a reviewer — can produce the number for
their own deployment and hardware, which is the only version of a latency
claim that survives contact with a skeptic.

Bounded by construction: a fixed-size ring per route, so a long-lived server
cannot grow memory through this path no matter how many requests it serves.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="server tests require the [server] extra")
pytest.importorskip("pydantic_settings", reason="server tests require the [server] extra")

from server.metrics import LatencyRegistry  # noqa: E402


def test_percentiles_of_a_known_distribution():
    reg = LatencyRegistry()
    for value in range(1, 101):  # 1..100 ms
        reg.record("GET /v1/search", float(value))
    snap = reg.snapshot()["GET /v1/search"]
    assert snap["count"] == 100
    assert snap["p50"] == 50.0
    assert snap["p95"] == 95.0
    assert snap["p99"] == 99.0
    assert snap["max"] == 100.0


def test_single_sample_reports_itself_at_every_percentile():
    reg = LatencyRegistry()
    reg.record("POST /v1/memories", 12.5)
    snap = reg.snapshot()["POST /v1/memories"]
    assert snap["count"] == 1
    assert snap["p50"] == snap["p95"] == snap["p99"] == 12.5


def test_routes_are_tracked_separately():
    reg = LatencyRegistry()
    reg.record("GET /v1/context", 10.0)
    reg.record("POST /v1/search", 200.0)
    snap = reg.snapshot()
    assert snap["GET /v1/context"]["p50"] == 10.0
    assert snap["POST /v1/search"]["p50"] == 200.0


def test_ring_is_bounded_and_keeps_the_most_recent():
    """A server that runs for a month must not accumulate a month of samples."""
    reg = LatencyRegistry(capacity=100)
    for value in range(1000):
        reg.record("GET /v1/memories", float(value))
    snap = reg.snapshot()["GET /v1/memories"]
    assert snap["count"] == 100
    assert snap["min"] == 900.0  # oldest 900 samples evicted
    assert snap["max"] == 999.0


def test_empty_registry_reports_nothing_rather_than_zeros():
    """Zeros would read as 'this route is instantaneous'; absence reads as
    'no data', which is the truth before any traffic."""
    assert LatencyRegistry().snapshot() == {}
