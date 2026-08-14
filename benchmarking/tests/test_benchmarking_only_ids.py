"""--only subset loading + --range helpers.

Empty --only file must error rather than silently run all 500.
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
pytest.importorskip(
    "openai",
    reason=(
        "the benchmark harness imports the OpenAI SDK at module level; "
        "it lives behind the [llm] extra"
    ),
)

from run_s500 import (  # noqa: E402
    eval_id_number,
    filter_by_q_range,
    load_only_ids,
    parse_q_range,
)


def test_only_ids_json_list(tmp_path):
    p = tmp_path / "ids.json"
    p.write_text('["q193", "q053"]')
    assert load_only_ids(str(p)) == {"q193", "q053"}


def test_only_ids_lines(tmp_path):
    p = tmp_path / "ids.txt"
    p.write_text("q193\nq053\n\n")
    assert load_only_ids(str(p)) == {"q193", "q053"}


def test_only_ids_empty_file_refuses_to_run_everything(tmp_path):
    p = tmp_path / "ids.txt"
    p.write_text("\n")
    with pytest.raises(SystemExit):
        load_only_ids(str(p))


def test_parse_q_range_ok():
    assert parse_q_range("1-300") == (1, 300)
    assert parse_q_range("51-100") == (51, 100)
    assert parse_q_range(" 7-7 ") == (7, 7)


def test_parse_q_range_rejects_bad():
    with pytest.raises(SystemExit):
        parse_q_range("300")
    with pytest.raises(SystemExit):
        parse_q_range("100-50")
    with pytest.raises(SystemExit):
        parse_q_range("0-10")


def test_filter_by_q_range():
    qs = [{"eval_id": f"q{i:03d}"} for i in (1, 50, 51, 100, 101)]
    got = [q["eval_id"] for q in filter_by_q_range(qs, 51, 100)]
    assert got == ["q051", "q100"]
    assert eval_id_number("q051") == 51
    assert eval_id_number("Q7") == 7
