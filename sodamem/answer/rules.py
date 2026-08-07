"""Declarative tool rules for the planner loop (spec §6.6).

Replaces the scattered saw_search/saw_compute booleans, the require_compute
parameter, and the runtime silently rewriting the planner's first decision.
The loop CONSULTS rules; it never mutates a decision behind the planner's
back — `loop.py` wires `initial_calls()`/`check()` in exactly where that
rewrite used to happen.

Vocabulary note: `DEFAULT_RULES`'s `InitToolRule(tool="search")` /
`TerminalRule(requires_tool_seen="search")` deliberately use the abstract
capability name `"search"`, not the planner-facing `browser_search` name
`sodamem.prompts.planner.TOOL_GUIDE` advertises. `rules.py` has no business
knowing the concrete tool-dispatch vocabulary (that is `loop.py`'s job, and
different callers may wire up entirely different tool surfaces against the
same rule); `loop.py` is the one piece that knows `"search"` and
`"browser_search"` name the same capability, and registers a tool as
"having satisfied the search rule" under both spellings — see that module's
`_add_tools_seen` helper.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class InitToolRule:
    """When the loop starts with zero tool calls made, GENERATE this call as
    step one instead of asking the planner and overriding its answer."""
    tool: str
    default_args: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TerminalRule:
    """A final decision is only accepted after `requires_tool_seen` has been
    called at least once. Violation -> typed violation, loop returns the
    decision to the planner with the violation attached (no silent rewrite)."""
    requires_tool_seen: str


@dataclass(frozen=True)
class PrerequisiteRule:
    """`tool` may only be called after `before` has been called at least once."""
    before: str
    tool: str


@dataclass(frozen=True)
class RuleViolation:
    rule: object
    detail: str


DEFAULT_RULES: tuple = (
    InitToolRule(tool="search"),
    TerminalRule(requires_tool_seen="search"),
)


def initial_calls(rules, question: str) -> list[dict]:
    """Calls the loop issues on step 0 before consulting the planner."""
    return [{"tool": r.tool, "args": {**r.default_args, "query": question}}
            for r in rules if isinstance(r, InitToolRule)]


def check(rules, tools_seen: set[str], proposed_calls: list[dict], *,
          is_final: bool) -> list[RuleViolation]:
    """Pure function; returns violations, never mutates proposed_calls."""
    out: list[RuleViolation] = []
    for r in rules:
        if isinstance(r, TerminalRule) and is_final and r.requires_tool_seen not in tools_seen:
            out.append(RuleViolation(r, f"final before any '{r.requires_tool_seen}' call"))
        if isinstance(r, PrerequisiteRule):
            for c in proposed_calls:
                if c.get("tool") == r.tool and r.before not in tools_seen:
                    out.append(RuleViolation(r, f"'{r.tool}' before '{r.before}'"))
    return out


__all__ = [
    "InitToolRule", "TerminalRule", "PrerequisiteRule", "RuleViolation",
    "DEFAULT_RULES", "initial_calls", "check",
]
