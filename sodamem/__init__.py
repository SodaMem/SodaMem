"""SodaMem — evidence-grounded temporal memory for AI agents."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from sodamem.answer.reader import Answer
    from sodamem.context import ContextBlock
    from sodamem.memory.ingest.config import IngestConfig
    from sodamem.memory.retrieval.config import RetrievalConfig
    from sodamem.memory.retrieval.search import SearchResult
    from sodamem.memory.storage.store import Store


__version__ = "0.0.1"


@dataclass
class SodaMem:
    """The top-level facade. Constructed via __init__ (explicit ports) or
    from_config (dict-driven convenience, mem0-style). Methods are added
    incrementally: Task 5 adds .ingest(), Task 6 adds .search(), Task 7 adds
    .dream() — each task ADDS a method to this same class body, it does not
    redefine the class."""

    # TYPE_CHECKING-only import: a top-level package __init__ eagerly
    # importing its own memory subpackage just for a type hint would be
    # backwards (and, combined with `from __future__ import annotations`,
    # unnecessary — the annotation is never evaluated at runtime).
    store: "Store"
    extractor: Any | None = None
    embedder: Any = None

    @classmethod
    def from_config(cls, cfg: dict) -> "SodaMem":
        """Sugar constructor (mem0-style) over a flat dict. Requires
        `data_dir`; optional `prompts`, `raw_recall_enabled`,
        `derived_currency`. `extractor` (needed by .ingest) is not
        dict-constructible — build a FactEventExtractorV2 and pass
        SodaMem.open(extractor=...) for the write path. Delegates to
        SodaMem.open()."""
        if "data_dir" not in cfg:
            from sodamem.errors import ConfigError, ErrorCode
            raise ConfigError("from_config requires 'data_dir'",
                              code=ErrorCode.CONFIG_INVALID,
                              details={"got_keys": sorted(cfg)})
        return cls.open(
            cfg["data_dir"],
            prompts=cfg.get("prompts"),
            raw_recall_enabled=cfg.get("raw_recall_enabled", True),
            derived_currency=cfg.get("derived_currency", False),
        )

    def ingest(self, turns, *, user_id: str, session_id: str, session_time,
               config: "IngestConfig | None" = None,
               agent_id: str = "", run_id: str = "", project_id: str = "",
               infer: bool = True):
        """session_time: ISO8601 | epoch | slash string; unparseable input
        RAISES (never falls back to wall-clock — spec §6.1). Delegates to
        sodamem.memory.ingest.client.IngestClient, constructed lazily from
        self.store / self.extractor / config.

        Type-hint note: the design's own "新生代码" wrote the fully-dotted
        forward reference `"sodamem.memory.ingest.config.IngestConfig | None"`
        inline; that string trips ruff's F821 (it parses quoted annotations
        as forward-ref expressions and `sodamem` is not a name bound in this
        module's own namespace — a top-level package can't name itself).
        Resolved the same way `store: "Store"` already does two lines up:
        a TYPE_CHECKING-only import + the bare class name, not a literal
        change in what the annotation documents."""
        from sodamem.memory.ingest.client import IngestClient
        from sodamem.memory.ingest.config import IngestConfig as _IngestConfig
        client = IngestClient(store=self.store, extractor=self.extractor,
                               config=config or _IngestConfig())
        return client.ingest_session(turns, user_id=user_id, session_id=session_id,
                                      session_time=session_time,
                                      agent_id=agent_id, run_id=run_id,
                                      project_id=project_id, infer=infer)

    def search(self, query: str, *, user_id: str,
               config: "RetrievalConfig | None" = None,
               as_of=None, agent_id: str = "", run_id: str = "",
               project_id: str = "") -> "SearchResult":
        """Delegates to sodamem.memory.retrieval.search.search (the I5 gate
        symbol), against self.store. See that function's docstring for why
        `as_of` currently always raises when not None (§6.7: no silent
        no-op for a caller-supplied argument the port doesn't implement
        yet)."""
        from sodamem.memory.retrieval.search import search as _search
        return _search(query, user_id=user_id, store=self.store, config=config,
                       as_of=as_of, agent_id=agent_id, run_id=run_id,
                       project_id=project_id)

    def dream(self, user_id: str, *, config: "IngestConfig | None" = None) -> None:
        """Rebuilds stale entity profiles for user_id. Idempotent — safe to
        call repeatedly; only entities marked dirty (D35 dirty-bit) are
        touched. Delegates to sodamem.memory.dreaming.run_dream."""
        from sodamem.memory.dreaming import run_dream
        run_dream(store=self.store, user_id=user_id, config=config)

    def build_context(self, query: str, *, user_id: str, token_budget: int,
                      organizer: "Any | None" = None,
                      agent_id: str = "", run_id: str = "",
                      project_id: str = "") -> "ContextBlock":
        """Prompt-ready evidence block for `query` (spec §6.2). Zero-LLM by
        default; pass an `organizer` (needs a provider) for the value-board /
        enumeration-sweep path. Delegates to sodamem.context.build_context.

        Import is deferred: sodamem.context imports SodaMem at module level,
        so an eager import here would be circular (same reason .ingest/.dream
        defer)."""
        from sodamem.context import build_context as _build_context
        return _build_context(self, query, user_id=user_id,
                              token_budget=token_budget, organizer=organizer,
                              agent_id=agent_id, run_id=run_id,
                              project_id=project_id)

    def answer(self, question: str, *, user_id: str, provider: Any,
               current_date: str, planner_config: Any | None = None,
               reader_config: Any | None = None, answer_bias: bool = False) -> "Answer":
        """End-to-end: run the planner loop over this user's memory, then read
        a final answer with citations (the +23-net-question path, spec §6.4;
        `[answer]` extra). `provider` is a sodamem.llm provider (encapsulates
        api_key/base_url/model — credentials never pass through this
        signature). Builds a MemoryTool over (self, user_id) as the loop's
        tool surface. Delegates to sodamem.answer.answer_question."""
        from sodamem.answer import answer_question
        from sodamem.tools import MemoryTool
        tool = MemoryTool(self, user_id=user_id)
        return answer_question(
            question, current_date=current_date, tools=tool, provider=provider,
            planner_config=planner_config, reader_config=reader_config,
            answer_bias=answer_bias,
        )

    def close(self) -> None:
        """Release the underlying store's OS resources (SQLite connection,
        chroma System — several file handles plus mmap'd hnsw indices per
        store; see Store._close_chroma).

        The facade owns the store, so the facade has to own its lifecycle.
        Without this, the only way to release was `mem.store.close()` —
        reaching through the facade into its own field — and callers
        predictably didn't: a rig opening one store per question over 500
        questions held every handle it ever took.

        Idempotent: a second close is a no-op, so `finally: mem.close()`
        nested inside a `with` block can't raise over the caller's own
        exception."""
        close = getattr(self.store, "close", None)
        if close is not None:
            close()

    def __enter__(self) -> "SodaMem":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @classmethod
    def open(cls, data_dir: "str | Path", *, embedder: Any | None = None,
             extractor: Any | None = None, prompts: "dict[str, str] | None" = None,
             raw_recall_enabled: bool = True,
             derived_currency: bool = False) -> "SodaMem":
        """Convenience constructor for the common local case: open (or create)
        a store under `data_dir` with the default in-process ONNX embedder and
        the active extraction prompts as its I6 fingerprint baseline. Pass an
        `extractor` (a FactEventExtractorV2 or any object with extract_session)
        to enable .ingest(); leave it None for a read-only store (search /
        build_context work without it). This is the composition root the
        stubbed from_config() gestured at — it owns 'which prompts fingerprint
        this store', keeping that single-sourced instead of scattered."""
        from pathlib import Path as _Path
        from sodamem.embedding.onnx_minilm import OnnxMiniLmEmbedder
        from sodamem.memory.storage.store import open_store
        from sodamem.prompts import extraction as _ex
        emb = embedder or OnnxMiniLmEmbedder()
        # Fingerprint exactly the prompts the extraction path assembles
        # (EXTRACT + DETERMINISM — COARSE_RULES is an unwired asset, see
        # prompts/extraction.py). Fingerprinting text that never reaches the
        # LLM would let two behaviorally different stores share a fingerprint.
        fp_prompts = prompts if prompts is not None else {
            "extract_system": _ex.EXTRACT_SYSTEM_PROMPT,
            "determinism_rules": _ex.DETERMINISM_RULES,
        }
        store = open_store(_Path(data_dir) / "memory.db", prompts=fp_prompts,
                           embedder=emb, raw_recall_enabled=raw_recall_enabled,
                           derived_currency=derived_currency)
        return cls(store=store, extractor=extractor, embedder=emb)


__all__ = ["__version__", "SodaMem"]
