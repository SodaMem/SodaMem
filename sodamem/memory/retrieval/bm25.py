"""BM25 lexical search over a Store's summaries/spans/raw turns/facts.

Ported from the predecessor implementation's `_bm25_tokens` (:61-74, a
pure tokenizer — NOT the same function as `sodamem.memory._shared._tokenize`,
which lacks the ies/es/s suffix-stemming this one does; the two were always
separate functions in the source and stay separate here) and
`bm25_search_summary_syntheses`/`bm25_search_source_spans`/
`bm25_search_raw_turns`/`bm25_search_fact_events` (:1823-1915).

CQRS move: the four per-user BM25Okapi caches come OFF
`Store` (a write-side object has no business holding a read-side lexical
index) and onto `BM25Index`, a class of its own. Cache invalidation is keyed
by `store.write_version(user_id)` — Task 3's public monotonic counter — not
by reaching into a private `Store` attribute.

`BM25Index` instances are cheap to construct but their VALUE is entirely in
being reused across calls (rebuilding a BM25Okapi model from every fact/span/
turn/summary on every single query would make the cache pointless). Since
`sodamem.memory.retrieval.search()` is a free function taking a `Store` per
call rather than a long-lived object holding one, `get_bm25_index(store)`
below keeps a process-wide, weakly-referenced registry keyed by the `Store`
object's identity — one `BM25Index` per live `Store`, automatically dropped
when the `Store` itself is garbage-collected. This is the retrieval-layer
equivalent of what used to be four plain instance-attribute dicts on
`StorageBackend` itself.
"""
from __future__ import annotations

import re
import weakref

from sodamem.memory.storage.store import Store
from sodamem.models import FactEvent, RawTurn, SourceSpan, SummarySynthesis, fact_search_document


def _bm25_tokens(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9][a-z0-9'\-]*", str(text or "").lower())
    expanded = []
    for tok in tokens:
        expanded.append(tok)
        if "-" in tok:
            expanded.extend(part for part in tok.split("-") if len(part) > 1)
        if len(tok) > 4 and tok.endswith("ies"):
            expanded.append(tok[:-3] + "y")
        elif len(tok) > 4 and tok.endswith("es"):
            expanded.append(tok[:-2])
        elif len(tok) > 3 and tok.endswith("s"):
            expanded.append(tok[:-1])
    return expanded


class BM25Index:
    """Lazily-built, write-version-invalidated BM25 indices for one `Store`.

    Each of the four caches holds `(write_version_at_build, BM25Okapi | None,
    corpus_items)` per `user_id` — `None` for the model means "built once,
    corpus was empty," matching the source's own cached-negative shape (avoids
    rebuilding an empty index every call for a user with nothing indexed yet).
    """

    def __init__(self, store: Store) -> None:
        self._store = store
        self._summary_cache: dict[str, tuple] = {}
        self._span_cache: dict[str, tuple] = {}
        self._raw_turn_cache: dict[str, tuple] = {}
        self._fact_cache: dict[str, tuple] = {}

    def search_summary_syntheses(self, query: str, user_id: str, n: int = 10) -> list[tuple[SummarySynthesis, float]]:
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            return []
        cached = self._summary_cache.get(user_id)
        current_ver = self._store.write_version(user_id)
        if cached is None or cached[0] != current_ver:
            # DR-004 #3: dirty / 版本不一致的 Summary 不进召回索引。
            summaries = self._store.get_summary_syntheses(user_id, routing_only=True)
            if not summaries:
                self._summary_cache[user_id] = (current_ver, None, [])
                return []
            bm25 = BM25Okapi([_bm25_tokens(s.summary_text) for s in summaries])
            self._summary_cache[user_id] = (current_ver, bm25, summaries)
        else:
            _, bm25, summaries = cached
            if bm25 is None:
                return []
        scores = bm25.get_scores(_bm25_tokens(query))
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:n]
        return [(summaries[i], float(scores[i])) for i in top_indices if scores[i] > 0]

    def search_source_spans(self, query: str, user_id: str, n: int = 30) -> list[tuple[SourceSpan, float]]:
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            return []
        cached = self._span_cache.get(user_id)
        current_ver = self._store.write_version(user_id)
        if cached is None or cached[0] != current_ver:
            spans = self._store.get_all_source_spans(user_id)
            if not spans:
                self._span_cache[user_id] = (current_ver, None, [])
                return []
            bm25 = BM25Okapi([_bm25_tokens(s.text) for s in spans])
            self._span_cache[user_id] = (current_ver, bm25, spans)
        else:
            _, bm25, spans = cached
            if bm25 is None:
                return []
        scores = bm25.get_scores(_bm25_tokens(query))
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:n]
        return [(spans[i], float(scores[i])) for i in top_indices if scores[i] > 0]

    def search_raw_turns(self, query: str, user_id: str, n: int = 30) -> list[tuple[RawTurn, float]]:
        """BM25 over immutable raw turns (issue #75 §2.3).

        Built lazily from SQLite, so it works on stores ingested before
        raw_recall existed — no backfill needed for the lexical path.
        """
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            return []
        cached = self._raw_turn_cache.get(user_id)
        current_ver = self._store.write_version(user_id)
        if cached is None or cached[0] != current_ver:
            turns = self._store.get_all_raw_turns(user_id)
            if not turns:
                self._raw_turn_cache[user_id] = (current_ver, None, [])
                return []
            bm25 = BM25Okapi([_bm25_tokens(t.content) for t in turns])
            self._raw_turn_cache[user_id] = (current_ver, bm25, turns)
        else:
            _, bm25, turns = cached
            if bm25 is None:
                return []
        scores = bm25.get_scores(_bm25_tokens(query))
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:n]
        return [(turns[i], float(scores[i])) for i in top_indices if scores[i] > 0]

    def search_fact_events(self, query: str, user_id: str, n: int = 30) -> list[tuple[FactEvent, float]]:
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            return []
        cached = self._fact_cache.get(user_id)
        current_ver = self._store.write_version(user_id)
        if cached is None or cached[0] != current_ver:
            facts = self._store.get_all_fact_events(user_id)
            if not facts:
                self._fact_cache[user_id] = (current_ver, None, [])
                return []
            bm25 = BM25Okapi([_bm25_tokens(fact_search_document(f)) for f in facts])
            self._fact_cache[user_id] = (current_ver, bm25, facts)
        else:
            _, bm25, facts = cached
            if bm25 is None:
                return []
        scores = bm25.get_scores(_bm25_tokens(query))
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:n]
        return [(facts[i], float(scores[i])) for i in top_indices if scores[i] > 0]


_INDEX_REGISTRY: "weakref.WeakKeyDictionary[Store, BM25Index]" = weakref.WeakKeyDictionary()


def get_bm25_index(store: Store) -> BM25Index:
    """One `BM25Index` per live `Store`, found/created by identity. See this
    module's docstring for why a registry rather than a `Store`-owned
    attribute or a fresh-per-call instance."""
    idx = _INDEX_REGISTRY.get(store)
    if idx is None:
        idx = BM25Index(store)
        _INDEX_REGISTRY[store] = idx
    return idx
