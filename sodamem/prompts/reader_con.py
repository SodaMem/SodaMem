"""The frozen LongMemEval official Chain-of-Note (CoN) reader prompt.

Kept in its own module, separate from `sodamem.prompts.reader`, because this is
a FROZEN external protocol (the official LongMemEval scoring harness's reader
prompt) that benchmark replay/judge paths must be able to import in isolation
without dragging in the rest of the general reader guidance text — mixing the
two would risk an accidental edit to one leaking into the other's byte-identity
guarantee. Read-side (governs how an answer gets phrased, not what a store
contains) — excluded from `active_prompt_fingerprint()` in `sodamem.versioning`
for the same reason as `sodamem.prompts.reader`/`sodamem.prompts.planner`; see
`sodamem.prompts.reader` module docstring for the full boundary rationale.

This module holds only the frozen prompt-template string constant itself;
prompt assembly and formatting live with the reader that consumes it.
"""

from __future__ import annotations

OFFICIAL_CON_PROMPT_TEMPLATE = (
    "I will give you several history chats between you and a user. Please answer the question based on the relevant chat history. "
    "Answer the question step by step: first extract all the relevant information, and then reason over the information to get the answer.\n\n\n"
    "History Chats:\n\n{}\n\nCurrent Date: {}\nQuestion: {}\nAnswer (step by step):"
)
