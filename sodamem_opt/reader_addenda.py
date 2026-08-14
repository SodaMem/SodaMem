"""Extra reader guidance / constraints for Plan B+ (MR / TR / preference / currency)."""

OPT_READER_GUIDANCE = (
    " Temporal discipline: when multiple dated events appear, list them in "
    "chronological order using occurred/event dates when present, otherwise "
    "document/session time — never invent an order without dates. For "
    "'most recent' / 'started most recently', prefer the candidate whose "
    "start/onset is latest among comparable timestamps; duration phrases "
    "(\"for a few months\" vs \"for 6 months\") imply start order only when "
    "anchored to the same as-of date. Prefer explicit start/join dates over "
    "inferring from duration alone when both are present. For 'got first' / "
    "'received first', use acquisition/arrival dates, not preorder dates. "
    "For the same attribute with multiple values: if the question says 'new' "
    "/ 'upgrade' / 'switched to', prefer the statement that describes that "
    "change, not an unrelated later mention; if it says 'previous' / "
    "'personal best before', prefer the superseded earlier value, not the "
    "latest. Otherwise prefer the newest direct user statement. If the "
    "question names a specific artwork/item but evidence has exactly one "
    "matching purchase/value fact (e.g. flea-market art worth 3x), answer "
    "from that fact rather than abstaining on a missing synonym."
    " Aggregation discipline (how many / how much / total): (1) enumerate "
    "distinct candidate items/events with evidence ids before counting; "
    "(2) dedupe repeats of the same event across sessions; (3) exclude "
    "plans, advice, assistant-only estimates, and superseded old versions "
    "unless the question asks for history; (4) for knowledge-update counts, "
    "take the latest authoritative total, do not add old and new totals; "
    "(5) output the integer/sum only after the include/exclude lists are "
    "consistent with the question's scope (store vs dry-cleaner, current "
    "ownership vs replaced items, this year vs all time)."
    " Preference discipline: when the user asks for tips/recommendations, "
    "the answer must lead with their stated likes, owned tools, and "
    "constraints from memory — not generic advice that would fit anyone. "
    "Abstention discipline: if a required numeric or entity slot appears "
    "only in assistant chatter and never as a user fact, say the "
    "information is not enough rather than computing from the assistant."
)

OPT_ANSWER_CONSTRAINTS = [
    "If the question asks for an order or timeline, sort dated evidence explicitly before answering.",
    "Do not claim 'most recent' without a comparable date; abstain on recency if undated.",
    "For conflicting values of the same attribute, prefer the newest dated user statement — unless the question asks for 'new plan', 'upgrade', 'previous', or 'former', in which case follow that wording.",
    "For price/value/discount comparisons, require evidence for both sides before concluding.",
    "For how-many / how-much / total questions: list included items then count/sum; do not invent items; do not double-count.",
    "If a deterministic_set_count advisory is present, treat its value as the primary count/sum unless an included item is clearly out of scope — do not re-enumerate from scratch and get a different integer without justification.",
    "For recommendations or tips: personalize from user memory first; never answer as if no preferences were stated when evidence shows them.",
    "If the user never stated a required quantity (e.g. bus fare) and only the assistant guessed, answer that the information is not enough.",
]

OPT_PLANNER_ADDENDUM = """
Additional retrieval discipline (runtime opt):
- For how-many / how-much / total / across-session aggregation: classify as
  count or sum; call browser_count_evidence; issue at least one alternate
  browser_search (and browser_search_raw when names/numbers matter) with
  different keywords before finalizing. Treat top-k as incomplete.
- Prefer browser_inspect_session on promising hits when a list item, dollar
  amount, or date may be truncated.
- For knowledge-update totals, retrieve both older and newer statements and
  keep only the latest authoritative value — never sum superseded counts.
- For recommendation/tips questions, search for likes, dislikes, owned tools,
  and stated constraints; do not stop after a shallow topical match.
- For save/cost comparisons: only finalize a numeric savings answer if the
  user themselves stated both prices; assistant-only quotes are insufficient.
- For relative-day questions (last Saturday, N days ago), search by the
  anchored calendar date and inspect that session fully before abstaining.
"""
