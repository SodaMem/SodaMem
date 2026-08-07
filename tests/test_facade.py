"""T13: the SodaMem facade must expose the product surface a pip-install user
touches — build_context (§6.2, the get_context table-stakes gap) and answer
(§6.4, +23 path) were reachable only via deep imports until now, and
from_config was a NotImplementedError stub after its T6 blocker cleared."""
import json
import sqlite3

from sodamem import SodaMem
from sodamem.errors import ConfigError
from sodamem.llm.testing import ScriptedProvider
from sodamem.memory.ingest.extractor import FactEventExtractorV2

import pytest

_EXTRACT = json.dumps({"facts": [{
    "kind": "fact", "predicate_canonical": "owns_pet",
    "predicate_raw": "has a golden retriever named Biscuit",
    "statement": "User owns a golden retriever named Biscuit",
    "entity_roles": {"pet": ["Biscuit"]}, "modality": "state",
    "source_type": "explicit_text", "confidence": 0.9,
    "source_spans": [{"turn_index": 0, "quote": "golden retriever named Biscuit"}],
}]})


def _mem(tmp_path):
    extractor = FactEventExtractorV2(provider=ScriptedProvider([_EXTRACT] * 4))
    mem = SodaMem.open(tmp_path, extractor=extractor)
    mem.ingest([{"role": "user", "content": "I have a golden retriever named Biscuit"}],
               user_id="u1", session_id="s1", session_time="2024/03/01 (Fri) 10:00")
    return mem


def test_open_gives_a_working_read_path(tmp_path):
    mem = _mem(tmp_path)
    sr = mem.search("what pet do I have", user_id="u1")
    assert sr.degraded == []
    assert len(sr.evidence) >= 1


def test_facade_build_context(tmp_path):
    mem = _mem(tmp_path)
    cb = mem.build_context("what pet do I have", user_id="u1", token_budget=500)
    assert cb.text and cb.citations
    # every citation must appear in the rendered text (T8 honesty invariant)
    for cid in cb.citations:
        assert cid in cb.text


def test_facade_answer_delegates_to_answer_question(tmp_path, monkeypatch):
    """Facade scope: prove .answer() builds a MemoryTool over (self, user_id)
    and forwards provider/current_date to answer_question. The loop itself is
    covered by T10's parity gate, not re-tested here."""
    from sodamem.tools import MemoryTool

    mem = _mem(tmp_path)
    captured = {}

    def _spy(question, *, current_date, tools, provider, **kw):
        captured.update(question=question, current_date=current_date,
                        tools=tools, provider=provider)
        class _A:  # stand-in Answer
            text = "You own a golden retriever named Biscuit."
        return _A()

    monkeypatch.setattr("sodamem.answer.answer_question", _spy)
    sentinel_provider = object()
    ans = mem.answer("what pet do I have", user_id="u1", provider=sentinel_provider,
                     current_date="2024/03/02")
    assert "Biscuit" in ans.text
    assert captured["provider"] is sentinel_provider
    assert captured["current_date"] == "2024/03/02"
    assert isinstance(captured["tools"], MemoryTool)
    assert captured["tools"]._user_id == "u1" and captured["tools"]._memory is mem


def test_from_config_delegates_to_open(tmp_path):
    mem = SodaMem.from_config({"data_dir": str(tmp_path)})
    assert mem.search("anything", user_id="u1").degraded == []


def test_from_config_requires_data_dir():
    with pytest.raises(ConfigError):
        SodaMem.from_config({"embedder": "onnx"})

# ---------------------------------------------------------------------------
# Lifecycle. `Store.close()` has always existed and always mattered — its own
# docstring spells out that chroma's PersistentClient leaks file handles and
# mmap'd hnsw indices unless the System is stopped. But the facade is what
# callers actually hold, and it exposed no way to release anything: the only
# route was `mem.store.close()`, reaching past the facade into the object it
# owns. An API that can only be used correctly by ignoring it is an API that
# will be used incorrectly — the S500 rig opened one store per question across
# 500 questions and never closed one, because from where it stood there was
# nothing to call.
# ---------------------------------------------------------------------------

def test_close_releases_the_stores_resources(tmp_path):
    mem = SodaMem.open(tmp_path)
    assert mem.store.chroma_available

    mem.close()

    assert not mem.store.chroma_available
    with pytest.raises(sqlite3.ProgrammingError):
        mem.store.get_fact_event("any-id")


def test_close_is_idempotent(tmp_path):
    """`finally: mem.close()` inside a `with` block double-fires. Releasing an
    already-released store must be a no-op, not the exception that masks
    whatever the caller was actually handling."""
    mem = SodaMem.open(tmp_path)
    mem.close()
    mem.close()


def test_context_manager_closes_on_exit(tmp_path):
    with SodaMem.open(tmp_path) as mem:
        assert mem.store.chroma_available
    assert not mem.store.chroma_available


def test_context_manager_closes_when_the_body_raises(tmp_path):
    """The leak that matters is the one on the error path — a rig that dies
    mid-question must not keep the handles."""
    mem_ref = {}
    with pytest.raises(RuntimeError):
        with SodaMem.open(tmp_path) as mem:
            mem_ref["mem"] = mem
            raise RuntimeError("boom")
    assert not mem_ref["mem"].store.chroma_available


def test_repeated_open_close_does_not_accumulate_os_resources(tmp_path):
    """The shape that produced the LongMemEval q463-q500 tail, with the fix
    applied: open one store per item in a loop and release each one.

    Measured cost of a store that is never closed: ~12 file descriptors and
    ~11 threads. Against macOS's hard `kern.num_taskthreads` ceiling of 4096
    that is a few hundred stores, after which chroma can no longer start —
    and `Store._init_chroma` swallows that failure (`except Exception` ->
    log -> `chroma_available = False`, no raise), so the store comes back
    looking healthy with its vector route silently gone. Every subsequent
    question then answers against degraded retrieval and scores as an
    ordinary miss.

    So the guard is on resource growth, not on close() returning cleanly:
    the failure this protects against never raised in the first place.
    """
    psutil = pytest.importorskip("psutil")
    proc = psutil.Process()

    def _dir(name):
        # SodaMem.open() does not create its data_dir (StoreManager mkdirs
        # before calling it), so each per-iteration dir has to be made here.
        d = tmp_path / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    # One warm-up cycle so lazily-imported modules and the embedder's one-off
    # setup are not counted as growth.
    with SodaMem.open(_dir("warmup")) as mem:
        assert mem.store.chroma_available

    before_fds, before_threads = proc.num_fds(), proc.num_threads()
    for i in range(12):
        with SodaMem.open(_dir(f"u{i}")) as mem:
            assert mem.store.chroma_available
    grew_fds = proc.num_fds() - before_fds
    grew_threads = proc.num_threads() - before_threads

    # Leaking would be ~144 fds / ~135 threads over these 12 cycles; the
    # thresholds leave room for allocator and pool jitter without leaving
    # room for the bug.
    assert grew_fds < 24, f"leaked {grew_fds} file descriptors over 12 open/close cycles"
    assert grew_threads < 24, f"leaked {grew_threads} threads over 12 open/close cycles"
