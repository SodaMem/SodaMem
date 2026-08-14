"""sodamem.prompts — all-LLM-prompt-in-one-place contract tests.

Deviation from the task brief's Step 2 skeleton, noted here on purpose: the
brief's suggested test parametrizes TOOL_GUIDE and DEFAULT_ANSWER_CONSTRAINTS
alongside the plain-string prompts under a single `isinstance(value, str)`
assertion. TOOL_GUIDE is a `dict[str, str]` indexed by tool name (future
test_tool_guide_covers_full_tool_registry needs it to stay a dict to check
key coverage) and DEFAULT_ANSWER_CONSTRAINTS is a `list[str]` (
consumed element-wise via `list(_DEFAULT_ANSWER_CONSTRAINTS)`), not strings.
Forcing them into `str` here would invent a data shape absent from the source
just to satisfy a test — that's the opposite of a byte-exact port. Both are
tested for non-emptiness with their real, source-faithful container types
instead.
"""
import pytest

from sodamem.prompts.extraction import COARSE_RULES, DETERMINISM_RULES, EXTRACT_SYSTEM_PROMPT
from sodamem.prompts.planner import PLANNER_SYSTEM_PROMPT, QUERY_PLAN_PROMPT, TOOL_GUIDE
from sodamem.prompts.reader import (
    DEFAULT_ANSWER_CONSTRAINTS,
    READER_GUIDANCE,
    READER_GUIDANCE_ANSWER_BIAS_ADDENDUM,
    READER_GUIDANCE_PLANNER_CLAIMS_ADDENDUM,
    READER_GUIDANCE_RUNTIME_RESOLVED_ADDENDUM,
)
from sodamem.prompts.reader_con import OFFICIAL_CON_PROMPT_TEMPLATE


@pytest.mark.parametrize("value", [
    EXTRACT_SYSTEM_PROMPT, DETERMINISM_RULES, COARSE_RULES,
    QUERY_PLAN_PROMPT, PLANNER_SYSTEM_PROMPT,
    READER_GUIDANCE, OFFICIAL_CON_PROMPT_TEMPLATE,
])
def test_prompt_constants_are_nonempty_strings(value):
    assert isinstance(value, str) and value.strip()


@pytest.mark.parametrize("value", [
    READER_GUIDANCE_PLANNER_CLAIMS_ADDENDUM,
    READER_GUIDANCE_ANSWER_BIAS_ADDENDUM,
    READER_GUIDANCE_RUNTIME_RESOLVED_ADDENDUM,
])
def test_reader_guidance_addenda_are_nonempty_strings(value):
    assert isinstance(value, str) and value.strip()


def test_tool_guide_is_nonempty_dict_of_nonempty_strings():
    assert isinstance(TOOL_GUIDE, dict) and TOOL_GUIDE
    for name, description in TOOL_GUIDE.items():
        assert isinstance(name, str) and name.strip()
        assert isinstance(description, str) and description.strip()


def test_default_answer_constraints_is_nonempty_list_of_nonempty_strings():
    assert isinstance(DEFAULT_ANSWER_CONSTRAINTS, list) and DEFAULT_ANSWER_CONSTRAINTS
    for item in DEFAULT_ANSWER_CONSTRAINTS:
        assert isinstance(item, str) and item.strip()


def test_default_answer_constraints_carries_no_reading_instructions():
    forbidden = ("answer_task", "official_", "cot", "answer_style")
    lowered = " ".join(DEFAULT_ANSWER_CONSTRAINTS).lower()
    leaked = [s for s in forbidden if s in lowered]
    assert not leaked, f"prompts/reader.py DEFAULT_ANSWER_CONSTRAINTS leaks reading-instruction vocabulary: {leaked}"


def test_tool_guide_covers_full_tool_registry():
    from sodamem.tools import _TOOL_REGISTRY
    missing = [name for name in _TOOL_REGISTRY if name not in TOOL_GUIDE]
    assert not missing, f"TOOL_GUIDE is missing tool(s), historical drift bug recurring: {missing}"


def test_planner_pins_question_classification_and_exhaustive_tool_guidance():
    for classification in ("ordinary", "enumeration", "count", "sum", "comparison"):
        assert classification in PLANNER_SYSTEM_PROMPT
        assert classification in QUERY_PLAN_PROMPT
    assert "browser_count_evidence" in PLANNER_SYSTEM_PROMPT
    assert "browser_timeline_events" in PLANNER_SYSTEM_PROMPT
    assert "never exhaustive" in PLANNER_SYSTEM_PROMPT
