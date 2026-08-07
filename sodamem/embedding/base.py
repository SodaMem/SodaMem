"""The Embedder port: the duck-typed contract `sodamem.memory.storage.Store`
depends on for turning text into vectors.

`Store` never lazily builds an embedder itself (that was the bug — see
`onnx_minilm.py`); it only ever holds one it was handed at construction time,
duck-typed against this Protocol so a fake/no-op embedder is a one-line class
in tests.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """In-process text embedder. Must not perform network egress (I5, enforced
    at the retrieval layer by tests/gates/test_i5_zero_llm_zero_egress.py, not
    here — Store just holds whatever it's given)."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...
