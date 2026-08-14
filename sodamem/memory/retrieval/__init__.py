"""sodamem.memory.retrieval — bm25/vector/fusion/query_plan + the I5 gate
symbol `search()`.

Re-exports the package's public surface so `sodamem.memory.retrieval.search`
resolves the FUNCTION (I5 gate symbol) directly off the package, not just via
the submodule path `sodamem.memory.retrieval.search.search` — both work, but
the gate fixture (`tests/gates/test_i5_zero_llm_zero_egress.py`) does
`import sodamem.memory.retrieval as retrieval; retrieval.search(...)`, which
needs the former.

Structurally enforced by import-linter's `retrieval-no-llm` contract
(`.importlinter`, Phase 0 B3): nothing reachable from this package may import
`sodamem.llm`.
"""
from __future__ import annotations

from .config import Degradation, DegradationCode, FusionConfig, RetrievalConfig
from .query_plan import QueryPlan, temporal_match
from .search import SearchResult, search

__all__ = [
    "search",
    "SearchResult",
    "Degradation",
    "DegradationCode",
    "RetrievalConfig",
    "FusionConfig",
    "QueryPlan",
    "temporal_match",
]
