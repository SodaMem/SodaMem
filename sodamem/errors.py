"""Typed error taxonomy + SDK-agnostic provider-error classifier.

Two orthogonal axes: a machine-readable ErrorCode (wire contract) and a Python
class hierarchy (catch granularity). Subclasses are typed constructors so a
caller can never build an inconsistent error. Classification walks the
cause/context chain and matches on exception CLASS NAME so it imports no
provider SDK.
"""
from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_EMPTY_CONTENT = "provider_empty_content"
    VECTOR_STORE_UNAVAILABLE = "vector_store_unavailable"
    EMBEDDER_UNAVAILABLE = "embedder_unavailable"
    CONFIG_INVALID = "config_invalid"
    TENANCY_INVALID = "tenancy_invalid"
    INGEST_FAILED = "ingest_failed"
    STORE_INCOMPATIBLE = "store_incompatible"
    UNKNOWN = "unknown"


class SodaMemError(Exception):
    def __init__(self, message: str, *, code: ErrorCode = ErrorCode.UNKNOWN,
                 details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class ConfigError(SodaMemError):
    pass


class IngestError(SodaMemError):
    pass


class RetrievalError(SodaMemError):
    pass


class ProviderError(SodaMemError):
    pass


class TenancyError(SodaMemError):
    pass


class StoreVersionError(SodaMemError):
    pass


_RATE_LIMIT_NAMES = frozenset({
    "RateLimitError", "TooManyRequestsError", "TooManyRequests", "OverloadedError",
})
_TIMEOUT_NAMES = frozenset({
    "Timeout", "TimeoutError", "APITimeoutError", "ReadTimeout", "ConnectTimeout",
})


def classify_provider_error(exc: BaseException) -> ProviderError:
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        name = type(cur).__name__
        if name in _RATE_LIMIT_NAMES:
            return ProviderError(str(exc), code=ErrorCode.PROVIDER_RATE_LIMITED,
                                 details={"cause": name})
        if name in _TIMEOUT_NAMES:
            return ProviderError(str(exc), code=ErrorCode.PROVIDER_TIMEOUT,
                                 details={"cause": name})
        cur = cur.__cause__ or cur.__context__
    return ProviderError(str(exc), code=ErrorCode.PROVIDER_UNAVAILABLE,
                         details={"cause": type(exc).__name__})
