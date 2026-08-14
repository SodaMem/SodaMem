"""SetEnumeration skill — force itemized enumeration before COUNT/SUM finalize.

Type-level skill for multi-hop set questions. Not question-id specific.
Stacks on Soft + Protocol v1.3 when applied from protocol patches.
"""
from __future__ import annotations

_APPLIED = False

PLANNER_ADDENDUM = """
SetEnumeration skill (MR set tasks):
- For how-many / total / which-all set questions: first build an explicit
  item_list from memory (one line per distinct item with date + short quote).
- Count or sum ONLY after the list is written. Do not jump to an integer.
- If the question asserts N items and your list has fewer than N, keep
  searching; do not finalize a complete count.
- Exclude planning-only mentions when the question asks for completed acts
  (led/attended/bought/fixed/serviced/purchased).
- For entity counts (bikes, instruments, tanks): merge repeated mentions of
  the same entity; do not count each service visit as a new entity.
"""

READER_GUIDANCE = (
    " SetEnumeration: When set_enumeration_board is present, your final answer "
    "must be consistent with that item_list (count = distinct items; sum = "
    "listed amounts only). If CARDINALITY says have < N, do not claim a complete set."
)

CONSTRAINTS = [
    "SetEnumeration: enumerate distinct items before giving a how-many/total answer.",
    "SetEnumeration: planning ≠ done (exclude plan-only rows for led/attended/bought).",
    "SetEnumeration: same entity mentioned twice still counts once when counting entities.",
]


def is_applied() -> bool:
    return _APPLIED


def apply() -> None:
    """Patch planner/reader prompts; advisories are built in protocol_v1."""
    global _APPLIED
    if _APPLIED:
        return

    import sodamem.prompts.planner as planner_prompts
    import sodamem.prompts.reader as reader_prompts

    if PLANNER_ADDENDUM.strip() not in planner_prompts.PLANNER_SYSTEM_PROMPT:
        planner_prompts.PLANNER_SYSTEM_PROMPT = (
            planner_prompts.PLANNER_SYSTEM_PROMPT.rstrip()
            + "\n"
            + PLANNER_ADDENDUM.strip()
            + "\n"
        )
    if READER_GUIDANCE not in reader_prompts.READER_GUIDANCE:
        reader_prompts.READER_GUIDANCE = (
            reader_prompts.READER_GUIDANCE + READER_GUIDANCE
        )
    constraints = list(reader_prompts.DEFAULT_ANSWER_CONSTRAINTS)
    for c in CONSTRAINTS:
        if c not in constraints:
            constraints.append(c)
    reader_prompts.DEFAULT_ANSWER_CONSTRAINTS = constraints
    _APPLIED = True
