"""Prometheus text exposition (PRD R2.10's remaining half).

`/v1/metrics` and `/v1/usage` are in-process counters that reset on restart.
They answer "what is this deployment doing right now" and nothing more — no
history, no alerting, no comparison across deploys. Everything longer-lived
needs a scraper, and a scraper needs this format.

Hand-rolled instead of `prometheus_client`, on the same reasoning the webhook
sender uses stdlib urllib: the `[server]` extra is deliberately three packages
wide (invariant I1), and one endpoint's output format is not worth widening
it. The format is small and stable — but small is not the same as obvious, so
the escaping rules that break a scrape are implemented explicitly and pinned
by tests rather than assumed.

**Not OpenTelemetry, deliberately.** OTel earns its cost through distributed
tracing across services; a single-process memory service has no spans worth
propagating yet, and adopting it would mean an SDK, an exporter and a
collector for data nobody is correlating. Scraping covers the actual gap here
— metrics that outlive a restart — at zero dependency cost. Tracing stays a
separate decision for when there is a second service to trace into.
"""
from __future__ import annotations

from typing import Any

from server.metrics import LatencyRegistry, RequestCounter
from server.usage import UsageRegistry

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

# Quantiles published for request duration. A bounded ring is a summary, not a
# histogram: it cannot produce the cumulative buckets a histogram needs, and
# faking them would let someone compute a wrong aggregate across instances.
_QUANTILES = (("0.5", "p50"), ("0.95", "p95"), ("0.99", "p99"))


def _escape_label(value: str) -> str:
    """Escape a label value per the exposition format.

    Order matters: backslash first, or the escapes we add get escaped again.
    An unescaped quote or newline does not corrupt one sample — it makes the
    ENTIRE scrape unparseable, taking every other metric down with it. Route
    templates are well-behaved, but a 404 carries whatever URL was requested.
    """
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


def _metric(out: list[str], name: str, help_text: str, type_: str) -> None:
    out.append(f"# HELP {name} {help_text}")
    out.append(f"# TYPE {name} {type_}")


def _is_number(value: Any) -> bool:
    # bool is an int subclass; a True/False in a usage summary is a flag, not a
    # measurement, and rendering it as 1/0 would invent a metric.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def render_exposition(*, latency: LatencyRegistry, requests: RequestCounter,
                      usage: UsageRegistry) -> str:
    out: list[str] = []

    _metric(out, "sodamem_http_requests_total",
            "Total HTTP requests served, by method, route template and status.",
            "counter")
    for (method, route, status), count in sorted(requests.snapshot().items()):
        labels = (f'method="{_escape_label(method)}",'
                  f'path="{_escape_label(route)}",'
                  f'status="{status}"')
        out.append(f"sodamem_http_requests_total{{{labels}}} {count}")

    _metric(out, "sodamem_http_request_duration_seconds",
            "Request duration over this process's most recent samples "
            "(bounded ring; see server/metrics.py).",
            "summary")
    for route, stats in sorted(latency.snapshot().items()):
        escaped = _escape_label(route)
        for quantile, key in _QUANTILES:
            # Base units: Prometheus convention is seconds, and the registry
            # stores milliseconds. Publishing ms under a `_seconds` name is an
            # error that only surfaces on a dashboard wrong by 1000x.
            seconds = stats[key] / 1000.0
            out.append(
                f'sodamem_http_request_duration_seconds{{route="{escaped}",'
                f'quantile="{quantile}"}} {seconds}'
            )
        out.append(
            f'sodamem_http_request_duration_seconds_count{{route="{escaped}"}} '
            f'{stats["count"]}'
        )

    _metric(out, "sodamem_llm_tokens_total",
            "Cumulative LLM tokens since process start, by operation and kind.",
            "counter")
    for operation, counts in sorted(usage.snapshot()["by_operation"].items()):
        for kind, value in sorted(counts.items()):
            if not _is_number(value):
                # Providers report a model NAME beside the counts. Emitting it
                # as a sample value yields a body the scraper rejects whole.
                continue
            out.append(
                f'sodamem_llm_tokens_total{{operation="{_escape_label(operation)}",'
                f'kind="{_escape_label(kind)}"}} {value}'
            )

    # Trailing newline is required by the format, not cosmetic.
    return "\n".join(out) + "\n"
