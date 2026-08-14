"""A run that failed a third of its questions must not report `errors: 0`.

That is what happened on 0730: the machine slept mid-run, 181 of 500 questions
came back `ProviderError: Connection error.`, and `summary.json` said

    "n_answered": 319, ... "errors": 0

The count is structurally unable to be anything else. Resume loads only rows
WITHOUT an error (so failures retry on the next run — correct), the summary
then derives `rows` from that same dict, and counts errors in a collection
that by construction contains none.

`n_answered` did drop to 319, so the evidence was there — but it sat next to
an explicit "errors: 0" that reads as an all-clear, and the per-category
denominators shrank to match, so every rate still looked normal. A run missing
36% of its questions has to be impossible to mistake for a clean one.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_s500 import load_previous_answers  # noqa: E402


def _write(tmp_path: Path, *rows) -> Path:
    path = tmp_path / "answers.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_failed_rows_are_counted_even_though_they_are_not_resumable(tmp_path):
    path = _write(
        tmp_path,
        {"eval_id": "q001", "judge": {"label": True}},
        {"eval_id": "q002", "error": "ProviderError: Connection error."},
        {"eval_id": "q003", "error": "ProviderError: Connection error."},
    )

    done, errored = load_previous_answers(path)

    # Only clean rows resume; the failures must retry.
    assert sorted(done) == ["q001"]
    # ...and must still be visible in the count.
    assert sorted(errored) == ["q002", "q003"]


def test_a_missing_file_is_an_empty_run_not_a_crash(tmp_path):
    done, errored = load_previous_answers(tmp_path / "nothing.jsonl")
    assert done == {} and errored == {}


def test_a_truncated_final_line_does_not_lose_the_whole_file(tmp_path):
    """A killed process can leave a half-written line. Everything before it is
    still valid work and must survive."""
    path = tmp_path / "answers.jsonl"
    path.write_text(json.dumps({"eval_id": "q001", "judge": {"label": True}})
                    + "\n" + '{"eval_id": "q002", "jud')

    done, errored = load_previous_answers(path)

    assert sorted(done) == ["q001"]
    assert errored == {}
