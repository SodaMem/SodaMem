"""Structural answer-path optimizations (not prompt addenda).

Implements optimization-notes options 1 + 3:
- set aggregation via include/exclude + code count/sum
- metric slot selection via code (new plan speed, redeem points, role duration)

Enable with ``SODAMEM_STRUCT_APPLY=1`` and ``python -m sodamem_struct.run_frozen``.
Does not modify ``sodamem`` sources.
"""
from __future__ import annotations

from sodamem_struct.apply import apply, is_applied

__all__ = ["apply", "is_applied"]
__version__ = "1.0.0"
