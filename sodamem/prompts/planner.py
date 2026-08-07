"""Planner (read-side) LLM prompts — govern HOW an already-built store gets
searched/reasoned over, not what got written into it.

Deliberately EXCLUDED from `active_prompt_fingerprint()`
(`sodamem.versioning`) — see `sodamem.prompts.reader` module docstring for the
write-side/read-side boundary rationale shared by both modules.
"""

from __future__ import annotations

# Answer-shape / temporal-intent classifier prompt. The
# `{q}`/`{__QUESTION__}`/`{__CURRENT_DATE__}` placeholders are filled by the
# caller at call time.
QUERY_PLAN_PROMPT = """You classify the answer shape for a question over a user's personal memory.
Output exactly one JSON object and no prose.

Schema:
{
  "question_classification": "ordinary|enumeration|count|sum|comparison",
  "comparison_requires_count_or_sum": false,
  "answer_shape": "single_slot_current_value|current_value|historical_value|enumeration|aggregate_sum|sum|state_location|open_text",
  "temporal_intent": "current|latest|historical|windowed|timeless",
  "requires_value_board": false,
  "requires_enumeration_sweep": false,
  "slot_hint": {
    "metric": "",
    "value_type": "money|count|time|duration|location|text|unknown",
    "total_vs_delta": "total|delta|unknown",
    "entities": []
  },
  "enumeration_hint": {
    "object_type": "",
    "actions": [],
    "time_window": "",
    "exclude_status": ["plan", "advice", "external_info"]
  },
  "confidence": 0.0
}

Positive examples:
- "What was the amount I was pre-approved for when I got my mortgage from Wells Fargo?"
  -> ordinary, single_slot_current_value, latest, value_board=true, metric="pre-approved mortgage amount", value_type=money, entities=["Wells Fargo"]
- "How many followers do I have on Instagram now?"
  -> single_slot_current_value, current, value_board=true, metric="Instagram follower count", value_type=count, total_vs_delta=total
- "How many different species of birds have I seen in my local park?"
  -> single_slot_current_value, latest, value_board=true, metric="bird species count in local park", value_type=count, total_vs_delta=total
- "How many hours have I spent on my abstract ocean sculpture?"
  -> single_slot_current_value, latest, value_board=true, metric="hours spent on abstract ocean sculpture", value_type=duration, total_vs_delta=total
- "What time do I wake up on Saturday mornings?"
  -> single_slot_current_value, current, value_board=true, metric="wake-up time on Saturday mornings", value_type=time
- "How many times did I bake something in the past two weeks?"
  -> count, enumeration answer_shape, windowed, enumeration_sweep=true, object_type="baking event", actions=["bake"], time_window="past two weeks"
- "How many kitchen items did I replace or fix?"
  -> enumeration, timeless, enumeration_sweep=true, object_type="kitchen item", actions=["replace", "fix"]
- "How many online courses have I completed in total?"
  -> aggregate_sum, timeless, value_board=false, enumeration_sweep=true, object_type="completed online course"
- "What is my total get-ready plus commute time?"
  -> sum, aggregate_sum answer_shape, timeless, value_board=false, enumeration_sweep=true, object_type="time component", actions=["get ready", "commute"]
- "Which happened first, my Boston trip or starting at Acme?"
  -> comparison, comparison_requires_count_or_sum=false
- "Which month had more flights, May or June?"
  -> comparison, comparison_requires_count_or_sum=true
- "Where do I currently keep my old sneakers?"
  -> state_location, current, value_board=false because location-state questions can conflict with future plans.
- "What time did I used to wake up before?"
  -> historical_value, historical, value_board=false.

Question: __QUESTION__
Current date: __CURRENT_DATE__
JSON:"""

# Autonomous evidence-retrieval planner system prompt.
PLANNER_SYSTEM_PROMPT = """You are an autonomous evidence-retrieval planner.

Your job is to gather and organize memory evidence. A separate reader writes
the final answer. Choose tools and their order yourself; there is no prescribed
workflow for any question type.

Each response MUST be one JSON object with:
{
  "state_update": {
    "objective": "optional concise objective",
    "question_classification": {
      "type": "ordinary|enumeration|count|sum|comparison",
      "comparison_requires_count_or_sum": false
    },
    "upsert_claims": [
      {
        "claim_id": "stable short id",
        "statement": "atomic evidence-grounded statement",
        "evidence_ids": ["exact ids visible in evidence_state"],
        "status": "supported|disputed|hypothesis",
        "material": true
      }
    ],
    "retract_claim_ids": [],
    "open_questions": [
      {"question": "material unknown", "material": true}
    ],
    "resolved_questions": [],
    "conflicts": [
      {
        "description": "conflicting claims or values",
        "evidence_ids": ["..."],
        "resolved": false,
        "resolution": ""
      }
    ]
  },
  "decision": {
    "action": "tool_calls|final",
    "reason": "short explanation based on current uncertainty",
    "calls": [
      {"tool": "browser_search", "args": {"query": "..."}}
    ],
    "selected_evidence_ids": [],
    "sufficiency": "sufficient|insufficient",
    "missing_information": ""
  }
}

Rules:
- Start with browser_search unless the runtime says it already ran.
- Classify every question in state_update.question_classification. Enumeration,
  count, and sum questions require a successful browser_count_evidence call
  before finalization. Comparison questions require a successful
  browser_timeline_events call; comparisons that ask for counts or sums require
  both calls.
- You may issue up to four independent calls in one decision.
- Ranked similarity top-k results are candidates and are never exhaustive.
  Record material conclusions as atomic claims with exact evidence ids.
- Distinct entities, events, collections, people, or categories are not
  versions of one another. Preserve each supported component.
- Search for counterevidence when a premise may be wrong or evidence conflicts.
- Use inspect_session when a promising result is truncated, when inspect cannot
  expand a span result, or when nearby turns in the same session may contain a
  missing list item, exact number, update, or prerequisite statement.
- For exact names or numbers, prefer user source turns over assistant
  paraphrases and retrieve the source turn before finalizing.
- Do not repeat a query unless you explain what new information it can reveal.
- open_questions is the COMPLETE current list, not an append-only log. Keep only
  concrete unresolved questions for which another action could materially
  change the answer. Do not keep an unbounded "could anything else exist?"
  question material after multiple meaningfully different searches have
  stabilized the supported claim set. In that case submit open_questions=[]
  (or mark the residual uncertainty material=false) before finalizing.
- Finalize only when remaining material uncertainty cannot change the answer.
- For sufficient finalization, select every evidence item needed by the reader.
- For insufficient finalization, state the missing information and select the
  evidence that demonstrates the mismatch or limitation when available.
- Never write the user's final answer. Never expose private chain-of-thought.
"""

# Per-tool one-line descriptions injected into the planner prompt.
#
# Known unfinished fix: this dict is hand-written and should be GENERATED from a
# canonical tool registry to rule out hand-copy drift by construction — there is
# a recorded incident where an unprefixed-key version of this dict silently hid
# 6 of 7 tools
# from the planner (intersection with `allowed_tools` collapsed to
# {browser_search}). `sodamem/tools/` (the registry's landing spot) does not exist
# yet (Task 9); this task only ports the text as-is and does the one-time manual
# drift check below. Task 9 must come back and add a build-time test asserting
# TOOL_GUIDE covers every name in `sodamem.tools._TOOL_REGISTRY` (see
# tests/test_prompts.py::test_tool_guide_covers_full_tool_registry, xfail until then).
#
# One-time manual drift check (2026-07-18): `memory.tool.*` is a DIFFERENT
# tool surface (HTTP/MemoryTool namespace: "memory.tool.search",
# "memory.tool.raw-search", ... — 11 names, zero overlap with the browser_-prefixed
# names below by design; it backs the FastAPI server, not this CLI agent loop).
# The actual canonical set is the CLI tool-schema `tools` key, which today
# holds exactly:
# browser_calculate_duration, browser_count_evidence, browser_inspect,
# browser_inspect_session, browser_search, browser_search_raw,
# browser_timeline_events (7 names). All 7 are present below. TOOL_GUIDE carries
# two additional entries (compute, compute_operators) dispatched by the same
# CliToolbox outside that schema file, per the source comment ("the CliToolbox
# dispatch accepts both spellings"). No drift found today.
#
# Task 9 update (2026-07-19): `sodamem.tools._TOOL_REGISTRY` has now landed
# (11 `memory.tool.*` entries — the HTTP-facing advertised registry, distinct
# from the `browser_*` CLI-facing surface above; both dispatch to the same
# `MemoryTool` methods, see `sodamem/tools/__init__.py`'s `_DISPATCH_TABLE`).
# `test_tool_guide_covers_full_tool_registry` (tests/test_prompts.py) asserts
# TOOL_GUIDE is a superset of `_TOOL_REGISTRY`'s names — this is a real
# namespace UNION, not a typo: the `memory.tool.*` block below exists purely
# to satisfy that drift guard (no current planner/CLI loop dispatches these
# names — `sodamem.answer`, Task 10+, hasn't been built yet, so which of the
# two namespaces becomes the single canonical one for the eventual planner is
# an open decision for that task, not this one). Descriptions are transcribed
# from `_TOOL_REGISTRY`'s own `description`/`params` fields, not invented.
TOOL_GUIDE = {
    "browser_search": "ranked mixed memory search (similarity); args: query, top_k, offset",
    "browser_inspect": "expand one candidate card in full; args: memory_id",
    "browser_inspect_session": "expand all source turns in one session; args: session_id",
    "browser_search_raw": "search raw source turns, optional time window; args: query, top_k, from_ts, to_ts",
    "browser_timeline_events": "retrieve and time-order named events/entities; args: events, top_k_per_event",
    "browser_calculate_duration": "deterministic date difference; args: from_date, to_date, mode",
    "browser_count_evidence": "count labeled occurrences across sessions; args: query, labels, top_k_per_label",
    "compute": "deterministic evidence computation; args: operator, inputs, params",
    "compute_operators": "list available deterministic operators; args: {}",
    # --- memory.tool.* (sodamem.tools._TOOL_REGISTRY) — see comment above ---
    "memory.tool.search": "initial wide evidence search, ranked candidate cards with provenance and next-tool suggestions; args: query, top_k, offset, session_id, include_context",
    "memory.tool.search-more": "continue a prior wide search from an offset, preserving ranking order; args: query, top_k, offset, session_id, include_context",
    "memory.tool.refine": "deterministically filter wide search results by metadata fields, no LLM/semantic gate; args: query plus optional filters (channels, types, entity, status, date ranges, confidence, terms)",
    "memory.tool.result": "expand one search result by fact_id or source span_id, with full provenance and follow-up actions; args: memory_id",
    "memory.tool.raw-search": "search raw conversation turns directly for exact entities/dates/numbers/wording; args: query, top_k, session_id, from_ts, to_ts",
    "memory.tool.session": "return the full ordered turn list of one session; args: session_id",
    "memory.tool.entity-timeline": "timeline of fact events mentioning a given entity, oldest first; args: entity_id",
    "memory.tool.explore": "graph BFS over FactEdges from a starting node; args: start_id, start_type, depth, edge_types, limit",
    "memory.tool.event-timeline": "for each event phrase, return matching evidence grouped by event, for ordering/comparison; args: events, top_k_per_event",
    "memory.tool.evidence-count": "collect candidate evidence per label without aggregating, for 'most'/'how many' questions; args: query, labels, from_ts, to_ts, top_k_per_label",
    "memory.tool.tools-list": "list all available memory tools and their parameters; args: {}",
}
