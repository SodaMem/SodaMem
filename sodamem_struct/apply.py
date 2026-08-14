"""Install structural answer path (no prompt addenda)."""
from __future__ import annotations

_APPLIED = False


def is_applied() -> bool:
    return _APPLIED


def apply() -> None:
    """Force tools + replace ``run_s500.answer_one`` with structural pipeline."""
    global _APPLIED
    if _APPLIED:
        return

    from sodamem_struct.force_tools import apply_tool_forcing

    apply_tool_forcing()

    import run_s500
    from sodamem_struct.answer_one import answer_one_struct

    run_s500.answer_one = answer_one_struct
    _APPLIED = True
