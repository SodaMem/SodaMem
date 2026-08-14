"""TAS schema: task typing only — no per-question entity packs."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_ROOT = ROOT / "benchmarking" / "protocol_v1.0"
sys.path[:0] = [str(PROTOCOL_ROOT), str(ROOT)]

from protocol_v1.schema import parse_question_schema  # noqa: E402


def test_how_many_routes_to_count_distinct():
    s = parse_question_schema("How many dinner parties have I attended in the past month?")
    assert s.task == "COUNT_DISTINCT"
    assert s.exclude_predicates == ()
    assert s.include_hints == ()


def test_how_much_routes_to_sum():
    s = parse_question_schema("How much did I spend in total last month?")
    assert s.task == "SUM"


def test_order_routes_to_ordered_list():
    s = parse_question_schema("In what order did I visit the museums, earliest to latest?")
    assert s.task == "ORDERED_LIST"


def test_relative_time_routes_to_temporal():
    s = parse_question_schema("Who did I meet last Saturday?")
    assert s.task == "TEMPORAL_EVENT"
    assert s.time_window_raw


def test_new_plan_routes_to_versioned_attr():
    s = parse_question_schema("What is my new internet plan speed after I upgraded?")
    assert s.task == "VERSIONED_ATTR"
    assert "new" in s.modifiers
