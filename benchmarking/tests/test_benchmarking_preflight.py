"""The two guards that used to be README warnings.

Both failure modes have the same shape: the run completes, produces a
plausible score, and is wrong in a way only a field nobody reads would have
told you. Documentation is the wrong fix for that — a reader who forgets to
check is exactly the reader the warning was for. These pin the code that now
refuses to start (or refuses to continue) instead.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# A base `pip install sodamem` has no [llm] extra, and CI's
# gate-i1-base-deps job collects this whole tree under exactly that
# install. Skipping is the contract; exploding at import is what the
# gate exists to catch.
pytest.importorskip("openai", reason="the benchmark harness imports the OpenAI SDK at module level; it lives behind the [llm] extra")

import run_s500  # noqa: E402


# ---------------------------------------------------------------------------
# Requested arms the installed build cannot honour
# ---------------------------------------------------------------------------

def test_preflight_passes_against_the_installed_build():
    """The arms this driver requests by default must all exist. If this fails,
    the driver and the library have drifted apart — which is the entire point
    of checking."""
    run_s500._preflight_arms()


def test_preflight_refuses_when_a_requested_arm_is_missing(monkeypatch):
    monkeypatch.setattr(run_s500, "_accepts_keyword",
                        lambda target, name: name != "count_roster")
    with pytest.raises(SystemExit) as exc:
        run_s500._preflight_arms()
    assert "count_roster" in str(exc.value)


def test_preflight_names_every_missing_arm_not_just_the_first(monkeypatch):
    """One line per launch: an operator who has to rerun to discover the
    second missing flag pays the launch twice."""
    absent = {"count_roster", "stall_stop"}
    monkeypatch.setattr(run_s500, "_accepts_keyword",
                        lambda target, name: name not in absent)
    with pytest.raises(SystemExit) as exc:
        run_s500._preflight_arms()
    for name in absent:
        assert name in str(exc.value)


def test_preflight_ignores_arms_that_are_switched_off(monkeypatch):
    """An explicitly-OFF boolean asks for the behavior an older build already
    has, so its absence changes nothing and must not block the run."""
    monkeypatch.setattr(run_s500, "ABSTENTION_GATE", False)
    monkeypatch.setattr(run_s500, "_accepts_keyword",
                        lambda target, name: name != "abstention_gate")
    run_s500._preflight_arms()


def test_preflight_still_refuses_a_threshold_it_cannot_pass(monkeypatch):
    """`0` is not interchangeable with False: a zero threshold is an active
    request, so an unsupported one has to fail closed."""
    monkeypatch.setattr(run_s500, "STALL_DUP_THRESHOLD", 0)
    monkeypatch.setattr(run_s500, "_accepts_keyword",
                        lambda target, name: name != "stall_dup_threshold")
    with pytest.raises(SystemExit) as exc:
        run_s500._preflight_arms()
    assert "stall_dup_threshold" in str(exc.value)


# ---------------------------------------------------------------------------
# A model other than the one requested answered
# ---------------------------------------------------------------------------

def test_default_model_is_the_one_that_actually_answers():
    """`deepseek-chat` was retired 2026-07-24 and rerouted server-side to
    `deepseek-v4-flash` — 7-9 questions apart, no error for days. Defaulting
    to the alias makes silent substitution the normal case."""
    assert run_s500.MODEL == "deepseek-v4-flash"


def test_substitution_aborts_on_the_first_answer():
    with pytest.raises(SystemExit) as exc:
        run_s500._assert_model_not_substituted(
            {"eval_id": "q1", "served_models": ["something-else"]})
    assert "q1" in str(exc.value)
    assert "something-else" in str(exc.value)


def test_matching_model_is_allowed_through():
    run_s500._assert_model_not_substituted(
        {"eval_id": "q1", "served_models": [run_s500.MODEL]})


def test_a_blend_of_models_is_refused_even_if_one_is_right():
    """More than one entry means the run is a blend; its score is not
    attributable to either model."""
    with pytest.raises(SystemExit):
        run_s500._assert_model_not_substituted(
            {"eval_id": "q1", "served_models": [run_s500.MODEL, "other"]})


@pytest.mark.parametrize("row", [
    {"eval_id": "q1"},                          # errored row, never reached a model
    {"eval_id": "q1", "served_models": []},
    {"eval_id": "q1", "served_models": [None]},
])
def test_rows_that_never_reached_a_model_pass_through(row):
    """A timeout or a transport error has no served model to compare. Treating
    that as substitution would abort the run over an ordinary retryable
    failure."""
    run_s500._assert_model_not_substituted(row)


# ---------------------------------------------------------------------------
# Drift: the preflight must cover everything `_supported()` can drop
# ---------------------------------------------------------------------------

def _kwargs_per_supported_call() -> dict[str, set[str]]:
    """Every kwarg name each `_supported(Target, ...)` call site passes, read
    from the source rather than from a second hand-maintained list."""
    import ast

    src = Path(run_s500.__file__).read_text()
    out: dict[str, set[str]] = {}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_supported":
            target = ast.unparse(node.args[0])
            out.setdefault(target, set()).update(
                kw.arg for kw in node.keywords if kw.arg)
    return out


def test_preflight_covers_every_kwarg_supported_can_drop():
    """The failure this catches is the one that shipped on 0806.

    `_preflight_arms()` was written against a hand-listed set of 17 arms while
    `_supported()` actually forwarded 23 names — the six missing ones were the
    pinned knobs (`max_steps`, `planner_max_tokens`, `temperature`,
    `fallback_top_k`, reader `max_tokens`). A build without `max_steps` would
    have passed preflight, run all 500 questions on the dataclass default step
    budget, and reported it only in `unsupported_flags` afterwards: the exact
    class of silent-wrong-score the preflight exists to prevent, reintroduced
    by the fix for it.

    Two parallel lists cannot be kept in sync by intention. This reads the
    real call sites, so adding a kwarg without registering it fails here
    instead of in a run."""
    targets = {"PlannerConfig": run_s500.PlannerConfig,
               "ReaderConfig": run_s500.ReaderConfig,
               "reader_answer": run_s500.reader_answer}
    # `_all_arms`, not `_requested_arms`: the latter filters out switched-off
    # flags, and "declared but off" is not "undeclared".
    registered: dict[int, set[str]] = {}
    for name, target, _value in run_s500._all_arms():
        registered.setdefault(id(target), set()).add(name)

    gaps = {}
    for target_name, kwargs in _kwargs_per_supported_call().items():
        target = targets.get(target_name)
        assert target is not None, f"unknown _supported() target {target_name!r}"
        missing = kwargs - registered.get(id(target), set())
        if missing:
            gaps[target_name] = sorted(missing)
    assert not gaps, (
        "these kwargs reach _supported() but _requested_arms() does not "
        f"declare them, so preflight cannot catch a build that drops them: {gaps}"
    )


def test_pinned_knobs_are_shared_not_copied():
    """The call site and the preflight must read one value, not two equal
    literals — equal today is the whole problem with a parallel list."""
    declared = {name: value for name, _t, value in run_s500._all_arms()}
    assert declared["max_steps"] is run_s500.MAX_STEPS
    assert declared["planner_max_tokens"] is run_s500.PLANNER_MAX_TOKENS
    assert declared["fallback_top_k"] is run_s500.FALLBACK_TOP_K
