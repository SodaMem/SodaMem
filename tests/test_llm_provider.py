

# ---------------------------------------------------------------------------
# R2.9 — env parsing must not swallow bad values.
#
# `MEMORY_LLM_TIMEOUT_SECONDS=5s` silently became 60.0. The operator typed the
# variable, so their intent was explicit; substituting a different number and
# saying nothing is the exact no-silent-failures violation this project pins
# elsewhere. An UNSET variable falling back to a default is fine — that is a
# default. A SET variable being ignored is not.
#
# Out-of-range is the same lie in a quieter voice: asking for 0.1s and getting
# 1.0s is still "you asked for X, we gave you Y, silently".
# ---------------------------------------------------------------------------

import pytest as _pytest

from sodamem.llm.base import (
    client_max_retries,
    client_timeout_seconds,
    empty_content_retries,
)


def test_unset_env_uses_the_documented_default(monkeypatch):
    for var in ("MEMORY_LLM_TIMEOUT_SECONDS", "BENCHMARK_LLM_TIMEOUT_SECONDS"):
        monkeypatch.delenv(var, raising=False)
    assert client_timeout_seconds() == 60.0


def test_valid_env_is_honoured(monkeypatch):
    monkeypatch.setenv("MEMORY_LLM_TIMEOUT_SECONDS", "12.5")
    assert client_timeout_seconds() == 12.5


def test_unparseable_timeout_raises_naming_the_variable(monkeypatch):
    monkeypatch.setenv("MEMORY_LLM_TIMEOUT_SECONDS", "30s")
    with _pytest.raises(ValueError) as exc:
        client_timeout_seconds()
    assert "MEMORY_LLM_TIMEOUT_SECONDS" in str(exc.value)
    assert "30s" in str(exc.value)


def test_below_floor_timeout_raises_rather_than_clamping(monkeypatch):
    """Clamping 0.1 to 1.0 is still substituting a number the operator did
    not ask for."""
    monkeypatch.setenv("MEMORY_LLM_TIMEOUT_SECONDS", "0.1")
    with _pytest.raises(ValueError) as exc:
        client_timeout_seconds()
    assert "1" in str(exc.value)  # the floor is stated


def test_unparseable_retry_counts_raise(monkeypatch):
    monkeypatch.setenv("MEMORY_LLM_MAX_RETRIES", "two")
    with _pytest.raises(ValueError):
        client_max_retries()
    monkeypatch.delenv("MEMORY_LLM_MAX_RETRIES")
    monkeypatch.setenv("MEMORY_LLM_EMPTY_RETRIES", "-")
    with _pytest.raises(ValueError):
        empty_content_retries()


def test_negative_retry_count_raises_rather_than_clamping_to_zero(monkeypatch):
    monkeypatch.setenv("MEMORY_LLM_MAX_RETRIES", "-3")
    with _pytest.raises(ValueError):
        client_max_retries()


def test_benchmark_alias_still_works(monkeypatch):
    """The rig sets BENCHMARK_*; those stores and scripts must keep running."""
    monkeypatch.delenv("MEMORY_LLM_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("BENCHMARK_LLM_TIMEOUT_SECONDS", "9")
    assert client_timeout_seconds() == 9.0
