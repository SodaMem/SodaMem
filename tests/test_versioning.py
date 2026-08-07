import pytest
from sodamem.errors import StoreVersionError, ErrorCode
from sodamem.versioning import (
    STORE_SCHEMA_VERSION, prompt_fingerprint, assert_store_compatible,
)


def test_fingerprint_is_order_independent_and_stable():
    a = prompt_fingerprint({"extract": "X", "reader": "Y"})
    b = prompt_fingerprint({"reader": "Y", "extract": "X"})
    assert a == b
    assert len(a) == 16


def test_fingerprint_changes_when_a_prompt_changes():
    a = prompt_fingerprint({"extract": "X"})
    b = prompt_fingerprint({"extract": "X!"})
    assert a != b


def test_compatible_store_passes():
    fp = prompt_fingerprint({"extract": "X"})
    assert_store_compatible(
        {"schema_version": STORE_SCHEMA_VERSION, "prompt_fingerprint": fp},
        expected_schema=STORE_SCHEMA_VERSION, expected_prompt_fp=fp,
    )  # no raise


def test_schema_mismatch_raises_not_silently_migrates():
    with pytest.raises(StoreVersionError) as ei:
        assert_store_compatible(
            {"schema_version": 0, "prompt_fingerprint": "abc"},
            expected_schema=1, expected_prompt_fp="abc",
        )
    assert ei.value.code is ErrorCode.STORE_INCOMPATIBLE
    assert ei.value.details["found_schema"] == 0


def test_prompt_drift_raises():
    with pytest.raises(StoreVersionError):
        assert_store_compatible(
            {"schema_version": 1, "prompt_fingerprint": "OLD"},
            expected_schema=1, expected_prompt_fp="NEW",
        )


# --- Fingerprint sensitivity, per-constant (T2 review carryover to T3) -----
#
# Keyed on the ACTUAL production constants, not synthetic strings
# (test_fingerprint_changes_when_a_prompt_changes above already covers those),
# so a typo that quietly shortens one during a future edit is caught here
# rather than discovered as a silently-stale store in production.
#
# There are exactly two write-side prompt constants, and both are what
# `SodaMem.open()` feeds the fingerprint. A third, COARSE_RULES, was covered
# here by an identical test until 0806, when it was deleted along with the
# extraction variant it belonged to.

def _base() -> dict[str, str]:
    from sodamem.prompts.extraction import DETERMINISM_RULES, EXTRACT_SYSTEM_PROMPT
    return {"extract": EXTRACT_SYSTEM_PROMPT, "determinism": DETERMINISM_RULES}


def test_fingerprint_sensitive_to_determinism_rules_change():
    base = _base()
    mutated = dict(base)
    mutated["determinism"] = base["determinism"] + "\nmutated for test"
    assert prompt_fingerprint(base) != prompt_fingerprint(mutated), (
        "prompt_fingerprint must change when DETERMINISM_RULES changes — "
        "a store built under the old rules must fail closed, not silently "
        "keep answering under the new ones."
    )


def test_fingerprint_sensitive_to_extract_system_prompt_change():
    """The other half of the pair, and the one that matters most:
    EXTRACT_SYSTEM_PROMPT decides what a fact IS, so two stores built under
    different versions of it do not hold the same thing."""
    base = _base()
    mutated = dict(base)
    mutated["extract"] = base["extract"] + "\nmutated for test"
    assert prompt_fingerprint(base) != prompt_fingerprint(mutated), (
        "prompt_fingerprint must change when EXTRACT_SYSTEM_PROMPT changes — "
        "a store built under the old extraction text must fail closed, not "
        "silently keep answering under the new one."
    )

# ---------------------------------------------------------------------------
# R1.7 — embedder identity.
#
# Swapping the embedder under an existing store silently poisons it: old
# vectors and new vectors land in the same collection, cosine distances
# between them are meaningless, and retrieval quality degrades with NO error
# anywhere. It is the only failure mode in this codebase that corrupts a
# user's data instead of failing a request, and by the time anyone notices,
# the store is already mixed.
#
# Identity is taken by BEHAVIOUR, not declaration — the same trick
# `prompt_fingerprint` already uses. The `Embedder` Protocol is one method
# wide (`embed`), so asking implementations to self-report a model name would
# both widen the port and trust a string that can lie. Embedding a fixed probe
# and hashing the result cannot lie: a different model produces a different
# vector.
# ---------------------------------------------------------------------------

from sodamem.versioning import embedder_fingerprint  # noqa: E402


class _Dim1:
    def embed(self, texts):
        return [[0.5] for _ in texts]


class _Dim3:
    def embed(self, texts):
        return [[0.5, 0.25, 0.125] for _ in texts]


class _Dim3Different:
    def embed(self, texts):
        return [[0.9, 0.8, 0.7] for _ in texts]


class _Dim3Jittered:
    """Same model, last-bits float noise — what a different onnxruntime build
    or a different CPU can produce for identical inputs."""
    def embed(self, texts):
        return [[0.5 + 1e-9, 0.25 - 3e-10, 0.125 + 2e-9] for _ in texts]


def test_fingerprint_is_stable_for_the_same_embedder():
    assert embedder_fingerprint(_Dim3()) == embedder_fingerprint(_Dim3())


def test_different_dimension_changes_the_fingerprint():
    assert embedder_fingerprint(_Dim1()) != embedder_fingerprint(_Dim3())


def test_same_dimension_different_model_changes_the_fingerprint():
    """Dimension alone is not identity — two 768-dim models are not
    interchangeable, and comparing only the width would wave that through."""
    assert embedder_fingerprint(_Dim3()) != embedder_fingerprint(_Dim3Different())


def test_float_jitter_does_not_change_the_fingerprint():
    """A store built on one machine must still open on another. Bit-exact
    hashing would refuse a perfectly valid store — a false alarm here costs
    more than the miss it prevents, since the values a different MODEL
    produces differ in the first decimals, not the ninth."""
    assert embedder_fingerprint(_Dim3()) == embedder_fingerprint(_Dim3Jittered())


def test_fingerprint_carries_the_dimension_for_readable_errors():
    """The operator reading the exception should see '384 vs 768', not two
    opaque hashes."""
    assert embedder_fingerprint(_Dim3()).startswith("3:")
