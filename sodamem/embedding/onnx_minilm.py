"""OnnxMiniLmEmbedder: the shared-session MiniLM embedder that backs Store's
chroma collections.

The proven bug this fixes: the predecessor implementation's `_init_chroma`
calls `chromadb_client.get_or_create_collection(...)` three times (spans,
facts, raw_turns) and never passes `embedding_function=`. Chroma then falls
back to its own default (`chromadb.utils.embedding_functions.ONNXMiniLM_L6_V2`),
constructed fresh wherever chroma needs one — and that class's ONNX
`InferenceSession` lives behind a `functools.cached_property`, so it is only
ever reused *within one instance*. Never sharing an instance means never
reusing the session: model + tokenizer + ONNX runtime session get rebuilt
on every collection fetch, purely mechanical overhead measured at ~2.4x read /
~3.2x write versus a shared instance (project ledger, 0715).

The fix is exactly this: build ONE `OnnxMiniLmEmbedder` per `Store` and pass
that SAME instance as `embedding_function=` to all three collections (see
`sodamem.memory.storage.store._init_chroma`) — one ONNX session, reused for
the lifetime of the Store, not one per collection per open.
"""
from __future__ import annotations

from typing import Any


class OnnxMiniLmEmbedder:
    """In-process MiniLM (all-MiniLM-L6-v2) embedder, ONNX runtime backend.

    Wraps chromadb's own `ONNXMiniLM_L6_V2` rather than reimplementing ONNX
    inference — that class already does the right thing (a `cached_property`
    session) *per instance*; the fix this class exists for is making sure
    only one instance ever gets constructed. `chromadb` is an optional
    dependency (the `[chroma]` extra); the import is deferred to `__init__`
    so importing this module — or `sodamem.embedding` — never requires it
    (spec I1: base install stays chromadb-free).

    Implements both `sodamem.embedding.Embedder` (`.embed()`) and chromadb's
    own `EmbeddingFunction` protocol (`__call__`), so the identical instance
    can be handed to `Store` for its own embedding needs AND passed straight
    through as `embedding_function=` on every collection — no separate
    adapter class needed.
    """

    def __init__(self) -> None:
        try:
            from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
        except ImportError as e:
            raise ImportError(
                "OnnxMiniLmEmbedder requires the 'chroma' extra: "
                "pip install 'sodamem[chroma]'"
            ) from e
        self._fn = ONNXMiniLM_L6_V2()

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._fn(list(texts))
        return [[float(x) for x in vec] for vec in vectors]

    def __call__(self, input: Any) -> Any:  # noqa: A002 - name matches chromadb's protocol
        return self._fn(input)

    @staticmethod
    def name() -> str:
        """chromadb's EmbeddingFunction protocol wants this to identify the
        function in collection metadata; mirrors the wrapped class's name."""
        return "sodamem-onnx-minilm-l6-v2"

    @staticmethod
    def is_legacy() -> bool:
        # Duck-typed, not a subclass of chromadb's EmbeddingFunction ABC (see
        # module docstring on why chromadb stays a deferred import) — no
        # get_config()/build_from_config() round-trip, so this is honestly
        # "legacy" by chromadb's own definition, not a workaround.
        return True
