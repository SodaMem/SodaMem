# Spec: 守护进程下 `server/` 的每一条 INFO 日志都被静默丢弃

Record: GitHub Issue #19 — https://github.com/SodaMem/SodaMem/issues/19
Branch: fix/19-daemon-logging
Worktree: /Users/aaron.w/Desktop/SodaMem-worktrees/issue-19
相关: #14（已合并 —— 本 bug 的发现现场，其诊断行至今骑在 `uvicorn.error` 上绕开本 bug）

> 本 worktree 无 venv。所有命令用 `/Users/aaron.w/Desktop/SodaMem-worktrees/issue-9/.venv`
> 的绝对路径；跑守护进程时把它的 `bin` 放在 `PATH` 最前。

## Problem

### 现象

`sodamem daemon ensure` 起来的服务，`server/` 里所有 `logger.info` / `logger.debug`
一条都不会出现在 `~/.sodamem/daemon.log` 里。最要命的一条是 `server/app.py:174` 的
**每请求行**：

```python
logger.info("%s %s -> %d in %.1fms (rid=%s)", ...)
```

用户说"它什么都没返回，我不知道为什么"时，这就是你要看的那一行。它大概从来没在任何一个
真实部署的守护进程日志里出现过。

### 机制（实测，非推断）

uvicorn 0.51.0，本机 venv 内实跑 `Config("server.app:build", factory=True)`
（`Config.__init__` 会调 `configure_logging()`）后的进程 logging 状态：

```
root.handlers:                    []
root.level:                       30 (WARNING)
uvicorn        handlers/propagate: [StreamHandler <stderr>]  False
uvicorn.error  handlers/propagate: []                        True   level=20
uvicorn.access propagate:                                    False
server.app     effective level:   WARNING
logging.lastResort level:         WARNING
```

**这里有两个独立的杀手，issue 正文只写了第二个：**

1. **level 关**（先命中，也是更根本的那个）。uvicorn 的 `LOGGING_CONFIG` 里**没有
   `root` 键**，dictConfig 因此完全不碰 root，root 停在 Python 默认的 `WARNING`。
   `server.app` 自己没设 level，`getEffectiveLevel()` 沿链上溯到 root = `WARNING`。
   `logger.info(...)` 在 `Logger.isEnabledFor()` 就返回 False —— 记录**根本没被创建**，
   连 handler 都没走到。
2. **handler 关**。就算 level 放行了，root 上一个 handler 都没有，记录走到
   `logging.lastResort`，那是个 `level=WARNING` 的 `_StderrHandler`，INFO 照样丢。

> **结论：只加 root handler 修不好这个 bug。必须同时降 level。** 任何只做其中一件事的
> 实现都会通过"handler 装上了"的断言而在真实守护进程里继续丢日志。

`logger.warning`（如 `server/app.py:120`）能出现在日志里，纯粹是因为它同时越过了这两道
WARNING 门槛 —— 走的是 `lastResort`，不是任何我们配置过的东西。

### 为什么一直没人发现

- **pytest 下正常。** 实测 pytest 运行中 root 上永远有 4 个 handler
  (`_LiveLoggingNullHandler`, `_FileHandler /dev/null`, 2×`LogCaptureHandler`)，
  `caplog.at_level()` 又会临时把 level 压到 INFO。
- **手起的 `uvicorn` + `--log-level info`** 只改 `uvicorn.*` 三个 logger，不改 root ——
  但人在终端里看到 access log 刷屏，容易误以为"日志是通的"。

### 受影响站点

| 文件 | 行 | 级别 |
|---|---|---|
| `server/app.py` | 174 | INFO — 每请求行，本 issue 的主角 |
| `server/console_mount.py` | 76, 89 | INFO — console 挂没挂上 |
| `server/control.py` | 732 | DEBUG |
| `sodamem/` 下 12 个 logger，13 处 INFO/DEBUG | — | 同一个 root，同一个病，但**本次不修**（修订 A） |

`server/control.py`、`server/stores.py`、`server/webhooks.py` 的 WARNING/ERROR 目前靠
`lastResort` 侥幸可见 —— 那是运气，不是设计。

## Value

- 守护进程的日志里真的有每请求行。用户报"recall 返回空"时，能从日志读出请求到没到、
  走了哪条路由、多少毫秒、rid 是多少。这是本次唯一重要的价值。
- ~~`sodamem.*` 的 ingest/extractor 诊断（13 处 INFO）一并变得可见。~~
  **【修订 A，验收时撤回 —— 见文末「Spec 修订记录」】** 本次范围只有 `server`。
  `sodamem` 保持 WARNING。
- Docker 部署同样受益（见下方决策），不需要第二次修。

## 决策：选 **Option 1 —— 在 `create_app()` 里配置日志**

守卫条件是 **root logger 上没有任何 handler**。

### 为什么不是 Option 2（`_serve_command()` 传 `log_config`）

Aaron 的弱倾向是 2，理由（策略跟 `--workers 1` 待在一起）是对的。但落到这个代码库的
具体形态上，它有三处硬伤：

1. **Docker 完全绕过它，这是构造性的。** `Dockerfile` 的 CMD 是
   `exec uvicorn server.app:build --factory --workers 1 ...`，根本不经过
   `sodamem_cli/daemon.py:_serve_command()`。Option 2 修完，自建部署的容器日志依旧丢
   INFO —— 按 issue 自己的话说，那是 partial fix。要补齐就得在 Dockerfile CMD 里再加一
   次同样的 flag，两处策略手工保持同步，而漂移时**没有任何测试会发现**（CMD 是字符串）。
2. **`_serve_command()` 只能传路径，不能传 dict。** 它 spawn 的是
   `sys.executable -m uvicorn`（#14 定死的、不可回退的约束）。uvicorn CLI 只有
   `--log-config PATH`，且 `Config.configure_logging()` 对 `.json` 的处理是
   `json.load` → `dictConfig`。所以必须落一个 JSON 文件：要么进包
   （`[tool.setuptools.package-data]` 现在只有 `sodamem = ["py.typed"]`，得改，
   `tests/test_packaging.py` 也得跟），要么运行时往 `state_dir()` 写一个 —— 两条路都是
   为了传一个 dict 而新增一个文件生命周期。
3. **`--log-config` 是替换，不是合并。** 传了它，uvicorn 的默认 `LOGGING_CONFIG` 整个
   不生效。那份 JSON 必须把 `uvicorn` / `uvicorn.access` 两个 logger、
   `uvicorn.logging.DefaultFormatter` / `AccessFormatter` 两个 `()` 工厂原样抄一遍。
   我们于是把 uvicorn 的内部默认值 vendored 进了自己的仓库，uvicorn 一升级就静默漂移，
   而且**恰好会打掉 #14 的诊断行**（它依赖 `uvicorn.error → uvicorn` 那条 handler 链）。

### 为什么不是 Option 3（承认丢弃，删掉死日志）

诚实，但方向错了。`server/app.py:174` 提升到 WARNING 意味着**每一个成功请求都是一条
警告** —— 那样的日志等于没有日志。`console_mount.py:76` 的"console 没挂上，这是正常的"
按定义就是 INFO。真正要删的不是这些行，是让它们不生效的那个配置空洞。

### Option 1 的守卫条件为什么恰好是对的

"root 上没有 handler" 精确等价于"这个进程里没人配过日志"，而这正是唯一该由我们出手的
场合。三条路径实测过：

| 场景 | 调用时 `root.handlers` | 行为 |
|---|---|---|
| `daemon ensure`（uvicorn `configure_logging` 先跑，再 `load_app`→`create_app`） | `[]` | **装** ✅ |
| Docker CMD（同一条 `uvicorn --factory` 路径） | `[]` | **装** ✅ 免费修好 |
| pytest（任何测试里） | 4 个 handler | **不装**，完全 no-op ✅ |

顺序保证来自 uvicorn 自身：`uvicorn.main.run()` 是 `Config(...)`（`__init__` 里
`configure_logging()`）→ `config.load_app()`。我们的代码永远跑在它的 dictConfig 之后，
不会被覆盖。

对"`create_app()` 不该因谁 import 它而行为不同"这个反对意见的回答：`create_app()` 已经
在做进程级副作用了 —— `require_auth_configured()`、`acquire_data_root_lock()`、
`_log_runtime_identity()`。它是**服务的 application factory**，不是库 API。而且守卫
条件本身就把"有人配过日志"的情况让开了，行为差异恰恰是被 root 状态而非 import 者决定的。

## Scope

| 文件 | 改动 |
|---|---|
| `server/logging_setup.py` | **新增**。一个函数，约 25 行 + 注释 |
| `server/app.py` | `create_app()` 顶部调用一次（在 `_log_runtime_identity()` 之前） |
| `tests/test_daemon_logging.py` | **新增**。AC1/AC3/AC4 的门 |
| `CHANGELOG.md` | Fixed 一条 |

不改：`Dockerfile`、`sodamem_cli/daemon.py`、`tests/_service.py`、`pyproject.toml`。

## Implementation Path

### 1. `server/logging_setup.py`

一个函数 `configure_logging_if_unconfigured() -> bool`（返回是否真的装了，供测试断言）：

```
root = logging.getLogger()
if root.handlers:            # 有人配过（pytest / 宿主应用 / 第二次调用）→ 什么都不做
    return False
handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(logging.Formatter(<见下>))
root.addHandler(handler)
logging.getLogger("server").setLevel(logging.INFO)   # 只有 server；sodamem 见修订 A
return True
```

四条**必须**遵守的约束，每条都对应一个已知的失败模式：

- **`StreamHandler(sys.stderr)`，绝不能用 `FileHandler`。**
  `logging.config.dictConfig()` 在非 incremental 分支会调 `_clearExistingHandlers()`，
  它 `logging.shutdown()` 掉全局 handler 表里的每一个 handler。`tests/_service.py` 的
  顺序是 `create_app()` 在前、`uvicorn.Config(...)` 在后，所以我们装的 handler**会**被
  close 一次。`StreamHandler.close()` 不关底层流，`FileHandler.close()` 关 —— 后者会让
  之后每一次 emit 抛 `ValueError: I/O operation on closed file`。
  spawn 时 `daemon.py` 已经把子进程的 stdout/stderr 重定向进 `daemon.log`，我们不需要
  也不应该自己开文件。
- **root 的 level 不动，只动 `server` / `sodamem` 两个具名 logger。**
  root 留在 WARNING，第三方（chromadb / httpx / openai / urllib3）就继承 WARNING，
  不会灌爆日志。handler 不设 level（NOTSET）。选择完全由 logger level 完成 ——
  没有 Filter，没有分支，没有特殊情况。
- **不加任何开关。** 没有 `SODAMEM_LOG_LEVEL`，没有 `SODAMEM_LOG_FORMAT`。
  一个诚实的默认值优于一个旋钮；"日志能不能工作"更不该是配置项。
- **幂等。** 第二次调用因 `root.handlers` 非空而直接返回 False。

格式（守护进程日志需要时间戳，uvicorn 自己的 `%(levelprefix)s %(message)s` 没有）：

```
%(asctime)s %(levelname)s %(name)s: %(message)s
```

跟 uvicorn 的行视觉上可区分，是好事：一眼能看出哪行是谁写的。

### 2. `server/app.py`

`create_app()` 第一件事就调它 —— 必须在 `_log_runtime_identity()` 和
`acquire_data_root_lock()` 之前，否则这两处自己的日志赶不上。

### 3. 不会出现重复行 —— 逐条核对过 propagate

- `uvicorn` → `propagate=False`，有自己的 handler。记录到不了 root。
- `uvicorn.access` → `propagate=False`。同上。
- `uvicorn.error` → `propagate=True` 但**父 logger `uvicorn` 是 `propagate=False`**，
  链子在 `uvicorn` 处终止。**到不了 root。**
- `server.*` / `sodamem.*` → 自己没 handler，propagate 到 root，被唯一那个 handler
  处理一次。

所以 root 和 `uvicorn` 上各有一个 handler 也不会双打。AC4 会把这条钉死。

### 4. #14 的 `uvicorn.error` 变通：**本次不动，另开 issue**

`server/app.py:94` 的 `_log_runtime_identity()` 故意在 `uvicorn.error` 上打这一行，
就是为了绕开本 bug。修完之后它确实不再必要 —— 那行可以搬回 `logger.info(...)`。

**但本 PR 不搬**，三个理由：

1. 它今天工作正常，修完之后**依然**工作正常且**不产生重复行**（见上一节的 propagate
   链：`uvicorn.error` 的记录停在 `uvicorn`，不会再被 root 打一遍）。零风险留着。
2. 搬动会改 `tests/test_daemon_command.py:170` 那条 `caplog.at_level(..., logger="uvicorn.error")`
   的断言目标，把一个与本 fix 无关的行为改动混进这个 diff。
3. 那条测试的注释本身记录了一个真实的 suite 级顺序依赖（`log_level="error"` 把
   `uvicorn.error` 全局钉在 ERROR）。拆掉它得先把那个坑填了，那是另一件事。

Implementer 需要做的是：在 CHANGELOG 或 PR 描述里点名"#14 的 workaround 现已冗余，
留待跟进"，并在 `_log_runtime_identity()` 的 docstring 里加一句指向 #19 的说明。

### 5. 其他入口点

已查：仓库里只有 `mcp_server/__main__.py` 和 `sodamem_cli/__main__.py`，
**没有 `server/__main__.py`**。`server` 的唯一 ASGI 入口是 `server.app:build`
（`daemon.py:_serve_command()` 和 Dockerfile CMD 都用它），两者都经过 `create_app()`。
`mcp_server` 走的是 stdio，不是这条路径，不在本次范围内。

## Acceptance Criteria

- [ ] **AC1 — 非空洞的单元门。** `tests/test_daemon_logging.py` 里有一个测试：先把
      `logging.getLogger().handlers` 清空（复现守护进程下 uvicorn 留下的真实状态：
      root 无 handler + root level WARNING），再调 `create_app()`，然后把新装上的
      handler 的 `.stream` 换成 `StringIO`，在 `logging.getLogger("server.app")` 上打一条
      INFO，断言它**渲染进了那个 stream**。

      **这条门必须同时覆盖 level 和 handler 两个杀手**：断言里要显式检查
      `logging.getLogger("server.app").getEffectiveLevel() <= logging.INFO`，否则一个
      "只加 handler、不降 level" 的实现会在 stream 断言上通过（因为测试自己打的那条
      INFO 若沿用 caplog 压低的 level 就会漏过去）。

      **必须避开的空洞陷阱**（明确写在测试注释里）：pytest 的 logging 插件在**每一个**
      测试期间都往 root 挂 4 个 handler（实测：`_LiveLoggingNullHandler`、
      `_FileHandler /dev/null`、两个 `LogCaptureHandler`），`caplog.at_level()` 还会把
      level 临时压到 INFO。因此任何形如"`with caplog.at_level(INFO): create_app();
      assert caplog.records`"的测试在**当前 main 上就是绿的**，它什么都不 gate。
      清空 root handlers 是这条 AC 的**前提条件**，不是风格问题。

- [ ] **AC2 — 测试自己不制造顺序依赖。** AC1 的测试用 fixture（或 `try/finally`）
      完整还原：`root.handlers`（原样列表对象内容）、`root.level`、
      `logging.getLogger("server").level`、`logging.getLogger("sodamem").level`。
      验证方式：`tests/test_daemon_logging.py` 单独跑、和整套一起跑、以及
      `-p no:randomly` 与打乱顺序下结果一致，且**整套的 stdout/stderr 没有新增
      任何日志噪声**（diff `pytest -q` 的输出与 main 的输出）。

- [ ] **AC3 — `create_app()` 在 pytest 下是 no-op。** 一个测试断言：在 pytest 正常
      状态下（root 已有 handler）调 `create_app()`，
      `configure_logging_if_unconfigured()` 返回 `False`，且 `root.handlers` 的长度
      和内容前后不变。这条是"修 bug 不许弄脏测试输出"的机器可读版本。

- [ ] **AC4 — 不重复。** 一个测试：清空 root handlers → 走一遍
      `uvicorn.config.Config(...).configure_logging()` 建立的真实 logger 树 → 调
      `create_app()` → 分别在 `server.app`、`uvicorn.error`、`uvicorn.access` 上各打
      一条记录 → 断言每条在捕获流里**恰好出现一次**。

- [ ] **AC5 — 端到端证据，before 和 after 都要记录。** 用
      `/Users/aaron.w/Desktop/SodaMem-worktrees/issue-9/.venv/bin` 打头的 PATH 和一个
      **一次性 `SODAMEM_HOME`**（别污染 `~/.sodamem`）：

      1. `sodamem daemon stop`（若有）→ 删掉那个 `daemon.log` → `sodamem daemon ensure`
      2. `curl` 打一次 `/health` 和一次真实的读接口
      3. `sodamem daemon stop`
      4. `grep -c 'GET /health -> 200' daemon.log`

      **before（当前 main / stash 掉 fix）：计数为 0。**
      **after（带 fix）：计数 ≥ 1，且日志里能看到带时间戳的 `INFO server.app:` 行。**
      两份 `daemon.log` 的相关片段都贴进 PR 描述。这是本 issue 唯一真正的验收 —— 前四条
      AC 都是它的可重复代理。

- [ ] **AC6 — 真实日志里不重复。** 对 AC5 的 after 日志：
      `grep -c 'GET /health -> 200'` 的值等于实际发出的请求数（不是 2 倍）；
      `grep -c 'serving on interpreter'`（#14 的诊断行）**恰好为 1**。

- [ ] **AC7 — 现有套件不退化。** `pytest` 结果为 **847 passed, 1 skipped** —— 即 main 基线的
      843 passed 加上本次新增的 4 个测试，既有测试零退化。（**修订 B**：原文写"逐字
      一致 843"，与本 spec 自己要求新增测试的 AC1/AC3/AC4 直接矛盾，是我的笔误。）特别检查 `tests/test_daemon_command.py::test_create_app_logs_the_running_interpreter`
      仍然通过（#14 的诊断没被打坏）。

### 每条门"在没有 fix 时如何失败" —— implementer 必须逐条演示

做法：`git stash` 掉 `server/logging_setup.py` + `server/app.py` 的改动（只留测试），
跑一遍，把输出贴进 PR。预期：

| 门 | 无 fix 时的失败形态 |
|---|---|
| AC1 | root handlers 被清空后 `create_app()` 不装任何 handler → 取 `root.handlers[0]` 直接 IndexError；即便改写成软断言，`server.app` 的 effective level 仍是 WARNING，INFO 记录压根没生成 |
| AC3 | 无 fix 时没有 `configure_logging_if_unconfigured` 这个符号 → ImportError（这条门本身在无 fix 时无意义，写明它 gate 的是"未来别把守卫条件删掉"） |
| AC4 | 同 AC1，`server.app` 那条出现 0 次而非 1 次 |
| AC5 | `grep -c` 返回 0 —— **这就是 issue 报告的原始事实，也是唯一不可伪造的证据** |
| AC7 | 无 fix 时本来就是绿的；它 gate 的是 fix 别把别人弄坏 |

**PARTIAL = FAIL。** 尤其是 AC5：一个只有单元测试绿、拿不出 before/after `daemon.log`
的 PR 不算修好了这个 bug —— 这个 bug 的全部性质就是"单元测试是绿的而真实日志是空的"。


---

## Spec 修订记录（验收时由 Iris 补，2026-08-19）

写 spec 时的两处错误，现按实际交付**修正 spec 本身**，而不是让代码事后定义 spec。

### 修订 A —— 范围收窄到 `server`，不含 `sodamem`。**接受，且这比我原来的判断更好。**

原 spec 的 Value 和 Implementation Path 都写了 `logging.getLogger("sodamem").setLevel(INFO)`。
**没有任何一条 AC 依赖它** —— AC1/AC5/AC6 通篇只断言 `server.app`。所以这是 Value 段的
范围蔓延，不是验收标准的变更。

收窄是对的，理由不是"issue 没要求"这么轻，而是：

1. #19 的标题和**全部**证据都是 `server/`。把 `sodamem` 捎带上是我加的，不是 issue 要的。
2. 实测确认（非转述）：`sodamem/memory/ingest/extractor.py:267` 渲染 `expr` / `raw_value` /
   `session_date`，`:785` 渲染 `predicate_raw` —— 全是**用户记忆里抽出来的内容**。唤醒它们
   等于让一个**记忆产品**开始把用户内容明文写进 `~/.sodamem/daemon.log`，而 daemon.log
   恰恰是用户会整个贴进 GitHub issue 的那个文件。已复核：不涉及凭据。
3. 风险不对称。窄→宽以后是改一个 tuple，带它自己的论证；宽→窄要等到东西已经写进了别人
   机器的磁盘之后。默认值该选诚实的那个。

代码把这段理由写在 `_OUR_LOGGERS` 的注释里，不是写在 commit message 里 —— 位置对。

### 修订 B —— AC7 的期望值是 847 passed, 1 skipped，不是 843。

原文要求"与 main 基线逐字一致 843 passed"，而同一份 spec 的 AC1/AC3/AC4 又要求新增测试。
两者不可能同时成立。正确读法：843 基线 + 4 个新测试 = 847，既有测试零退化。已实测 847。
