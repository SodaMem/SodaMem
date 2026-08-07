"""Reader (read-side) LLM prompt text — governs HOW an already-built store gets
turned into an answer, not what got written into it.

Write-side / read-side fingerprint boundary (why this module is excluded from
`active_prompt_fingerprint()` in `sodamem.versioning`, while
`sodamem.prompts.extraction` is included): a store's CONTENT — which facts
exist, what they say — is fixed entirely by the extraction prompts at ingest
time. Changing how a reader is told to phrase or reason about an answer does
not change what is IN the store; the same store remains valid data under a
new reader prompt, so a reader-prompt change must NOT trip
`assert_store_compatible`'s fail-closed check (that would force needless
re-ingestion of unchanged data). Only a change to `sodamem.prompts.extraction`
can change store content, so only that module participates in the fingerprint.
`sodamem.prompts.planner` carries the same read-side exclusion for the same
reason (it governs tool choice/ordering during retrieval, not extraction).

Decomposition note for READER_GUIDANCE: this text is assembled by string
concatenation, conditionally including three addenda depending on runtime
state (planner claims present / answer-bias arm / a runtime_resolved_value
block present). Collapsing all four pieces into one always-on string would
silently change behavior — the addenda would stop being conditional. Each
piece is therefore its own string constant, and the reader recomposes them
with the same conditionals.
"""

from __future__ import annotations

# Base reader guidance, always included.
READER_GUIDANCE = (
    "\n\nThe retrieval planner's sufficiency judgment is advisory, not authoritative. "
    "Independently answer from the supplied source evidence. Use ordinary supported "
    "inference. For the same user attribute, prefer the newest direct user statement "
    "unless it is explicitly hypothetical, negated, or only a future plan. Do not "
    "treat absence of matching evidence as proof of zero. When the user asks for "
    "recommendations or advice, use the memory as personalization context and provide "
    "useful recommendations; the recommendations themselves need not have been listed "
    "verbatim in memory. If the evidence truly does not answer a factual request about "
    "the requested entity or relation, say that information is missing."
)

# Appended only when planner_claims or planner_conflicts are present.
READER_GUIDANCE_PLANNER_CLAIMS_ADDENDUM = (
    " The planner's supported claims and resolved conflicts above are advisory "
    "pre-resolved context; you may rely on an entity link the planner already "
    "established when the cited source evidence backs it, but still apply your own "
    "reasoning and abstain when the question cannot actually be answered."
)

# Appended only under the answer_bias arm (conditional anti-abstention, arm E).
READER_GUIDANCE_ANSWER_BIAS_ADDENDUM = (
    " You have already retrieved evidence for this question. If any supplied "
    "evidence or supported claim bears on the asked entity, relation, or event, "
    "commit to your best supported answer instead of declaring the information "
    "missing. Reserve 'information is missing' strictly for when NONE of the "
    "supplied evidence relates to what is being asked."
)

# Appended only when a runtime_resolved_value semantic advisory is present.
# Softened 0731 from "Answer
# with that resolved_value … Do not recompute a different value": the old
# wording turned a ranking aid into an answer override — q053's reader held
# both "need 300 total" and "have 200 so far" and was forbidden the
# subtraction the question asked for (gold 100). Paired replay over all 21
# strong-narrowed questions: this flips q053 right with zero regressions on
# the 16 originally-correct valid questions, KU 14/14 intact.
READER_GUIDANCE_RUNTIME_RESOLVED_ADDENDUM = (
    " When a runtime_resolved_value block is present, treat it as the "
    "top-ranked candidate for the asked current/latest value, computed from "
    "the supplied evidence. Prefer it over older or lower-ranked context, but "
    "verify it against the cited evidence, and if the question requires "
    "arithmetic over the evidence (a gap, a remainder, a sum), compute that "
    "from the evidence rather than echoing the candidate."
)

# NEW (0730). Appended only under
# personalization_bias. Targets the worst-performing question type on the 0729
# full-500: single-session-preference at 23/30, a 23% failure rate against
# 10.6% overall. All seven misses open with the reader's scratch work
# ("**Step 1: Extract Relevant Information**") and land on advice that would
# fit anyone.
#
# The two sentences are one unit. Leading with personalization WITHOUT also
# checking against stated dislikes is how q212 happens — the user said screens
# keep them awake and the reader recommended a meditation app. The positive
# half alone just produces confident wrong suggestions faster. Never ship half
# of this.
READER_GUIDANCE_PERSONALIZATION_ADDENDUM = (
    " When the question asks for a recommendation or suggestion, lead with "
    "what you know about this person — their stated habits, tools they "
    "already own, and things they have said they enjoy — and do not pad the "
    "answer with generic alternatives that would fit anyone. Before including "
    "any suggestion, check it against what they have said they avoid or "
    "dislike, and drop it if it conflicts; a thing they explicitly do not "
    "want is not made acceptable by being listed as a secondary option."
)

# Default answer constraints. A list, not a single prompt string — it is consumed
# element-wise (`answer_constraints=list(_DEFAULT_ANSWER_CONSTRAINTS)`), not as
# prose text.
DEFAULT_ANSWER_CONSTRAINTS = [
    "Answer directly.",
    "Use only recalled key_evidence.",
    "Do not mention internal tool steps.",
    "Do not mention excluded candidates.",
    "Mention ambiguity only if it changes the conclusion or affects confidence.",
    "Reason from the recalled source text when arithmetic or temporal comparison is needed.",
]
