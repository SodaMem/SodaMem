# Spec: run_s500 summary 的 anchor / mcnemar 字段必须由实际加载结果派生

Record: GitHub Issue #24 — https://github.com/SodaMem/SodaMem/issues/24
Branch: fix/issue-24-anchor-provenance
Worktree: /Users/aaron.w/Desktop/SodaMem-worktrees/issue-24

## Problem

`benchmarking/run_s500.py:852` 把 summary 的 `anchor` 写成字符串字面量：

```python
"anchor": "entitysubj_consensus (3-run majority vote, 461/500 reference) — store longmemeval_s_500_Hobs_entitysubj",
```

它跟 `_paths.anchor_labels()` 究竟加载到了什么毫无关系。而 `benchmarking/paths.py::anchor_labels()`
在 data 目录里既没有 `entitysubj_consensus_anchor.json` 也没有 `anchor_slim2_labels.json` 时
返回 `None`（第 89-93 行，刻意设计：没有 anchor 也要能出分）。此时 run_s500 里
`anchor = {}`、`paired = {}`、`len(paired) == 0`，summary 却仍然宣称对照了 entitysubj。

已实测复现：`/Users/aaron.w/Desktop/LongMemEval-ingest/run/locomo_core1540_0807/summary.json`

```json
{"run": "locomo_core1540_0807", "n_answered": 1540, "paired_n": 0,
 "anchor": "entitysubj_consensus (3-run majority vote, 461/500 reference) — …",
 "anchor_only_correct": 0, "mcnemar_exact_p": 1.0}
```

同一处的坑还有两个：

1. `mcnemar_exact(0, 0)` 返回 `1.0`（run_s500.py:664-672），于是 `paired_n=0` 的跑分
   报 `mcnemar_exact_p: 1.0`。空集的 p 值读起来是"测了，没显著差异"，实际是"根本没测"。
2. 就算 anchor 文件在，字面量里的数字也已经和文件自述对不上：
   `entitysubj_consensus_anchor.json` 的 `_score_of_record` 是
   `"453/500 (90.6%), median of N=3, band [453,464], sigma=6.35"`，字面量写的是 "461/500 reference"。
   凡是手抄的 provenance 都会漂移，这是它一定要被派生掉的理由。

这个 summary dict 上方三行的注释（run_s500.py:838-842）已经把这个失效模式讲完了 ——
当时只修了 `run` 和 `sodamem`，`anchor` 是同一个坑里漏掉的那个。

`benchmarking/paths.py::anchor_labels()` 的返回标注写的是 `-> Path`，实际会返回 `None`。
标注本身就是这次 bug 的共犯：它告诉调用方"永远有值"。

## Value

- summary.json 是唯一被人读、被贴进 issue/PR、被拿来做对照结论的产物。带一个假的对照来源
  意味着任何非 entitysubj 题集（LOCOMO 等）的跑分都会被误读成"跟 entitysubj 比过了"。
- `mcnemar_exact_p: 1.0` + `paired_n: 0` 是最糟的组合：两个字段各自都不算错，合起来撒谎。
- provenance 派生之后，anchor 文件换了、少了、只对上一部分 eval_id，读 summary 的人都能直接看出来。

## Scope

只动 `benchmarking/`。不碰运行时代码（`sodamem/`、`server/`、`adapters/`），不改任何分数计算。
已发布的 92.8% 产物不重新生成 —— 已核实：仓库里没有任何已提交的 summary.json 带这句字面量，
唯一出现位置是 `run_s500.py` 自己，所以本次改动不影响任何已归档产物。

| 文件 | 改动 |
|---|---|
| `benchmarking/run_s500.py` | 新增 `_load_anchor()` / `_paired_stats()`；summary 里 `anchor` 与四个 paired 字段改为派生 |
| `benchmarking/paths.py` | `anchor_labels()` 返回标注改为 `Path \| None` |
| `benchmarking/tests/test_benchmarking_anchor.py`（新增） | 覆盖无 anchor 文件、两种 anchor 文件形态、零重叠、非零重叠 |

## Implementation Path

### 1. 把"加载 anchor"从 main() 里抠出来，连同 provenance 一起返回

现在 main() 第 717-726 行做了三件事（选路径、拆两种文件形态、过滤非 bool），没有任何测试，
而且丢掉了路径信息。改成一个纯函数：

```python
def _load_anchor(path: Path | None) -> tuple[dict[str, bool], dict | None]:
    """returns (labels, provenance-or-None)"""
```

- `path is None` → `({}, None)`。这是唯一让 `anchor` 字段为 `null` 的分支。
- 否则读 JSON，保留现有的两种形态处理（bare `{eval_id: bool}` / 包在 `"labels"` 下的 consensus
  形态）与 `isinstance(v, bool)` 过滤 —— **这段行为不许改**，它的注释解释了为什么读错会静默降级。
- provenance 是 dict，字段全部来自实际读到的东西：
  - `file`：文件名（`path.name`）
  - `path`：绝对路径字符串
  - `n_labels`：过滤后的 bool 标签条数
  - `note`：consensus 文件里的 `_what`（若存在，原样透传，不改写、不概括）；
    legacy 文件没有元数据时该键缺省为 `None`
  - 不再手抄任何比分数字。想知道 anchor 打了多少分的人应该去读 anchor 文件本身。

`_what` 是 anchor 文件自己写的自述；透传而不重述，是为了让 provenance 永远等于文件内容，
不会再漂移一次。

### 2. paired 统计与 p 值收进一个函数

```python
def _paired_stats(anchor: dict[str, bool], labels: dict[str, bool]) -> dict:
    """summary 里的 paired_n / anchor_only_correct / new_only_correct / mcnemar_exact_p"""
```

- 逻辑与现第 820-823 行等价（同样的 b/c 定义）。
- `paired_n == 0` → `mcnemar_exact_p` 为 `None`，不调用 `mcnemar_exact`。
- `paired_n > 0 且 b + c == 0` → 仍然是 `1.0`。这是合法结果："有配对，无不一致对，无差异证据"。
  两者必须区分开，所以门限是 `paired_n`，不是 `b + c`。
- summary 里用 `**_paired_stats(...)` 展开，`round(p, 4)` 只在 p 不是 None 时做。

### 3. `mcnemar_exact()` 本身不改

它是纯数学函数，参数只有 b 和 c —— 它看得见的只有"不一致对为 0"，而这种情况下 `1.0` 是对的。
它看不见"根本没有配对样本"，那个信息只存在于调用点。把 `Optional[float]` 塞进一个二项检验的
返回值，是让纯函数去背调用方的上下文。在调用点处理，是让"空集"这个特殊情况死在唯一知道它存在的
那一层。docstring 里补一行说明 `n == 0` 的语义边界，指向调用点的门。

### 4. 让 paired_n == 0 在跑完时看得见

现有 `incomplete` 已经有一条 `!! INCOMPLETE` 打印。同样处理：`paired_n == 0` 时打一行
`!! NO PAIRED COMPARISON: ...`（区分"没有 anchor 文件"和"anchor 有但零 eval_id 重叠"两种原因）。
一个只在 JSON 里为 null 的字段还是会被跳过；这一行不会。

### 5. 测试

新增 `benchmarking/tests/test_benchmarking_anchor.py`，沿用同目录的
`pytest.importorskip("openai")` + `sys.path.insert` 惯例（见 `test_benchmarking_preflight.py`）。
只测这两个纯函数，不跑 main()，不发任何 LLM 请求。

## Acceptance Criteria

- [ ] **AC1**: `run_s500.py` 里不再存在字符串字面量 `"entitysubj_consensus (3-run majority vote, 461/500 reference) …"`；
      `grep -n "entitysubj_consensus (3-run" benchmarking/run_s500.py` 无输出。
- [ ] **AC2**: `_paths.anchor_labels()` 返回 `None` 时，summary 的 `anchor` 为 `None`（JSON 里是 `null`）。
      由 `_load_anchor(None) == ({}, None)` 与 summary 直接使用该返回值两处代码共同证明。
- [ ] **AC3**: anchor 文件存在时，summary 的 `anchor` 是派生 dict，至少含实际文件名、绝对路径、
      实际 bool 标签条数；consensus 形态下 `_what` 原样透传。dict 里不含任何手写的比分/store 名字符串。
- [ ] **AC4**: `paired_n == 0` 时 `mcnemar_exact_p` 为 `None`；`paired_n > 0 且 b + c == 0` 时仍为 `1.0`。
- [ ] **AC5**: `mcnemar_exact()` 的函数签名与数值行为不变（`n == 0` 仍返回 `1.0`），仅允许改 docstring；
      分数计算路径（labels/correct/accuracy/per_category）零改动。
- [ ] **AC6**: 新增测试文件覆盖至少四种情况并全部通过：
      (a) 无 anchor 文件 → `anchor is None` 且 `mcnemar_exact_p is None`；
      (b) legacy bare `{eval_id: bool}` 形态加载正确、条数正确；
      (c) consensus `{"labels": {...}, "_what": ...}` 形态加载正确且不把包装层当标签；
      (d) anchor 有内容但与本次 labels 零重叠 → `paired_n == 0` 且 `mcnemar_exact_p is None`（`anchor` 非 None）。
- [ ] **AC7**: `benchmarking/paths.py::anchor_labels()` 返回标注为 `Path | None`；`anchor_labels()` 的
      查找顺序与返回值行为不变。
- [ ] **AC8**: `paired_n == 0` 时跑分结束会打印一行显式提示，且区分"无 anchor 文件"与"anchor 存在但零重叠"。
- [ ] **AC9**: 改动只落在 `benchmarking/` 下；`git diff --name-only main` 不含该目录与 `specs/` 之外的任何文件。
