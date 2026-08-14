"""Answer-side optimizations for SodaMem Miss reductions (Plan B+).

Plan B: time windows, TR timeline forcing, value-query rewrite.
Plan B+: MR aggregation recall, preference personalization hook,
abstention / currency reader discipline.

Does not modify ``sodamem`` sources. Call :func:`apply` before answering.
"""
from __future__ import annotations

from sodamem_opt.patches import apply, is_applied

__all__ = ["apply", "is_applied"]

__version__ = "0.3.0"
