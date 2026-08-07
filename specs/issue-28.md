# Spec: 根 README 与 8 个翻译版补上 LoCoMo 徽章与跑分条目

Record: GitHub Issue #28 — https://github.com/SodaMem/SodaMem/issues/28
Branch: docs/issue-28-locomo-badge
Worktree: /Users/aaron.w/Desktop/SodaMem-worktrees/issue-28
Base: main @ cc29c4f

## Problem

`benchmarking/README.md` 里已经有一节 `## LoCoMo (Cat 1-4)`（PR #27，cc29c4f 合并），
写清了 1338/1540、口径、provenance、复现步骤。但对外门面 —— 根 `README.md` 与 7 个翻译版 ——
徽章行和 `## Benchmark` 一节仍然只有 LongMemEval-S 92.8%。

结果是：这套 harness 在第二个 benchmark 上出过分这件事，只有翻 `benchmarking/` 的人知道。
而这个数字一旦要放到门面上，有三个地方会以"看起来没问题"的方式骗人：

1. **不带口径的 LoCoMo 徽章**。榜单上同时存在含 Cat5 的、用 F1 的、用 1,813/1,986 题的
   LoCoMo 数字。一枚只写 `LoCoMo` 的徽章等于邀请一个不成立的比较。
2. **徽章链接指向 `artifacts/`**。LongMemEval 徽章指过去是对的（那里有 500 条逐题产物）；
   LoCoMo 一个字节的产物都没有发布，同样指过去就是把读者送到一个空承诺上。
3. **两个数字并排、只有一句 "Every answer and every retrieved memory is published"**。
   那句话是 LongMemEval 段落的收尾，读者会顺手把它套到 LoCoMo 头上。这是本 ticket
   漏了最伤的一条。

`scripts/sync_readme_langs.py` 只重写 `<!-- langs -->` … `<!-- /langs -->` 之间的语言切换行，
徽章和正文一概不管（已读源码确认）。`tests/test_readme_claims.py` 断言的是 MCP 工具数、
`SODAMEM_MCP_ALLOW_WRITE`、`my_extractor`/`FactEventExtractorV2` 占位符、curl 的
Content-Type、`/v1/...` 路由存在性 —— **它不断言任何跑分数字**（41 项 = 5 个
parametrize × 8 文件 + 1 个 translations 计数）。所以这 9 份副本没有任何机械保护，
只能靠这份 spec 的 AC 钉住。

## 一手数据（PM 已复算，未抄 issue）

复算源：`/Users/aaron.w/Desktop/LongMemEval-ingest/run/locomo_core1540_0807/answers.jsonl`
（1540 行，`eval_id` 唯一，`error` 全空），判对口径 `judge.label is true`；
交叉核对 `summary.json`。

| 项 | 复算值 | 与 `benchmarking/README.md` |
|---|---|---|
| 总分 | 1338 / 1540 = **86.8831%** | 一致（README 写 86.88%） |
| SH（type 4） | 764/841 = 90.8% | 一致 |
| TR（type 2） | 277/321 = 86.3% | 一致 |
| MH（type 1） | 231/282 = 81.9% | 一致 |
| OD（type 3） | 66/96 = 68.8% | 一致 |
| errors | 0 | 一致 |
| 每会话准确率 | 82.6% – 90.8%（10 个库） | 一致 |
| judge prompt 分布 | multi-session 1219 / temporal-reasoning 321 | 一致 |
| reader/planner、judge 模型 | `deepseek-v4-flash`（逐行 1540/1540） | 一致 |
| store | `locomo10_Hobs`，2,905 fact events，10 会话 / 272 session / 5,918 turn | 一致 |
| code HEAD | `ea4cc21`（summary.json 的 `sodamem` 字段） | 一致 |

结论：`benchmarking/README.md` 的 LoCoMo 一节数据可信，本 ticket 全部措辞与数字以它为准，
不另起一套说法。

## 决断：徽章精度用 `86.88%`

**选 `86.88%`，不选 `86.9%`。**

理由不是"跟 benchmarking/README 一致"这么弱的对齐，而是既有徽章的真实约定被误读了：

- 现有 `92.8%` 看着像"一位小数风格"，但 464/500 **精确等于** 92.8%。那枚徽章的约定是
  **打印精确值**，一位小数只是它恰好只需要一位。
- 1338/1540 = 86.8831…%，两种写法都是近似。按"打印精确值"的既有约定，应取更接近真值、
  且已经是全仓库唯一公开写法的那个 —— `86.88%`。
- 硬要求是徽章与正文不得互相矛盾。用 `86.88%` 时，全仓库对这个分数只有**一种**字符串
  表示（badge / 9 份正文 / benchmarking/README 全都是 `86.88`）。用 `86.9%` 则凭空造出
  第二种表示，将来任何一处改动都要同时维护两个数 —— 这正是这 9 份副本已经存在的病，
  没必要再加一个维度。

因此：**`86.9` 这个字符串不得出现在本次改动的任何文件中。**
（de / es / fr / pt-BR 正文按各自文件既有的本地化写法写成 `86,88 %`；这是小数分隔符的
本地化，不是第二个数字 —— 与现有 `92.8%` 徽章 / `92,8 %` 正文的处理完全同构。）

徽章颜色沿用 `brightgreen`，与 LongMemEval 徽章一致 —— 换颜色等于夹带一个未经说明的评价。

## Scope

只改这 9 个文件，一个不多：

```
README.md
docs/i18n/README.zh-CN.md
docs/i18n/README.ja.md
docs/i18n/README.ko.md
docs/i18n/README.fr.md
docs/i18n/README.es.md
docs/i18n/README.de.md
docs/i18n/README.pt-BR.md
```
（8 个语言文件 + 根 README = 9 份；根 README 是英文版。）

**明令不改：** `benchmarking/**`、任何 `.py`、任何 `artifacts/` 内容、
`scripts/sync_readme_langs.py`、`tests/**`、运行时代码。

## Implementation Path

### 1. 徽章（每个文件第 11 行 LongMemEval 徽章之后新增一行）

根 `README.md`：
```markdown
[![LoCoMo](https://img.shields.io/badge/LoCoMo--Cat1--4-86.88%25-brightgreen.svg)](benchmarking/README.md#locomo-cat-1-4)
```

7 个翻译版（仅链接前缀不同，深一层）：
```markdown
[![LoCoMo](https://img.shields.io/badge/LoCoMo--Cat1--4-86.88%25-brightgreen.svg)](../../benchmarking/README.md#locomo-cat-1-4)
```

- shields.io 的 `--` 渲染为 `-`，所以徽章上显示 `LoCoMo-Cat1-4 | 86.88%`。口径在徽章本体上。
- 锚点 `#locomo-cat-1-4` 由标题 `## LoCoMo (Cat 1-4)` 生成（小写、空格转连字符、括号丢弃），
  已核对该标题在 `benchmarking/README.md` 第 76 行原文如此。
- 徽章文本在 9 个文件里逐字节相同（包括数字写法 `86.88%`），只有 href 前缀差 `../../` ——
  与现有三枚徽章的处理方式一致。

### 2. `## Benchmark` 一节：LongMemEval 段落保持原样，其后追加 LoCoMo 段落

英文版（根 README，插在 LongMemEval 那句 "…Neither needs access to anything of ours." 之后、
本节末尾的 `---` 之前）：

```markdown
**86.88% (1338/1540)** on LoCoMo, categories 1-4 — Cat 5 (adversarial) excluded,
which is 1,540 of the 1,986 questions. End-to-end QA accuracy, LLM-as-judge.

| | |
|---|---|
| reader / planner / judge | `deepseek-v4-flash` |
| judge prompts | the LongMemEval benchmark's own templates, byte-copied |
| store | `locomo10_Hobs`, 10 user stores / 2,905 fact events |
| code | HEAD `ea4cc21` |

**No per-question artifacts are published for LoCoMo** — no answers, no retrieved
context, no run directory. What is published is
[the LoCoMo section of `benchmarking/README.md`](benchmarking/README.md#locomo-cat-1-4):
the per-category breakdown, the per-conversation spread, provenance and repro steps.
```

结构要求（对 9 个文件都成立）：

- LoCoMo 段落**整体在 LongMemEval 段落之后**，且 LongMemEval 那句"每条答案都已公开"
  仍然紧跟在 LongMemEval 表格之后 —— 不得出现"LongMemEval 表 → LoCoMo 表 → 公开性说明"
  这种排版，那会让公开性说明看起来同时管两个数。
- 每个 LoCoMo 段落**自己**以"LoCoMo 没有逐题产物"结尾。两个段落各自自洽，读者无论从哪
  一段读起都不会串。
- 表格值（模型名、store 名、`ea4cc21`）在 9 个文件里完全相同，只有表头/左列标签翻译。
- **不写** reader 与 judge 同模型的任何解读（自评闭环 / 分数偏松 / 建议换 judge）。
  只列事实模型名 —— 这是用户明确的编辑决定，与 `benchmarking/README.md` 的处理一致。

### 3. 翻译版是真翻译

散文（含"没有逐题产物"那句、口径描述、表格左列标签）必须译成该语言。
保持英文原样的只有：模型名 `deepseek-v4-flash`、store 名 `locomo10_Hobs`、
commit `ea4cc21`、路径 `benchmarking/README.md`、`LoCoMo` / `Cat 1-4` / `LongMemEval`
这些专名，以及现有文件已经保留英文的 `reader / planner / judge`。

数字本地化跟随各文件对 LongMemEval 已有的写法：
zh-CN / ja / ko → `86.88%`；de / es / fr → `86,88 %`；pt-BR → `86,88%`
（照抄该文件 `92,8 %` / `92,8%` 的空格与分隔符习惯，不要自创）。

### 4. 顺手修一处会立刻变假的措辞

7 个翻译版的文档表里有一行指向 `benchmarking/README.md`，描述是
"LongMemEval 那个数字是怎么跑出来的"（zh-CN:209 / ja:223 / ko:222 / fr:235 /
es:233 / de:233 / pt-BR:230）。合并后那个文件里有两个数字，这句话即刻失真。
改成不限定 benchmark 的说法（如"跑分数字是怎么跑出来的"）。
根 `README.md` 的文档表没有这一行，不新增。

## Acceptance Criteria

- [ ] **AC1 — 覆盖面**：`git diff --name-only main` 输出恰好是 Scope 里那 9 个 README 文件
      （加上本 spec 文件），无 `benchmarking/`、无 `.py`、无 `artifacts/` 下的任何新增。
- [ ] **AC2 — 徽章带口径**：9 个文件各新增恰好一枚 LoCoMo 徽章，label 为 `LoCoMo--Cat1--4`
      （渲染为 `LoCoMo-Cat1-4`）。任何文件里都不存在 label 只写 `LoCoMo` 的徽章。
- [ ] **AC3 — 徽章链接**：LoCoMo 徽章 href 为 `benchmarking/README.md#locomo-cat-1-4`（根）
      / `../../benchmarking/README.md#locomo-cat-1-4`（7 个翻译版）。**新增内容中不存在任何
      指向 `artifacts/` 的 LoCoMo 链接**；现有 LongMemEval 的 `artifacts/` 链接保持不变。
- [ ] **AC4 — 口径进正文**：9 个文件的 `## Benchmark` 一节都写出：`1338/1540`、`86.88%`
      （按 AC8 的本地化写法）、Cat 1–4、排除 Cat 5(adversarial)、1,540 题、
      end-to-end QA accuracy、LLM-as-judge。缺任何一项即 FAIL。
- [ ] **AC5 — provenance**：9 个文件的 LoCoMo 段落各有一张表，含 reader/planner/judge
      `deepseek-v4-flash`、judge prompts 为 LongMemEval 原模板逐字节复制、store
      `locomo10_Hobs`（10 个库 / 2,905 fact events）、code HEAD `ea4cc21`。四行的**值**
      在 9 个文件里逐字节一致。
- [ ] **AC6 — 无逐题产物的声明**：9 个文件各自用本语言明确写出"LoCoMo 未发布逐题产物
      （无 answers、无检索上下文、无 run 目录）"，且该句位于 LoCoMo 段落内、LongMemEval
      的"每条答案都已公开"仍紧接 LongMemEval 表格之后 —— 排版上不存在一句公开性说明
      横跨两个 benchmark 的读法。
- [ ] **AC7 — 不夹带解读**：9 个文件中不存在 reader 与 judge 同模型的评价性文字
      （自评/闭环/偏松/建议换 judge 之类）。只有事实模型名。
- [ ] **AC8 — 数字不自相矛盾**：徽章 `86.88%` 与正文数字一致；字符串 `86.9` 不出现在
      这 9 个文件的任何位置；de/es/fr/pt-BR 正文用 `86,88` 且其空格与分隔符习惯与同文件
      既有的 `92,8` 写法相同。
- [ ] **AC9 — 真翻译**：7 个翻译版的 LoCoMo 散文与表格左列为该语言，未整段照抄英文；
      允许保留英文的仅限专名/标识符（`deepseek-v4-flash`、`locomo10_Hobs`、`ea4cc21`、
      `LoCoMo`、`Cat 1-4`、`LongMemEval`、`reader / planner / judge`、路径）。
- [ ] **AC10 — 文档表措辞**：7 个翻译版指向 `benchmarking/README.md` 的那一行描述不再
      把该文件说成只讲 LongMemEval。
- [ ] **AC11 — 机械校验**：`pytest tests/test_readme_claims.py` 41/41 通过；
      `python scripts/sync_readme_langs.py` 运行后 `git diff` 无新增改动
      （证明 `<!-- langs -->` 块未被碰过）。

## Open Questions

无。徽章精度问题已在本 spec 决断为 `86.88%`（见上）。
