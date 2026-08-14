"""Extraction (write-side) LLM prompts — the ONLY prompts that determine what a
store's content IS.

These are the prompts consumed by `active_prompt_fingerprint()`
(`sodamem.versioning`): changing any string in this module changes what facts
land in a store, so a store built under the old text must fail closed against
code running the new text (I6 / `assert_store_compatible`). planner/reader
prompts (`sodamem.prompts.planner`, `sodamem.prompts.reader`) do NOT participate
in that fingerprint — see `sodamem.prompts.reader` module docstring for why.

Two sibling prompts are deliberately absent: a salience prompt and a
consolidation prompt were only ever reachable behind `INGEST_SALIENCE_GATE` /
`INGEST_CONSOLIDATE`, two feature flags that lost their S500 A/B and were never
flipped on in a shipped config.
Dead flag, dead prompt — no store was ever built with either text active.
"""

from __future__ import annotations

# Core extraction system prompt. Always active —
# every extraction call is built on this schema.
EXTRACT_SYSTEM_PROMPT = """You extract answer-grade FactEvent records from conversation SourceSpans.

Return only a JSON array. Do not use markdown.

Each item must use this schema:
{
  "kind": "fact|event|state|preference",
  "predicate_raw": "short exact factual statement, preserving names/numbers/dates",
  "predicate_canonical": "stable snake_case predicate",
  "event_type": "purchase|meeting|travel|work|health|media|food|social|admin|preference|state|advice|other",
  "modality": "past_event|future_plan|current_state|preference|question|intent|assistant_advice|external_info",
  "occurred_start": "YYYY-MM-DD only when explicitly stated as an absolute date, otherwise null",
  "occurred_date": {"expr": "relative date phrase or null", "anchor": "session_date"},
  "occurred_end": "YYYY-MM-DD only when explicitly stated as an absolute date, otherwise null",
  "valid_from": "YYYY-MM-DD only when explicitly stated as an absolute date, otherwise null",
  "valid_from_date": {"expr": "relative effective-date phrase or null", "anchor": "session_date"},
  "valid_until": "YYYY-MM-DD only when explicitly stated as an absolute date, otherwise null",
  "quantity_value": number or null,
  "quantity_unit": "money|duration|count|distance|weight|percent|item_count|none",
  "entity_roles": {"subject":"user", "role_name":"entity value", "...":"..."},
  "source_span_ids": ["SourceSpan id"],
  "support_text": "short quote or paraphrase from the SourceSpan"
}

Rules:
- Extract user facts, events, preferences, plans, corrections, states, and answer-useful assistant advice.
- Assistant recommendations are modality=assistant_advice, not user facts.
- Pure generic assistant advice can be omitted unless it is likely needed later.
- Preserve exact quantities, dates, names, titles, brands, and durations.
- Use kind=state and modality=current_state for current values, personal records/bests,
  collection sizes, totals, and rolling-window counts.
  Set valid_from_date={"expr":"today","anchor":"session_date"} when no more precise
  effective date is stated.
- Set quantity_value/quantity_unit only when the source states a number. Do not
  infer counts from a description that merely implies one.
- Use source_span_ids from the prompt only.
- Do not calculate relative dates yourself. For "yesterday", "last Tuesday",
  "two weeks ago", "next month", etc., copy the phrase into occurred_date or
  valid_from_date with anchor="session_date" and leave the absolute date field null.
"""

# Modality-guidance addendum appended to EXTRACT_SYSTEM_PROMPT (was gated by
# INGEST_DETERMINISM). Pairs with the deterministic _normalize_modality post-step.
DETERMINISM_RULES = """
Modality guide (choose by MEANING, not verb tense):
- past_event: a completed one-off event ("watched the movie last night").
- current_state: a value/status that holds NOW, including the RESULT of a change.
  A relocation/switch/replacement is a current_state, even when stated in past tense:
  "I moved to Chicago" / "switched to oat milk" / "now use Todoist" => kind=state,
  modality=current_state, with the NEW value as the state.
- future_plan: a scheduled/intended future event. intent: a want/goal not yet acted on.
- preference: a stable like/dislike. assistant_advice: the assistant's recommendation.
"""

# Granularity + hard-grounding addendum (Hindsight-style).
#
# UNWIRED ASSET (audit 0723 reverted an unconditional append): in production
# this text was gated behind INGEST_EXTRACT_COARSE,
# default OFF with no config override — the S500 stores of record were built
# without it, and the coarse extraction route measurably lost on hard-47
# (H 24 > BASE 21 > C 18). The byte-identical text is kept ONLY as the raw
# material for PRD R2.7's future domain-profile system, which needs a real
# design (incl. I6 fingerprint integration) before anything appends this.
COARSE_RULES = """
Granularity & grounding (apply strictly — this governs everything above):
- Emit a SMALL number of consolidated, SELF-CONTAINED facts. Each fact should
  capture a durable piece of information covering an exchange — NOT a per-turn
  transcript. Aim for a few rich facts per topic, not many fragments.
- Do NOT emit facts about the mechanics of the conversation: no "user asked X",
  no "assistant stated/explained Y", no greetings/acknowledgements/typo notes,
  no play-by-play (individual game moves, intermediate steps). Extract the durable
  fact an exchange ESTABLISHES, fold the surrounding turns into it.
- Grounding: state ONLY what the source quote licenses. NEVER invent a number,
  entity, brand, place, date, or purpose that is not in the quote; NEVER generalize
  (e.g. "natural cleaning stuff" is NOT "eco-friendly products"; "Chiefs won 14
  games, Jaguars won 9" is NOT "14 games against Jaguars"). Preserve exact
  quantities/names/dates that ARE present.
"""


# --- VARIANT (0726, pending cheap gate then S500): entity-subject extraction ---
# Measured problem (docs/design/currency-and-graph-shape-0726.md): the default
# prompt above hardcodes `"subject":"user"` in the schema example AND says
# "Extract user facts" in its first rule. The LLM copies the example, so
# subject_entity_id is `entity_user` on 100.0% of 4700 measured facts — the
# graph is a perfect star. World knowledge ("Omega was founded in 1848 by Louis
# Brandt") gets flattened into `user --omega_founding_year_founder--> Omega`,
# which (a) pollutes the user profile with facts that are not about the user
# (external_info alone is 25.8% of facts / world-knowledge text is 46.5% of all
# support_text chars) and (b) destroys the entity-to-entity structure that
# multi-hop traversal and cross-user knowledge sharing would need.
#
# This variant changes exactly ONE thing — who may be the subject — so the
# cheap gate can attribute any score movement to that and nothing else. It does
# NOT touch the predicate_canonical shape (the attribute+value fusion behind
# the starved supersession); that is a separate variable for a separate run.
def _require_replace(text: str, old: str, new: str) -> str:
    """`str.replace` that refuses to no-op.

    This variant is built by substituting into `EXTRACT_SYSTEM_PROMPT`. When
    that base text changes — as it did when the domain vocabulary came out —
    a plain `.replace()` whose target no longer matches silently returns the
    string unchanged, and the "variant" becomes a byte-identical copy of the
    base. An A/B between two identical prompts reports "no difference" and
    looks like a valid result. Failing at import is the only outcome that
    cannot be mistaken for an answer.
    """
    if old not in text:
        raise ValueError(
            "EXTRACT_SYSTEM_PROMPT_ENTITY_SUBJECT expected to find and replace:\n"
            f"  {old!r}\n"
            "…but the base prompt no longer contains it. Update this variant to "
            "match the current base text — leaving it would silently produce a "
            "copy of the base and make any A/B meaningless."
        )
    return text.replace(old, new)


EXTRACT_SYSTEM_PROMPT_ENTITY_SUBJECT = _require_replace(
    _require_replace(
        EXTRACT_SYSTEM_PROMPT,
        '"entity_roles": {"subject":"user", "role_name":"entity value", "...":"..."},',
        '"entity_roles": {"subject":"user OR the real entity the statement is about", '
        '"role_name":"entity value", "...":"..."},',
    ),
    "- Extract user facts, events, preferences, plans, corrections, states, and answer-useful assistant advice.",
    "- Extract user facts, events, preferences, plans, corrections, states, and answer-useful assistant advice.\n"
    "- SUBJECT: use \"user\" only when the statement is about the user. When the\n"
    "  statement is about the world (modality=external_info) or is advice about a\n"
    "  thing (modality=assistant_advice), set subject to THAT entity's name.\n"
    "  \"Omega was founded in 1848 by Louis Brandt\" -> subject=\"Omega\",\n"
    "  predicate_canonical=\"founded_by\", entity_roles={\"subject\":\"Omega\",\n"
    "  \"founder\":\"Louis Brandt\",\"year\":\"1848\"} — NOT subject=\"user\".\n"
    "  The user's relationship to that entity is a SEPARATE fact\n"
    "  (subject=\"user\", predicate_canonical=\"owns\", entity_roles={\"item\":\"Omega Seamaster\"}).",
)
