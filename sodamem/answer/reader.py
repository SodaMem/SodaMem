"""`reader.answer()`: builds the official LongMemEval Chain-of-Note (CoN)
prompt from already-assembled evidence and calls the LLM to produce the
final answer text.

Split into:

- `ReaderConfig`: the 3 knobs that used to be read from the environment at
  call time (`BENCHMARK_READER_FULL_POOL`/`_DROP_EXTERNAL`/`_CURRENCY`) are
  explicit fields.
- `assemble_reader_context()`: the evidence-selection + organizer-glue half
  of `_reader_answer` (source :1707-1839) — full_pool/drop_external/
  currency filtering, then the query-plan-gated value_board/enumeration_
  sweep organizers. This IS "the build_context() caller" the task brief's
  Step 1 riding-along note refers to when it says the organizer-selection
  glue "moves out to build_context's caller": `sodamem.context.
  build_context()` assumes a single `mem.search()` call as its sole
  evidence source (that facade's own docstring, spec §6.2) — it does not
  fit an agentic loop's multi-step, tool-accumulated `EvidenceStore`. This
  function is the analogous assembly path for THAT evidence source,
  reusing the exact same organizer primitives
  (`sodamem.context.organizers.*`) `build_context()` would have delegated
  to. T8's own report predicted this ("这个组合属于 Task 10 — 唯一需要同时
  持有 LLM provider 与 context 两层的调用方 — 的接线范围").
- `answer()`: the prompt-build + LLM-call + reason-then-answer retry half
  (source :1902-2023).

Six fixes applied on port (task brief, Step 1 bullet 2):

1. Three env booleans -> `ReaderConfig.full_pool`/`.drop_external`/
   `.currency` (source :1716/:1740/:1757 `os.getenv(...)` reads).
2. Organizer-selection glue (source :1783-1839) -> `assemble_reader_context()`
   below (moved, not deleted). The four env gates that used to decide
   whether the organizer pipeline ran at all (`BENCHMARK_READER_QUERY_PLAN`/
   `_KU_VALUE_BOARD`/`_ENUM_SWEEP`/`_KU_VALUE_STRONG`) all defaulted ON in
   the S500-winning config (`_env_on(..., "1")` at every call site) — Task
   10's brief-given `ReaderConfig` carries no toggle fields for them, so
   this port makes the pipeline unconditionally on, matching the winning
   default rather than reintroducing four more config knobs the design did
   not ask for.
3. Title-routing / evidence-contract call (source :1832-1839,
   `_build_evidence_contract`/`_classify_evidence_contract`) -> DELETED, not
   ported. 0714 migration-map veto (see `sodamem.context.build_context`'s
   module docstring for the sibling deletion at the context layer, and
   Task 8's report: "zero 实际代码残留"). `sodamem.answer` never had a copy
   to begin with.
4. EvidenceBoard temporal-reorder block (source :1841-1889, `GRAPH_V2_BOARD`)
   -> DELETED, following Task 6's R2 verdict (production-dead retrieval-side
   time-constraint machinery). `sodamem.memory.retrieval.query_plan` DOES
   exist (Task 6 ported `QueryPlan`, live fusion-scoring
   code), but the specific symbols this block needed —
   `TimeConstraint`/`parse_time_constraint`/`apply_evidence_board`,
   the predecessor implementation — were the ones R2
   deleted (that module's own docstring: "gated by GRAPH_V2_BOARD,
   production default OFF"); there is nothing left to import for this block.
5. `bundle['answer_task']['current_date']` read (source :1904) -> DELETED.
   `current_date` is an explicit parameter throughout this module (T1's
   report predicted exactly this: "current_date 已作为显式参数贯穿...Task 10
   落地 answer 层时对照迁移"), matching `AnswerEvidenceBundle.answer_task`'s
   I4 removal (Task 1) — there is no bundle dict here to read it from;
   `_build_reader_prompt` below takes `current_date` directly.
6. Guidance text (source :1930-1967) -> imported from
   `sodamem.prompts.reader.READER_GUIDANCE` + its three addenda constants
   (Task 2's port), composed here with the exact same conditionals the
   source used, not re-typed as a literal string.

`_build_reader_prompt` below owns the frozen official CoN prompt assembly,
taking `current_date` as an explicit parameter rather than reading it out of
a bundle dict. Two related pieces are deliberately absent: a `key_derived`
bundle branch (that key was never populated on this call path, so it would be
dead code with no gate able to exercise it), and `BENCHMARK_OFFICIAL_READER`'s
raw-turn-only A/B toggle (an experimental arm that bypassed the
`[Evidence metadata: ...]` annotation, never a `ReaderConfig` field — this
reader always annotates, matching the production default).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from sodamem.context.organizers.enumeration_sweep import _valid_enumeration_plan, build_enumeration_sweep
from sodamem.context.organizers.query_plan import reader_query_plan
from sodamem.context.organizers.value_board import _valid_value_board_plan, build_value_board
from sodamem.context.store import EvidenceStore, _one_line
from sodamem.llm import LLMProvider
from sodamem.memory.retrieval.config import Degradation
from sodamem.prompts.reader import (
    READER_GUIDANCE,
    READER_GUIDANCE_ANSWER_BIAS_ADDENDUM,
    READER_GUIDANCE_PERSONALIZATION_ADDENDUM,
    READER_GUIDANCE_PLANNER_CLAIMS_ADDENDUM,
    READER_GUIDANCE_RUNTIME_RESOLVED_ADDENDUM,
)
from sodamem.prompts.reader_con import OFFICIAL_CON_PROMPT_TEMPLATE

# Read-time currency (BENCHMARK_READER_CURRENCY) is SKIPPED when the question
# BENCHMARK_READER_DROP_EXTERNAL is intent-gated: when the question asks about
# the ASSISTANT's own prior statements, external_info rows ARE the gold.
# Ported byte-for-byte from source :45-52.
_ASSISTANT_RECALL_RE = re.compile(
    r"\b(?:did\s+you|(?:what|which|when|how)\s+(?:\w+\s+){0,3}you\s+"
    r"(?:mention|recommend|suggest|say|said|tell|told|gave|provide|list|share|answer)|"
    r"you\s+(?:mention|recommend|suggest|say|said|tell|told|gave|provide|list|share|answer)(?:ed)?\b|"
    r"your\s+(?:suggestion|recommendation|advice|answer|list|response)s?\b|"
    r"(?:our|previous|last|earlier)\s+(?:conversation|chat|discussion)|you\s+previously)\b",
    re.I,
)


@dataclass(frozen=True)
class ReaderConfig:
    """Reader input-shaping. Defaults = the S500-validated winning
    configuration (FULL_POOL / DROP_EXTERNAL intent-gated all default-ON in
    the benchmark harness)."""
    full_pool: bool = True          # was BENCHMARK_READER_FULL_POOL
    drop_external: bool = True      # was BENCHMARK_READER_DROP_EXTERNAL (intent-gated)
    max_tokens: int = 3000
    temperature: float = 0.0


@dataclass(frozen=True)
class ReaderContext:
    """Evidence + advisory text already assembled for one question, ready
    for `answer()` to turn into a final answer. New code — NOT
    `sodamem.context.ContextBlock`: that dataclass's `.evidence` field is
    the generic `.compact()` projection `sodamem.context.cards.
    compact_cards()` renders for ANY `build_context()` caller (support/
    predicate/entities/date/source/role — no `support_text`/`session_time`/
    `source_role`/`occurred_start`/etc). `_build_reader_prompt`'s per-session
    grouping needs the RICHER `EvidenceStore.reader_rows()` projection this
    dataclass carries instead. Two different consumers, two different
    shapes — sharing one type would force one of them to carry fields it
    never uses."""
    key_evidence: list[dict[str, Any]]
    citations: list[str]
    insufficient: bool = False
    missing_information: str = ""
    planner_claims: list[dict[str, Any]] = field(default_factory=list)
    planner_conflicts: list[dict[str, Any]] = field(default_factory=list)
    semantic_advisories: list[str] = field(default_factory=list)
    degraded: list[Degradation] = field(default_factory=list)


@dataclass(frozen=True)
class Answer:
    """New code. Source's `_reader_answer` returned a bare `str` and smuggled
    its trace out through a module-level `threading.local()`
    (`_READER_TRACE_LOCAL`, source :28/:1891-1896/:2022) because the only
    other caller needing that trace (`run_autonomous_agent`) shared the same
    process/thread and read it back immediately after the call returned.
    That side channel existed to work around `_reader_answer`'s `-> str`
    signature — deleting `_single_shot_baseline` (the other competing writer
    of the same thread-local, task brief Step 1) removes its last reason to
    exist as anything other than a return value. `Answer.trace` replaces it:
    the caller gets the trace directly, no shared mutable global, no
    thread-locality assumption to audit."""
    text: str
    citations: list[str]
    trace: dict[str, Any]


def assemble_reader_context(
    evidence: EvidenceStore,
    selected_ids: list[str],
    question: str,
    *,
    current_date: str,
    provider: LLMProvider,
    config: ReaderConfig,
    insufficient: bool = False,
    missing_information: str = "",
    planner_claims: list[dict[str, Any]] | None = None,
    planner_conflicts: list[dict[str, Any]] | None = None,
) -> ReaderContext:
    """Ported from source :1707-1839 (fixes #1/#2 above)."""
    if config.full_pool:
        # BENCHMARK_READER_FULL_POOL (source :1716-1720): skip the agent's
        # evidence selection AND the lexical augment_reader_ids backfill,
        # feeding the reader the FULL retrieved pool with the agent's
        # selected ids first.
        reader_ids = list(dict.fromkeys(
            [evidence.resolve(v) for v in selected_ids] + list(evidence.records.keys())
        ))
    else:
        reader_ids = evidence.augment_reader_ids(selected_ids, question)

    # BENCHMARK_READER_DROP_EXTERNAL (source :1739-1747), intent-gated.
    drop_external = config.drop_external and not _ASSISTANT_RECALL_RE.search(question or "")
    if drop_external:
        kept = [
            rid for rid in reader_ids
            if (evidence.records[rid].raw.get("modality") if rid in evidence.records else None)
            != "external_info"
        ]
        reader_ids = kept or reader_ids

    # Organizer glue (source :1781-1831; fix #2 — moved here, see module
    # docstring). Always-on: the four env gates that used to guard it all
    # defaulted ON in the winning config.
    degraded: list[Degradation] = []
    semantic_advisories: list[str] = []
    plan_pool_ids = list(dict.fromkeys(reader_ids + list(evidence.records.keys())))
    if drop_external:
        kept_pool = [
            rid for rid in plan_pool_ids
            if rid in evidence.records and evidence.records[rid].raw.get("modality") != "external_info"
        ]
        plan_pool_ids = kept_pool or plan_pool_ids
    semantic_plan = reader_query_plan(
        question=question, current_date=current_date, provider=provider, degraded=degraded,
    )
    appended_ids: list[str] = []
    narrow_reader_ids: list[str] = []
    if _valid_value_board_plan(semantic_plan):
        block, ids, narrow_ids = build_value_board(
            plan=semantic_plan, evidence=evidence, reader_ids=plan_pool_ids,
            strong=True, question=question,
        )
        if block:
            semantic_advisories.append(block)
            appended_ids.extend(ids)
            narrow_reader_ids.extend(narrow_ids)
    if _valid_enumeration_plan(semantic_plan):
        block, ids = build_enumeration_sweep(
            plan=semantic_plan, evidence=evidence, reader_ids=plan_pool_ids,
        )
        if block:
            semantic_advisories.append(block)
            appended_ids.extend(ids)
    if narrow_reader_ids:
        # Front-load, don't replace. The ported source replaced `reader_ids`
        # with the board's top-4 outright — a regex-scored component erasing
        # the selection the planner spent its steps building. q193: planner's
        # final named all four instruments across 7 selected rows; the
        # replacement left the reader a top-4 of ownership-duration rows and
        # it answered 3, in 12 of 13 runs. The board keeps its ranking power
        # (its rows go first, and the advisory block still names its pick);
        # it loses the power to hide evidence.
        reader_ids = list(dict.fromkeys(narrow_reader_ids + reader_ids))
    elif appended_ids:
        reader_ids = list(dict.fromkeys(reader_ids + appended_ids))

    rows = evidence.reader_rows(reader_ids, question)
    citations = [str(r["evidence_id"]) for r in rows if r.get("evidence_id")]
    return ReaderContext(
        key_evidence=rows,
        citations=citations,
        insufficient=insufficient or not rows,
        missing_information=missing_information,
        planner_claims=list(planner_claims or []),
        planner_conflicts=list(planner_conflicts or []),
        semantic_advisories=semantic_advisories,
        degraded=degraded,
    )



_TURN_TAIL_NUMBER_RE = re.compile(r"(\d+)\s*$")

def _turn_sort_key(turn_id: Any) -> tuple[int, str]:
    """Numeric-suffix sort key so session_5_turn_10 sorts after ..._turn_2.
    Ported byte-for-byte from `longmemeval_official_answer.py:33-40`."""
    s = str(turn_id or "")
    m = _TURN_TAIL_NUMBER_RE.search(s)
    return (int(m.group(1)) if m else 1 << 30, s)


def _label_is_a_copy_of(label: Any, content: str) -> bool:
    """True when `label` is just the row's own text repeated.

    Compared after whitespace normalisation, because the copy retrieval
    carries has its newlines collapsed. The 40-character floor keeps the rule
    off short real labels — those save nothing and are worth their bytes.
    """
    norm = lambda s: re.sub(r"\s+", " ", str(s)).strip().lower()   # noqa: E731
    flat_label = norm(label)
    return len(flat_label) > 40 and flat_label[:200] in norm(content)


def _content_with_evidence_metadata(ev: dict[str, Any], content: str) -> str:
    """Ported byte-for-byte from `longmemeval_official_answer.py:131-158`,
    minus the `OFFICIAL_READER` raw-turn-only branch (see module docstring —
    not a config field this port exposes; always annotates)."""
    bits = []
    for label, key in (
        ("evidence_id", "evidence_id"),
        ("label", "label"),
        ("date", "occurred_start"),
        ("source_type", "source_type"),
    ):
        value = ev.get(key)
        if value in (None, "", [], {}):
            continue
        if key == "label" and _label_is_a_copy_of(value, content):
            # A raw turn has no `predicate_raw`, so `reader_evidence` falls
            # back to `row["label"]` — and retrieval's label for a raw turn IS
            # that turn's text, truncated. The row then ships its own first
            # ~400-500 characters twice. Measured on two live captures: 13.2%
            # of q001's reader prompt, 8.8% of q193's.
            #
            # Only a long verbatim copy is dropped. An extracted predicate
            # (`owns_korg_b1_digital_piano`) is a conclusion that does NOT
            # appear in the text — it is the reader's only cue to what the row
            # is asserting, and dropping it would cost information, not bytes.
            continue
        bits.append(f"{label}={value}")
    value = ev.get("value")
    unit = ev.get("unit")
    if value not in (None, ""):
        quantity = str(value)
        if unit not in (None, ""):
            quantity = f"{quantity} {unit}"
        bits.append(f"quantity={quantity}")
    if not bits:
        return content
    return "[Evidence metadata: {}]\n{}".format("; ".join(bits), content)


def _history_from_key_evidence(key_evidence: list[dict[str, Any]]) -> str:
    """Ported from `longmemeval_official_answer.py:43-100`
    (`_history_from_answer_bundle`), operating directly on a `key_evidence`
    list instead of `bundle.get("key_evidence")` — the rest of that
    function's `bundle` parameter was only ever the `answer_task`/
    `key_derived` lookups this port drops (module docstring)."""
    sessions: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for idx, ev in enumerate(key_evidence):
        content = (ev.get("support_text") or "").strip()
        if not content:
            continue
        sid = ev.get("source_session_id") or ev.get("source_span_id") or ev.get("fact_id") or f"evidence_{idx}"
        ev_time = ev.get("session_time") or ev.get("source_created_at") or ev.get("occurred_start")
        if sid not in sessions:
            order.append(sid)
            sessions[sid] = {"date": ev_time or "unknown", "messages": [], "seen": set()}
        elif sessions[sid]["date"] in ("", None, "unknown") and ev_time:
            sessions[sid]["date"] = ev_time
        role = ev.get("source_role") or "user"
        content = _content_with_evidence_metadata(ev, content)
        # Dedup on the SOURCE text: the [Evidence metadata: ...] prefix would
        # otherwise make same-text rows under different evidence ids
        # permanently distinct (0706 audit: 31% duplicate rows / 26%
        # duplicate chars in full-pool prompts).
        key = (role, re.sub(r"^\[Evidence metadata:[^\]]*\]\s*", "", content))
        if key in sessions[sid]["seen"]:
            continue
        sessions[sid]["seen"].add(key)
        sessions[sid]["messages"].append({
            "role": role, "content": content, "_turn": _turn_sort_key(ev.get("source_turn_id")),
        })

    history_parts = []
    for i, sid in enumerate(order, 1):
        session = sessions[sid]
        messages = [
            {"role": m["role"], "content": m["content"]}
            for m in sorted(session["messages"], key=lambda m: m["_turn"])
        ]
        if not messages:
            continue
        history_parts.append(
            "### Session {}:\nSession Time: {}\nSession Content:\n\n{}".format(
                i, session["date"], json.dumps(messages, ensure_ascii=False),
            )
        )
    return "\n\n".join(history_parts)


def _build_reader_prompt(*, question: str, current_date: str, key_evidence: list[dict[str, Any]]) -> str:
    """Ported from `longmemeval_official_answer.py:161-166`
    (`build_official_answer_prompt`). `current_date` is an explicit
    parameter (fix #5 above), not `bundle.get('answer_task', {}).get(
    'current_date')`."""
    history_string = _history_from_key_evidence(key_evidence)
    return OFFICIAL_CON_PROMPT_TEMPLATE.format(history_string, current_date or "unknown", question)


def answer(
    question: str,
    context: ReaderContext,
    *,
    current_date: str,
    provider: LLMProvider,
    config: ReaderConfig,
    answer_bias: bool = False,
    personalization_bias: bool = False,
) -> Answer:
    """Ported from source :1840-2023 (`_reader_answer`'s prompt-build +
    LLM-call half). reason-then-answer order is unchanged (answer-first was
    proven harmful — see `sodamem.prompts.reader` and the repository's
    `answer-first-inverts-con-harmful` memory note; this port does not
    revisit that finding)."""
    if not context.key_evidence:
        # Ported from source :1890-1900.
        detail = _one_line(context.missing_information, 800)
        text = (
            f"Not enough information in memory to answer this question. {detail}"
            if detail else "Not enough information in memory to answer this question."
        )
        return Answer(text=text, citations=[], trace={
            "selected_ids": context.citations, "evidence_rows": 0, "prompt_chars": 0, "attempts": [],
        })

    prompt = _build_reader_prompt(question=question, current_date=current_date, key_evidence=context.key_evidence)

    # Carry the planner's already-resolved conclusions so the reader
    # verifies them instead of re-deriving entity links and conflict
    # resolutions from scratch (source :1911-1929).
    planner_block = ""
    if context.planner_claims:
        claim_lines = "\n".join(
            f"- {_one_line(c.get('statement'), 300)} [evidence: {', '.join(c.get('evidence_ids') or [])}]"
            for c in context.planner_claims
        )
        planner_block += "\n\nplanner_supported_claims (pre-resolved):\n" + claim_lines
    if context.planner_conflicts:
        conflict_lines = "\n".join(
            f"- {_one_line(c.get('description'), 300)} -> {_one_line(c.get('resolution'), 300)}"
            for c in context.planner_conflicts
        )
        planner_block += "\n\nplanner_resolved_conflicts:\n" + conflict_lines
    if context.semantic_advisories:
        planner_block += "\n\n" + "\n\n".join(context.semantic_advisories)

    # Fix #6: guidance composed from sodamem.prompts.reader constants
    # (source :1930-1967 literal string -> Task 2's ported constants).
    guidance = READER_GUIDANCE
    if context.planner_claims or context.planner_conflicts:
        guidance += READER_GUIDANCE_PLANNER_CLAIMS_ADDENDUM
    if answer_bias:
        guidance += READER_GUIDANCE_ANSWER_BIAS_ADDENDUM
    if personalization_bias:
        guidance += READER_GUIDANCE_PERSONALIZATION_ADDENDUM
    if any(note.startswith("runtime_resolved_value") for note in context.semantic_advisories):
        guidance += READER_GUIDANCE_RUNTIME_RESOLVED_ADDENDUM

    full_prompt = prompt + planner_block + guidance
    trace: dict[str, Any] = {
        "selected_ids": context.citations,
        "evidence_rows": len(context.key_evidence),
        "prompt_chars": len(full_prompt),
        "semantic_advisories": len(context.semantic_advisories),
        "attempts": [],
    }

    def _ask(reader_prompt: str, budget: int) -> tuple[str, bool]:
        started = time.time()
        text = provider.complete(
            messages=[{"role": "user", "content": reader_prompt}],
            max_tokens=budget, temperature=config.temperature, usage_phase="answer_reader",
        )
        elapsed_ms = round((time.time() - started) * 1000, 1)
        # Truncation signal (source's finish_reason == "length" check,
        # :2014): LLMProvider.complete() returns bare text (no envelope), so
        # this reads the provider's own OPTIONAL `_last_finish_reason`
        # attribute (sodamem.llm.openai_compat.OpenAICompatibleProvider sets
        # it explicitly "so callers can re-chunk"; AnthropicProvider and test
        # doubles don't set it, so this degrades gracefully to "never retry"
        # rather than raising — the retry is a reliability nicety, not a
        # correctness requirement).
        truncated = getattr(provider, "_last_finish_reason", None) == "length"
        trace["attempts"].append({
            "budget": budget, "prompt_chars": len(reader_prompt), "elapsed_ms": elapsed_ms,
            "output_chars": len(text or ""), "truncated": truncated,
        })
        return (text or "").strip(), truncated

    text, truncated = _ask(full_prompt, config.max_tokens)
    # If the model ran out of budget, retry once asking it to keep its
    # reasoning short and finish with the complete answer — the answer is
    # never lost to truncation without inverting reason-then-answer order
    # (source :2011-2021).
    if truncated:
        retry_prompt = full_prompt + (
            "\n\nYour previous response was cut off before finishing. Answer again, keeping "
            "any reasoning brief, and make sure the complete final answer is included."
        )
        retry_text, _ = _ask(retry_prompt, config.max_tokens)
        if retry_text:
            text = retry_text

    return Answer(text=text or "Not enough information.", citations=context.citations, trace=trace)


__all__ = ["ReaderConfig", "ReaderContext", "Answer", "assemble_reader_context", "answer"]
