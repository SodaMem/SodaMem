"""sodamem.memory.ingest — extraction pipeline.

Ports the predecessor implementation
plus the ingest half of `client.py`/`_helpers.py`. Consumes `sodamem.models`,
`sodamem.prompts.extraction`, `sodamem.memory.storage`, `sodamem.llm`, and
`sodamem.memory._shared`; not depended on by `sodamem.memory.retrieval`
(Task 6) or the reverse.
"""
from __future__ import annotations

from .calendar_resolve import iso_precision, resolve_date, resolve_range
from .client import IngestClient, IngestResult, parse_session_time
from .config import (
    ConfidenceWeights,
    EdgeConfidenceWeights,
    ExtractConfig,
    ExtractWindowConfig,
    IngestConfig,
    SummaryConfig,
)
from .extractor import FactEventExtractorV2
from .maintainer import EntityResolver, GraphMaintainer, SummarySynthesizer

__all__ = [
    "ConfidenceWeights",
    "EdgeConfidenceWeights",
    "EntityResolver",
    "ExtractConfig",
    "ExtractWindowConfig",
    "FactEventExtractorV2",
    "GraphMaintainer",
    "IngestClient",
    "IngestConfig",
    "IngestResult",
    "SummaryConfig",
    "SummarySynthesizer",
    "iso_precision",
    "parse_session_time",
    "resolve_date",
    "resolve_range",
]
