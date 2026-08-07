"""GET /metrics — Prometheus text exposition (PRD R2.10's remaining half).

`/v1/metrics` and `/v1/usage` are in-process counters that reset on restart.
They answer "what is this deployment doing right now" and nothing else: no
history, no alerting, no comparison across deploys. Anything longer-lived
needs a scraper, and a scraper needs the exposition format.

Hand-rolled rather than pulling `prometheus_client`, for the same reason the
webhook sender uses stdlib urllib: the `[server]` extra is deliberately three
packages wide (invariant I1). The format is small and stable — but "small" is
not "obvious", so the escaping rules that break scrapers are pinned below
rather than assumed.

Deliberately NOT OpenTelemetry. OTel's value is distributed tracing across
services, which needs the SDK, an exporter and a collector; a memory service
that is one process does not have spans worth propagating yet. Scraping covers
the actual need here — metrics that outlive a restart — at zero dependency
cost. Tracing stays a separate, later decision.
"""
from __future__ import annotations

import pytest

from server.prometheus import render_exposition
from server.metrics import LatencyRegistry, RequestCounter
from server.usage import UsageRegistry


def _render(**kw) -> str:
    return render_exposition(
        latency=kw.get("latency") or LatencyRegistry(),
        requests=kw.get("requests") or RequestCounter(),
        usage=kw.get("usage") or UsageRegistry(),
    )


def test_empty_deployment_renders_valid_but_dataless_output():
    """A scraper must get a parseable body before any traffic, not a 500."""
    text = _render()
    assert text.endswith("\n"), "exposition must end with a newline"
    assert "# HELP" in text and "# TYPE" in text


def test_request_counter_is_monotonic_and_labelled():
    """Prometheus counters may only go up. The latency ring evicts samples, so
    it cannot back a counter — this is a separate, additive tally."""
    counter = RequestCounter()
    counter.record("GET", "/v1/search", 200)
    counter.record("GET", "/v1/search", 200)
    counter.record("GET", "/v1/search", 500)
    text = _render(requests=counter)
    assert 'sodamem_http_requests_total{method="GET",path="/v1/search",status="200"} 2' in text
    assert 'sodamem_http_requests_total{method="GET",path="/v1/search",status="500"} 1' in text
    assert "# TYPE sodamem_http_requests_total counter" in text


def test_latency_is_exposed_in_SECONDS_not_milliseconds():
    """Prometheus convention is base units. Publishing milliseconds under a
    `_seconds` name is the kind of error that only shows up on a dashboard
    that is wrong by 1000x."""
    latency = LatencyRegistry()
    latency.record("GET /v1/search", 250.0)  # ms
    text = _render(latency=latency)
    line = next(ln for ln in text.splitlines()
                if ln.startswith("sodamem_http_request_duration_seconds")
                and 'quantile="0.5"' in ln)
    assert line.rsplit(" ", 1)[1] == "0.25"


def test_token_usage_is_exposed_per_operation():
    usage = UsageRegistry()
    usage.record("ingest", {"prompt_tokens": 100, "completion_tokens": 20})
    usage.record("answer", {"prompt_tokens": 7})
    text = _render(usage=usage)
    assert 'sodamem_llm_tokens_total{operation="ingest",kind="prompt_tokens"} 100' in text
    assert 'sodamem_llm_tokens_total{operation="answer",kind="prompt_tokens"} 7' in text


def test_non_numeric_usage_values_are_skipped_not_rendered():
    """Providers report a model NAME alongside counts. Emitting
    `... deepseek-v4-flash` produces a body the scraper rejects wholesale —
    one bad label would take every other metric down with it."""
    usage = UsageRegistry()
    usage.record("ingest", {"model": "deepseek-v4-flash", "prompt_tokens": 5})
    text = _render(usage=usage)
    assert "deepseek-v4-flash" not in text
    assert 'kind="prompt_tokens"} 5' in text


@pytest.mark.parametrize("raw,escaped", [
    ('/v1/a"b', r'/v1/a\"b'),
    ("/v1/a\\b", r"/v1/a\\b"),
    ("/v1/a\nb", r"/v1/a\nb"),
])
def test_label_values_are_escaped(raw, escaped):
    """Unescaped quotes or newlines in a path make the whole scrape
    unparseable. Paths come from routing, but a 404 carries the raw URL."""
    counter = RequestCounter()
    counter.record("GET", raw, 404)
    assert f'path="{escaped}"' in _render(requests=counter)


def test_every_metric_declares_help_and_type_before_its_samples():
    counter = RequestCounter()
    counter.record("GET", "/v1/search", 200)
    lines = _render(requests=counter).splitlines()
    idx = next(i for i, ln in enumerate(lines) if ln.startswith("sodamem_http_requests_total{"))
    preceding = lines[:idx]
    assert any(ln.startswith("# HELP sodamem_http_requests_total") for ln in preceding)
    assert any(ln.startswith("# TYPE sodamem_http_requests_total") for ln in preceding)


# --- wired into the app -----------------------------------------------------

def test_metrics_endpoint_serves_the_prometheus_content_type(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from tests.test_server_routes import _configure_env
    from server.app import create_app

    _configure_env(monkeypatch, tmp_path)
    monkeypatch.setattr("sodamem.llm.factory.create_provider", lambda **kw: None)
    client = TestClient(create_app())
    client.get("/v1/memories", params={"user_id": "scraped"})

    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "sodamem_http_requests_total" in resp.text


def test_metrics_endpoint_is_behind_auth(tmp_path, monkeypatch):
    """Route-level traffic shape and token spend are operational data. A
    scraper can send a bearer token; the open internet should not get this."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from tests.test_server_routes import _configure_env
    from server.app import create_app

    _configure_env(monkeypatch, tmp_path, auth_disabled=False)
    monkeypatch.setattr("sodamem.llm.factory.create_provider", lambda **kw: None)
    assert TestClient(create_app()).get("/metrics").status_code == 401
