# Spec: benchmarking/README 补上 LoCoMo 跑分结果（Cat1–4, 1540 题）

Record: GitHub Issue #26 — https://github.com/SodaMem/SodaMem/issues/26
Branch: docs/issue-26-locomo-score
Worktree: /Users/aaron.w/Desktop/SodaMem-worktrees/issue-26

## Problem

2026-08-07 在冻结库 `locomo10_Hobs` 上跑完了 LoCoMo Cat1–4（1540 题），结果只存在于本机
`run/locomo_core1540_0807/`，仓库里一个字都没有。`benchmarking/README.md` 现在只讲
LongMemEval-S 的 92.8%，读者看不出这套 harness 还在第二个 benchmark 上出过分，也就无从判断
这个分数是什么口径、跑在什么库上、拿什么模型读和判。

跑分产物按仓库既有政策不能提交（`benchmarking/README.md` 明写禁止 `answers.jsonl`、raw traces、
run 目录），所以唯一的落地形式就是 README 里的一节 —— 分数 + 拆解 + provenance + 复现步骤。
这一节必须自证口径，否则它就是一个无法核对的数字。

## Value

- 让 LoCoMo 分数可比：写清"Cat1–4 / 1540 / 排除 Cat5 / 端到端 QA accuracy / LLM-as-judge"，
  读者能直接对上主流严格口径，而不是对上一个来路不明的 86.88%。
- 让 provenance 不漂移：模型、库、代码 HEAD、成本都从 `summary.json` / `answers.jsonl` 抄实值。
- 让别人能跑：环境变量、`--only` 的真实用法、`SODAMEM_BENCH_MODEL` 的实际取值一次写全。

## 一手数据（已由 PM 复算，不是抄 issue）

复算脚本读 `/Users/aaron.w/Desktop/LongMemEval-ingest/run/locomo_core1540_0807/answers.jsonl`
（1540 行，`eval_id` 唯一，`error` 全空），判对口径 `judge.label is true`：

| 项 | 复算值 |
|---|---|
| 总分 | **1338 / 1540 = 86.88%** |
| 单跳 SH（locomo_type 4） | 764/841 = 90.8% |
| 时序 TR（locomo_type 2） | 277/321 = 86.3% |
| 多跳 MH（locomo_type 1） | 231/282 = 81.9% |
| 开放域 OD（locomo_type 3） | 66/96 = 68.8% |
| errors | 0 |
| judge model（逐行） | `deepseek-v4-flash`，1540/1540 一致 |
| served_models（逐行） | `["deepseek-v4-flash"]`，1540/1540 一致 |
| tokens | prompt 17,232,956 / completion 2,253,907 / total 19,486,863 / cached_input 6,133,248 |
| calls | 8,169 |

分库（`locomo_conv_id` ↔ `user_id`）：

| conv | user | 分数 | |
|---|---|---|---|
| conv-26 | lme_q001 | 135/152 | 88.8% |
| conv-30 | lme_q002 | 70/81 | 86.4% |
| conv-41 | lme_q003 | 138/152 | 90.8% |
| conv-42 | lme_q004 | 172/199 | 86.4% |
| conv-43 | lme_q005 | 147/178 | 82.6% |
| conv-44 | lme_q006 | 110/123 | 89.4% |
| conv-47 | lme_q007 | 126/150 | 84.0% |
| conv-48 | lme_q008 | 169/191 | 88.5% |
| conv-49 | lme_q009 | 137/156 | 87.8% |
| conv-50 | lme_q010 | 134/158 | 84.8% |

区间 82.6%–90.8%，十库无塌陷 —— 与 issue 一致。

题集（`locomo-bench-data/questions_slim.json`，1986 题）：

- 类型分布 `{1: 282, 2: 321, 3: 96, 4: 841, 5: 446}`，1–4 合计 **1540**。
- `core1540_ids.json` 的 1540 个 id 与"全部 locomo_type != 5"的集合**完全相等**（已 set 比对）。
- `answers.jsonl` 的 eval_id 集合与 `core1540_ids.json` **完全相等** —— 跑的就是这 1540 题。
- 源文件 provenance 来自 `locomo10_v4_inputs/manifest.json`：
  `source = .../locomo/locomo-refined/locomo_refined.json`，
  `type_counts` 里第五类标为 `adversarial-refined`（446）。
- Cat5 refinement 实测（`locomo10_v4_inputs/qa_questions.json`）：446 道 Cat5 **全部**有非空
  `answer` 与非空 `locomo_evidence`，**没有任何一道**带 `adversarial_answer` 字段 —— 即全部被改写为
  可回答。本次 1540 题不含任何 Cat5，故不受该 refinement 影响。
- **未验证**：与官方 `locomo10.json` 的逐题文本 diff（本机无官方副本）。文档里不得出现"逐题一致 /
  byte-identical / 与官方文件相同"这类说法。

Judge 模板（`run_s500.py:194-215`）：本次只会命中两个模板 —— `question_type` 统计为
`multi-session` 1219 题（走 `_JUDGE_STD`）、`temporal-reasoning` 321 题（走 `_JUDGE_TR`），
均为 LongMemEval 官方 judge prompt 的逐字拷贝。

库（`locomo10_Hobs/bootstrap_report.json`）：10 个 user store，`status` 全为 `built`，
ingest 阶段模型 `deepseek-chat`（0707 ingest，本次未重建）。
manifest 记的对话规模：10 段对话 / 272 sessions / 5,918 turns。

fact 数以活体 SQLite 为准：十个 `memory.db` 的 `fact_events` 合计 **2,905**，`raw_turns` 合计
5,918（= manifest total turns）。`bootstrap_report.json` 的 `counts.*` 是逐会话累计快照，求和
即重复计数 —— `sum(counts.fact_events)` = 41,469、`sum(counts.raw_turns)` = 85,300，两个都不能
写进文档。详见 AC10。

代码 HEAD（`summary.json.sodamem`）：`ea4cc21`。

**不得写进文档的东西**：`summary.json` 里的 `anchor` / `paired_n` / `mcnemar_exact_p`。这份
summary 是修 anchor bug（issue #24）之前跑的，`anchor` 仍是旧的假字符串而 `paired_n=0` ——
本次跑分**没有**做任何 anchor 配对或 McNemar 检验。新一节里不得出现 anchor / McNemar / 显著性
任何字样。

## Scope

**唯一允许改动的文件：**

- `benchmarking/README.md`

**明确不动：**

- 根 `README.md`（不加徽章、不加分数）
- `benchmarking/artifacts/` 下任何文件（不新增产物，不改 `artifacts/README.md`）
- `benchmarking/run_s500.py`、`paths.py` 及任何运行时代码
- 不提交 `answers.jsonl` / `raw_traces.jsonl.gz` / `summary.json` / run 目录 / 题集 / 库

## Implementation Path

在 `benchmarking/README.md` 增加一节 `## LoCoMo (Cat 1–4)`，位置放在 `## Store of record`
之后、`## Traces` 之前 —— 分数属于"这套 harness 出过什么结果"，紧跟库的段落最自然。

小节结构：

1. **一句话结论 + 口径**：`1338/1540 (86.88%)` on LoCoMo Cat 1–4；口径写明排除 Cat5/adversarial、
   端到端 QA accuracy、LLM-as-judge。
2. **四类拆解表**：SH / TR / MH / OD，四行，分子分母 + 百分比。可附一行分库区间 82.6%–90.8%。
3. **provenance 表**：score / 口径 / answered+errors / store / reader-planner / judge / code HEAD / 成本。
   reader/planner 与 judge 的模型名**如实列出**（都是 `deepseek-v4-flash`）。
4. **题集口径**：只声明"题数与分布对齐官方严格口径（Cat1–4 / 1540）"，以及"源文件是 LoCoMo-refined
   变体，其 refinement 经实测只落在 Cat5，而 Cat5 本次全部排除"。
5. **产物政策**：明说本次不发布逐题产物，理由指向本文件已有的 artifact policy 段。
6. **复现**：环境变量实值 + 命令行。

复现段的必要内容（都已核对过代码）：

```bash
export SODAMEM_BENCH_DATA=/path/to/locomo-bench-data     # questions_slim.json, core1540_ids.json
export SODAMEM_BENCH_STORES=/path/to/locomo10_Hobs       # <root>/<user_id>/memory.db
export SODAMEM_BENCH_MODEL=deepseek-v4-flash             # reader/planner 与 judge 同取此值
export DEEPSEEK_API_KEY=...

python benchmarking/run_s500.py \
  --out results/locomo_core1540 \
  --only /path/to/locomo-bench-data/core1540_ids.json \
  --concurrency 6
```

要点：

- `--only` 收的是**文件路径**，文件内容是 JSON 列表或每行一个 eval_id
  （`run_s500.py:647 load_only_ids`）。Cat1–4 的筛选就是靠这个 id 清单完成的，**harness 未改**。
- `SODAMEM_BENCH_MODEL` 同时决定 reader/planner 和 judge —— `run_s500.py:897` 把
  `model_requested` 与 `judge_model_requested` 都写成同一个 `MODEL`。这是事实陈述，
  **不写任何关于"自评闭环"的解读**（`artifacts/README.md` 里 92.8% 那份的 "Reading the number"
  段落，这次不写对应段落）。
- `SODAMEM_BENCH_SRC` 与 anchor 文件本次不需要，可不设。

**顺带修正（同文件、同一件事的一致性）**：README 第 56 行现有示例
`python benchmarking/run_s500.py --only q193,q053     # a subset` 与 `load_only_ids` 的实际行为
矛盾（它会去读一个名为 `q193,q053` 的文件并抛错）。新增的复现段紧接着教人用 `--only`，
留着这行会直接把人带沟里，故一并改成文件路径写法。仅改这一行示例，不动其他文字。

写作风格对齐现有 README：陈述句、给测得的数字、不写营销话术、不加 emoji、不加徽章。

## Acceptance Criteria

- [ ] **AC1 — 文件边界**：本次 diff 只触碰 `benchmarking/README.md` 与 `specs/issue-26.md`。
      根 `README.md`、`benchmarking/artifacts/**`、任何 `.py`、任何数据文件均无改动
      （`git diff --name-only main...HEAD` 可验）。
- [ ] **AC2 — 分数与拆解正确**：新增小节写出总分 `1338/1540`（86.88% 或 86.9%），
      且四类拆解与复算值逐条一致：SH 764/841、TR 277/321、MH 231/282、OD 66/96。
      任何一个数字对不上即为 FAIL。
- [ ] **AC3 — provenance 表如实**：表中至少含 store（`locomo10_Hobs`，10 个库，0707 ingest，
      本次未重建）、reader/planner = `deepseek-v4-flash`、judge = `deepseek-v4-flash`
      （LongMemEval 官方 judge prompt）、code HEAD `ea4cc21`、answered 1540/1540 且 errors 0。
      reader 与 judge 的模型名必须各自出现，不得省略或含糊成"同一个模型"以外的表述。
- [ ] **AC4 — 口径声明合规**：出现"Cat 1–4 / 1540 / 排除 Cat5（adversarial）"的口径说明；
      出现"源文件为 LoCoMo-refined 变体，refinement 只落在 Cat5，本次 Cat5 全部排除"的说明；
      **全文不含**任何"与官方 locomo10.json 逐题一致 / byte-identical / 逐题 diff 已做"之类断言。
- [ ] **AC5 — 无 anchor/McNemar 污染**：新增内容中不出现 anchor、McNemar、p 值、显著性、
      `entitysubj` 等与本次跑分无关的对照字样。
- [ ] **AC6 — 不写自评闭环解读**：不出现"self-grading / 自评闭环 / 同一个模型既读又判所以分数偏松"
      一类解读文字，也不复制 `artifacts/README.md` 的 "Reading the number" 段落。
      （AC3 的模型名如实列出与本条不冲突：列事实，不作解读。）
- [ ] **AC7 — 复现可照做**：复现段同时给出 `SODAMEM_BENCH_DATA`、`SODAMEM_BENCH_STORES`、
      `SODAMEM_BENCH_MODEL=deepseek-v4-flash`，以及 `--only <eval_id 清单文件路径>` 的调用示例，
      并写明筛选靠 id 清单完成、harness 未改。
- [ ] **AC8 — `--only` 用法一致**：README 全文不再出现 `--only q193,q053` 这类逗号内联 id 的
      错误示例；所有 `--only` 示例都是文件路径。
- [ ] **AC9 — 产物政策**：明说本次不发布逐题产物（`answers.jsonl` / 检索上下文 / run 目录），
      且 `benchmarking/artifacts/` 目录内容零变化。
- [ ] **AC10 — 成本数字准确（若写）**：若写 token/调用成本，须与复算一致：
      prompt 17.2M / completion 2.3M / cached 6.1M / total 19.5M / 8,169 calls。
      不写成本也算通过；写错即 FAIL。同理，若写库的 fact 数，必须是 **2,905**，
      不得引用 `raw_turns` 85,300 当对话轮数。

      **这个计数必须从活体 SQLite 查，不能读 `bootstrap_report.json` 的 `counts.*` 字段。**
      那些字段是逐会话累计快照，求和等于重复计数 —— `sum(counts.fact_events)` = 41,469，
      而十个 `memory.db` 的 `fact_events` 实测合计只有 2,905。两条独立佐证：
      (1) 活体 `raw_turns` 合计 5,918，恰等于 manifest 的 total turns，而
      `sum(counts.raw_turns)` = 85,300 对不上任何东西；
      (2) `sum(counts.extracted_facts_in_session)` = 2,905，与活体表逐库吻合。
      本 spec 初版把 41,469 钉成必须值，是踩了 `raw_turns` 同一族的坑，已订正。

## Decisions taken（PM 判断，如需推翻请回话）

1. **README 开头那句 "The published result is 92.8% …" 不改**。它讲的是"有产物背书的已发布结果"，
   本次 LoCoMo 没有产物，混进去反而削弱那句话。新一节自成一段即可。
   → 不列为 AC，coding agent 不要动它。
2. **顺带修 `--only q193,q053` 示例**（AC8）。同文件、一行、纯文档、零代码风险，且不修就会与
   新写的复现说明自相矛盾。若 Aaron 认为这该单开 ticket，删掉 AC8 即可，其余不受影响。
