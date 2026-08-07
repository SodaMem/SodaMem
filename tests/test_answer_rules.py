"""Task 10 Step 2: declarative tool-rules unit tests (zero LLM).

InitToolRule produces the first search without consulting the planner;
TerminalRule blocks "final before any search" and returns a typed
violation; PrerequisiteRule gates one tool behind another; check() never
mutates its `proposed_calls` argument.
"""
from __future__ import annotations

from sodamem.answer.rules import (
    DEFAULT_RULES,
    InitToolRule,
    PrerequisiteRule,
    RuleViolation,
    TerminalRule,
    check,
    initial_calls,
)


def test_init_tool_rule_produces_first_search_without_consulting_planner():
    calls = initial_calls(DEFAULT_RULES, "what is my favorite color?")
    assert calls == [{"tool": "search", "args": {"query": "what is my favorite color?"}}]


def test_init_tool_rule_merges_default_args_under_query():
    rules = (InitToolRule(tool="search", default_args={"top_k": 10, "include_context": True}),)
    calls = initial_calls(rules, "q")
    assert calls == [{"tool": "search", "args": {"top_k": 10, "include_context": True, "query": "q"}}]


def test_rules_without_init_tool_rule_produce_no_initial_calls():
    rules = (TerminalRule(requires_tool_seen="search"),)
    assert initial_calls(rules, "q") == []


def test_terminal_rule_blocks_final_before_any_search():
    violations = check(DEFAULT_RULES, tools_seen=set(), proposed_calls=[], is_final=True)
    assert len(violations) == 1
    v = violations[0]
    assert isinstance(v, RuleViolation)
    assert isinstance(v.rule, TerminalRule)
    assert "search" in v.detail


def test_terminal_rule_passes_once_search_was_seen():
    violations = check(DEFAULT_RULES, tools_seen={"search"}, proposed_calls=[], is_final=True)
    assert violations == []


def test_terminal_rule_only_applies_when_is_final():
    # A non-final check with zero tools seen must not raise a terminal violation
    # -- the rule only gates finalization, not every step.
    violations = check(DEFAULT_RULES, tools_seen=set(), proposed_calls=[], is_final=False)
    assert violations == []


def test_prerequisite_rule_blocks_tool_before_its_prerequisite():
    rules = (PrerequisiteRule(before="search", tool="compute"),)
    proposed = [{"tool": "compute", "args": {}}]
    violations = check(rules, tools_seen=set(), proposed_calls=proposed, is_final=False)
    assert len(violations) == 1
    assert isinstance(violations[0].rule, PrerequisiteRule)
    assert "compute" in violations[0].detail and "search" in violations[0].detail


def test_prerequisite_rule_passes_once_prerequisite_was_seen():
    rules = (PrerequisiteRule(before="search", tool="compute"),)
    proposed = [{"tool": "compute", "args": {}}]
    violations = check(rules, tools_seen={"search"}, proposed_calls=proposed, is_final=False)
    assert violations == []


def test_prerequisite_rule_ignores_unrelated_tools():
    rules = (PrerequisiteRule(before="search", tool="compute"),)
    proposed = [{"tool": "browser_inspect", "args": {"memory_id": "x"}}]
    violations = check(rules, tools_seen=set(), proposed_calls=proposed, is_final=False)
    assert violations == []


def test_check_does_not_mutate_proposed_calls():
    rules = (PrerequisiteRule(before="search", tool="compute"),)
    proposed = [{"tool": "compute", "args": {"operator": "sum", "inputs": ["a"]}}]
    snapshot = [dict(c) for c in proposed]
    check(rules, tools_seen=set(), proposed_calls=proposed, is_final=True)
    assert proposed == snapshot


def test_default_rules_shape():
    assert len(DEFAULT_RULES) == 2
    assert isinstance(DEFAULT_RULES[0], InitToolRule)
    assert DEFAULT_RULES[0].tool == "search"
    assert isinstance(DEFAULT_RULES[1], TerminalRule)
    assert DEFAULT_RULES[1].requires_tool_seen == "search"


def test_forced_initial_search_counts_against_max_steps():
    """Source fidelity (re-pinned by the bug-#9 restoration): the loop
    consults the planner exactly max_steps times — step 0 included, whose
    proposed CALLS are replaced by the forced search while its state_update
    applies. Neither one fewer (the pre-#9 port skipped step-0's consult)
    nor one more (forced search must not be a bonus slot)."""
    from sodamem.answer.loop import run_planner_loop, PlannerConfig
    from sodamem.answer.rules import DEFAULT_RULES
    from sodamem.llm.testing import ScriptedProvider

    class _NoopTools:
        def dispatch(self, name, **kw):
            return {"items": []}

    # Bug-#9 restoration: the planner is consulted at EVERY step including
    # step 0 (source consulted max_steps times; step-0's CALLS were replaced
    # by the forced search but the consult itself — and its state_update —
    # happened). max_steps=5 -> exactly 5 planner turns, not 4, not 6.
    provider = ScriptedProvider(
        ['{"decision": {"action": "continue", "tool_calls": []}}'] * 5
    )
    run_planner_loop(
        question="q", current_date="2024/01/01", tools=_NoopTools(),
        provider=provider, config=PlannerConfig(max_steps=5), rules=DEFAULT_RULES,
    )
    assert len(provider.calls) == 5, (
        f"planner consulted {len(provider.calls)} times; source consulted "
        f"exactly max_steps times (step-0 consult included, its calls overridden)"
    )
