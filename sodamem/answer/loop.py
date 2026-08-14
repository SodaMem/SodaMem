"""`run_planner_loop`: the autonomous evidence-retrieval planner loop.

This module owns tool-calling orchestration ONLY. It does not answer the
question — `sodamem.answer.reader.answer()` does that, over a
`ReaderContext` a caller assembles from this module's
`AutonomousAgentResult` (see `sodamem/answer/__init__.py`'s
`answer_question()` for the composition). Splitting these apart is the
point of the protocol/loop/reader/rules decomposition (task brief title):
`run_autonomous_agent` used to call `_reader_answer` internally as its last
step, and this module deliberately does not re-create that call — `loop.py`
must never import `reader.py` (nothing here needs the reader's LLM prompt
machinery, and importing it would make the loop/reader split cosmetic
rather than real).

Step-0 semantics (bug #9 restoration, 0724): the planner is consulted at
  EVERY step including step 0, exactly like source `run_autonomous_agent`.
  Until a search has run, the planner's proposed calls are REPLACED by the
  forced first search (`browser_search`, `query=<question>`, `top_k=10`,
  `include_context=true`, `include_multimodal=true`, source :2215-2225) —
  but its step-0 `state_update` (objective rewrite / open_questions) is
  applied FIRST and survives, as in source (:2185 precedes :2215). An
  earlier port skipped the step-0 planner consult entirely; that discarded
  the state shaping and shrank 12 planner turns to 11 — see
  `tests/test_answer_loop_parity.py`

Tool vocabulary bridge: the planner speaks `sodamem.prompts.planner.
TOOL_GUIDE`'s `browser_*`/`compute`/`compute_operators` names (unchanged
from source — the system prompt text literally says "Start with
browser_search"). `sodamem.tools.MemoryTool.dispatch()` (Task 9) speaks a
different vocabulary (`memory.tool.*`), because T9 explicitly left "which
namespace becomes canonical for the planner loop" as an open decision for
this task (T9 report Concern #2). This module is the answer: `_TOOL_DISPATCH`
below translates browser_*/compute names to `MemoryTool` dispatch names +
per-tool argument renames, entirely inside this file — neither
`sodamem.tools` (frozen at T9) nor `sodamem.prompts.planner` (frozen at T2)
needed to change.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

import sodamem.answer.rules as rules_mod
from sodamem.answer.agent_guidance import AgentGuidance
from sodamem.answer.context_offload import (
    PlannerContextOffload,
    cards_chars,
    projection_protections,
)
from sodamem.answer.protocol import PlannerState
from sodamem.context.store import EvidenceStore, _json_object
from sodamem.llm import LLMProvider
from sodamem.answer.timewords import resolve_time_window
from sodamem.prompts.planner import PLANNER_SYSTEM_PROMPT, TOOL_GUIDE
from sodamem.tools import MemoryTool, ToolError

# Planner-facing tool vocabulary (matches TOOL_GUIDE / PLANNER_SYSTEM_PROMPT,
# unchanged from source). `_TOOL_DISPATCH` maps each name (plus the rule-only
# alias "search") to (MemoryTool dispatch name, arg-rename table).
_TOOL_DISPATCH: dict[str, tuple[str, dict[str, str]]] = {
    "search": ("memory.tool.search", {}),
    "browser_search": ("memory.tool.search", {}),
    "browser_inspect": ("memory.tool.result", {}),
    "browser_inspect_session": ("memory.tool.session", {}),
    "browser_search_raw": ("memory.tool.raw-search", {}),
    "browser_timeline_events": ("memory.tool.event-timeline", {}),
    "browser_calculate_duration": ("memory.tool.date-calc", {"from_date": "from_", "to_date": "to"}),
    "browser_count_evidence": ("memory.tool.evidence-count", {}),
    "compute": ("memory.tool.compute", {}),
    "compute_operators": ("memory.tool.operators-list", {}),
}

# The rule vocabulary's "search" capability is satisfied by any tool that
# dispatches to memory.tool.search, under either of its two planner-facing
# spellings ("search" from rules.initial_calls(), "browser_search" from an
# actual planner proposal). See module docstring's "Tool vocabulary bridge".
_RULE_ALIASES: dict[str, str] = {"browser_search": "search"}
_TOOL_FAMILY_CAPABILITIES: dict[str, str] = {
    "browser_count_evidence": "count_family",
    "browser_timeline_events": "timeline_family",
}

DEFAULT_ALLOWED_TOOLS: tuple[str, ...] = tuple(
    name for name in TOOL_GUIDE if not name.startswith("memory.tool.")
)

_MAX_SELECTED_EVIDENCE_DEFAULT = 24


@dataclass(frozen=True)
class PlannerConfig:
    """Planner-loop knobs. These used to be bare parameters
    (`max_steps`/`planner_max_tokens`/`temperature`/`fallback_top_k`/
    `allowed_tools`). `final_max_tokens` does NOT become a field here — it only ever fed `_reader_answer(max_tokens=...)`,
    which is `sodamem.answer.reader.ReaderConfig.max_tokens`'s job now that
    the reader call is external to this loop (see module docstring).

    `fallback_top_k=10` is not an invented number: it is the literal value
    every inspected S500 live trace's forced first search used
    (`~/Desktop/LongMemEval-ingest/.../agent_traces/*.json`, `cli_tools[0].
    args.top_k`), applied to the "search" init call's args by
    the step-0 override inside `run_planner_loop`.
    """
    max_steps: int = 12  # source run_benchmark.py:200 (BENCHMARK_AGENT_MAX_STEPS)
    planner_max_tokens: int = 1200  # source :201 (BENCHMARK_AGENT_STEP_MAX_TOKENS)
    temperature: float = 0.0
    fallback_top_k: int = 10
    allowed_tools: tuple[str, ...] = DEFAULT_ALLOWED_TOOLS
    max_selected_evidence: int = _MAX_SELECTED_EVIDENCE_DEFAULT
    # 0730 abstention gate — see `_unproven_abstention_errors`. Stays OFF, but
    # NOT because it was shown to be harmful: the measurement could not resolve
    # it either way, and an earlier version of this comment said "measured and
    # rejected" on the strength of a number that means nothing.
    #
    # Full-500 paired, same store, only this flag differing: OFF 451/500, ON
    # 443/500 — net -8 across 42 questions moved, McNemar p = 0.28. Then two
    # baselines that differ by NOTHING (44c7cc8 vs 5928abc, every arm off,
    # default paths verified identical) came out 451 and 443: net -8 across 40
    # questions moved, p = 0.27. Zero change reproduces this arm's entire
    # "effect", so the run says nothing about the gate.
    #
    # What does survive, because it is structural rather than a score claim:
    # `max_steps_reader_fallback` went 98 -> 113 with the gate on, and 8 of the
    # 25 regressions are `planner_final` -> fallback flips (q196 answers
    # correctly in 3 steps off, wrongly in 12 on). Blocking finalization does
    # not make the planner look harder; it makes it run out of steps. That is a
    # real mechanism and a good reason for scepticism, but it is not a score.
    #
    # Do not re-run this as-is. A single paired run on this rig cannot resolve
    # anything under ~12 questions (see benchmarking/README.md, "Noise floor");
    # either repeat the arm several times or find a deterministic metric.
    abstention_gate: bool = False
    # 0731 — see `_finalization_errors`. Default ON since the c3 full-500
    # (58c95d2 arm): omit-rejections 61 -> 0 on top of the c2 baseline,
    # score 453 vs 452 (p=1.0, inside the noise floor — the requirement for
    # a cost mechanism). Earlier standalone measurement on the old baseline:
    # omit-rejections 258 -> 0, pure re-submit steps 175 -> 0, tokens -3.5%.
    #
    # It does NOT fix citation integrity at max_steps, and an earlier version
    # of this comment claimed it did. The fallback below already seeds
    # `selected` from every supported claim's `evidence_ids` — it reaches the
    # same goal by another route, so there was never a hole there. The "22
    # surviving violations" that motivated the claim came from a metric that
    # read `packet.decision.selected_evidence_ids`, i.e. what the model
    # PROPOSED; on a question that runs out of steps the model never finalizes
    # successfully, so that field only holds rejected attempts.
    claim_evidence_autofill: bool = True
    # 0730 count arm — surface `evidence_count`'s deduplicated, date-ordered
    # roster in the planner's observation. Default ON since 0731: the full-500
    # A/B (+5/40 discordants, inside the ±8-12 noise floor) could never
    # resolve it, but the stable-set measurement can — on the 19 questions
    # wrong in all five traced runs plus a 50-question stable-right regression
    # sample, the roster flipped 6 and regressed 1 (official judge). Content
    # verification splits the 6: q170/q182 genuinely count right now (3
    # weddings named, 4 events with the yoga fundraiser excluded), q219
    # answers instead of refusing (rubric-real), and q178/q313/q328 are
    # judge-format artifacts — the same wrong value judged yes in terse prose
    # and no in step-by-step form. Net content-real +2~3, zero content-real
    # regressions attributable to the mechanism (q023 flipped in 3 of 4 new
    # runs including the flag-default one — planner-roll, not roster).
    count_roster: bool = True
    # 0730 temporal arm — resolve the question's relative date expression into
    # an explicit window before the planner searches. Default OFF, same rule.
    time_window: bool = False
    # 0731 stall stop — end the loop early when the planner is provably
    # spinning: a second exact-duplicate proposal (the runtime already skips
    # the call, so the step's only content is being told so), a fourth
    # zero-row retrieval, or two consecutive steps with no new evidence.
    # Offline counterfactual on b5_traced_0731 (see cut_step in
    # benchmarking/replay_stoploss.py): triggers on 137/500 questions, cuts
    # 603/3445 planner steps — 17.5% of run tokens at the ~4K-token marginal
    # step cost. Of the 11 questions whose final selection would lose rows,
    # the reader replay got 8 valid controls: 1 flipped wrong (q214),
    # 1 flipped right (q017), net zero. Default OFF, same rule as the others.
    stall_stop: bool = True
    # 0731 truncation retry — 96 of the 99 unparseable planner outputs in
    # b5_traced_0731 are hard truncation at the 1200-token cap (2985-4626
    # chars, a JSON head with no tail), clustered on count questions whose
    # state_update re-upserts a growing claim set (24 questions own all 99;
    # q173 alone lost 10 steps). The failure then costs a WHOLE extra step:
    # feedback says "not valid JSON" and the planner regenerates from
    # scratch at the same cap. Retrying once at double the cap converts that
    # wasted step into one longer completion. Default OFF, same rule.
    truncation_retry: bool = True
    # 0731 prompt-cache layout — same information, ordered for prefix
    # caching. The b5 census: the planner re-serializes 33.8M chars across
    # 3445 steps, 70.8% of it evidence cards re-sent whole (each card 4.6x)
    # and 8.4% a byte-identical allowed_tools block, while DeepSeek bills a
    # cache-hit prefix at roughly a tenth of a miss — and gets almost none
    # of it, because volatile state (objective, claims, feedback) serializes
    # BEFORE the cards and the cards themselves are relevance-reranked
    # (1636/2945 adjacent steps break the card prefix; 769 of those are pure
    # reorders of an unchanged set). ON: allowed_tools moves into the system
    # prompt (constant across every call of a run), the payload becomes
    # constant-per-question fields -> cards in first-seen order ->
    # search_history -> volatile state last. First-seen order lifts stable
    # adjacent card prefixes from 44% to 70.5% of steps; the 867 eviction
    # transitions stay unstable (membership actually changes). Token count
    # is unchanged — this arm buys billing and latency, and must show a
    # score inside the noise floor to be kept. Default OFF, same rule.
    prompt_cache_layout: bool = True
    # 0731 short evidence ids — cards carry `ev_fact:fact_<uuid>` ids: 43
    # chars each, 13.4% of all card bytes, repeated again in claims,
    # search_history rosters and selected_evidence_ids. ON: the loop aliases
    # each id to `e1`/`e2`/... in first-seen order at the serialization
    # boundary (the model only ever sees aliases) and translates the
    # packet's exact-string matches back before anything executes or lands
    # in the trace, so observations, claims, selections and the duplicate-
    # call signature all keep real ids. ~10% of planner input. Default OFF,
    # same rule.
    short_evidence_ids: bool = True
    # Promoted after issue #7's reviewed full-S500 cost validation: Planner
    # context shrank with accuracy inside noise. EvidenceStore and Reader
    # remain untouched.
    context_offload: bool = True
    # Stall thresholds (see agent_guidance.AgentGuidance). The tightened
    # c3-validated values (dup 2->1, zero-rows 4->3): predicted a further
    # 11.8% of steps on the c2 counterfactual at 4 questions' evidence risk;
    # the c3 full-500 landed with score 453 vs 452 and the stalled
    # population unharmed. The c1 values were 2/4 — set them back to opt
    # out to the looser cut.
    stall_dup_threshold: int = 1
    stall_zero_rows_threshold: int = 3
    stall_zero_novelty_threshold: int = 2
    # 0731 c3 arm — settle a finalization's missing count/timeline-family
    # debt by running the required call instead of bouncing the step. The c2
    # census: 151 bounces, each exactly once, 151/151 obediently issuing the
    # call next step — a 3-planner-call dance (bounced final -> make-up call
    # -> final again) the runtime can do in 1. Default ON since the c3
    # full-500: family bounces 151 -> 0; the 143 autocalled questions
    # scored 137 against 136 for the same questions bouncing on c2 — the
    # runtime's keyword-synthesized args answer exactly as well as the
    # model's crafted make-up call.
    capability_autocall: bool = True
    # Diagnostic only: keep each step's planner user message in the trace.
    # Default OFF — the message is 1.4-12.5 KB per step and grows as evidence
    # cards accumulate, a cost only a diagnostic run should pay.
    capture_planner_input: bool = False


@dataclass
class AutonomousAgentResult:
    """R17: lives in loop.py, alongside the loop it is the return type of
    (task brief Produces section). No `answer: str` field — source's
    `run_autonomous_agent` (:1499-1506) had one because it called
    `_reader_answer` as its last internal step; this loop does not (module
    docstring). `evidence`/`insufficient`/`missing_information`/
    `planner_claims`/`planner_conflicts` are ADDITIVE vs. source: they used
    to be local variables inside `run_autonomous_agent`, computed right
    before its `_reader_answer` call (source :2329-2339) and then thrown
    away. Now that assembling reader context is the caller's job
    (`sodamem.answer.reader.assemble_reader_context`, `sodamem.answer.
    __init__.answer_question`), they must survive past this function's
    return — this is the necessary data-contract consequence of the
    protocol/loop/reader split, not scope creep.
    """
    evidence: EvidenceStore
    planner_trace: list[dict[str, Any]]
    usage: dict[str, Any]
    state: dict[str, Any]
    selected_evidence_ids: list[str]
    termination: str
    insufficient: bool
    missing_information: str
    planner_claims: list[dict[str, Any]]
    planner_conflicts: list[dict[str, Any]]


def _translate_ids(obj: Any, mapping: dict[str, str]) -> Any:
    """Exact-string id translation over a JSON tree, both directions.

    Exact match only: an id quoted inside a support text stays untouched,
    which is the conservative side — a missed alias costs bytes, a partial
    rewrite would corrupt evidence text.
    """
    if isinstance(obj, str):
        return mapping.get(obj, obj)
    if isinstance(obj, list):
        return [_translate_ids(x, mapping) for x in obj]
    if isinstance(obj, dict):
        return {k: _translate_ids(v, mapping) for k, v in obj.items()}
    return obj


def _planner_user_message(
    *,
    question: str,
    current_date: str,
    state: PlannerState,
    evidence: EvidenceStore,
    step: int,
    max_steps: int,
    allowed_tools: tuple[str, ...],
    time_window: bool = False,
    cache_layout: bool = False,
    id_aliases: dict[str, str] | None = None,
    context_offload: PlannerContextOffload | None = None,
    offload_telemetry: dict[str, Any] | None = None,
) -> str:
    """Ported from source :1509-1535, minus the deleted `oracle_seeded`
    parameter and its `oracle_ablation_note` payload key (paired with
    `_seed_oracle_evidence`'s deletion — see module docstring)."""
    preferred = []
    for claim in state.claims.values():
        preferred.extend(claim.evidence_ids)
    preferred.extend(state.selected_evidence_ids)
    full_cards = evidence.compact_cards(
        preferred_ids=preferred,
        newest_step=step - 1 if step else None,
        query=state.objective,
    )
    if context_offload is not None:
        supported, unresolved, selected = projection_protections(state)
        projection = context_offload.project(
            full_cards,
            supported_ids=supported,
            unresolved_conflict_ids=unresolved,
            selected_ids=selected,
        )
        cards = projection.cards
    else:
        projection = None
        cards = full_cards
    evidence_state = state.compact(
        evidence, step, max_steps, evidence_cards=cards
    )
    if not cache_layout:
        payload = {
            "question": question,
            "current_date": current_date or "unknown",
            "allowed_tools": {
                name: TOOL_GUIDE[name]
                for name in allowed_tools
                if name in TOOL_GUIDE
            },
            "evidence_state": evidence_state,
        }
        # Absent when nothing unambiguous was found, so the planner sees no
        # key rather than a null it has to interpret — an empty window and "I
        # could not resolve one" are different facts and must not share a
        # rendering.
        if time_window:
            window = resolve_time_window(question, current_date=current_date)
            if window:
                payload["resolved_time_window"] = window
    else:
        # Prefix-cache layout (see PlannerConfig.prompt_cache_layout):
        # constant-per-question fields, then the two append-mostly blocks
        # (cards in first-seen order, search_history), then everything that
        # mutates every step. allowed_tools is NOT here — it rides the
        # system prompt. Same keys otherwise; a reader of either shape finds
        # the same facts.
        cards = evidence_state.pop("evidence_cards")
        first_seen = {rid: i for i, rid in enumerate(evidence.records)}
        cards = sorted(
            cards,
            key=lambda c: first_seen.get(str(c.get("evidence_id")), len(first_seen)),
        )
        payload = {
            "protocol": evidence_state.pop("protocol"),
            "question": question,
            "current_date": current_date or "unknown",
        }
        if time_window:
            window = resolve_time_window(question, current_date=current_date)
            if window:
                payload["resolved_time_window"] = window
        payload["evidence_cards"] = cards
        payload["search_history"] = evidence_state.pop("search_history")
        payload["evidence_state"] = evidence_state
    if id_aliases:
        payload = _translate_ids(payload, id_aliases)
    message = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if projection is not None:
        context_offload.commit(projection)
        telemetry = projection.telemetry(enabled=True)
    else:
        ids = [str(card.get("evidence_id") or "") for card in full_cards]
        telemetry = {
            "enabled": False,
            "hot_count": len(ids), "warm_count": 0, "folded_count": 0,
            "hot_ids": ids, "warm_ids": [], "folded_ids": [],
            "rehydrated_ids_consumed": [],
            "full_card_chars": cards_chars(full_cards),
            "projected_card_chars": cards_chars(full_cards),
        }
    if offload_telemetry is not None:
        offload_telemetry.update(telemetry)
    return message


def _normalize_calls(decision: dict[str, Any]) -> list[dict[str, Any]]:
    """Ported byte-for-byte from source :1585-1598."""
    calls = decision.get("calls")
    if not isinstance(calls, list):
        tool = decision.get("tool")
        calls = [{"tool": tool, "args": decision.get("args") or {}}] if tool else []
    normalized = []
    for row in calls[:4]:
        if not isinstance(row, dict):
            continue
        tool = str(row.get("tool") or "")
        args = row.get("args") if isinstance(row.get("args"), dict) else {}
        if tool:
            normalized.append({"tool": tool, "args": args})
    return normalized


def _call_signature(tool: str, args: dict[str, Any]) -> str:
    """Ported byte-for-byte from source :1601-1607."""
    normalized = dict(args)
    if isinstance(normalized.get("query"), str):
        normalized["query"] = re.sub(
            r"\s+", " ", normalized["query"].strip().lower()
        )
    return f"{tool}:{json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(',', ':'))}"


def _finalization_errors(
    *,
    decision: dict[str, Any],
    state: PlannerState,
    evidence: EvidenceStore,
    max_selected_evidence: int,
    abstention_gate: bool = False,
    claim_evidence_autofill: bool = False,
) -> tuple[list[str], list[str]]:
    """Ported from source :1610-1684 (R16: stays in the loop). Deletions vs.
    source: no `require_compute` parameter, no `state.saw_search`/
    `state.saw_compute` checks — "at least one browser_search is required"
    is now `rules.check(..., is_final=True)`'s job, called by the caller
    BEFORE this function runs (see `run_planner_loop` below); the
    require_compute ablation is dead code, deleted with its parameter
    (module docstring)."""
    errors: list[str] = []
    errors.extend(_missing_capability_errors(state))
    selected = []
    for raw_id in decision.get("selected_evidence_ids") or []:
        eid = evidence.resolve(str(raw_id))
        if eid in evidence.records and eid not in selected:
            selected.append(eid)
        else:
            errors.append(f"unknown evidence id: {raw_id}")
    if claim_evidence_autofill:
        # The loop already holds these ids. Asking the model to repeat them
        # back costs a step per attempt and, on the questions where it keeps
        # failing, never lands at all — the run hits max_steps and the reader
        # gets the claims unbacked anyway.
        #
        # Resolved through the store exactly like the model's own picks: a
        # claim citing an id that does not exist must not become a way to put
        # a fabricated reference in front of the reader, and the omission
        # error below still fires for it.
        #
        # Claim evidence takes the cap's places first. Truncating it instead
        # would reintroduce the very omission this is here to prevent.
        claim_ids: list[str] = []
        for claim in state.claims.values():
            if not (claim.material and claim.status == "supported"):
                continue
            for raw in claim.evidence_ids:
                eid = evidence.resolve(str(raw))
                if eid in evidence.records and eid not in claim_ids:
                    claim_ids.append(eid)
        room = max_selected_evidence
        merged = claim_ids[:room] + [e for e in selected if e not in claim_ids]
        selected = merged[:room] if room else merged
    sufficiency = str(decision.get("sufficiency") or "")
    if sufficiency not in {"sufficient", "insufficient"}:
        errors.append("sufficiency must be sufficient or insufficient")
    if sufficiency == "sufficient" and not selected:
        errors.append("sufficient finalization requires selected evidence")
    if len(selected) > max_selected_evidence:
        errors.append(
            f"select at most {max_selected_evidence} material evidence items"
        )
    supported_material = [
        claim
        for claim in state.claims.values()
        if claim.material and claim.status == "supported"
    ]
    unsupported_absence_claims = [
        claim.claim_id
        for claim in supported_material
        if re.search(
            r"\b(no evidence|nothing found|not found|no information|never mentioned)\b",
            claim.statement,
            re.IGNORECASE,
        )
    ]
    if sufficiency == "sufficient" and unsupported_absence_claims:
        errors.append(
            "absence of retrieved evidence cannot support a sufficient zero/none claim: "
            + ", ".join(unsupported_absence_claims[:6])
        )
    selected_set = set(selected)
    omitted_claims = [
        claim.claim_id
        for claim in supported_material
        if not set(claim.evidence_ids).issubset(selected_set)
    ]
    if sufficiency == "sufficient" and omitted_claims:
        errors.append(
            "selected evidence omits material supported claims: "
            + ", ".join(omitted_claims[:6])
        )
    material_open = [
        row.get("question")
        for row in state.open_questions
        if row.get("material", True)
    ]
    if sufficiency == "sufficient" and material_open:
        errors.append("material open questions remain: " + "; ".join(map(str, material_open[:3])))
    unresolved = [
        row.get("description")
        for row in state.conflicts
        if not row.get("resolved")
    ]
    if sufficiency == "sufficient" and unresolved:
        errors.append("unresolved conflicts remain: " + "; ".join(map(str, unresolved[:3])))
    if sufficiency == "insufficient" and not str(decision.get("missing_information") or "").strip():
        errors.append("insufficient finalization requires missing_information")
    if abstention_gate:
        errors.extend(_unproven_abstention_errors(sufficiency, state))
    return errors, selected


def _unproven_abstention_errors(sufficiency: str, state: PlannerState) -> list[str]:
    """Abstention needs a retrieval that came back empty, not just a claim.

    Symmetric with `_missing_capability_errors`: there a count question may
    not finalize until a count-family call has actually succeeded; here an
    `insufficient` finalization may not stand until some successful retrieval
    has actually returned nothing. Saying what is missing is cheap — the 0729
    diagnosis found 17 questions where the planner said it and the evidence
    was sitting in the store the whole time.

    Only successful calls count, and an empty result IS success — same rule as
    the capability gate. A failed call proves nothing about what exists.

    Deliberately silent when nothing was searched: "at least one search before
    finalizing" is already `rules.check(..., is_final=True)`'s job, and
    duplicating it here would report one mistake as two.
    """
    if sufficiency != "insufficient":
        return []
    successful = [row for row in state.search_history if row.get("success")]
    if not successful:
        return []
    if any(int(row.get("returned_rows") or 0) == 0 for row in successful):
        return []
    return [
        "insufficient finalization requires a successful retrieval that "
        "returned no rows; every search so far returned rows"
    ]


def _dispatch_args(tool: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    mapping = _TOOL_DISPATCH.get(tool)
    if mapping is None:
        raise ToolError("unknown_tool", f"Unknown tool: {tool}", status=404)
    dispatch_name, renames = mapping
    return dispatch_name, {renames.get(k, k): v for k, v in args.items()}


def _run_tool(
    *, tools: MemoryTool, evidence: EvidenceStore, tool: str, args: dict[str, Any], step: int,
) -> dict[str, Any]:
    """Dispatch one tool call through the browser_*->memory.tool.* bridge
    (module docstring) and ingest its result. `MemoryTool.dispatch()` raises
    `ToolError` "so the agent loop can recover and retry instead of crashing
    the whole question" (that module's own docstring) — source never
    exercised this path (`toolbox.run()` returned an error STRING that
    `_parse_stdout` failed to parse, source :2254-2256 has no try/except at
    all), but honoring MemoryTool's documented contract here is a strict
    reliability improvement, not a behavior source ever relied on."""
    try:
        dispatch_name, dispatch_kwargs = _dispatch_args(tool, args)
        payload = tools.dispatch(dispatch_name, **dispatch_kwargs)
        error = ""
    except ToolError as exc:
        payload = None
        error = f"{exc.code}: {exc.message}"
    observation = evidence.ingest(tool, args, payload, step)
    if error:
        observation["error"] = error
    return observation


def _add_tools_seen(tools_seen: set[str], tool: str) -> None:
    tools_seen.add(tool)
    alias = _RULE_ALIASES.get(tool)
    if alias:
        tools_seen.add(alias)


def _add_successful_capabilities(capabilities: set[str], tool: str) -> None:
    _add_tools_seen(capabilities, tool)
    family = _TOOL_FAMILY_CAPABILITIES.get(tool)
    if family:
        capabilities.add(family)


def _missing_capability_families(state: PlannerState) -> list[tuple[str, str]]:
    """(family, planner-facing tool) pairs the classification still owes.

    Structured so `capability_autocall` can settle the debt by running the
    tool; `_missing_capability_errors` renders the same pairs as the bounce
    message — one source of truth for what is owed.
    """
    classification = state.question_classification
    if not classification:
        return []
    kind = classification["type"]
    required: list[tuple[str, str]] = []
    if kind in {"enumeration", "count", "sum"}:
        required.append(("count_family", "browser_count_evidence"))
    elif kind == "comparison":
        required.append(("timeline_family", "browser_timeline_events"))
        if classification.get("comparison_requires_count_or_sum"):
            required.append(("count_family", "browser_count_evidence"))
    return [
        (family, tool)
        for family, tool in required
        if family not in state.successful_tool_capabilities
    ]


def _missing_capability_errors(state: PlannerState) -> list[str]:
    if not state.question_classification:
        return ["question_classification is required before finalization"]
    return [
        f"missing required successful tool family: {family} ({tool})"
        for family, tool in _missing_capability_families(state)
    ]


def run_planner_loop(
    question: str,
    *,
    current_date: str,
    tools: MemoryTool,
    provider: LLMProvider,
    config: PlannerConfig,
    rules: tuple = rules_mod.DEFAULT_RULES,
) -> AutonomousAgentResult:
    """Ported from source `run_autonomous_agent` (:2105-2406). Credentials
    (`api_key`/`base_url`/`model`) do not appear in this signature — `provider`
    already encapsulates them (Task 4). `current_date` DOES appear explicitly
    (not shown in the task brief's abbreviated signature sketch) — every
    other Task 10 function threads it explicitly per T1's report ("current_date
    已作为显式参数贯穿...Task 10 落地 answer 层时对照迁移"); there is no
    credential to hide here, only per-question data.
    """
    evidence = EvidenceStore()
    state = PlannerState(objective=f"Gather sufficient evidence to answer: {question}")
    trace: list[dict[str, Any]] = []
    final_decision: dict[str, Any] | None = None
    termination = "max_steps"
    stalled: str | None = None
    guidance = AgentGuidance(config)
    system_prompt = PLANNER_SYSTEM_PROMPT
    if config.prompt_cache_layout:
        # Constant across every planner call of a run — the longest prefix
        # the provider's cache can hold.
        system_prompt = PLANNER_SYSTEM_PROMPT + "\n\nallowed_tools:\n" + json.dumps(
            {name: TOOL_GUIDE[name] for name in config.allowed_tools if name in TOOL_GUIDE},
            ensure_ascii=False, separators=(",", ":"),
        )
    id_aliases: dict[str, str] = {}
    id_reverse: dict[str, str] = {}
    context_offload = PlannerContextOffload() if config.context_offload else None

    # Bug #9 restoration (audit 0724): the loop consults the planner at EVERY
    # step including step 0, exactly like source. The earlier "skip the wasted
    # step-0 LLM call" optimization (_run_initial_calls before the loop) turned
    # out to discard more than the call: source :2185's apply_update ran BEFORE
    # :2215's forced-search override, so the step-0 state_update (objective
    # rewrite + open_questions) always survived into step 1 — and the planner
    # got 12 turns, not 11. The InitToolRule override now lives INSIDE the
    # tool_calls branch, mirroring source's `if not state.saw_search:` block.
    for step in range(config.max_steps):
        if config.short_evidence_ids:
            # First-seen assignment keeps every already-issued alias stable
            # within the question; only new records get new names.
            for rid in evidence.records:
                if rid not in id_aliases:
                    alias = f"e{len(id_aliases) + 1}"
                    id_aliases[rid] = alias
                    id_reverse[alias] = rid
        offload_telemetry: dict[str, Any] = {}
        user_message = _planner_user_message(
            question=question, current_date=current_date, state=state,
            evidence=evidence, step=step, max_steps=config.max_steps,
            allowed_tools=config.allowed_tools,
            time_window=config.time_window,
            cache_layout=config.prompt_cache_layout,
            id_aliases=id_aliases if config.short_evidence_ids else None,
            context_offload=context_offload,
            offload_telemetry=offload_telemetry,
        )
        started = time.time()
        content = provider.complete(
            messages=[{"role": "user", "content": user_message}],
            system=system_prompt,
            max_tokens=config.planner_max_tokens,
            temperature=config.temperature,
            usage_phase="answer_planner",
        )
        packet = _json_object(content)
        truncation_retried = False
        retry_cap = guidance.truncation_retry_max_tokens() if packet is None else None
        if retry_cap:
            content = provider.complete(
                messages=[{"role": "user", "content": user_message}],
                system=system_prompt,
                max_tokens=retry_cap,
                temperature=config.temperature,
                usage_phase="answer_planner",
            )
            packet = _json_object(content)
            truncation_retried = True
        if packet is not None and id_reverse:
            # Back to real ids BEFORE anything reads the packet: claims,
            # selections, tool args and the duplicate-call signature all
            # operate on store ids, and the trace must too — the aliases
            # exist only inside the serialized message and the raw
            # planner_output text.
            packet = _translate_ids(packet, id_reverse)
        elapsed_ms = round((time.time() - started) * 1000, 1)
        trace_row: dict[str, Any] = {
            "step": step,
            "planner_input_chars": len(user_message),
            **({"planner_input": user_message} if config.capture_planner_input else {}),
            "planner_elapsed_ms": elapsed_ms,
            "planner_output": content,
            "packet": packet,
            "observations": [],
            "context_offload": offload_telemetry,
            **({"truncation_retry": True} if truncation_retried else {}),
        }
        trace.append(trace_row)
        state.feedback = []
        if packet is None:
            state.feedback.append("Response was not a valid JSON protocol object.")
            continue

        state.apply_update(packet.get("state_update"), evidence)
        decision = packet.get("decision")
        if not isinstance(decision, dict):
            state.feedback.append("Missing decision object.")
            continue
        action = str(decision.get("action") or "")
        if action == "final":
            violations = rules_mod.check(
                rules, state.successful_tool_capabilities, [], is_final=True
            )
            if violations:
                details = [v.detail for v in violations]
                state.feedback.extend(details)
                trace_row["finalization_rule_violations"] = details
                continue
            auto_calls = guidance.capability_calls(
                _missing_capability_families(state), question
            )
            for call in auto_calls:
                # Same bookkeeping as a model-proposed call: the observation
                # reaches the trace, the history row reaches the model (moot
                # if the finalization stands, load-bearing if it still
                # bounces), and a SUCCESSFUL call settles the family debt so
                # `_finalization_errors` accepts the final as proposed. The
                # retrieved rows land in the evidence store, where the
                # full-pool reader sees them whether or not they were
                # "selected".
                observation = _run_tool(
                    tools=tools, evidence=evidence,
                    tool=call["tool"], args=call["args"], step=step,
                )
                observation["capability_autocall"] = True
                trace_row["observations"].append(observation)
                state.attempted_tools.add(call["tool"])
                guidance.note_observation(observation)
                state.search_history.append({
                    "step": step, "tool": call["tool"], "args": call["args"],
                    "signature": _call_signature(call["tool"], call["args"]),
                    "new_evidence": observation["new_evidence"],
                    "returned_rows": observation["returned_rows"],
                    "success": not bool(observation.get("error")),
                    "capability_autocall": True,
                })
                if not observation.get("error"):
                    _add_successful_capabilities(
                        state.successful_tool_capabilities, call["tool"]
                    )
            if auto_calls:
                trace_row["capability_autocall"] = [c["tool"] for c in auto_calls]
            errors, selected = _finalization_errors(
                decision=decision, state=state, evidence=evidence,
                max_selected_evidence=config.max_selected_evidence,
                abstention_gate=config.abstention_gate,
                claim_evidence_autofill=config.claim_evidence_autofill,
            )
            if errors:
                state.feedback.extend(errors)
                trace_row["finalization_rejected"] = errors
                continue
            state.selected_evidence_ids = selected
            final_decision = decision
            termination = "planner_final"
            break
        if action != "tool_calls":
            state.feedback.append("decision.action must be tool_calls or final")
            continue

        calls = _normalize_calls(decision)
        if not calls:
            state.feedback.append("tool_calls decision contained no calls")
            continue

        # Source :2215-2225: until a search has run, whatever the planner
        # proposed is REPLACED by the forced first search (its state_update
        # above already applied — only the calls are overridden). Driven by
        # the rules engine's InitToolRule instead of a hardcoded literal.
        initial = rules_mod.initial_calls(rules, question)
        if initial and not any(
            _RULE_ALIASES.get(c["tool"], c["tool"]) in state.successful_tool_capabilities
            or c["tool"] in state.successful_tool_capabilities
            for c in initial
        ):
            forced = []
            for call in initial:
                args = dict(call["args"])
                if call["tool"] == "search":
                    args.setdefault("top_k", config.fallback_top_k)
                    args.setdefault("include_context", True)
                    args.setdefault("include_multimodal", True)
                forced.append({"tool": "browser_search" if call["tool"] == "search" else call["tool"], "args": args})
            calls = forced
            trace_row["runtime_first_search_enforced"] = True

        step_new = 0
        for call in calls:
            tool = call["tool"]
            args = call["args"]
            if tool not in config.allowed_tools:
                state.feedback.append(f"tool not allowed: {tool}")
                continue
            signature = _call_signature(tool, args)
            prior_signatures = {
                str(row.get("signature") or "")
                for row in state.search_history
                if row.get("success", True)
            }
            if signature in prior_signatures:
                feedback = (
                    f"Skipped exact duplicate call: {tool} "
                    f"{json.dumps(args, ensure_ascii=False, separators=(',', ':'))}. "
                    "Use a materially different query, pagination offset, or tool."
                )
                state.feedback.append(feedback)
                guidance.note_duplicate_proposal()
                trace_row["observations"].append({
                    "step": step, "tool": tool, "args": args,
                    "skipped": "exact_duplicate", "new_evidence": 0,
                })
                continue
            observation = _run_tool(tools=tools, evidence=evidence, tool=tool, args=args, step=step)
            trace_row["observations"].append(observation)
            if (
                context_offload is not None
                and tool in {"browser_inspect", "browser_inspect_session"}
                and not observation.get("error")
            ):
                context_offload.queue_rehydration(
                    evidence.resolve(str(value))
                    for value in observation.get("seen_ids") or []
                )
            state.attempted_tools.add(tool)
            step_new += int(observation["new_evidence"])
            history_row = {
                "step": step, "tool": tool, "args": args,
                "signature": signature,
                "new_evidence": observation["new_evidence"],
                "returned_rows": observation["returned_rows"],
                "success": not bool(observation.get("error")),
            }
            # search_history is the only one of these two that reaches the
            # model: `PlannerState.compact()` projects it into the planner's
            # user message, while `trace_row["observations"]` is telemetry the
            # planner never sees. A roster left in the observation alone would
            # be a data structure nobody reads.
            if config.count_roster and observation.get("roster"):
                history_row["roster"] = observation["roster"]
            state.search_history.append(history_row)
            guidance.note_observation(observation)
            if not observation.get("error"):
                _add_successful_capabilities(
                    state.successful_tool_capabilities, tool
                )
                if tool == "browser_search":
                    state.saw_search = True
                if tool == "compute":
                    state.saw_compute = True

        state.consecutive_zero_novelty = (
            state.consecutive_zero_novelty + 1 if step_new == 0 else 0
        )
        if state.consecutive_zero_novelty >= 1 and state.claims:
            state.feedback.append(
                "The latest decision added no new evidence while supported claims "
                "already exist. Resolve or downgrade residual non-actionable uncertainty "
                "and finalize unless a materially different action can change the answer."
            )
        stalled = guidance.stall_verdict(state.consecutive_zero_novelty)
        if stalled:
            # Break at the END of the step: this step's retrievals are
            # kept, only the steps after it are forgone — the same cut
            # the offline counterfactual measured.
            trace_row["stall_stop"] = stalled
            break

    if final_decision is None:
        selected = []
        for claim in state.claims.values():
            if claim.status == "supported":
                selected.extend(claim.evidence_ids)
        if not selected:
            selected = [
                card["evidence_id"]
                for card in evidence.compact_cards(limit=12)
                if card.get("evidence_id")
            ]
        selected = list(dict.fromkeys(selected))
        material_open = any(
            row.get("material", True) for row in state.open_questions
        )
        unresolved_conflicts = any(
            not row.get("resolved") for row in state.conflicts
        )
        missing_capabilities = _missing_capability_errors(state)
        fallback_insufficient = (
            not selected
            or material_open
            or unresolved_conflicts
            or bool(missing_capabilities)
        )
        # Same gate as `_finalization_errors`, applied to the path that builds
        # its own decision. Running out of steps is not the same fact as the
        # store not having it, but both used to land in `insufficient`, and the
        # reader renders that as "I don't have that information" — false, and
        # measurably so, whenever every search did return rows.
        #
        # Two conditions are deliberately NOT overridable: `not selected`
        # (nothing was retrieved, so there is genuinely nothing to answer
        # from) and `missing_capabilities` (spec I1 AC5 — the max-step fallback
        # must not become a way around the count/timeline gate).
        if (
            config.abstention_gate
            and fallback_insufficient
            and selected
            and not missing_capabilities
            and _unproven_abstention_errors("insufficient", state)
        ):
            fallback_insufficient = False

        missing_parts = []
        if not selected:
            missing_parts.append("No source-backed evidence was retrieved.")
        if fallback_insufficient and material_open:
            missing_parts.append("Material questions remained unresolved.")
        if fallback_insufficient and unresolved_conflicts:
            missing_parts.append("Evidence conflicts remained unresolved.")
        missing_parts.extend(missing_capabilities)
        final_decision = {
            "sufficiency": "insufficient" if fallback_insufficient else "sufficient",
            "selected_evidence_ids": selected,
            "missing_information": " ".join(missing_parts),
        }
        state.selected_evidence_ids = selected
        termination = (
            "stall_stop_reader_fallback" if stalled else "max_steps_reader_fallback"
        )

    insufficient = final_decision.get("sufficiency") == "insufficient"
    planner_claims = [
        {"statement": claim.statement, "evidence_ids": claim.evidence_ids}
        for claim in state.claims.values()
        if claim.status == "supported" and claim.evidence_ids
    ]
    planner_conflicts = [
        {"description": row.get("description"), "resolution": row.get("resolution")}
        for row in state.conflicts
        if row.get("resolved") and row.get("resolution")
    ]

    final_state = state.compact(evidence, len(trace), config.max_steps)
    final_state["selected_evidence_ids"] = state.selected_evidence_ids
    final_state["termination"] = termination

    return AutonomousAgentResult(
        evidence=evidence,
        planner_trace=trace,
        usage=provider.usage_summary(),
        state=final_state,
        selected_evidence_ids=state.selected_evidence_ids,
        termination=termination,
        insufficient=insufficient,
        missing_information=str(final_decision.get("missing_information") or ""),
        planner_claims=planner_claims,
        planner_conflicts=planner_conflicts,
    )


__all__ = ["AutonomousAgentResult", "PlannerConfig", "DEFAULT_ALLOWED_TOOLS", "run_planner_loop"]
