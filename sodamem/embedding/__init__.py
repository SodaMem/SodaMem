"""sodamem.embedding — the in-process embedder port + its default implementation.

Two symbols:
  - `Embedder` (base.py): the Protocol `sodamem.memory.storage.Store` depends
    on. Fake/no-op embedders in tests just implement `.embed()`.
  - `OnnxMiniLmEmbedder` (onnx_minilm.py): the concrete MiniLM/ONNX embedder a
    composition root wires in by default, with the shared-session fix applied
    (see that module's docstring for the bug this closes).

Importing this package never requires `chromadb` — only constructing
`OnnxMiniLmEmbedder` does (spec I1: base install stays chromadb-free).
"""
from __future__ import annotations

from .base import Embedder
from .onnx_minilm import OnnxMiniLmEmbedder

__all__ = ["Embedder", "OnnxMiniLmEmbedder"]
