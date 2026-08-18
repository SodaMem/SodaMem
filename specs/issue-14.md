# Spec: `daemon ensure` 从 PATH 解析 uvicorn，守护进程可能跑在另一个解释器上

Record: GitHub Issue #14 — https://github.com/SodaMem/SodaMem/issues/14
Branch: fix/14-daemon-interpreter
Worktree: /Users/aaron.w/Desktop/SodaMem-worktrees/issue-14
相关: #13（已合并，PR #16 —— 本 bug 制造的现场）、#15（同一现场的缓存后果）

## Problem

`sodamem_cli/daemon.py:168` 的 `_serve_command()`：

```python
uvicorn = shutil.which("uvicorn")
base = [uvicorn] if uvicorn else [sys.executable, "-m", "uvicorn"]
```

`shutil.which` 搜的是 **PATH**，不是"SodaMem 装在哪个环境里"。整个仓库里能离开当前解释器的
只有这一行。

它为什么能真的跑起来（而不是失败得很明显）：uvicorn 的 CLI 会
`sys.path.insert(0, app_dir)`，`app_dir` 默认 `"."`
（`uvicorn/main.py:548-549`，venv 内 uvicorn 0.51.0 已核对）。所以一个 homebrew 解释器
在仓库 cwd 下能 import 到 `server`，服务照常起来、照常应答健康检查 —— 用的却是**它自己的
依赖树**，尤其是**它自己的 chromadb**。

后果不是外观问题，是**不可逆的数据后果**：chroma 在 open 时会把 store 的 schema 向前迁移。

| store | `sysdb` migration | 在 chromadb 1.1.1 下打开 |
|---|---|---|
| 被走错解释器（1.5.8）打开过一次 | **10**（`00010-collection-schema`） | ❌ `PanicException`（rust/sqlite/src/db.rs:157） |
| 只被安装的 1.1.1 打开过 | **9**（`00009-segment-collection-not-null`） | ✅ 正常 |

一次误起就**永久**把 store 迁到了新 schema，**没有回退路径**，正确安装的 SodaMem 从此打不开
自己的记忆库。这条链子实际制造了 #13（表现为 rust panic 逃逸成 500，追了一下午的假 chroma
bug）、#15（半死 store 被缓存），以及已发布 README 里一句错误的公开结论。

`sys.executable` 分支本来就是对的。**要删的是 `which` 分支。**

### 「which 分支有没有必须存在的场景」—— 明确回答：没有

按约束逐个检查过，找不到需要它的环境：

1. **当前解释器有 uvicorn** → `python -m uvicorn` 必然可用（uvicorn 装了
   `uvicorn/__main__.py`，`<venv>/bin/python -m uvicorn --version` 已实测）。which 分支
   在这里只可能选到**别的**环境，即纯粹的坏处。
2. **当前解释器没有 uvicorn**（`pip install sodamem` 基础安装 —— uvicorn 在 `[server]`
   extra 里，见 pyproject `optional-dependencies.server`）→ which 分支能找到的只有**别的**
   环境的 uvicorn，而那个环境既没有 SodaMem 的依赖，也只能靠 cwd 恰好是源码检出才 import
   得到 `server`。它要么起不来（一个难懂的 ImportError），要么起得来（就是本 issue 的事故）。
   **两种结果都比"清楚地说你没装 `sodamem[server]`"差。**
3. **容器 / Dockerfile** 直接 CMD 调 uvicorn，不走这条代码路径，不受影响。

所以删掉分支不会让任何人失去能力；它只会让第 2 种情况从"沉默地跑错"变成"明确地报错"。
为此本次补一个**开跑前的前置检查**（下方 Implementation Path §2）—— 这是删掉分支后唯一
需要补的东西，不是新机制。

## Value

- 守护进程**只可能**跑在运行 CLI 的那个解释器上。用户的 store 不会再被一个碰巧在 PATH 上的
  chromadb 迁走。这是本次唯一重要的价值，其余都是附带。
- 删代码而不是加代码：一个分支消失，一个"取决于环境"的行为变成确定行为。
- 没装 `sodamem[server]` 的用户拿到一句能照做的话，而不是日志里的 `code 1`。
- 一条启动日志把"到底是哪个解释器、哪个 chromadb 在服务"从**一下午**变成**一眼**。

## Scope

| 文件 | 改动 |
|---|---|
| `sodamem_cli/daemon.py` | `_serve_command()` 删掉 `shutil.which` 分支；`ensure()` 增加 uvicorn 前置检查；启动日志头记录解释器与实际命令；模块内 `shutil` 若无其他用处则删 import |
| `server/app.py` | `create_app()` 内一条 INFO 启动日志：`sys.executable` + chromadb 版本（复用 `server/stores.py:_installed_chroma()`） |
| `tests/test_cli.py`（或新建 `tests/test_daemon_command.py`） | AC1–AC5 的载体 |

**不改：** `--workers 1` 与 ADR 0001 §2 相关的任何东西；`ensure()` 的幂等/健康检查循环语义；
`status()` / `stop()` / pid 文件握手；`Health` 模型与 `/health` 响应体（理由见下）；
任何路由签名；`sodamem_cli/install.py`（理由见下）。

**不加任何配置开关。**

### 决定一：`install.py` 的两处 —— 本次不改，另立 issue

`install.py:40`（`shutil.which("sodamem-mcp")`）与 `install.py:200`（`shutil.which("sodamem")`）
是同一个 pattern，但**不是同一个问题**：

- 它们不 spawn 进程，而是把一条命令**写进别人的配置文件**（Cursor / Claude Code / Codex /
  Copilot），在未来某个时刻、某个 PATH 已经变了的环境里被执行。
- MCP 的 **local 模式直接打开 store**（`mcp_server/README.md`），所以同一个 schema 迁移风险
  在那边成立，而且**是持久化的配置**。
- 但 `shutil.which` 返回的已经是绝对路径，今天的行为是"钉死在安装时 PATH 上碰巧那一个"——
  它既不是"永远用当前环境"，也不是"永远重新解析"，是两个自洽方案之外的第三种。

**判断：值得修，但不属于本次。** 三条理由：

1. **风险量级差一个数量级。** daemon 侧命中条件是"机器上另有一个带 uvicorn 的 python"——
   uvicorn 是极常见的传递依赖，这个条件在开发机上几乎默认成立，而且后果**不可逆**
   （store schema 前移）。install 侧命中条件是"用户装了**两份 SodaMem**"（`sodamem-mcp` /
   `sodamem` 只由 SodaMem 自己安装），概率低得多，且后果**可逆**——重跑一次 `sodamem install`
   就把配置写回去了。把一个 P0 和一个 P2 绑在一起改，只会让本次那条"分支被删掉了"的门变糊。
2. **正确解法不是把 `which` 换成 `sys.executable -m`。** 那样会在"装了 sodamem 但没装
   `[mcp]` extra"的环境里写出一条永远跑不起来的配置。install 侧该走的是**第三条路**：
   优先取 `Path(sys.executable).parent / "sodamem-mcp"`（本环境自己的 console script，
   shebang 天然钉住正确解释器），不存在再退到 `sys.executable -m mcp_server`。这是另一套
   实现和另一套验收（四种客户端方言 × dry-run 输出）。
3. **它自带一个本 spec 回答不了的迁移问题**：那些**已经**被写进用户配置的错误绝对路径，
   要不要在下次 `sodamem install` 时识别并重写？这是产品决定，不是 bugfix。

**follow-up issue 应当包含**（供 Felix 立单）：
- 标题：`sodamem install writes a PATH-resolved command into client configs, which can pin the wrong interpreter`
- 覆盖 `install.py:40` `mcp_command()` 与 `install.py:200` `_hook_argv()` 两处；
- 采用"同目录 console script → `sys.executable -m` 兜底"的解析顺序，并说明为什么不是
  裸 `sys.executable -m`（缺 extra 时写出跑不起来的配置）；
- 明确回答：venv 被重建/移动后配置失效如何处理（重跑 install？启动时自检？）；
- 明确回答：是否重写已有配置里的旧路径；
- 验收要求 dry-run 的四个客户端输出里，命令的可执行部分位于 `Path(sys.executable).parent`
  之下或以 `sys.executable` 开头，且 PATH 里放诱饵 `sodamem` / `sodamem-mcp` 时不被选中；
- 引用 #14 与 `mcp_server/README.md` 的 local 模式说明，写明"同一个 schema 迁移风险，只是
  概率更低、可逆"。

### 决定二：诊断 —— 只做日志，**不动 `/health`**

issue 提的第 1 条建议（把 `sys.executable` 和 chromadb 版本报进 `/health` 或
`daemon status`）价值是真的：它把这次的排查从一下午压到几秒。但**不能就这么放进 `/health`**：

- `/health` 是**未认证**的（`server/app.py:213` docstring），而 `server/settings.py` 的 `host`
  默认 `0.0.0.0`；
- #13 刚刚为此立了规矩：错误 body **不得泄露文件系统路径**（specs/issue-13.md AC8(v)）。
  `sys.executable` 就是一个绝对路径，而且它同时暴露用户名、venv 布局、Python 版本。
  在刚刚收紧的同一个暴露面上，掉头把解释器路径放进一个匿名可读的端点，是自相矛盾。

正确做法（把它放到一个**需要认证的** ops 端点，或让 `daemon status` 经认证通道取）需要动
模型、路由、OpenAPI、CLI 渲染和文档，并且要先决定"未认证时降级成什么"。那是另一个改动。

**本次做的是零暴露、覆盖同样场景的那一半：日志。** 两条，都落在 `~/.sodamem/daemon.log`：

1. `ensure()` 写的启动分隔行里带上 `sys.executable` 与**将要执行的完整命令**——
   进程秒退时（还没轮到服务自己打日志）也留下证据；
2. 服务自己在 `create_app()` 里打一条 INFO：运行中的 `sys.executable` + chromadb 版本。
   这条覆盖**不是我们启动的**守护进程（docker、手工 uvicorn），而那正是"神秘守护进程是谁"
   最难查的情形。

注意这两条的区别不能糊：CLI 只知道**它打算启动的**解释器，服务知道**实际在跑的**。
`status()` 报告的是"谁在应答"，可能是另一个安装启动的——所以**绝不**在 `status()` 的输出里
用 CLI 自己的 `sys.executable` 冒充守护进程的解释器。那正是这次让人追错方向的谎言形状。

issue 提的第 2 条（主动暴露 schema-forward 的 store）：**本次不做**。#13 已经在 open 失败时
点名 chromadb 版本与 migration 号，#15 在跟"半死 store 被缓存"。再加一层扫描既重复又要回答
"扫哪些 user、启动时扫还是定时扫"，与本次约束（不改启动语义）冲突。

## Implementation Path

### 1. 删掉分支

```python
def _serve_command(host: str, port: int) -> list[str]:
    ...
    return [sys.executable, "-m", "uvicorn", "server.app:build", "--factory",
            "--host", host, "--port", str(port), "--workers", "1"]
```

`--workers 1` 与 `--factory` 一字不动。docstring 里除了原有的 ADR 0001 §2 说明，再写清
**为什么这里永远是 `sys.executable`**（一句话 + issue 号），否则下一个人会觉得"用 PATH 上的
uvicorn 更快"而把它加回来。

### 2. 前置检查代替 which 分支

`ensure()` 在 `subprocess.Popen` **之前**，用 `importlib.util.find_spec("uvicorn")`
（只查不 import，代价接近零）判断当前解释器是否装了 uvicorn。没有就直接返回既有失败字典形状
`{"running": False, "url": ..., "started": False, "error": ...}`，**不 spawn、不写 pid 文件**。
错误文本必须点名两件事：跑的是哪个解释器（`sys.executable`），以及怎么修
（`pip install 'sodamem[server]'`）。

这条信息**只走 CLI 的返回值**（本机命令行输出），不进任何 HTTP body，所以不涉及路径泄露那条规矩。

### 3. 两条日志

- `ensure()` 里已有的那行 `--- sodamem daemon start <ts> ---` 追加
  `interpreter=<sys.executable>` 与 `command=<shlex.join(command)>`。
- `create_app()` 里一条 `logger.info(...)`，含 `sys.executable` 与
  `_installed_chroma()[0]`（`server/stores.py:112` 已有的 best-effort 探针，chromadb 是可选
  extra，没装时返回 `None`，**不得**因此抛异常或打 WARNING）。这条日志绝不能让 app 起不来。

### 4. `shutil` import

`daemon.py` 里 `shutil` 只有这一处用。删干净，不要留一个没人用的 import 当"以后可能要"的暗示。

## Acceptance Criteria

- [ ] **AC1（回归门 —— 必须能真的抓住分支被加回来）**：`_serve_command()` 的单测在
      **PATH 被投毒**的条件下运行：`tmp_path` 里造一个可执行文件名叫 `uvicorn`
      （内容随意，chmod +x），`monkeypatch.setenv("PATH", str(tmp_path))`。断言三件事，
      缺一不可：
      1. **测试环境自证有效**：`shutil.which("uvicorn") == str(诱饵)`。没有这一条，
         整个测试可能只是因为"PATH 上恰好没有 uvicorn"而通过 —— 那是偶然通过，不是门。
      2. `cmd[0] == sys.executable` 且 `cmd[1:3] == ["-m", "uvicorn"]`。
      3. 诱饵目录的字符串**不出现在** `cmd` 的任何一项里。
      把 `which` 分支加回来时，这个测试必须**失败**（实现者需在 review 记录里贴出"临时还原
      旧代码 → 该用例 red"的输出，作为门有效的证据）。
- [ ] **AC2（命令其余部分未被顺手改坏）**：同一命令里 `--workers 1` 存在且值为 `"1"`、
      `--factory` 存在、app target 为 `server.app:build`、`--host`/`--port` 与入参一致
      （用非默认的 host/port 断言，避免默认值巧合）。ADR 0001 §2 不许被这次改动动到。
- [ ] **AC3（缺 uvicorn 时不 spawn）**：让 `find_spec("uvicorn")` 返回 `None`（monkeypatch
      接缝），调用 `ensure()`：断言返回 `running is False` / `started is False`，
      错误文本同时含 `sys.executable` 与 `sodamem[server]`，且
      **`subprocess.Popen` 一次都没被调用**、**pid 文件没有被写出**
      （`SODAMEM_HOME` 指到 `tmp_path`）。
- [ ] **AC4（启动日志头带解释器与命令）**：`SODAMEM_HOME=tmp_path` 下让 `ensure()` 走到
      spawn（Popen 打桩，不真起进程），断言 `daemon.log` 内容含 `sys.executable`
      与 `-m uvicorn`，且不含诱饵路径。
- [ ] **AC5（服务自报家门）**：`create_app()` 时 `caplog` 在 INFO 级捕获到一条日志，含
      运行中的 `sys.executable`；且 `_installed_chroma()` 抛异常时（打桩）`create_app()`
      仍正常返回 app —— 诊断不得成为启动失败的新来源。
- [ ] **AC6（`/health` 未被扩大暴露面）**：断言 `/health` 响应的键集合仍恰好是
      `{status, version, schema_version, auth}`，其值里**不含**任何绝对路径（不含
      `sys.executable`、不含 `os.sep` 开头的串）。这是对 #13 AC8(v) 那条规矩的显式守门，
      也钉住"诊断只走日志"这个决定，防止后来者顺手把解释器塞进匿名端点。
- [ ] **AC7（真起真答 —— 不是对字符串列表的断言）**：用
      `/Users/aaron.w/Desktop/SodaMem-worktrees/issue-9/.venv` 的绝对路径解释器，
      `SODAMEM_HOME` 与 `SODAMEM_DATA_ROOT` 都指向 scratchpad 下的**全新**目录
      （**不得**指向任何既有 store，尤其不得指向 schema-forward 的那个），跑两轮，
      两轮的状态码与关键输出都要贴进 review 记录：
      1. **正常轮**：venv 的 `bin` 放在 PATH 最前，
         `<venv>/bin/python -m sodamem_cli daemon ensure --api-url http://127.0.0.1:<free>`
         → `running: true`；随后一个真实 HTTP 请求（`/health` 200，且至少一个
         `/v1/context` 请求得到 200 或明确的类型化响应）；
      2. **投毒轮**：先 `daemon stop`，把一个诱饵目录（内含可执行的 `uvicorn` 脚本，
         执行即 `exit 1` 并 `touch` 一个 marker 文件）放在 PATH **最前**，再跑一次
         `daemon ensure` → 仍然 `running: true`，marker 文件**不存在**，
         且 `ps -p <pid> -o command=` 输出的第一个词等于 `<venv>/bin/python`。
      收尾必须 `daemon stop`，不留游荡进程。
- [ ] **AC8（日志在真实运行里确实可读）**：AC7 正常轮之后，`$SODAMEM_HOME/daemon.log` 里
      同时能读到 (a) `ensure` 写的 `interpreter=<venv python>`，(b) 服务自己打的那条含
      解释器与 chromadb 版本（该 venv 为 chromadb **1.1.1**）的 INFO。贴原文片段。
      这一条是本次"几秒 vs 一下午"的兑现证据。
- [ ] **AC9**：`sodamem_cli/daemon.py` 里不再出现 `shutil`（import 与调用都没有），
      且全仓库 `grep -rn "shutil.which" --include=*.py` 的结果**恰好剩 `install.py` 的两处**
      —— 明确记录本次的 scope 边界，将来多出第三处能被看见。
- [ ] **AC10**：现有全量测试仍通过 —— origin/main 基线 **835 passed / 1 skipped**，
      新增用例后应为 **835 + 新增 passed / 1 skipped**，无新增失败、无新增 skip。
      跑测试用 `/Users/aaron.w/Desktop/SodaMem-worktrees/issue-9/.venv` 的绝对路径。

**PARTIAL = FAIL。** AC1（含"环境自证有效"与 red 证据）、AC6、AC7 投毒轮任一缺失即为未通过。
