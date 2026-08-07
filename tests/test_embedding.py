"""Tests for sodamem.embedding: the Embedder port + OnnxMiniLmEmbedder.

Deliberately does NOT call OnnxMiniLmEmbedder.embed() — the first real call
triggers chromadb's model download (~90MB from S3) via
_download_model_if_not_exists(), which this suite must not depend on network
access for. Construction itself is network-free (the ONNX InferenceSession is
a cached_property, built lazily on first use), so construction + identity/
sharing behavior is what's covered here.
"""
from __future__ import annotations

from sodamem.embedding import Embedder, OnnxMiniLmEmbedder


class _FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t))] for t in texts]


def test_fake_embedder_satisfies_embedder_protocol():
    assert isinstance(_FakeEmbedder(), Embedder)


def test_onnx_minilm_embedder_constructs_without_network():
    # Constructing must not touch the network (only the first .embed() call
    # would, via chromadb's lazy model download) and must satisfy Embedder.
    embedder = OnnxMiniLmEmbedder()
    assert isinstance(embedder, Embedder)
    assert hasattr(embedder, "embed")


def test_onnx_minilm_embedder_is_chroma_embedding_function_compatible():
    # chromadb's EmbeddingFunction protocol only requires __call__; verify
    # OnnxMiniLmEmbedder can stand in directly (Store's own adapter doesn't
    # depend on this, but nothing should stop it from also working directly).
    embedder = OnnxMiniLmEmbedder()
    assert callable(embedder)
    assert embedder.name() == "sodamem-onnx-minilm-l6-v2"


def test_chroma_embedding_function_adapter_shares_one_embedder_instance():
    from sodamem.memory.storage.store import _ChromaEmbeddingFunctionAdapter

    calls = []

    class _CountingEmbedder:
        def embed(self, texts):
            calls.append(list(texts))
            return [[1.0] for _ in texts]

    embedder = _CountingEmbedder()
    adapter = _ChromaEmbeddingFunctionAdapter(embedder)
    adapter(["a", "b"])
    assert calls == [["a", "b"]]
    # The SAME adapter (wrapping the SAME embedder) is what Store hands to
    # every collection — confirm identity is preserved, not re-wrapped.
    assert adapter._embedder is embedder
