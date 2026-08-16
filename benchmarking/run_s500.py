"""Standalone S500 regression for SodaMem — no external rig involved.

Answer side: sodamem's public three-stage API (run_planner_loop ->
assemble_reader_context -> reader_answer), exactly the composition
SodaMem.answer() uses internally. One SodaMem.open per question store
(adopt-in-place on the frozen Hobs stores: verified additive-only —
store_meta table; data tables byte-identical).

Judge side: the official LongMemEval anscheck prompts (5 task templates +
abstention variant, category=="ABS" gates abstention), temperature 0,
max_tokens 10, label = "yes" in response — byte-matched to the anchor run's
judge (OfficialLongMemEvalJudge).

Anchor: s500_planner_slim2_0713 = 456/500 (91.2%), same frozen store, max_steps=12 / planner 1200 / reader 3000 / temp 0 /
fallback_top_k 10 / PLANNER_SLIM (now the only code path). Paired McNemar
(exact binomial on discordant pairs) against its per-eval_id labels.

Model: `deepseek-v4-flash` throughout, and pinned rather than trusted — the
alias this rig used to default to kept routing server-side to a different
model for days without a single error, so any run whose `served_models`
disagrees with the request aborts on the first question
(`_assert_model_not_substituted`). Thinking is explicitly disabled both sides
(v4-flash defaults ON, which can eat the judge's max_tokens=10 budget with
reasoning tokens). Runs against the 0713 anchor are CROSS-MODEL — McNemar
there is informative, not a same-model paired gate. This run also carries the bug #8-#11 fixes: forced step-0
search no longer fails, step-0 planner consult + state_update restored,
saw_search/saw_compute keys restored, CLI JS-number rendering emulated.

0724 stall fix: the driver now runs each question in its own subprocess
(answer_one_question.py) via `subprocess.run(timeout=QUESTION_TIMEOUT_S)`.
A real run stalled 6+ hours on a handful of questions (network-level read
hang past the HTTP client's own timeout — see answer_one_question.py's
docstring). subprocess.run's timeout is OS-level and unconditionally kills
the child; that's the only mechanism here that can't itself hang.

Usage:
  uv run --project ../SodaMem python run_s500.py --count 3        # smoke
  uv run --project ../SodaMem python run_s500.py                  # full 500
  uv run --project ../SodaMem python run_s500.py --category KU    # one category
Resume-safe: reruns skip eval_ids already in answers.jsonl (errors retry).

A/B arms are selected by env var, not by a flag, because every question runs
in its own subprocess (below) and the environment is what crosses that
boundary. Each arm needs its own --out, or the resume logic will read the
other arm's answers as already-done:

  SODAMEM_READER_ROLE_TIMELINE=0 ... --category KU --out results/ku_off
  SODAMEM_READER_ROLE_TIMELINE=1 ... --category KU --out results/ku_on

0728 MR arms (baseline = results/s500_postfix_0727, all flags off — reusable,
the new flags do not touch the default path):
  SODAMEM_READER_ANSWER_BIAS=1 ...                     --out results/s500_A
  SODAMEM_READER_ANSWER_BIAS=1 SODAMEM_READER_MEMBERSHIP_BIAS=1 ... --out results/s500_AB
"""
from __future__ import annotations

import argparse
import functools
import gzip
import json
import math
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from openai import OpenAI

from sodamem import SodaMem
from sodamem.answer import (
    PlannerConfig,
    ReaderConfig,
    assemble_reader_context,
    reader_answer,
    run_planner_loop,
)
from sodamem.llm.factory import create_provider
from sodamem.tools import MemoryTool

import paths as _paths


@functools.cache
def _sodamem_provenance() -> str:
    """Git HEAD of the `sodamem` package that is actually imported.

    Resolved from the installed module's own path, not from the working
    directory: the driver may be launched from anywhere, and an editable
    install can point at a checkout other than the one you are standing in.
    """
    import sodamem
    pkg = Path(sodamem.__file__).resolve().parent.parent
    try:
        head = subprocess.run(["git", "-C", str(pkg), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(["git", "-C", str(pkg), "status", "--porcelain"],
                               capture_output=True, text=True, timeout=10)
        if head.returncode != 0:
            return f"{pkg} (not a git checkout)"
        mark = "-dirty" if dirty.stdout.strip() else ""
        return f"{pkg} @ {head.stdout.strip()}{mark}"
    except Exception as exc:                                    # noqa: BLE001
        return f"{pkg} (provenance unavailable: {exc})"


@functools.cache
def store_root() -> Path:
    """The frozen store root, resolved on first use rather than at import.

    Import must stay side-effect free: answer_one_question.py imports this
    module to reuse `answer_one`, and an import-time raise would make a
    missing variable look like a broken module.
    """
    return _paths.store_root()

HERE = Path(__file__).parent
# The default is the model that ANSWERS, not the alias that routes to it.
# It used to default to a since-retired alias that silently resolved
# server-side to a different model — 7-9 questions apart on identical code and
# stores, with no error for days. A run is now aborted the
# moment what answered differs from what was asked for (see
# `_assert_model_not_substituted`), so a rerouted alias costs one question
# instead of a full 500.
BASE_URL = os.environ.get("SODAMEM_BENCH_BASE_URL", "https://api.deepseek.com")
MODEL = os.environ.get("SODAMEM_BENCH_MODEL", "deepseek-v4-flash")
#: The non-arm knobs. Hoisted out of `answer_one` so `_requested_arms()` and
#: the call site read the SAME values — a preflight that checks a parallel
#: copy of the list is a preflight that drifts, which is the failure it exists
#: to prevent. These are not experiment arms; they are the pinned
#: configuration every published run used.
MAX_STEPS = 12
PLANNER_MAX_TOKENS = 1200
READER_MAX_TOKENS = 3000
TEMPERATURE = 0.0
FALLBACK_TOP_K = 10

# A/B arm selector — read at import so the subprocess worker
# (answer_one_question.py, which imports this module) sees the same value the
# driver was launched with. Default 0 = the validated configuration.
ROLE_TIMELINE = os.environ.get("SODAMEM_READER_ROLE_TIMELINE", "0") == "1"
# 0728 MR ticket, two independently switchable arms (three-arm A/B needs them
# separable, or the result cannot be attributed to either):
#   A = answer_bias      — closes the "information is missing" escape hatch
#   B = membership_bias  — category naming is semantic, scope stays literal
ANSWER_BIAS = os.environ.get("SODAMEM_READER_ANSWER_BIAS", "0") == "1"
MEMBERSHIP_BIAS = os.environ.get("SODAMEM_READER_MEMBERSHIP_BIAS", "0") == "1"
# 0730 planner arm: abstention must be earned by a retrieval that came back
# empty (see sodamem/answer/loop.py::_unproven_abstention_errors).
ABSTENTION_GATE = os.environ.get("SODAMEM_ANSWER_ABSTENTION_GATE", "0") == "1"
# 0730 count arm: surface evidence_count's deduplicated, date-ordered roster
# in the planner payload (see sodamem/tools/__init__.py::_count_roster).
COUNT_ROSTER = os.environ.get("SODAMEM_ANSWER_COUNT_ROSTER", "1") == "1"
# 0730 preference arm: lead with personalization, hard-check anti-preferences
# (see sodamem/prompts/reader.py::READER_GUIDANCE_PERSONALIZATION_ADDENDUM).
PERSONALIZATION_BIAS = os.environ.get("SODAMEM_READER_PERSONALIZATION", "0") == "1"
# Diagnostic capture. Off by default because it multiplies the sidecar ~4.5x
# (the planner message is 1.4-12.5 KB per step and grows within a question as
# evidence cards accumulate); on, a trace records what the planner saw, not
# only what it decided — which is the missing half of any divergence analysis.
CAPTURE_INPUT = os.environ.get("SODAMEM_BENCH_CAPTURE_INPUT", "0") == "1"
# 0731 citation-integrity arm: add a claim's own evidence to the selection
# instead of rejecting the finalization and asking the model for it back.
CLAIM_AUTOFILL = os.environ.get("SODAMEM_ANSWER_CLAIM_AUTOFILL", "1") == "1"
# 0731 stall-stop arm: end the loop on the second exact-duplicate proposal,
# fourth zero-row retrieval, or two consecutive zero-novelty steps (see
# sodamem/answer/loop.py::PlannerConfig.stall_stop for the counterfactual).
STALL_STOP = os.environ.get("SODAMEM_ANSWER_STALL_STOP", "1") == "1"
# 0731 truncation-retry arm: an unparseable planner output retries once at
# double the token cap instead of wasting the step (96/99 parse failures in
# b5 were hard truncation at the 1200-token cap).
TRUNC_RETRY = os.environ.get("SODAMEM_ANSWER_TRUNC_RETRY", "1") == "1"
# 0731 prompt-cache-layout arm: same payload, ordered for prefix caching
# (allowed_tools -> system prompt, cards in first-seen order, volatile
# state last). Token count unchanged; buys billing and latency.
CACHE_LAYOUT = os.environ.get("SODAMEM_PROMPT_CACHE_LAYOUT", "1") == "1"
# 0731 short-ids arm: alias ev_fact:<uuid> ids to e1/e2/... at the
# serialization boundary (~10% of planner input); everything past the
# message boundary keeps real ids.
SHORT_IDS = os.environ.get("SODAMEM_SHORT_EVIDENCE_IDS", "1") == "1"
# 0731 c3 arm: settle a finalization's count/timeline-family debt by running
# the required call instead of bouncing (151 bounces on c2, all repaid on
# the next step anyway — see agent_guidance.AgentGuidance.capability_calls).
CAPABILITY_AUTOCALL = os.environ.get("SODAMEM_ANSWER_CAPABILITY_AUTOCALL", "1") == "1"
# Issue #7 validated Planner-only Hot/Warm/Folded projection. Promoted to the
# baseline; explicit 0 preserves the paired control and rollback path.
CONTEXT_OFFLOAD = os.environ.get("SODAMEM_ANSWER_CONTEXT_OFFLOAD", "1") == "1"
# 0731 c3 arm: tightened stall thresholds (c2-counterfactual: dup 2->1 and
# zero-rows 4->3 cut a further 11.8% of steps at 4 questions' evidence risk).
STALL_DUP_THRESHOLD = int(os.environ.get("SODAMEM_STALL_DUP_THRESHOLD", "1"))
STALL_ZERO_THRESHOLD = int(os.environ.get("SODAMEM_STALL_ZERO_THRESHOLD", "3"))
# --- official LongMemEval judge prompts (byte-copied from the anchor run's
# --- OfficialLongMemEvalJudge; task = question_type) -------------------------

_JUDGE_STD = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
_JUDGE_TR = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no. In addition, do not penalize off-by-one errors for the number of days. If the question asks for the number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., predicting 19 days when the answer is 18), the model's response is still correct. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
_JUDGE_KU = "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response contains some previous information along with an updated answer, the response should be considered as correct as long as the updated answer is the required answer.\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
_JUDGE_PREF = "I will give you a question, a rubric for desired personalized response, and a response from a model. Please answer yes if the response satisfies the desired response. Otherwise, answer no. The model does not need to reflect all the points in the rubric. The response is correct as long as it recalls and utilizes the user's personal information correctly.\n\nQuestion: {}\n\nRubric: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
_JUDGE_ABS = "I will give you an unanswerable question, an explanation, and a response from a model. Please answer yes if the model correctly identifies the question as unanswerable. The model could say that the information is incomplete, or some other information is given but the asked information is not.\n\nQuestion: {}\n\nExplanation: {}\n\nModel Response: {}\n\nDoes the model correctly identify the question as unanswerable? Answer yes or no only."


def judge_prompt(task: str, question: str, answer: str, response: str, abstention: bool) -> str:
    if abstention:
        return _JUDGE_ABS.format(question, answer, response)
    if task in ("single-session-user", "single-session-assistant", "multi-session"):
        return _JUDGE_STD.format(question, answer, response)
    if task == "temporal-reasoning":
        return _JUDGE_TR.format(question, answer, response)
    if task == "knowledge-update":
        return _JUDGE_KU.format(question, answer, response)
    if task == "single-session-preference":
        return _JUDGE_PREF.format(question, answer, response)
    raise NotImplementedError(task)


def run_judge(client: OpenAI, item: dict, hypothesis: str) -> dict:
    prompt = judge_prompt(
        item["question_type"], item["question"], str(item["answer"]),
        hypothesis, item["category"] == "ABS",
    )
    last_err = None
    for attempt in range(3):
        try:
            completion = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                n=1, temperature=0, max_tokens=10,
                extra_body={"thinking": {"type": "disabled"}},
            )
            text = (completion.choices[0].message.content or "").strip()
            return {"model": MODEL, "label": "yes" in text.lower(), "response": text}
        except Exception as e:  # rate limit / transient — simple expo retry
            last_err = e
            time.sleep(2 ** attempt)
    return {"model": MODEL, "label": False, "response": f"JUDGE_ERROR: {last_err}"}


# --- forward/backward compatibility shim -------------------------------------
# This driver drives read-side arm flags (role_timeline, membership_bias) that
# live on unpushed branches. Against a SodaMem that predates them, passing the
# kwarg is a hard TypeError before a single question runs. Filter to what the
# target build actually accepts, so one driver works against both.
#
# NOT a silent downgrade: every dropped flag is printed once at startup and
# recorded in each answer row, so a run can never be mistaken for an arm it did
# not actually execute. When the build DOES support a flag, nothing changes —
# the value is passed exactly as before.
_DROPPED_FLAGS: list[str] = []


def _accepts_keyword(target, name: str) -> bool:
    """Whether ``target`` can receive a named experimental arm flag."""
    import inspect
    try:
        params = inspect.signature(target).parameters
    except (TypeError, ValueError):
        return True
    return name in params or any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in params.values()
    )


def _supported(target, **kwargs):
    """Keep accepted kwargs; report only unsupported arms requested ON.

    Older checkouts legitimately lack experimental Reader/Planner fields.
    Dropping an explicitly disabled boolean ``False`` arm preserves the
    requested behavior and must not invalidate a run. Non-boolean falsy values
    can be semantic (for example a zero stall threshold), so they remain
    fail-closed through ``unsupported_flags``.
    """
    import inspect
    try:
        params = inspect.signature(target).parameters
    except (TypeError, ValueError):
        return kwargs
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return kwargs
    keep = {}
    for k, v in kwargs.items():
        if k in params:
            keep[k] = v
        # Only an explicit boolean OFF (or a genuinely absent None) is a
        # harmless unsupported arm. ``0`` is not interchangeable with False:
        # threshold=0 is an active semantic request and must fail closed.
        elif v is not False and v is not None and k not in _DROPPED_FLAGS:
            _DROPPED_FLAGS.append(k)
    return keep


#: Every arm flag this driver can request, and the callable that has to
#: accept it. Checked ONCE before the first LLM call — see `_preflight_arms`.
def _all_arms() -> list[tuple[str, object, object]]:
    """(name, target callable, current value) for EVERY kwarg this driver
    forwards through `_supported()`.

    One registry, read by two things that must not disagree: `_requested_arms`
    filters it down to what preflight has to verify, and a test walks the real
    `_supported()` call sites and fails if any kwarg is missing from here. The
    first version of this list was hand-maintained alongside the call sites
    and was already six names short on the day it was written."""
    return [
        # The pinned configuration. Not arms, but dropped by the same code
        # path if the installed build lacks them — and a run that silently
        # falls back to a different step budget is exactly as wrong as one
        # that silently ignores an arm.
        ("max_steps", PlannerConfig, MAX_STEPS),
        ("planner_max_tokens", PlannerConfig, PLANNER_MAX_TOKENS),
        ("temperature", PlannerConfig, TEMPERATURE),
        ("fallback_top_k", PlannerConfig, FALLBACK_TOP_K),
        ("max_tokens", ReaderConfig, READER_MAX_TOKENS),
        ("temperature", ReaderConfig, TEMPERATURE),
        # The arms.
        ("abstention_gate", PlannerConfig, ABSTENTION_GATE),
        ("count_roster", PlannerConfig, COUNT_ROSTER),
        ("capture_planner_input", PlannerConfig, CAPTURE_INPUT),
        ("claim_evidence_autofill", PlannerConfig, CLAIM_AUTOFILL),
        ("stall_stop", PlannerConfig, STALL_STOP),
        ("truncation_retry", PlannerConfig, TRUNC_RETRY),
        ("prompt_cache_layout", PlannerConfig, CACHE_LAYOUT),
        ("short_evidence_ids", PlannerConfig, SHORT_IDS),
        ("capability_autocall", PlannerConfig, CAPABILITY_AUTOCALL),
        ("context_offload", PlannerConfig, CONTEXT_OFFLOAD),
        ("stall_dup_threshold", PlannerConfig, STALL_DUP_THRESHOLD),
        ("stall_zero_rows_threshold", PlannerConfig, STALL_ZERO_THRESHOLD),
        ("role_timeline", ReaderConfig, ROLE_TIMELINE),
        ("answer_bias", reader_answer, ANSWER_BIAS),
        ("membership_bias", reader_answer, MEMBERSHIP_BIAS),
        ("personalization_bias", reader_answer, PERSONALIZATION_BIAS),
    ]


def _requested_arms() -> list[tuple[str, object, object]]:
    """The subset of `_all_arms()` that is actually asking for something.

    An explicitly-OFF boolean the installed build has never heard of asks for
    the behavior it already has, so dropping it changes nothing. A non-boolean
    (a threshold, a token budget) always counts: `0` is an active request and
    is not interchangeable with False."""
    return [(n, t, v) for n, t, v in _all_arms() if v is not False and v is not None]


def _preflight_arms() -> None:
    """Refuse to start if the installed `sodamem` cannot honour a requested arm.

    This used to be a per-row `unsupported_flags` field: the run completed,
    produced a plausible score, and had silently ignored the flag you set —
    discoverable only if you remembered to read that field before treating two
    directories as a pair. It happened. Checking the signatures costs
    microseconds and happens before the first billed token, so the failure is
    now a message at launch instead of a wrong number hours later."""
    missing = [name for name, target, _ in _requested_arms()
               if not _accepts_keyword(target, name)]
    if missing:
        raise SystemExit(
            "the installed sodamem does not accept these requested arms: "
            + ", ".join(sorted(missing))
            + f"\n  sodamem: {_sodamem_provenance()}"
            + "\n  Unset them, or install a build that has them. Running would "
              "produce a score for a configuration you did not ask for."
        )


def _assert_model_not_substituted(row: dict) -> None:
    """Abort on the FIRST answer that came from a model we did not request.

    `served_models` was reported in the summary, which is the wrong end of the
    run: a rerouted alias is only visible after 500 questions have been paid
    for. A score from a model you did not pin is not attributable to anything,
    so there is nothing worth finishing."""
    served = [m for m in (row.get("served_models") or []) if m]
    if served and served != [MODEL]:
        raise SystemExit(
            f"model substitution on {row.get('eval_id')}: requested {MODEL!r}, "
            f"served {served!r}. The score would not be attributable to the "
            f"requested model.\n  Pin SODAMEM_BENCH_MODEL to what actually "
            f"answers, or use a base URL that does not reroute."
        )


def _run_arm_status(rows: list[dict], *, requested: bool) -> dict:
    """Aggregate child-process support/effective stamps, failing closed."""
    unsupported = sorted({
        str(flag)
        for row in rows
        for flag in (row.get("unsupported_flags") or [])
    } | set(_DROPPED_FLAGS))
    effective_values = sorted({
        bool(row.get("context_offload", False)) for row in rows
    })
    if not effective_values:
        effective_values = [requested and _accepts_keyword(PlannerConfig, "context_offload")]
    effective = effective_values[0] if len(effective_values) == 1 else None
    valid = (
        not unsupported
        and len(effective_values) == 1
        and effective == requested
    )
    return {
        "context_offload_requested": requested,
        "context_offload": effective,
        "context_offload_effective_values": effective_values,
        "unsupported_flags": unsupported,
        "arm_configuration_valid": valid,
    }


class IncompleteRun(RuntimeError):
    """An arm that did not finish, asked for as if it had."""


def load_arm(arm_dir, *, allow_incomplete: bool = False) -> list[dict]:
    """Answered rows of one arm, or a refusal if the run did not finish.

    Written after the flag it checks was ignored. `b6_traced_0731` stopped at
    221/500 on an exhausted API balance; `summary.json` recorded
    `incomplete: true`, and an analysis then read `correct` out of the file,
    divided by 500, and produced a mean of 404.3 across six runs. Writing the
    guard was not enough — the read path needs it too, because the person
    reading is the one who has already forgotten.

    A partial run is still analysable (a 221-question subset says real things
    about those 221 questions); it just cannot be picked up by accident.
    """
    arm_dir = Path(arm_dir)
    done, errored = load_previous_answers(arm_dir / "answers.jsonl")
    rows = [r for r in done.values() if r.get("judge")]
    total = None
    summary = arm_dir / "summary.json"
    if summary.exists():
        try:
            total = json.load(open(summary)).get("n_questions")
        except Exception:                                       # noqa: BLE001
            total = None
    if total and len(rows) < total and not allow_incomplete:
        raise IncompleteRun(
            f"{arm_dir.name}: {len(rows)}/{total} answered, {len(errored)} errored. "
            f"Pass allow_incomplete=True to analyse the subset — its score is "
            f"not comparable to a full run."
        )
    return rows


def load_previous_answers(path) -> tuple[dict, dict]:
    """Split a prior run's answers.jsonl into (resumable, failed).

    Two dicts rather than one filtered pass: only clean rows may resume (a
    failed question must retry), but the failures still have to be counted, or
    the summary reports a run that lost a third of its questions as clean.
    That is exactly what happened when the machine slept mid-run on 0730 —
    181 of 500 came back as connection errors and `errors` read 0, because it
    was derived from the already-filtered dict.

    A truncated final line is skipped and everything before it kept: a killed
    process leaves one half-written row, and the work above it is still good.
    """
    done: dict[str, dict] = {}
    errored: dict[str, dict] = {}
    path = Path(path)
    if not path.exists():
        return done, errored
    for line in open(path):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        eval_id = row.get("eval_id")
        if not eval_id:
            continue
        (errored if row.get("error") else done)[eval_id] = row
    return done, errored


# Query keys in priority order. Different tools name their subject
# differently; a diagnosis reading "what did it look for" needs whichever one
# is present, and an empty string where a call has no subject at all
# (inspect_session takes an id, not a query) rather than a missing key.
_QUERY_KEYS = ("query", "text", "term", "entity", "q")


def compact_trace(planner_trace) -> list[dict]:
    """(step, tool, query) for every call the planner made, in order.

    The raw trace carries each observation's evidence ids and, with the count
    arm on, its roster — megabytes per question. Keeping that for every
    question of every arm is not an option; keeping the queries is, and the
    queries are the part a diagnosis needs. `tools_used`/`planner_steps` (the
    only things kept before this) answer "did the count family fire" and
    nothing about what was actually searched for.
    """
    out: list[dict] = []
    for row in planner_trace or []:
        step = row.get("step")
        for obs in (row.get("observations") or []):
            tool = obs.get("tool")
            if not tool:
                continue
            args = obs.get("args") or {}
            query = next((str(args[k]) for k in _QUERY_KEYS if args.get(k)), "")
            out.append({"step": step, "tool": str(tool), "query": query})
    return out


def prune_raw_trace(planner_trace) -> list[dict]:
    """The raw trace, minus the one field that is stored twice.

    `trace_row` carries `planner_output` (the model's raw text) and `packet`
    (that same text, parsed). Keeping both doubles the file for nothing —
    except when the parse failed, where the raw text is the only record of
    what the model actually said, and that is exactly the case worth reading.

    Everything else stays: observations with their args and returned_rows,
    `finalization_rejected`, `finalization_rule_violations`, timings. Those
    are the fields a diagnosis is made of.
    """
    out: list[dict] = []
    for row in planner_trace or []:
        kept = dict(row)
        if kept.get("packet") is not None:
            kept.pop("planner_output", None)
        out.append(kept)
    return out


def answer_one(item: dict, api_key: str) -> dict:
    t0 = time.time()
    provider = create_provider(provider="openai", model=MODEL,
                               api_key=api_key, base_url=BASE_URL)
    # deepseek-v4-flash DEFAULTS to thinking, and reasoning tokens can eat the
    # whole max_tokens budget on terse/JSON outputs.
    # User directive: thinking OFF for this run. Explicit, not left to the
    # server default — factory.create_provider() has no thinking knob (that's
    # `create_provider()` has no thinking knob, so set the tri-state flag
    # directly on the provider.
    provider._thinking = False
    # `with`, not a bare open: chroma's PersistentClient holds ~8 FDs plus a
    # started rust System per store, and this rig opens one store PER QUESTION.
    # Measured on the frozen Hobs corpus (500 sequential opens, no close):
    # FDs 466@50 -> 3306@500, and from store #370 onward every open FAILS
    # INSIDE _init_chroma ("Resource temporarily unavailable", os error 35).
    # That failure does not raise — Store catches it, sets chroma_available
    # False, and hands back a store whose vector route is simply gone. The
    # question then answers against an empty retrieval and looks like an
    # ordinary miss. That is the mechanism behind the historical q463-q500
    # "0/38 silent empty retrieval" tail. With close: FDs flat at 66,
    # chroma 500/500.
    with SodaMem.open(store_root() / item["user_id"]) as mem:
        # Recorded per question, not just logged: this is the one bit that
        # distinguishes "this user genuinely has no matching evidence" from
        # "this process ran out of resources and the index silently vanished".
        # A run whose tail is all False is a broken RUN, not a low score.
        chroma_available = mem.store.chroma_available
        tool = MemoryTool(mem, user_id=item["user_id"])
        planner_config = PlannerConfig(**_supported(
            PlannerConfig, max_steps=MAX_STEPS,
            planner_max_tokens=PLANNER_MAX_TOKENS,
            temperature=TEMPERATURE, fallback_top_k=FALLBACK_TOP_K,
            abstention_gate=ABSTENTION_GATE, count_roster=COUNT_ROSTER,
            capture_planner_input=CAPTURE_INPUT,
            claim_evidence_autofill=CLAIM_AUTOFILL,
            stall_stop=STALL_STOP, truncation_retry=TRUNC_RETRY,
            prompt_cache_layout=CACHE_LAYOUT, short_evidence_ids=SHORT_IDS,
            capability_autocall=CAPABILITY_AUTOCALL,
            context_offload=CONTEXT_OFFLOAD,
            stall_dup_threshold=STALL_DUP_THRESHOLD,
            stall_zero_rows_threshold=STALL_ZERO_THRESHOLD))
        reader_config = ReaderConfig(**_supported(
            ReaderConfig, max_tokens=READER_MAX_TOKENS,
            temperature=TEMPERATURE, role_timeline=ROLE_TIMELINE))
        loop_result = run_planner_loop(
            item["question"], current_date=item["question_date"], tools=tool,
            provider=provider, config=planner_config,
        )
        context = assemble_reader_context(
            loop_result.evidence, loop_result.selected_evidence_ids,
            item["question"], current_date=item["question_date"], provider=provider,
            config=reader_config, insufficient=loop_result.insufficient,
            missing_information=loop_result.missing_information,
            planner_claims=loop_result.planner_claims,
            planner_conflicts=loop_result.planner_conflicts,
        )
        result = reader_answer(item["question"], context,
                               current_date=item["question_date"],
                               provider=provider, config=reader_config,
                               **_supported(reader_answer,
                                            answer_bias=ANSWER_BIAS,
                                            membership_bias=MEMBERSHIP_BIAS,
                                            personalization_bias=PERSONALIZATION_BIAS))
    usage = provider.usage_summary()
    # Mechanism check for the Issue #1 MR gate, independent of the score:
    # the old run's symptom was that the count/timeline families were called
    # ZERO times across all 500 questions, so the planner answered
    # enumeration/count/comparison questions off similarity top-k. If these
    # stay empty, a flat MR score means "the gate never fired", not "the gate
    # didn't help" — two completely different follow-ups.
    tools_used = sorted({
        str(obs.get("tool")) for row in loop_result.planner_trace
        for obs in (row.get("observations") or []) if obs.get("tool")
    })
    return {
        "hypothesis": result.text,
        "termination": loop_result.termination,
        "planner_steps": len(loop_result.planner_trace),
        "trace": compact_trace(loop_result.planner_trace),
        # Full trace rides in a sidecar, not here: every analysis so far reads
        # answers.jsonl end to end, and an order-of-magnitude bigger file
        # would slow down work that never touches the trace.
        "_raw_trace": prune_raw_trace(loop_result.planner_trace),
        "selected_evidence": len(loop_result.selected_evidence_ids),
        "chroma_available": chroma_available,
        "unsupported_flags": list(_DROPPED_FLAGS),
        "tools_used": tools_used,
        # Stamped per row so an answers.jsonl can never be misfiled as the
        # wrong arm — the A/B is worthless if the two files can be confused.
        "role_timeline": ROLE_TIMELINE,
        "answer_bias": ANSWER_BIAS,
        "membership_bias": MEMBERSHIP_BIAS,
        "abstention_gate": ABSTENTION_GATE,
        "count_roster": COUNT_ROSTER,
        "personalization_bias": PERSONALIZATION_BIAS,
        "capture_planner_input": CAPTURE_INPUT,
        "claim_evidence_autofill": CLAIM_AUTOFILL,
        "stall_stop": STALL_STOP,
        "truncation_retry": TRUNC_RETRY,
        "prompt_cache_layout": CACHE_LAYOUT,
        "short_evidence_ids": SHORT_IDS,
        "capability_autocall": CAPABILITY_AUTOCALL,
        # Effective, not requested: an older imported PlannerConfig cannot
        # stamp an unsupported treatment as if it ran.
        "context_offload": bool(getattr(planner_config, "context_offload", False)),
        "context_offload_requested": CONTEXT_OFFLOAD,
        "stall_dup_threshold": STALL_DUP_THRESHOLD,
        "stall_zero_rows_threshold": STALL_ZERO_THRESHOLD,
        "usage_totals": {k: usage.get(k) for k in
                         ("calls", "prompt_tokens", "completion_tokens",
                          "total_tokens", "cached_input_tokens")},
        # What actually answered, per question. The requested name proves
        # nothing, which is why the driver aborts as soon as this disagrees
        # with it rather than reporting the mismatch in the summary.
        "served_models": usage.get("served_models") or [],
        "elapsed_s": round(time.time() - t0, 1),
    }


def load_only_ids(path: str) -> set[str]:
    """eval_ids from a JSON list or a one-per-line text file. Raises on an
    empty result: a typo'd path silently running all 500 questions is the
    same failure family as the errors:0 bug — the guard belongs at read time."""
    text = Path(path).read_text().strip()
    try:
        ids = json.loads(text)
        if not isinstance(ids, list):
            raise ValueError("JSON --only file must hold a list")
    except json.JSONDecodeError:
        ids = [line.strip() for line in text.splitlines()]
    keep = {str(i) for i in ids if str(i).strip()}
    if not keep:
        raise SystemExit(f"--only {path}: no eval_ids found")
    return keep


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact binomial on discordant pairs (b=anchor-only-correct,
    c=new-only-correct).

    `n == 0` returns 1.0, and that is the right answer to the only question
    this function can see: no discordant pairs is no evidence of a difference.
    It cannot see whether there were any PAIRS at all — an empty comparison
    reaches here as b=c=0 and comes back looking like a clean null result. That
    distinction lives at the call site, so `_paired_stats()` refuses to call
    this when `paired_n == 0`."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def _load_anchor(path: Path | None) -> tuple[dict[str, bool], dict | None]:
    """(labels, provenance-or-None) for the paired McNemar comparison.

    The summary's `anchor` field used to be a string literal, which meant it
    described whichever run the literal was typed for and nothing else: a run
    with NO anchor file on disk still announced it had been compared against
    entitysubj, next to `paired_n: 0`. The hand-copied score inside it had also
    already drifted from the anchor file's own self-description — every
    transcribed provenance eventually does, which is the reason this is derived
    instead of written down.

    Everything below is read off the file that was actually opened. `None`
    means no file was, and that is the only way `anchor` becomes null.
    """
    if path is None:
        return {}, None
    raw = json.load(open(path))
    # Two shapes in the wild: the legacy anchor is a bare {eval_id: bool}, the
    # consensus anchor wraps its labels under "labels" beside provenance
    # fields. Reading the wrapper as labels would match zero eval_ids and
    # degrade SILENTLY to "no paired comparison" — a run that looks fine and
    # reports nothing.
    labels = raw["labels"] if isinstance(raw.get("labels"), dict) else raw
    labels = {k: v for k, v in labels.items() if isinstance(v, bool)}
    return labels, {
        "file": path.name,
        "path": str(path.resolve()),
        "n_labels": len(labels),
        # The anchor file's own `_what`, passed through verbatim. Summarising
        # or re-wording it here would recreate the literal this replaced: a
        # second copy of the file's story, free to disagree with it. Legacy
        # anchors carry no metadata, so this is None for them.
        "note": raw.get("_what"),
    }


def _paired_stats(anchor: dict[str, bool], labels: dict[str, bool]) -> dict:
    """The summary's four paired-comparison fields, from this run's labels.

    `paired_n == 0` yields `mcnemar_exact_p: None`, not 1.0. The pair
    (`paired_n: 0`, `mcnemar_exact_p: 1.0`) is the worst reading in the file —
    neither field is wrong alone, and together they say "we tested and found no
    difference" about a comparison that never happened. The gate is `paired_n`
    rather than `b + c` precisely because `b + c == 0` WITH pairs is a real
    result and has to keep its 1.0.
    """
    paired = {e: (anchor[e], labels[e]) for e in labels if e in anchor}
    b = sum(1 for a, n in paired.values() if a and not n)   # anchor-only correct
    c = sum(1 for a, n in paired.values() if n and not a)   # new-only correct
    return {
        "paired_n": len(paired),
        "anchor_only_correct": b,
        "new_only_correct": c,
        "mcnemar_exact_p": round(mcnemar_exact(b, c), 4) if paired else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=0, help="limit (0 = all)")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--out", default=str(HERE / "results" / "s500_sodamem_regression_0724_v4flash"))
    ap.add_argument("--question-timeout", type=int, default=600,
                    help="hard per-question wall-clock cap in seconds (subprocess-killed on expiry)")
    ap.add_argument("--category", default="",
                    help="restrict to one LongMemEval category (IE/TR/MR/KU/ABS)")
    ap.add_argument("--only", default="",
                    help="path to a file of eval_ids (JSON list or one per "
                         "line); run only those questions. Built for the "
                         "stable-set methodology: the 19 always-wrong + a "
                         "stable-right regression sample resolve a targeted "
                         "arm at 1/13 the cost of a full 500, where the "
                         "±8-12 noise floor would swallow the effect.")
    args = ap.parse_args()

    # Presence check only — the key is used in the subprocess worker, which
    # inherits the environment. Failing here beats failing 500 times later.
    os.environ["DEEPSEEK_API_KEY"]
    # Same principle, one step further: a requested arm the installed build
    # cannot accept must stop the run before the first billed token, not show
    # up as a field somebody was supposed to read afterwards.
    _preflight_arms()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    answers_path = out_dir / "answers.jsonl"
    # gzip: these rows are JSON with heavy repetition across steps (each
    # planner message re-includes the accumulated evidence cards), so the
    # compressor removes most of what capture adds back.
    raw_trace_path = out_dir / "raw_traces.jsonl.gz"

    questions = json.load(open(_paths.questions_slim()))
    questions = [q for q in questions
                 if (store_root() / q["user_id"] / "memory.db").exists()]
    if args.category:
        questions = [q for q in questions
                     if (q.get("category") or q.get("question_type")) == args.category]
    if args.only:
        keep = load_only_ids(args.only)
        questions = [q for q in questions if q["eval_id"] in keep]
    anchor, anchor_provenance = _load_anchor(_paths.anchor_labels())
    context_offload_supported = _accepts_keyword(PlannerConfig, "context_offload")
    context_offload_effective = CONTEXT_OFFLOAD and context_offload_supported
    print(f"ARM role_timeline={ROLE_TIMELINE} answer_bias={ANSWER_BIAS} "
          f"membership_bias={MEMBERSHIP_BIAS} abstention_gate={ABSTENTION_GATE} "
          f"count_roster={COUNT_ROSTER} "
          f"personalization={PERSONALIZATION_BIAS} stall_stop={STALL_STOP} "
          f"truncation_retry={TRUNC_RETRY} cache_layout={CACHE_LAYOUT} "
          f"short_ids={SHORT_IDS} autocall={CAPABILITY_AUTOCALL} "
          f"context_offload_requested={CONTEXT_OFFLOAD} "
          f"context_offload_effective={context_offload_effective} "
          f"context_offload_supported={context_offload_supported} "
          f"stall_thresholds=dup{STALL_DUP_THRESHOLD}/zero{STALL_ZERO_THRESHOLD}  "
          f"category={args.category or 'ALL'}  "
          f"out={args.out}", flush=True)

    done, previously_errored = load_previous_answers(answers_path)
    todo = [q for q in questions if q["eval_id"] not in done]
    if args.count:
        todo = todo[: args.count]
    print(f"total={len(questions)} done={len(done)} todo={len(todo)}", flush=True)

    lock = threading.Lock()
    worker_script = str(HERE / "answer_one_question.py")

    def work(item: dict) -> dict:
        eval_id = item["eval_id"]
        row = dict(item)
        try:
            proc = subprocess.run(
                [sys.executable, worker_script, "--eval-id", eval_id],
                capture_output=True, text=True, timeout=args.question_timeout,
            )
            # answer_one_question.py's contract: exactly one JSON line on stdout.
            stdout_line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
            if not stdout_line:
                row["error"] = f"empty subprocess stdout (rc={proc.returncode}): {proc.stderr[-300:]}"
            else:
                row = json.loads(stdout_line)
        except subprocess.TimeoutExpired:
            # subprocess.run already killed the child (and its process group is
            # NOT separately created here, so any grandchild the SDK/httpx spawns
            # could in principle survive — none observed in practice; the openai
            # SDK is pure-Python/asyncio, no subprocesses of its own).
            row["error"] = f"TIMEOUT after {args.question_timeout}s (subprocess killed)"
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {e}"
        # The raw trace is split off before the row is written. Keeping it in
        # answers.jsonl would multiply the file every existing analysis reads
        # end to end; keeping it at all is the point — `compact_trace`'s
        # (step, tool, query) answers "what did it search for" and nothing
        # about why the planner chose that, which is in its own output.
        raw = row.pop("_raw_trace", None)
        with lock:
            with open(answers_path, "a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            if raw is not None:
                with gzip.open(raw_trace_path, "at") as f:
                    f.write(json.dumps({"eval_id": row["eval_id"], "trace": raw},
                                       ensure_ascii=False) + "\n")
        return row

    n_done = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = [ex.submit(work, q) for q in todo]
        for fut in as_completed(futs):
            r = fut.result()
            # First answered question settles it: every question in a run hits
            # the same endpoint with the same model string.
            _assert_model_not_substituted(r)
            n_done += 1
            mark = "ERR " if r.get("error") else ("ok  " if r.get("judge", {}).get("label") else "MISS")
            print(f"[{n_done}/{len(todo)}] {mark} {r['eval_id']} {r.get('error','')[:80]}", flush=True)

    # ---- score + paired comparison ----
    _, final_errored = load_previous_answers(answers_path)
    rows = list(done.values())
    for line in open(answers_path):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not r.get("error") and r["eval_id"] not in {x["eval_id"] for x in rows}:
            rows.append(r)
    labels = {r["eval_id"]: bool(r["judge"]["label"]) for r in rows if not r.get("error")}
    correct = sum(labels.values())
    cats: dict[str, list[int]] = {}
    for r in rows:
        if r.get("error"):
            continue
        c = r["category"] or r["question_type"]
        cats.setdefault(c, [0, 0])
        cats[c][0] += bool(r["judge"]["label"])
        cats[c][1] += 1
    paired_stats = _paired_stats(anchor, labels)
    # Every model that answered any question in this run. One entry is the
    # only healthy outcome; more than one means the run is a blend and its
    # score is not attributable to anything.
    served = sorted({m for r in rows for m in (r.get("served_models") or [])})
    arm_status = _run_arm_status(rows, requested=CONTEXT_OFFLOAD)
    print(
        "ARM_RESULT "
        f"context_offload_requested={arm_status['context_offload_requested']} "
        f"context_offload_effective={arm_status['context_offload']} "
        f"valid={arm_status['arm_configuration_valid']} "
        f"unsupported_flags={arm_status['unsupported_flags']}",
        flush=True,
    )
    summary = {
        # Derived, never hardcoded. These two fields used to be string
        # literals, so every run inherited the provenance of whichever run the
        # literals were written for — three separate directories all claimed
        # to be the same run at the same commit, and reading them cost real
        # time before the answers.jsonl stamps settled what had actually run.
        "run": out_dir.name,
        "sodamem": _sodamem_provenance(),
        "model_requested": MODEL, "judge_model_requested": MODEL,
        "served_models": served,
        "model_substituted": bool(served) and served != [MODEL],
        "store_root": str(store_root()),
        "n_answered": len(labels), "correct": correct,
        "accuracy": round(correct / len(labels), 4) if labels else None,
        "per_category": {k: f"{a}/{b2}" for k, (a, b2) in sorted(cats.items())},
        # Same story as `run`/`sodamem` above: this was the third literal in
        # this dict, and the one the earlier fix missed.
        "anchor": anchor_provenance,
        **paired_stats,
        # Counted from the file's final state, not from `rows` — `rows` comes
        # from the resume dict, which excludes failures by construction, so
        # this field could only ever have been 0. It read 0 through a run that
        # lost 181 of 500 questions to connection errors.
        "errors": len(final_errored),
        "n_questions": len(questions),
        # One boolean a reader cannot skim past. `n_answered` dropping to 319
        # was technically visible last time, but it sat beside "errors": 0 and
        # per-category denominators that had shrunk to match, so every rate
        # still looked normal.
        "incomplete": len(labels) < len(questions),
        **arm_status,
    }
    if summary["incomplete"]:
        print(f"!! INCOMPLETE: {len(labels)}/{len(questions)} answered, "
              f"{len(final_errored)} errored — rerun this arm to fill them in; "
              f"failed questions retry automatically.", flush=True)
    # A field that is merely null in the JSON gets skimmed past; this line does
    # not. The two causes need different fixes — one is a missing file, the
    # other is an anchor built for a different question set — so say which.
    if summary["paired_n"] == 0:
        why = ("no anchor file in SODAMEM_BENCH_DATA"
               if anchor_provenance is None else
               f"{anchor_provenance['file']} loaded "
               f"{anchor_provenance['n_labels']} labels, none of them "
               f"eval_ids this run answered")
        print(f"!! NO PAIRED COMPARISON: {why} — mcnemar_exact_p is null, "
              f"this run was not compared against anything.", flush=True)
    json.dump(summary, open(out_dir / "summary.json", "w"), indent=1)
    print(json.dumps(summary, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
