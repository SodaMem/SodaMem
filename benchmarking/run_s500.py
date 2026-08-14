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
there is informative, not a same-model paired gate. This run also carries the bug #8-#11 fixes (d5ae1c7): forced step-0
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
# 0730 temporal arm: resolve the question's relative date into an explicit
# window in code (see sodamem/answer/timewords.py).
TIME_WINDOW = os.environ.get("SODAMEM_ANSWER_TIME_WINDOW", "0") == "1"
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
        ("time_window", PlannerConfig, TIME_WINDOW),
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


def _read_jsonl_text(path: Path) -> str:
    """Read answers.jsonl written under either UTF-8 or legacy Windows GBK."""
    raw = path.read_bytes()
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        # Pre-fix runs on Chinese Windows often wrote with the locale codec.
        for enc in ("gb18030", "gbk", "cp936"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")


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
    for line in _read_jsonl_text(path).splitlines():
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
    # only wired for create_provider_for_model()'s registry path), so set the
    # tri-state flag directly, same as sodamem/llm/factory.py:164 does.
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
            time_window=TIME_WINDOW, capture_planner_input=CAPTURE_INPUT,
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
        "time_window": TIME_WINDOW,
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


def parse_q_range(spec: str) -> tuple[int, int]:
    """Parse inclusive 1-based question numbers: ``1-300``, ``51-100``.

    Matches ``eval_id`` forms like ``q051`` / ``q51`` (leading zeros optional).
    """
    raw = (spec or "").strip()
    if not raw:
        raise SystemExit("--range: empty (expected START-END, e.g. 1-300)")
    if raw.count("-") != 1:
        raise SystemExit(
            f"--range {spec!r}: expected START-END with one hyphen "
            f"(e.g. 1-300 or 51-100)"
        )
    left, right = raw.split("-", 1)
    try:
        lo, hi = int(left.strip()), int(right.strip())
    except ValueError as e:
        raise SystemExit(
            f"--range {spec!r}: START and END must be integers"
        ) from e
    if lo < 1 or hi < 1:
        raise SystemExit(f"--range {spec!r}: numbers must be >= 1")
    if hi < lo:
        raise SystemExit(f"--range {spec!r}: END ({hi}) < START ({lo})")
    return lo, hi


def eval_id_number(eval_id: str) -> int | None:
    """``q051`` / ``Q51`` → 51; non-numeric ids → None."""
    s = str(eval_id).strip()
    if len(s) >= 2 and s[0] in "qQ":
        s = s[1:]
    try:
        return int(s)
    except ValueError:
        return None


def filter_by_q_range(questions: list, lo: int, hi: int) -> list:
    """Keep questions whose eval_id number is in ``[lo, hi]`` inclusive."""
    out = []
    for q in questions:
        n = eval_id_number(q.get("eval_id", ""))
        if n is not None and lo <= n <= hi:
            out.append(q)
    return out


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact binomial on discordant pairs (b=anchor-only-correct,
    c=new-only-correct)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=0, help="limit (0 = all)")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--out", default=str(HERE / "results" / "s500_sodamem_regression_0724_v4flash"))
    ap.add_argument(
        "--question-timeout",
        type=int,
        default=int(os.environ.get("SODAMEM_QUESTION_TIMEOUT", "240")),
        help="hard per-question wall-clock cap in seconds (default 240; was 600)",
    )
    ap.add_argument(
        "--heartbeat-stale",
        type=int,
        default=int(os.environ.get("SODAMEM_HEARTBEAT_STALE", "90")),
        help="kill worker if heartbeat file not refreshed for this many seconds "
             "(0 disables; default 90). Worker pulses before/after each LLM call.",
    )
    ap.add_argument("--category", default="",
                    help="restrict to one LongMemEval category (IE/TR/MR/KU/ABS)")
    ap.add_argument("--only", default="",
                    help="path to a file of eval_ids (JSON list or one per "
                         "line); run only those questions. Built for the "
                         "stable-set methodology: the 19 always-wrong + a "
                         "stable-right regression sample resolve a targeted "
                         "arm at 1/13 the cost of a full 500, where the "
                         "±8-12 noise floor would swallow the effect.")
    ap.add_argument(
        "--range",
        dest="q_range",
        default="",
        help="inclusive question-number slice, e.g. 1-300 or 51-100 "
             "(matches eval_id q001..q500). Combines with --only / "
             "--category as an intersection. For splitting one 500-run "
             "across machines, use different --out dirs then merge "
             "answers.jsonl.",
    )
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
    if args.q_range:
        lo, hi = parse_q_range(args.q_range)
        questions = filter_by_q_range(questions, lo, hi)
        if not questions:
            raise SystemExit(
                f"--range {args.q_range}: no questions left after filter "
                f"(check --only / stores / numbering)"
            )
    _anchor_path = _paths.anchor_labels()
    # Two shapes in the wild: the legacy anchor is a bare {eval_id: bool}, the
    # consensus anchor wraps its labels under "labels" beside provenance
    # fields. Reading the wrapper as labels would match zero eval_ids and
    # degrade SILENTLY to "no paired comparison" — a run that looks fine and
    # reports nothing.
    anchor = json.load(open(_anchor_path)) if _anchor_path else {}
    if isinstance(anchor.get("labels"), dict):
        anchor = anchor["labels"]
    anchor = {k: v for k, v in anchor.items() if isinstance(v, bool)}
    context_offload_supported = _accepts_keyword(PlannerConfig, "context_offload")
    context_offload_effective = CONTEXT_OFFLOAD and context_offload_supported
    print(f"ARM role_timeline={ROLE_TIMELINE} answer_bias={ANSWER_BIAS} "
          f"membership_bias={MEMBERSHIP_BIAS} abstention_gate={ABSTENTION_GATE} "
          f"count_roster={COUNT_ROSTER} time_window={TIME_WINDOW} "
          f"personalization={PERSONALIZATION_BIAS} stall_stop={STALL_STOP} "
          f"truncation_retry={TRUNC_RETRY} cache_layout={CACHE_LAYOUT} "
          f"short_ids={SHORT_IDS} autocall={CAPABILITY_AUTOCALL} "
          f"context_offload_requested={CONTEXT_OFFLOAD} "
          f"context_offload_effective={context_offload_effective} "
          f"context_offload_supported={context_offload_supported} "
          f"stall_thresholds=dup{STALL_DUP_THRESHOLD}/zero{STALL_ZERO_THRESHOLD}  "
          f"category={args.category or 'ALL'}  "
          f"range={args.q_range or 'ALL'}  "
          f"out={args.out}", flush=True)

    done, previously_errored = load_previous_answers(answers_path)
    todo = [q for q in questions if q["eval_id"] not in done]
    if args.count:
        todo = todo[: args.count]
    print(f"total={len(questions)} done={len(done)} todo={len(todo)}", flush=True)

    lock = threading.Lock()
    worker_script = str(HERE / "answer_one_question.py")
    hb_dir = out_dir / "_heartbeats"
    hb_dir.mkdir(parents=True, exist_ok=True)
    question_timeout = max(30, int(args.question_timeout))
    heartbeat_stale = max(0, int(args.heartbeat_stale))
    print(
        f"timeouts: question={question_timeout}s heartbeat_stale="
        f"{heartbeat_stale or 'off'}s",
        flush=True,
    )

    def _kill_worker(proc: subprocess.Popen) -> None:
        try:
            proc.kill()
        except OSError:
            pass
        if sys.platform == "win32" and proc.pid:
            # Ensure grandchildren die too (httpx / SDK threads won't, but
            # orphaned console children sometimes do).
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=10,
                )
            except Exception:
                pass

    def _run_worker(eval_id: str) -> tuple[str, str, int | None, str | None]:
        """Return (stdout, stderr, returncode, error_tag).

        error_tag is set when we killed for TIMEOUT / HEARTBEAT_STALE.

        Continuously drain stdout/stderr while waiting — otherwise a finished
        worker blocked on a full PIPE can look like HEARTBEAT_STALE (stage
        stays at ``done`` and never exits).
        """
        hb_path = hb_dir / f"{eval_id}.json"
        try:
            if hb_path.exists():
                hb_path.unlink()
        except OSError:
            pass
        hb_path.write_text(
            json.dumps({"t": time.time(), "stage": "spawn"}),
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["SODAMEM_HEARTBEAT_PATH"] = str(hb_path)
        # Also write the final JSON line to a sidecar file so a pipe stall
        # cannot lose a completed answer.
        result_path = hb_dir / f"{eval_id}.result.json"
        env["SODAMEM_RESULT_PATH"] = str(result_path)
        try:
            if result_path.exists():
                result_path.unlink()
        except OSError:
            pass

        proc = subprocess.Popen(
            [sys.executable, worker_script, "--eval-id", eval_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        out_chunks: list[str] = []
        err_chunks: list[str] = []

        def _drain(stream, bucket: list[str]) -> None:
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    bucket.append(chunk)
            except Exception:
                pass

        t_out = threading.Thread(
            target=_drain, args=(proc.stdout, out_chunks), daemon=True
        )
        t_err = threading.Thread(
            target=_drain, args=(proc.stderr, err_chunks), daemon=True
        )
        t_out.start()
        t_err.start()

        deadline = time.time() + question_timeout
        kill_reason: str | None = None
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            # Completed answer already on disk — stop waiting on the process.
            if result_path.is_file() and result_path.stat().st_size > 2:
                break
            now = time.time()
            if now >= deadline:
                _kill_worker(proc)
                kill_reason = f"TIMEOUT after {question_timeout}s (subprocess killed)"
                break
            if heartbeat_stale > 0:
                last = 0.0
                stage = ""
                try:
                    meta = json.loads(hb_path.read_text(encoding="utf-8"))
                    last = float(meta.get("t") or 0)
                    stage = str(meta.get("stage") or "")
                except Exception:
                    last = 0.0
                    stage = ""
                # After worker finished answering, do not treat quiet heartbeat
                # as a hang (flush / judge teardown can be quiet).
                if stage in {"done", "judge_begin"}:
                    pass
                elif last > 0 and (now - last) > heartbeat_stale:
                    _kill_worker(proc)
                    kill_reason = (
                        f"HEARTBEAT_STALE after {heartbeat_stale}s "
                        f"(stage={stage or '?'}; subprocess killed)"
                    )
                    break
            time.sleep(1.0)

        try:
            proc.wait(timeout=15)
        except Exception:
            _kill_worker(proc)
        t_out.join(timeout=5)
        t_err.join(timeout=5)
        stdout = "".join(out_chunks)
        stderr = "".join(err_chunks)
        # Prefer sidecar result if stdout empty but worker finished.
        if (not stdout.strip()) and result_path.is_file():
            try:
                stdout = result_path.read_text(encoding="utf-8")
                kill_reason = None  # recovered completed answer
            except OSError:
                pass
        return stdout or "", stderr or "", proc.returncode, kill_reason

    def work(item: dict) -> dict:
        eval_id = item["eval_id"]
        row = dict(item)
        try:
            stdout, stderr, rc, kill_reason = _run_worker(eval_id)
            row_parsed = None
            parse_err = None
            if stdout.strip():
                try:
                    row_parsed, _end = json.JSONDecoder().raw_decode(stdout.strip())
                except json.JSONDecodeError as e:
                    parse_err = e
                    flat = (
                        stdout.replace("\u2028", " ")
                        .replace("\u2029", " ")
                        .strip()
                    )
                    candidates = [
                        ln.strip()
                        for ln in flat.splitlines()
                        if ln.strip().startswith("{") and ln.strip().endswith("}")
                    ]
                    if candidates:
                        try:
                            row_parsed = json.loads(candidates[-1])
                            parse_err = None
                        except json.JSONDecodeError as e2:
                            parse_err = e2
            if row_parsed is not None and isinstance(row_parsed, dict):
                # Prefer a completed answer even if we also tripped a kill race.
                row = row_parsed
                if row.get("error"):
                    pass
                elif kill_reason and not row.get("judge"):
                    row["error"] = kill_reason
            elif kill_reason:
                row["error"] = kill_reason
            elif not stdout.strip():
                row["error"] = (
                    f"empty subprocess stdout (rc={rc}): {stderr[-300:]}"
                )
            else:
                row["error"] = (
                    f"JSONDecodeError: {parse_err} | rc={rc} | "
                    f"stdout_head={stdout[:200]!r} | stderr_tail={stderr[-200:]!r}"
                )
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {e}"
        # The raw trace is split off before the row is written. Keeping it in
        # answers.jsonl would multiply the file every existing analysis reads
        # end to end; keeping it at all is the point — `compact_trace`'s
        # (step, tool, query) answers "what did it search for" and nothing
        # about why the planner chose that, which is in its own output.
        raw = row.pop("_raw_trace", None)
        with lock:
            # Windows defaults to locale encoding (often GBK); hypothesis text
            # can contain ¥ / − / emoji which are not encodable there.
            with open(answers_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            if raw is not None:
                with gzip.open(raw_trace_path, "at", encoding="utf-8") as f:
                    f.write(json.dumps({"eval_id": row["eval_id"], "trace": raw},
                                       ensure_ascii=False) + "\n")
        return row

    n_done = 0
    # Tiny sidecar on the *parent* of --out so status never touches the hot
    # results dir (answers.jsonl / _heartbeats contend heavily on Windows).
    live_path = out_dir.parent / f"_live_{out_dir.name}.json"
    live_lock = threading.Lock()
    resume_ok = sum(
        1 for r in done.values() if bool((r.get("judge") or {}).get("label"))
    )
    resume_miss = len(done) - resume_ok
    live = {
        "total": len(questions),
        "resume_done": len(done),
        "resume_ok": resume_ok,
        "resume_miss": resume_miss,
        "todo": len(todo),
        "pass_ok": 0,
        "pass_miss": 0,
        "pass_err": 0,
        "pass_done": 0,
        "ok": resume_ok,
        "miss": resume_miss,
        "err": 0,
        "pending": len(todo),
        "last_eval_id": "",
        "last_mark": "",
        "updated_at": time.time(),
    }

    def _write_live(mark: str, eval_id: str) -> None:
        with live_lock:
            live["pass_done"] = n_done
            live["last_eval_id"] = eval_id
            live["last_mark"] = mark.strip()
            live["updated_at"] = time.time()
            live["ok"] = live["resume_ok"] + live["pass_ok"]
            live["miss"] = live["resume_miss"] + live["pass_miss"]
            live["err"] = live["pass_err"]
            live["pending"] = live["todo"] - live["pass_done"]
            tmp = live_path.with_suffix(".json.tmp")
            try:
                tmp.write_text(json.dumps(live, indent=1), encoding="utf-8")
                tmp.replace(live_path)
            except OSError:
                pass

    _write_live("", "")
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = [ex.submit(work, q) for q in todo]
        for fut in as_completed(futs):
            r = fut.result()
            # First answered question settles it: every question in a run hits
            # the same endpoint with the same model string.
            _assert_model_not_substituted(r)
            n_done += 1
            mark = "ERR " if r.get("error") else ("ok  " if r.get("judge", {}).get("label") else "MISS")
            with live_lock:
                if r.get("error"):
                    live["pass_err"] += 1
                elif r.get("judge", {}).get("label"):
                    live["pass_ok"] += 1
                else:
                    live["pass_miss"] += 1
            _write_live(mark, r["eval_id"])
            print(f"[{n_done}/{len(todo)}] {mark} {r['eval_id']} {r.get('error','')[:80]}", flush=True)

    # ---- score + paired comparison ----
    _, final_errored = load_previous_answers(answers_path)
    rows = list(done.values())
    for line in open(answers_path, encoding="utf-8"):
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
    paired = {e: (anchor[e], labels[e]) for e in labels if e in anchor}
    b = sum(1 for a, n in paired.values() if a and not n)   # anchor-only correct
    c_ = sum(1 for a, n in paired.values() if n and not a)  # new-only correct
    p = mcnemar_exact(b, c_)
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
        # to be `s500_..._0724_v4flash @ d5ae1c7`, and reading them cost real
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
        "anchor": "entitysubj_consensus (3-run majority vote, 461/500 reference) — store longmemeval_s_500_Hobs_entitysubj",
        "paired_n": len(paired), "anchor_only_correct": b, "new_only_correct": c_,
        "mcnemar_exact_p": round(p, 4),
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
    json.dump(summary, open(out_dir / "summary.json", "w", encoding="utf-8"), indent=1)
    print(json.dumps(summary, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
