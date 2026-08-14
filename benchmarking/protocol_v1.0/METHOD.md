# Protocol v1.0 — keep-count only（无 HARD_STOP）

> 原内部代号 Protocol v1.5；现统一为 **v1.0**。

| 组件 | 改动 |
|------|------|
| `set_enumeration` | `is_plan_only_row` / `count_kept_admitted` |
| `cardinality` advisories | `have`=keep-count；`have<N` 仅 soft gate |

## 成绩

**468/500 = 93.6%** — 见 `RESULTS_S500.md`、`ARCHIVE_S500/`。
