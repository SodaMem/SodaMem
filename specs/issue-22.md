# Spec: 拆掉 #14 的 `uvicorn.error` 变通（#19 已修掉它的理由）

Record: GitHub issue #22
Branch: `chore/22-unwind-uvicorn-error-workaround`
Worktree: `/Users/aaron.w/Desktop/SodaMem-worktrees/issue-22`
相关: #14（加了这条诊断和这个变通）、#19（修掉了变通的前提，并明确把拆除留给本 issue）

---

## Problem

`server/app.py:_log_runtime_identity()` 至今在 **`uvicorn.error`** 上打那条
"serving on interpreter ..."，而不是本模块的 `logger`。理由写在 docstring 里
（`server/app.py:76-89`）：

> uvicorn configures only its own loggers, so the root logger has no handler and
> a `server.app` INFO record is dropped by `lastResort`. It would be absent from
> `~/.sodamem/daemon.log` — the one file this line exists to appear in.

#19 已经把这个前提消灭了：`create_app()` 第一件事就是
`configure_logging_if_unconfigured()`，root 无 handler 时装一个 stderr handler，
并把 `server` 降到 INFO（`server/logging_setup.py:49-77`）。所以现在
`server/app.py` 里一条普通的 `logger.info(...)` 自己就能到 `daemon.log`。

**代码本身没坏。坏的是那段 docstring。** 它告诉下一个读者一件不再为真的事：
"`server.app` 的 INFO 会从 daemon.log 里消失"。一条解释"某个已被修复的 bug 的变通"
的注释，比没有注释更糟 —— 它教给人一个假约束，下一个要加诊断行的人会照抄这条绕路。

**这就是本次改动的全部理由。改动幅度必须与之相称。**

## Value

- 删掉唯一一处会误导后来者的假约束，`server/` 里加诊断行只剩一条路：模块 logger。
- 顺带让 `daemon.log` 里这一行获得统一格式（`时间戳 INFO server.app: ...`），
  和 #19 之后其他 `server.*` 行一致，而不是混在 uvicorn 的 `INFO:     ` 里。
- 不追求任何行为改进 —— 本 issue 不修 bug，不加功能。

## Scope

| 文件 | 改动 |
|---|---|
| `server/app.py` | `_log_runtime_identity()` 的 emit 从 `logging.getLogger("uvicorn.error")` 换成模块级 `logger`；删掉 docstring 里第 4、5 两段（第 76-89 行，讲 logger 选择和"这个变通现在多余了"的那两段） |
| `tests/test_daemon_command.py` | `test_create_app_logs_the_running_interpreter`（158-176）：`caplog.at_level(..., logger="uvicorn.error")` → `logger="server.app"`；同步重写 162-169 行那段解释 `uvicorn.error` 的注释 |
| `CHANGELOG.md` | Unreleased/#19 条目末尾"the `uvicorn.error` workaround ... is left to a follow-up"这句话在本 PR 合并后即为假，改掉（见下方决策 3） |
| `specs/issue-22.md` | 本文件 |

**不在 scope**：`server/logging_setup.py`、`tests/test_daemon_logging.py`、
任何 `specs/*.md` 历史文档。

---

## 两个必须先回答的问题

### 决策 1：树里还有别的东西为同样理由骑 `uvicorn.error` 吗？—— 没有。

全仓库 grep `uvicorn\.error`（排除 `.venv`/`node_modules`）命中 6 处文件，逐个判定：

| 命中 | 判定 |
|---|---|
| `server/app.py:76,87,102` | **唯一的生产变通点，本次改动对象。** |
| `tests/test_daemon_command.py:162,168,170` | 该变通的测试侧镜像，**在 scope 内**。 |
| `tests/test_daemon_logging.py:35,200,225` | **不是变通，保持不动。** 这是 #19 的 AC4：它在 uvicorn 真实 logger 树上打三条记录（`server.app` / `uvicorn.error` / `uvicorn.access`）各断言恰好出现一次。它测的是 uvicorn 的树本身，跟我们把诊断放在哪没关系；而且它正是本次改动"不会重复打印"的现成证据（见 AC5），删了反而丢证据。 |
| `specs/issue-14.md` / `specs/issue-19.md` | **历史记录，不动。** 它们准确记载了当时的判断（issue-19.md:209 明确写着"本次不动，另开 issue"）。改写已归档的 spec 是伪造历史，而且这两份文件没有任何"这是当前约束"的语气。 |
| `CHANGELOG.md:29` | **不是历史，在 scope 内。** 它在 `## [Unreleased]` 里，描述的是"当前这个未发布版本的状态"，本 PR 合并后那句话就是错的。 |

另外确认：`grep -rn 'getLogger("'` 在非测试源码里只有 `server/app.py:102` 这一处硬编码
logger 名，其余全是 `getLogger(__name__)`。**这条绕路是孤例，不存在"一类"要清。**

也确认没有任何脚本/文档在解析这一行的文本形态（`grep -rn "serving on interpreter"`
只命中 `server/app.py` 自己和 `specs/issue-19.md` 的历史记载），所以行首格式从
uvicorn 的 `INFO:     ` 变成 `2026-.. INFO server.app: ` **不破坏任何消费者**。

### 决策 2：docstring 还该说点什么吗？—— 该，而且现有的话已经够了，一个字都不用加。

现在的 docstring 是六段。按段判定：

| 段 | 内容 | 判定 |
|---|---|---|
| 1（63-70） | **这个诊断为什么存在**：#14 花了一下午，就因为跑着的进程上没有任何东西说自己是谁；CLI 只能记它"打算"启动的解释器，这一行是"真正在跑的那个"写的，还覆盖不是我们启动的守护进程 | **保留，逐字不动。** 这是本函数唯一不可替代的解释 —— "记录解释器"本身不是自明有价值的事，价值全在这一段里 |
| 2（72-74） | 只进日志、不进 `/health` 响应体（#13 AC8(v)：响应体不带文件系统路径） | **保留。** 是一条现在依然生效的约束，而且是安全性质的 |
| 3（76-82） | "在 `uvicorn.error` 上打，因为 root 没 handler，`server.app` 的 INFO 会被丢掉" | **删除。** 这就是那句假话 |
| 4（84-89） | "这个变通现在多余了，因为 #19……拆除是另一次改动" | **删除。** 变通本身没了，讲变通为什么还留着的段落也就没了对象 |
| 5（91-93） | 为什么用裸 `except`：诊断绝不能成为一种新的启动失败方式 | **保留。** 解释的是下面代码里两个 `except Exception`，与本次改动无关 |

**不新增任何段落。** 有人可能想补一句"这条记录靠 #19 装的 root handler 才到得了
daemon.log，所以 `_log_runtime_identity()` 必须在 `configure_logging_if_unconfigured()`
之后调"—— 这个顺序约束是真的，但**它已经被写在它该在的地方**：
`server/app.py:111-114`，`create_app()` 里那句 "First, before anything that logs"。
在函数 docstring 里再写一遍，就是在重新制造本 issue 要消灭的那种东西：一段讲日志
管道内情的话，长在一个跟日志管道无关的函数上。

结论：**删两段，不加段。** 删完的函数解释的是"我为什么存在、我为什么不进响应体、
我为什么不会炸"，一条都不少。

### 决策 3：CHANGELOG 那一句怎么改

`CHANGELOG.md:28-30` 现在写着：

> Note: the `uvicorn.error` workaround added in #14 is now redundant (it still
> works and still produces no duplicate line); unwinding it is left to a follow-up.

**不新增一条 `### Fixed` 条目** —— 本次对用户没有任何行为变化，用户可见的日志内容
一模一样，只有行首前缀变了。为一次纯清理往 changelog 加条目是噪声。做法是**把那句
话改成事实**，比如收敛成一句"…and #22 has since unwound it"，或者整句删掉。
实现者二选一，只要合并后 CHANGELOG 里不再有任何指向"待办的 follow-up"的句子。

**同一段里附带一处更正**（AC7 覆盖）：第 23 行写 `create_app()` "lowers the
`server` / `sodamem` loggers to `INFO`"，这与同一段第 25-27 行自己写的"`sodamem` 的
logger 留在 `WARNING`"以及代码（`server/logging_setup.py:46`，`_OUR_LOGGERS =
("server",)`）直接矛盾 —— 是 #19 中途改了 scope 后漏改的一处。删掉 `/ sodamem`
这一个词即可。它跟本 issue 是同一种病（已发布的文字在骗读者），就在同一段里，
改动是一个词、零风险。**如果 Felix 认为这越界，砍掉 AC7 的这半条，其余不受影响。**

### 决策 4（验收时补记）：一个真实存在的行为差异，判定为可接受，只记在这里

审查发现并且我复核确认：设想一个**宿主应用**，它自己配了 logging（root 有 handler，
且 root 在 WARNING），同时又让 uvicorn 跑它的默认 `LOGGING_CONFIG`。改动前这一行走
`uvicorn.error`（uvicorn 的 dictConfig 把它设成 INFO 且自带 handler），宿主看得见；
改动后走 `server.app`，effective level 继承 root 的 WARNING，**记录不再产生**。

判定：**可接受，不需要任何代码或注释来兜底。**

1. 在那个进程里，`server/` 的**每一条** INFO（包括 #19 千辛万苦救回来的 per-request
   行）本来就已经被丢掉了。改动前这条诊断是**唯一一条享有特权**的 server 记录 ——
   靠的正是我们现在要删掉的那条绕路。改完之后它和同层的所有记录一视同仁。
2. 这就是 #19 守卫的语义：root 有 handler = 宿主自己管日志 = 不关我们的事
   （`server/logging_setup.py:50-59`）。想看的宿主一行就能打开：
   `logging.getLogger("server").setLevel(logging.INFO)`。
3. 旧 docstring 自己就承认过这条绕路在非 uvicorn 宿主下"behaves exactly like any
   other"，所以"任何宿主下都保证可见"从来就不是这行代码许过的诺。

**记在 spec 里，不记进代码。** 往 `_log_runtime_identity()` 的 docstring 里写一段
"在某种宿主 logging 配置下你看不到我"，等于把刚刚删掉的那类东西（长在诊断函数上的
日志管道内情）原样请回来 —— 而且这次连"当前为真"都只是勉强成立。

### 决策 3 复核（验收时）

CHANGELOG 里 `/ sodamem` 那处删除：**维持在本次 scope 内**。它和本 issue 是同一个
缺陷类型（已发布的文字与代码不符）、在同一段落、属于同一条未发布条目，改动是一个
词。为它单开一个 issue 的开销远大于缺陷本身，那是流程表演不是工程。

---

## Implementation Path

### 1. `server/app.py`

```python
        logger.info(
            "serving on interpreter %s (python %s, chromadb %s)",
            sys.executable, platform.python_version(), chroma or "not installed",
        )
```

`logger` 是文件顶部第 32 行的 `logging.getLogger(__name__)`（即 `server.app`）。
外层 `try/except Exception` 保留不动 —— 诊断永远不能成为启动失败的新路径。

**为什么这样就到得了 `daemon.log`**（`create_app()` 的调用顺序已经保证了，不需要新代码）：

1. `create_app()` 第 115 行先跑 `configure_logging_if_unconfigured()`；守护进程下
   uvicorn 的 dictConfig 没有 `root` 键，root 无 handler → 守卫成立 → 装一个
   stderr `StreamHandler`，并把 `server` 设成 `INFO`。
2. 第 120 行才调 `_log_runtime_identity()`，此时 `server.app` 的 effective level 是
   INFO，记录得以创建；propagate 到 root，被那个 handler 写到 stderr。
3. `sodamem_cli/daemon.py:120-121` 把子进程的 stdout/stderr 都接到 `daemon.log`。

**为什么不会重复打印**：`server.app` 自己没有 handler，向上只有 root 一个 handler；
`uvicorn` / `uvicorn.access` 是 `propagate=False` 且自带 handler，`uvicorn.error`
虽然 propagate 但链在 `uvicorn` 处终止 —— 三条链没有交点。这一点不是推理，是
`tests/test_daemon_logging.py::test_no_duplicate_lines_against_uvicorns_real_config`
在 uvicorn 真实 logger 树上量出来的，而且**搬家之后它测的正是我们现在走的那条路**
（它里面本来就有 `logging.getLogger("server.app").info("MARKER-server-app")`，
断言恰好一次）。

### 2. `tests/test_daemon_command.py:158-176`

`caplog.at_level(logging.INFO, logger="server.app")`。

原注释里那段"必须用 `uvicorn.error` 而不是 root，因为 uvicorn 的 dictConfig 是进程
全局的、`tests/_service.py` 用 `log_level="error"` 会把 `uvicorn.error` 永久钉在
ERROR"—— 这个陷阱随着搬家一起消失：uvicorn 的 `LOGGING_CONFIG` 只配 `uvicorn`
三兄弟且 `disable_existing_loggers: False`，**碰不到 `server.*`**。新注释应当写
明的是另一件事：为什么仍然显式带 `logger="server.app"` 而不是裸
`caplog.at_level(logging.INFO)` —— 因为 pytest 下 root 已有 handler，
`configure_logging_if_unconfigured()` 会正确地按兵不动，`server` 保持 NOTSET，
所以这个 level 必须由测试自己压下来。

不要在这个文件里新增测试。这条断言的职责只有一个：**诊断整行被删时它必须变红**
（AC3 会实测这一点）。

### 3. `CHANGELOG.md` — 按决策 3 处理。

---

## 端到端验证配方（AC4/AC5 用，实现者照抄）

> **修订 A（2026-08-19，验收时改）**：本节初版给的调用方式是错的，而且错得正好是
> 它自己上一段警告过的那种错。原样留着就等于把本 issue 要消灭的那种"过期指令"再
> 生产一份，所以就地改掉，并把踩到的两个坑写下来：
>
> 1. `sodamem --api-url URL daemon ensure` **根本 parse 不了**。`--api-url` 是
>    **挂在子命令上的**（`sodamem_cli/main.py:78` 的 `_add_service_flags(p)`），
>    不是全局 flag。正确写法是 `daemon ensure --api-url URL`。
> 2. **别用 `sodamem` 这个 console script。** 它的 `sys.path[0]` 是 venv 的
>    `bin/`，不是 cwd，于是"cd 到 worktree"这一步完全失效，跑的是别人的源码。
>    必须用 `python -m sodamem_cli`（`-m` 才把 cwd 放进 `sys.path[0]`）。
> 3. editable install 的真实指向也测错了：实测
>    `sys.path[0]=<venv>/bin` 时 `import server` 落在
>    **`/Users/aaron.w/Desktop/SodaMem-worktrees/issue-9/server/__init__.py`**
>    （issue-9 worktree），不是主仓库。初版看到主仓库路径，只是因为当时 cwd 恰好
>    是主仓库。结论不变、而且更尖锐：**唯一决定跑哪份源码的是 cwd**。

本 worktree **没有 venv**。用 issue-9 的，绝对路径，`bin` 放 PATH 最前：

```bash
V=/Users/aaron.w/Desktop/SodaMem-worktrees/issue-9/.venv
W=/Users/aaron.w/Desktop/SodaMem-worktrees/issue-22
export PATH="$V/bin:$PATH"
export SODAMEM_HOME="$(mktemp -d)/sodamem"     # 一次性，绝不碰 ~/.sodamem
cd "$W"                                        # 必须！见上面的修订 A

# —— 前置自证：我到底在跑哪份源码 ——
python -c "import server, sodamem_cli; print(server.__file__); print(sodamem_cli.__file__)"
# 必须打印 $W/server/__init__.py。这一行输出必须贴进 PR。
# daemon 用 subprocess.Popen 且不传 cwd，子进程继承同一个 cwd，因此守护进程
# 跑的也是同一份源码 —— 而且它会自己在 daemon.log 里签名（console_mount 那条
# INFO 打印 `$W/console/dist`），那是比父进程自证更硬的证据。

P=$(python -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1])")
# 拿一个空闲端口。不要用默认端口：ensure() 见到有人应答会直接返回
# started=False 且不 spawn，那样日志里一行都不会有，证据自动作废。

python -m sodamem_cli daemon ensure --api-url "http://127.0.0.1:$P"
# 输出里 running 和 started 必须都是 true —— 否则这次运行不算证据
grep -c 'serving on interpreter' "$SODAMEM_HOME/daemon.log"
grep -n  'serving on interpreter' "$SODAMEM_HOME/daemon.log"
python -m sodamem_cli daemon stop --api-url "http://127.0.0.1:$P"
```

预期差异：

| | 计数 | 行的形态 |
|---|---|---|
| before | 1 | `INFO:     serving on interpreter /…/python (python 3.11.13, chromadb …)`（uvicorn 的 formatter，无时间戳，无 logger 名） |
| after | 1 | `2026-… INFO server.app: serving on interpreter /…/python (python 3.11.13, chromadb …)`（#19 的 formatter） |

before 轮可以在动代码之前跑（worktree 此时 == origin/main）。**修订 A 补充**：验收时
只跑了 after 轮 —— 因为 after 的 `daemon.log` 里 uvicorn 自己的 `INFO:     Started
server process` 就紧挨着我们这一行，两种 formatter 并排摆着，对比不需要第二次运行；
而 before 轮若改用主仓库 cwd 会把写入引到 `main` 仓库目录（`__pycache__`），不值得。

**诚实说明**：这是一次清理，before 轮**本来就是绿的**。这份 before/after 不是
"没有改动就会失败"的证明 —— 它证明的是另一件同样必要的事：**这一行确实换了投递
路径，并且换完之后仍然真的落在了 `daemon.log` 里，且只落一次**。#14 的教训是
"单测绿不等于那行到了文件里"，能证伪这次搬家的只有真实文件本身。

---

## Acceptance Criteria

- [ ] **AC1 — emit 换到模块 logger。** `server/app.py` 的
      `_log_runtime_identity()` 用文件顶部的 `logger`（`server.app`）发这条记录；
      全仓库（排除 `.venv`、`node_modules`、`specs/`）`grep -rn 'uvicorn\.error'`
      在**非测试源码**里零命中，`tests/test_daemon_logging.py` 里的三处保持原样。
      外层 `try/except Exception` 仍在。
      *"没有改动就会失败"如何演示*：**不适用** —— 这条 AC 就是改动本身，验证方式
      是 diff + grep，不是红灯。硬要造一个红灯测试来"证明"这条，等于为清理发明
      一个新约束，正是本 issue 反对的东西。

- [ ] **AC2 — docstring 只减不增。** 第 3、4 段（现 76-89 行，讲 logger 选择与
      "变通现在多余"的两段）整段消失；第 1 段（#14 的来龙去脉）、第 2 段（#13：
      不进响应体）、第 5 段（裸 except 的理由）**逐字保留**；**没有新增任何段落或
      行内注释**，`create_app()` 里 111-114 行的顺序说明保持不动。
      *如何演示*：**不适用**（同 AC1），验收靠读 diff。Iris 会逐段核对，
      新增解释 logger 管道的文字一律判 FAIL。

- [ ] **AC3 — 既有单测门仍然是门。** `tests/test_daemon_command.py::
      test_create_app_logs_the_running_interpreter` 改用
      `logger="server.app"` 后通过；然后**实测**：把 `_log_runtime_identity()` 里
      那次 `logger.info(...)` 整段删掉（或改成 `pass`），重跑该测试，**必须红**，
      失败输出贴进 PR。跑完把改动还原。
      *这是本次唯一一条真正的"没有它就会失败"演示。*

- [ ] **AC4 — 真实 `daemon ensure` 里存在，且只有一条。** 按上面的配方跑 after 轮，
      PR 里同时给出：(a) `import server` 的路径自证输出指向本 worktree；
      (b) `daemon ensure` 的 started=true 输出；
      (c) `grep -c 'serving on interpreter' daemon.log` 的结果**恰好为 1**；
      (d) 该行的完整文本，其中含 `server.app`（证明走的是新路径，不是 uvicorn 的
      formatter）。只有单测断言、拿不出真实 `daemon.log` 的 PR 直接判 FAIL ——
      这正是 #14 当初栽的那个跟头。

- [ ] **AC5 — 没有第二条投递路径。** 两处证据都要：
      (a) AC4 的真实日志里计数是 1 不是 2；
      (b) `tests/test_daemon_logging.py::test_no_duplicate_lines_against_uvicorns_real_config`
      **一个字都不改**仍然通过（它已经在 uvicorn 的真实 logger 树上断言
      `server.app` 的记录恰好渲染一次）。
      *如何演示"没有它就会失败"*：**不适用** —— 这条防的是回归，不是修复。

- [ ] **AC6 — 全套零退化。** `cd $W && /Users/aaron.w/Desktop/SodaMem-worktrees/issue-9/.venv/bin/python -m pytest -q`
      结果为 **857 passed, 1 skipped**，与 main 基线**逐字一致** —— 本次不新增测试，
      所以数字必须原样不动。若实现者认为需要新增测试，必须先说明为什么 AC3 的现有
      门不够，并在 PR 里写出新数字与增量来源；不解释就改数字判 FAIL。

- [ ] **AC7 — CHANGELOG 不再撒谎。** `## [Unreleased]` 里不再有任何句子声称
      `uvicorn.error` 变通仍然存在或"留待 follow-up"；同一段里 `create_app()`
      "lowers the `server` / `sodamem` loggers to `INFO`" 中多余的 `/ sodamem`
      删除（与同段后文及 `server/logging_setup.py:46` 一致）。
      **不新增 `### Fixed` 条目**（用户可见行为无变化）。

- [ ] **AC8 — 改动面与理由相称。** `git diff origin/main --stat` 只出现四个文件：
      `server/app.py`、`tests/test_daemon_command.py`、`CHANGELOG.md`、
      `specs/issue-22.md`。`server/logging_setup.py`、`tests/test_daemon_logging.py`、
      任何 `specs/issue-1*.md` 被改动 → FAIL。`server/app.py` 的净变化不超过
      "删 14 行 docstring + 改 1 行 emit"的量级。

**PARTIAL = FAIL。** 尤其是 AC4：这次改动的全部理由是"别让文字骗人"，那么用一份
真实的 `daemon.log` 证明我们自己写下的新说法为真，是最低限度的自洽。

---

## 验收记录（Iris，2026-08-19，HEAD `4f10e54`）

全部 8 条 AC 独立复核通过，证据见 PR 讨论。要点：

| AC | 证据 |
|---|---|
| AC1 | `server/app.py` 第 85 行 `logger.info(...)`；非测试源码 `uvicorn.error` 零命中 |
| AC2 | 只删第 3、4 段，其余三段逐字保留，零新增 |
| AC3 | 在内存里把 `_log_runtime_identity` 换成 no-op（不改仓库），`test_create_app_logs_the_running_interpreter` 变红：`AssertionError: []` |
| AC4 | 真实 `daemon ensure`（临时 `SODAMEM_HOME`，端口 55296），`grep -c` = **1**，行为 `2026-08-19 09:02:57,573 INFO server.app: serving on interpreter …`；同一份日志里 `server.console_mount` 打印 `…/issue-22/console/dist`，守护进程自证跑的是本 worktree |
| AC5 | 同上计数 1；`tests/test_daemon_logging.py` blob 与 origin/main 完全相同且全绿 |
| AC6 | `857 passed, 1 skipped` |
| AC7 | CHANGELOG 不再有 follow-up 措辞，`/ sodamem` 已删 |
| AC8 | `git diff origin/main --stat` 恰好四个文件；`tests/test_daemon_logging.py`、`specs/issue-14.md`、`specs/issue-19.md`、`server/logging_setup.py` 的 blob hash 与 origin/main 逐字相同 |
