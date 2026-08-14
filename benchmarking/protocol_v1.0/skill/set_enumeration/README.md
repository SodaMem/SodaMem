# SetEnumeration Skill (MR)

Type-level skill for multi-session set/count/sum questions.

## Behavior

- Planner: require explicit `item_list` before counting/summing
- Reader: honor `set_enumeration_board` from protocol advisories
- Exclude plan-only rows when question asks for completed acts
- Entity counts: merge repeated mentions

## Wire-in

Applied automatically from `protocol_v1.patches.apply()` when running Protocol v1.3
(path: `Version/v1.3/skill/set_enumeration`).
