from sodamem.errors import (
    ErrorCode, SodaMemError, ProviderError, TenancyError,
    classify_provider_error,
)


def test_error_carries_code_and_details():
    e = TenancyError("bad user_id", code=ErrorCode.TENANCY_INVALID, details={"user_id": ".."})
    assert isinstance(e, SodaMemError)
    assert e.code is ErrorCode.TENANCY_INVALID
    assert e.details["user_id"] == ".."


def test_classify_rate_limit_by_name_without_sdk_import():
    class RateLimitError(Exception):
        pass
    out = classify_provider_error(RateLimitError("429"))
    assert isinstance(out, ProviderError)
    assert out.code is ErrorCode.PROVIDER_RATE_LIMITED
    assert out.details["cause"] == "RateLimitError"


def test_classify_walks_cause_chain():
    class APITimeoutError(Exception):
        pass
    try:
        try:
            raise APITimeoutError("slow")
        except APITimeoutError as inner:
            raise RuntimeError("wrapper") from inner
    except RuntimeError as e:
        out = classify_provider_error(e)
    assert out.code is ErrorCode.PROVIDER_TIMEOUT


def test_classify_unknown_falls_back_to_unavailable():
    out = classify_provider_error(ValueError("weird"))
    assert out.code is ErrorCode.PROVIDER_UNAVAILABLE
    assert out.details["cause"] == "ValueError"


def test_classify_survives_cyclic_cause_chain():
    a = Exception("a")
    b = Exception("b")
    a.__cause__ = b
    b.__cause__ = a  # cycle
    out = classify_provider_error(a)  # must not hang
    assert out.code is ErrorCode.PROVIDER_UNAVAILABLE
