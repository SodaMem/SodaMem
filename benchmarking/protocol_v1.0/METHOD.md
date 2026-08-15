# Protocol v1.0 — method notes

Stacks on Soft / Plan B+ (`sodamem_opt.apply()`), then Protocol patches.

## Compared to Soft

| Component | Soft (0.x) | Protocol v1.0 |
|---|---|---|
| Relative dates / timeline force | Yes | Kept |
| Deterministic count roster | Yes | Kept + `admission` / `dedup` filters |
| Question schema | No | `schema.parse_question_schema` |
| Cardinality `have` | — | **keep-count** (plan-only excluded); soft gate only |
| HARD_STOP on incomplete set | — | **Off** |
| SetEnumeration board | — | Yes |
| Saturation residual searches | — | Schema-routed |
| OrderedTimeline / EventAnchor | — | Yes |
| Slot conflict board | — | Yes |
| Sum money-role gate | — | Yes |

## Apply order

1. `sodamem_opt.patches.apply()` — Plan B+
2. Protocol planner/reader addenda + advisory assembly + roster filters

## Scope

v1.0 is the keep-count baseline that remains current for the public headline score.
