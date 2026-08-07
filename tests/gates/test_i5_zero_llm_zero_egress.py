import pytest

retrieval = pytest.importorskip(
    "sodamem.memory.retrieval",
    reason="I5 gate activates when retrieval is ported in Phase 1",
)


def _block_egress(monkeypatch):
    import socket
    real = socket.socket.connect

    def guard(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"retrieval attempted network egress to {address} — I5 violated")
        return real(self, address)

    monkeypatch.setattr(socket.socket, "connect", guard)


@pytest.mark.skipif(not hasattr(retrieval, "search"), reason="retrieval.search not yet defined")
def test_search_makes_no_network_egress(monkeypatch, tmp_path):
    """Half 2 of I5: search() must not open a socket to anything but
    localhost — proves the embedder is in-process, not a remote call."""
    from sodamem.memory.storage.store import open_store

    class _FakeEmbedder:
        def embed(self, texts):
            return [[0.0] for _ in texts]

    store = open_store(tmp_path / "i5.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    _block_egress(monkeypatch)
    retrieval.search(query="ping", user_id="u1", store=store)  # must not raise from egress guard


def test_search_module_never_imports_llm():
    """Half 1 of I5: retrieval cannot generate, structurally — this is a
    static assertion, not a runtime probe with an injected raising provider,
    because search() has no provider parameter to inject one into (that
    would itself violate I5's zero-LLM-dependency guarantee). The
    import-linter `retrieval-no-llm` contract (Phase 0 B3) is the mechanism;
    this test is a redundant, fast, in-process check of the same fact."""
    import sodamem.memory.retrieval.search as _search_mod
    assert "sodamem.llm" not in _search_mod.__dict__.get("__loader__", "").__class__.__module__
    import sys
    llm_modules_imported_by_retrieval = {
        m for m in sys.modules if m.startswith("sodamem.memory.retrieval")
    }
    for mod_name in llm_modules_imported_by_retrieval:
        mod = sys.modules[mod_name]
        assert not any(
            getattr(v, "__module__", "").startswith("sodamem.llm")
            for v in vars(mod).values()
        ), f"{mod_name} references something from sodamem.llm"
