"""Store version contract. A store carries a schema_version and a fingerprint of
the prompts that produced it. On mismatch we RAISE (fail closed) — never a silent
ALTER — because a frozen benchmark store must never be reused under changed
extraction semantics, and changing a prompt invalidates every built store.

Supersedes the predecessor implementation's INGEST_PIPELINE_VERSION constant (audit 0723 note):
that was a manually-bumped string with zero programmatic consumers — a comment
in config.toml asked humans to bump it. This module replaces the intent with
enforced machinery: schema_version is compared fail-closed on open, and the
prompt fingerprint changes automatically when prompt bytes change. The
constant was deliberately not ported; nothing reads a pipeline version.
"""
from __future__ import annotations

import hashlib

from sodamem.errors import ErrorCode, StoreVersionError

STORE_SCHEMA_VERSION = 1


def prompt_fingerprint(prompts: dict[str, str]) -> str:
    joined = "\n".join(f"{k}={prompts[k]}" for k in sorted(prompts))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


# A fixed, boring probe. Its content is irrelevant — only that it never
# changes, since changing it would invalidate every existing store's
# fingerprint for no reason.
_EMBEDDER_PROBE = "sodamem embedder identity probe"

# Vectors are rounded before hashing. Bit-exact hashing would make a store
# built on one machine refuse to open on another whenever a different
# onnxruntime build or CPU produces last-bit differences for identical input —
# a false alarm that costs more than the miss it prevents, because a genuinely
# DIFFERENT model differs in the first decimals, not the ninth.
_EMBEDDER_PRECISION = 4


def embedder_fingerprint(embedder) -> str:
    """Identify an embedder by what it DOES, not by what it claims.

    Swapping the embedder under an existing store is the one failure mode here
    that corrupts data rather than failing a request: old and new vectors share
    a collection, distances between them stop meaning anything, and retrieval
    quality rots with no error raised anywhere.

    Behavioural identity is the same trick `prompt_fingerprint` uses. The
    `Embedder` port is one method wide (`embed`) — asking implementations to
    self-report a model name would widen the port AND trust a string that can
    be wrong, while a probe vector cannot be: a different model returns
    different numbers.

    Returns `"<dim>:<hash>"`. The dimension is carried in the clear so an
    operator reading the error sees "384 vs 768" instead of two opaque hashes.
    """
    vector = embedder.embed([_EMBEDDER_PROBE])[0]
    rounded = ",".join(f"{round(float(x), _EMBEDDER_PRECISION):.4f}" for x in vector)
    digest = hashlib.sha256(rounded.encode("utf-8")).hexdigest()[:16]
    return f"{len(vector)}:{digest}"


def assert_store_compatible(store_meta: dict, *, expected_schema: int,
                            expected_prompt_fp: str,
                            expected_embedder_fp: str | None = None) -> None:
    found_schema = store_meta.get("schema_version")
    if found_schema != expected_schema:
        raise StoreVersionError(
            f"store schema {found_schema} != expected {expected_schema}; "
            "refusing to start (no silent migration)",
            code=ErrorCode.STORE_INCOMPATIBLE,
            details={"found_schema": found_schema, "expected_schema": expected_schema},
        )
    found_fp = store_meta.get("prompt_fingerprint")
    if found_fp != expected_prompt_fp:
        raise StoreVersionError(
            "store prompt fingerprint drift; store was built with different prompts",
            code=ErrorCode.STORE_INCOMPATIBLE,
            details={"found_fp": found_fp, "expected_fp": expected_prompt_fp},
        )
    if expected_embedder_fp is None:
        # Caller opted out (or is a legacy call site) — nothing to check.
        return
    found_embedder = store_meta.get("embedder_fingerprint")
    if found_embedder is None:
        # Store predates R1.7. Refusing would break every store built before
        # this check existed, for a risk that MIGHT not be present; the store
        # is only poisoned if the embedder actually changed, which we cannot
        # know retroactively. Warn-by-absence is handled at the call site.
        return
    if found_embedder != expected_embedder_fp:
        raise StoreVersionError(
            "store embedder drift; store was built with a different embedder "
            f"({found_embedder}) than the one now configured "
            f"({expected_embedder_fp}). Opening it anyway would mix vectors "
            "from two models in one index — distances between them are "
            "meaningless and retrieval would silently degrade. Re-embed the "
            "store, or point at the original embedder.",
            code=ErrorCode.STORE_INCOMPATIBLE,
            details={"found_embedder": found_embedder,
                     "expected_embedder": expected_embedder_fp},
        )
