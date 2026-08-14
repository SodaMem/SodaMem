# Typed Answer Schema (TAS) — method notes

Package path remains `benchmarking/protocol_v1.0/` for compatibility.
Public name: **Typed Answer Schema (TAS)**.

Stacks on Soft / Plan B+ (`sodamem_opt.apply()`), then TAS patches.

## What TAS is

TAS is an **answer-side** discipline: classify the question into a task type,
then apply structured constraints (enumerate before count, temporal pin,
named-slot conflict resolution). It does **not** replace the SodaMem memory
engine.

Headline on LongMemEval-S (same store / model family): **468/500 (93.6%)**,
up from the published artifact **464/500 (92.8%)**.

## Compared to Soft

| Component | Soft (0.x) | TAS |
|---|---|---|
| Relative dates / timeline force | Yes | Kept |
| Deterministic count roster | Yes | Kept + admission / dedup |
| Question schema (task typing) | No | `schema.parse_question_schema` |
| Cardinality `have` | — | **keep-count** (plan-only excluded); soft gate only |
| HARD_STOP on incomplete set | — | **Off** |
| SetEnumeration board | — | Yes |
| Saturation residual searches | — | Schema-routed |
| OrderedTimeline / EventAnchor | — | Yes |
| Slot conflict board | — | Yes |
| Sum money-role gate | — | Yes |

## Apply order

1. `sodamem_opt.patches.apply()` — Plan B+
2. TAS planner/reader addenda + advisory assembly + roster filters

## Scope

TAS is the public answer-protocol surface and the current LongMemEval-S
headline method in this repository.
