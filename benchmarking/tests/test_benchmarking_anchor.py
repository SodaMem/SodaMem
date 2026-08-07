"""The summary's anchor provenance must equal what was actually loaded.

`anchor` was a string literal, so a run with no anchor file on disk still
reported that it had been compared against entitysubj — beside `paired_n: 0`
and `mcnemar_exact_p: 1.0`, which together read as "tested, no difference"
about a test that never ran. These pin both halves: the provenance is derived
from the opened file, and the empty comparison reports null instead of 1.0.

Pure functions only — no store, no LLM, no network.
"""
from __future__ import annotations

import json
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


def _write(tmp_path: Path, name: str, payload: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload))
    return path


# ---------------------------------------------------------------------------
# (a) no anchor file — the only branch that may report a null anchor
# ---------------------------------------------------------------------------

def test_no_anchor_file_yields_no_labels_and_no_provenance():
    """`_paths.anchor_labels()` returns None when neither file is on disk, and
    that has to survive all the way into the summary as null. Anything else is
    the bug: a claimed comparison that did not happen."""
    assert run_s500._load_anchor(None) == ({}, None)


def test_no_anchor_means_no_p_value():
    stats = run_s500._paired_stats({}, {"q1": True, "q2": False})
    assert stats["paired_n"] == 0
    assert stats["mcnemar_exact_p"] is None


# ---------------------------------------------------------------------------
# (b) legacy bare {eval_id: bool}
# ---------------------------------------------------------------------------

def test_legacy_bare_anchor_loads_every_label(tmp_path):
    path = _write(tmp_path, "anchor_slim2_labels.json",
                  {"q1": True, "q2": False, "q3": True})
    labels, prov = run_s500._load_anchor(path)
    assert labels == {"q1": True, "q2": False, "q3": True}
    assert prov["file"] == "anchor_slim2_labels.json"
    assert prov["path"] == str(path.resolve())
    assert prov["n_labels"] == 3
    # No metadata in the legacy shape — absent, not invented.
    assert prov["note"] is None


def test_provenance_carries_no_hand_written_score(tmp_path):
    """The literal this replaced said "461/500" while the file said 453/500.
    Nothing in the provenance may be typed by hand, or it drifts again."""
    path = _write(tmp_path, "anchor_slim2_labels.json", {"q1": True})
    _, prov = run_s500._load_anchor(path)
    blob = json.dumps(prov)
    assert "461" not in blob
    assert "entitysubj" not in blob


# ---------------------------------------------------------------------------
# (c) consensus {"labels": {...}, "_what": ...}
# ---------------------------------------------------------------------------

def test_consensus_anchor_unwraps_labels_and_passes_through_what(tmp_path):
    """Reading the wrapper as labels would match zero eval_ids and degrade
    silently to "no paired comparison"."""
    what = "453/500 (90.6%), median of N=3, band [453,464], sigma=6.35"
    path = _write(tmp_path, "entitysubj_consensus_anchor.json", {
        "_what": what,
        "_score_of_record": "ignored — provenance is the file's own words",
        "labels": {"q1": True, "q2": False},
    })
    labels, prov = run_s500._load_anchor(path)
    assert labels == {"q1": True, "q2": False}
    # The wrapper keys are not labels.
    assert "_what" not in labels and "labels" not in labels
    assert prov["n_labels"] == 2
    # Verbatim: a re-worded copy is free to disagree with the file.
    assert prov["note"] == what


def test_non_bool_values_are_dropped(tmp_path):
    """A label that is not a bool is a malformed anchor entry; counting it
    would make `n_labels` disagree with what the comparison can use."""
    path = _write(tmp_path, "entitysubj_consensus_anchor.json",
                  {"labels": {"q1": True, "q2": "yes", "q3": None}})
    labels, prov = run_s500._load_anchor(path)
    assert labels == {"q1": True}
    assert prov["n_labels"] == 1


# ---------------------------------------------------------------------------
# (d) an anchor that overlaps this run by nothing
# ---------------------------------------------------------------------------

def test_anchor_with_zero_overlap_reports_null_p_but_keeps_provenance(tmp_path):
    """The reproducing case: a LOCOMO run against a LongMemEval anchor. The
    anchor is real and must still be named — what is missing is the overlap."""
    path = _write(tmp_path, "entitysubj_consensus_anchor.json",
                  {"labels": {"lme_1": True, "lme_2": False}})
    anchor, prov = run_s500._load_anchor(path)
    assert prov is not None
    stats = run_s500._paired_stats(anchor, {"locomo_1": True, "locomo_2": True})
    assert stats["paired_n"] == 0
    assert stats["mcnemar_exact_p"] is None
    assert stats["anchor_only_correct"] == 0
    assert stats["new_only_correct"] == 0


# ---------------------------------------------------------------------------
# The p-value gate: paired_n, not b + c
# ---------------------------------------------------------------------------

def test_full_agreement_still_reports_one():
    """Pairs exist and none of them disagree — 1.0 is the real answer here and
    must not be swallowed by the empty-set guard."""
    anchor = {"q1": True, "q2": False}
    stats = run_s500._paired_stats(anchor, {"q1": True, "q2": False})
    assert stats["paired_n"] == 2
    assert stats["anchor_only_correct"] == 0 and stats["new_only_correct"] == 0
    assert stats["mcnemar_exact_p"] == 1.0


def test_discordant_pairs_are_counted_in_the_old_direction():
    """b is anchor-only-correct, c is new-only-correct. Swapping them would
    flip every FIXED/BROKE reading of a run."""
    anchor = {"q1": True, "q2": False, "q3": True}
    labels = {"q1": False, "q2": True, "q3": True}
    stats = run_s500._paired_stats(anchor, labels)
    assert stats["paired_n"] == 3
    assert stats["anchor_only_correct"] == 1   # q1
    assert stats["new_only_correct"] == 1      # q2
    assert stats["mcnemar_exact_p"] == 1.0


def test_only_overlapping_eval_ids_are_paired():
    anchor = {"q1": True, "q2": True}
    stats = run_s500._paired_stats(anchor, {"q2": False, "q9": False})
    assert stats["paired_n"] == 1
    assert stats["anchor_only_correct"] == 1


def test_mcnemar_exact_itself_is_unchanged():
    """The pure function keeps its 1.0 for n == 0 — it can only see "no
    discordant pairs", and for that 1.0 is correct. The empty-comparison case
    is the caller's to know about."""
    assert run_s500.mcnemar_exact(0, 0) == 1.0
    assert run_s500.mcnemar_exact(5, 0) == pytest.approx(0.0625)
    assert run_s500.mcnemar_exact(3, 3) == 1.0
