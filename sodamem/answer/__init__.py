"""sodamem.answer — the planner loop + reader behind the `[answer]` extra
(D7: in-process, no CLI subprocess).

Four submodules:

- `protocol`: `Claim`/`PlannerState` — the structured evidence-and-claims
  state the planner reads/writes each turn.
- `rules`: declarative tool rules (`InitToolRule`/`TerminalRule`/
  `PrerequisiteRule`) replacing the runtime's old silent rewrite of the
  planner's first decision.
- `loop`: `run_planner_loop()` — the tool-calling orchestration loop.
- `reader`: `assemble_reader_context()` + `answer()` — evidence shaping and
  the final LLM answer call.

`answer_question()` below is composition code: it wires the four submodules
end to end, the way `sodamem.context.build_context()` wires
`EvidenceStore`/`cards`/`organizers` end to end for its own layer. Because
`run_planner_loop` and `reader.answer` are both first-class exports rather
than one fused entry point, something has to recompose them for the common
"just answer the question" case — that is this function's only job.
A caller that wants the intermediate `AutonomousAgentResult` (planner trace,
termination reason, usage) or a hand-built `ReaderContext` can call
`run_planner_loop`/`assemble_reader_context`/`answer` directly instead.
"""
from __future__ import annotations

from sodamem.answer.loop import AutonomousAgentResult, DEFAULT_ALLOWED_TOOLS, PlannerConfig, run_planner_loop
from sodamem.answer.protocol import Claim, PlannerState
from sodamem.answer.reader import Answer, ReaderConfig, ReaderContext, answer as reader_answer, assemble_reader_context
from sodamem.answer.rules import DEFAULT_RULES, InitToolRule, PrerequisiteRule, RuleViolation, TerminalRule
from sodamem.llm import LLMProvider
from sodamem.tools import MemoryTool


def answer_question(
    question: str,
    *,
    current_date: str,
    tools: MemoryTool,
    provider: LLMProvider,
    planner_config: PlannerConfig | None = None,
    reader_config: ReaderConfig | None = None,
    rules: tuple = DEFAULT_RULES,
    answer_bias: bool = False,
) -> Answer:
    """Run the planner loop to gather evidence, then read a final answer
    from it. The single end-to-end entrypoint most callers want; see the
    module docstring for how to reach the intermediate steps instead."""
    planner_config = planner_config or PlannerConfig()
    reader_config = reader_config or ReaderConfig()
    result = run_planner_loop(
        question, current_date=current_date, tools=tools, provider=provider,
        config=planner_config, rules=rules,
    )
    context = assemble_reader_context(
        result.evidence, result.selected_evidence_ids, question,
        current_date=current_date, provider=provider, config=reader_config,
        insufficient=result.insufficient, missing_information=result.missing_information,
        planner_claims=result.planner_claims, planner_conflicts=result.planner_conflicts,
    )
    return reader_answer(
        question, context, current_date=current_date, provider=provider,
        config=reader_config, answer_bias=answer_bias,
    )


__all__ = [
    "answer_question",
    "AutonomousAgentResult", "PlannerConfig", "DEFAULT_ALLOWED_TOOLS", "run_planner_loop",
    "Claim", "PlannerState",
    "Answer", "ReaderConfig", "ReaderContext", "reader_answer", "assemble_reader_context",
    "DEFAULT_RULES", "InitToolRule", "TerminalRule", "PrerequisiteRule", "RuleViolation",
]
