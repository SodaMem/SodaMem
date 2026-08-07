"""Per-route latency percentiles (PRD R1.13 / goal G5).

Why an endpoint and not a number in the README: the percentiles the PRD
records (warm p50 322ms / p95 399ms / cold p50 629ms) were measured on one
machine, against an earlier codebase. Reprinting them under this project's
name would be exactly the unverifiable self-report the PRD faults
competitors for — and it would be wrong the moment hardware, embedder, or
store size differ. Shipping the recorder lets every deployment produce its
own number, which is the only latency claim that survives a skeptic.

Bounded by construction: one fixed-size ring per route. A server running for
a month holds `capacity` samples per route, not a month of them. Percentiles
are computed on read from at most `capacity` values, so the cost is paid by
whoever asks, not by every request.

Deliberately not Prometheus: a text-format exporter is a dependency and a
scrape target, and the goal here is "curl it and read the number". If a real
metrics stack is ever wanted, this registry is the natural thing to feed it.
"""
from __future__ import annotations

import math
import threading
from collections import deque
from typing import Any

DEFAULT_CAPACITY = 1024


def _percentile(ordered: list[float], q: float) -> float:
    """Nearest-rank percentile on an already-sorted list.

    Nearest-rank, not interpolated: with a bounded sample the interpolated
    variant invents values that were never observed, and a latency number
    nobody's request actually experienced is a bad thing to publish.
    """
    if not ordered:
        return 0.0
    rank = max(1, min(len(ordered), math.ceil(q * len(ordered))))
    return ordered[rank - 1]


class LatencyRegistry:
    """Thread-safe, bounded per-route latency samples in milliseconds."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._capacity = capacity
        self._rings: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def record(self, route: str, elapsed_ms: float) -> None:
        with self._lock:
            ring = self._rings.get(route)
            if ring is None:
                ring = deque(maxlen=self._capacity)
                self._rings[route] = ring
            ring.append(elapsed_ms)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Percentiles per route. A route with no samples is ABSENT rather
        than reported as zeros — zeros read as "instantaneous", absence reads
        as "no data", and only one of those is true before any traffic."""
        with self._lock:
            rings = {route: list(ring) for route, ring in self._rings.items() if ring}
        out: dict[str, dict[str, Any]] = {}
        for route, values in rings.items():
            ordered = sorted(values)
            out[route] = {
                "count": len(ordered),
                "min": ordered[0],
                "p50": _percentile(ordered, 0.50),
                "p95": _percentile(ordered, 0.95),
                "p99": _percentile(ordered, 0.99),
                "max": ordered[-1],
            }
        return out

    def reset(self) -> None:
        with self._lock:
            self._rings.clear()


class RequestCounter:
    """Monotonic per-(method, route, status) request tally.

    Separate from `LatencyRegistry` because the two have incompatible
    lifetimes: the latency ring EVICTS old samples by design, so it can never
    back a Prometheus counter, which may only increase. Keeping one structure
    for both would mean either an unbounded latency buffer or a counter that
    goes backwards — and a counter that goes backwards is read by Prometheus
    as a process restart.
    """

    def __init__(self) -> None:
        self._counts: dict[tuple[str, str, int], int] = {}
        self._lock = threading.Lock()

    def record(self, method: str, route: str, status: int) -> None:
        key = (method, route, int(status))
        with self._lock:
            self._counts[key] = self._counts.get(key, 0) + 1

    def snapshot(self) -> dict[tuple[str, str, int], int]:
        with self._lock:
            return dict(self._counts)

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()


_REQUESTS: RequestCounter | None = None


def get_request_counter() -> RequestCounter:
    global _REQUESTS
    if _REQUESTS is None:
        _REQUESTS = RequestCounter()
    return _REQUESTS


def reset_request_counter() -> None:
    global _REQUESTS
    _REQUESTS = None


_REGISTRY: LatencyRegistry | None = None


def get_latency_registry() -> LatencyRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = LatencyRegistry()
    return _REGISTRY


def reset_latency_registry() -> None:
    global _REGISTRY
    _REGISTRY = None
