"""Loading an arm must refuse to hand back a partial run as if it were a score.

This exists because of a mistake, not a hypothetical. `b6_traced_0731` stopped
at 221/500 when the API balance ran out. `summary.json` said `incomplete: true`
— the guard added earlier did its job — and then an analysis script read
`correct` straight out of the file, divided by 500, and reported a mean of
404.3 across six runs. The flag was written and not read.

So the read path gets the guard too. `load_arm` returns the rows only when the
run finished; a partial run has to be asked for explicitly, and then it comes
with the counts that say so.
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

from run_s500 import IncompleteRun, load_arm  # noqa: E402


def _arm(tmp_path: Path, n_ok: int, n_err: int, total: int = 500) -> Path:
    d = tmp_path / "arm"
    d.mkdir()
    lines = []
    for i in range(n_ok):
        lines.append({"eval_id": f"q{i:03d}", "judge": {"label": i % 2 == 0},
                      "category": "MR"})
    for i in range(n_err):
        lines.append({"eval_id": f"e{i:03d}", "error": "boom"})
    (d / "answers.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in lines))
    (d / "summary.json").write_text(json.dumps({"n_questions": total}))
    return d


def test_a_complete_run_loads(tmp_path):
    rows = load_arm(_arm(tmp_path, n_ok=500, n_err=0))
    assert len(rows) == 500


def test_a_partial_run_raises_rather_than_returning_a_number(tmp_path):
    with pytest.raises(IncompleteRun) as exc:
        load_arm(_arm(tmp_path, n_ok=221, n_err=279))
    # The message has to carry the counts, or the next person re-derives them.
    assert "221" in str(exc.value) and "500" in str(exc.value)


def test_a_partial_run_can_be_asked_for_explicitly(tmp_path):
    rows = load_arm(_arm(tmp_path, n_ok=221, n_err=279), allow_incomplete=True)
    assert len(rows) == 221


def test_error_rows_are_never_returned_as_answers(tmp_path):
    rows = load_arm(_arm(tmp_path, n_ok=10, n_err=3, total=10))
    assert all("error" not in r for r in rows)
