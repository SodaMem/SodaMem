from sodamem import SodaMem
from sodamem.context import build_context, ContextBlock
from sodamem.memory.storage.store import open_store


class _FakeEmbedder:
    def embed(self, texts):
        return [[0.0] for _ in texts]


def test_build_context_returns_context_block_on_empty_store(tmp_path):
    store = open_store(tmp_path / "s.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    mem = SodaMem(store=store, embedder=_FakeEmbedder())
    block = build_context(mem, "anything", user_id="u1", token_budget=1000)
    assert isinstance(block, ContextBlock)
    assert isinstance(block.text, str)


def test_build_context_default_path_is_organizer_free(tmp_path):
    store = open_store(tmp_path / "s2.sqlite3", prompts={"x": "y"}, embedder=_FakeEmbedder())
    mem = SodaMem(store=store, embedder=_FakeEmbedder())
    # organizer=None is the default and must not raise or require an LLM.
    block = build_context(mem, "anything", user_id="u1", token_budget=1000, organizer=None)
    assert block.degraded == [] or isinstance(block.degraded, list)


def test_truncated_context_never_lies_about_citations():
    """T8-review Critical: when token_budget truncates the rendered text, the
    citations/evidence lists MUST shrink to the rendered subset — a citation
    for evidence absent from the text is a lie the reader will repeat."""
    from sodamem.context.cards import compact_cards
    from sodamem.context.store import EvidenceStore

    store = EvidenceStore()
    payload = {
        "items": [
            {"evidence_id": f"ev{i}", "support_text": f"fact number {i} with several words"}
            for i in range(10)
        ]
    }
    store.ingest("search", {"query": "facts"}, payload, step=0)

    # budget=5 tokens ≈ 20 chars: no card line fits → everything truncated
    text, evidence, citations = compact_cards(store, query="facts", token_budget=5)
    assert text == ""
    assert evidence == [] and citations == [], (
        f"empty text but {len(citations)} citations — lying references"
    )
    # partial budget: whatever IS cited must appear in the rendered text
    text2, evidence2, citations2 = compact_cards(store, query="facts", token_budget=200)
    assert citations2, "sanity: some cards should fit at budget=200"
    for cid in citations2:
        assert cid in text2, f"citation {cid} not present in rendered text"
    assert len(evidence2) == len(citations2)
