"""sodamem.memory.storage — the SQLite + chroma persistence layer.

Re-exports the package's public surface: `Store` (formerly `StorageBackend`),
`open_store` + `store_meta_schema` (I6 gate symbols — names frozen, see spec),
and `Embedder` (re-exported from `sodamem.embedding` for callers that only
import the storage package).
"""
from __future__ import annotations

from .entity_rules import EntityRulesStore
from .schema import store_meta_schema
from .store import Embedder, Store, open_store

__all__ = ["Embedder", "EntityRulesStore", "Store", "open_store", "store_meta_schema"]
