# Spec: MCP remote backend 的 `_call()` 只认 `HTTPError`/`URLError`，应答阶段的异常全部逃逸

Record: GitHub Issue #21 — https://github.com/SodaMem/SodaMem/issues/21
Branch: fix/21-mcp-backend-connection-errors
Worktree: /Users/aaron.w/Desktop/SodaMem-worktrees/issue-21
相关: #18（已合并，CLI 侧同一形状的修复，本 spec 大量引用其结论）、#13 / #14（同一家族：没预料到的异常类逃到用户脸上）

## Problem

`mcp_server/backend.py:364-374`，`RemoteBackend._call()` 是 `sodamem_cli/http.py` 里
`Client.call()` 的一份复制品，带着同一个缺陷：

```python
        try:
            with urllib.request.urlopen(request, timeout=timeout or self._timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as exc:
            raise BackendError(self._explain_http_error(exc)) from exc
        except urllib.error.URLError as exc:
            raise BackendError(...) from exc
```

urllib 的 `AbstractHTTPHandler.do_open` 只把 `h.request(...)` 抛的 `OSError` 包成
`URLError`。`h.getresponse()` 和 `r.read()` 抛的东西**原样往外走**。连接建立阶段被处理了，
应答阶段没有。

### 实测：这个 worktree 的这份代码，今天有四种输入能逃出去

在 `/Users/aaron.w/Desktop/SodaMem-worktrees/issue-9/.venv`（CPython 3.11.13）上，用真 socket
服务端直接打 `RemoteBackend(...).list_memories(...)` 逐个跑过（不是从 #18 抄的结论，是在
`mcp_server/backend.py` 上重跑的）：

| 服务端行为 | 逃出来的异常 | `str(exc)` | `isinstance(e, URLError)` |
|---|---|---|---|
| accept 后**读完请求**再 close（优雅关停） | `http.client.RemoteDisconnected` | `Remote end closed connection without response` | **False** |
| accept 后**不读**直接 close（macOS 发 RST） | `builtins.ConnectionResetError` | `[Errno 54] Connection reset by peer` | **False** |
| accept 后挂住不答（读超时） | `builtins.TimeoutError` | `timed out` | **False** |
| 应答体比 `Content-Length` 短 | `http.client.IncompleteRead` | `IncompleteRead(5 bytes read, 95 more expected)` | **False** |
| **没人监听**（连接被拒） | `BackendError`（`__cause__` 是 `URLError`） | `cannot reach the SodaMem service at ...: [Errno 61] Connection refused. Start it ...` | **True，今天就是对的，不要动它** |
| `SODAMEM_API_URL=http://127.0.0.1:notaport` | `http.client.InvalidURL` | `nonnumeric port: 'notaport'` | **False**（见「决定二」，**保持逃逸**） |

第一行与第二行的区别是修法的关键：**同一个关停场景，取决于服务端有没有先把请求读掉，抛出来的
类不一样**。只 catch `RemoteDisconnected` 会漏掉另一半。

触发条件极普通：`sodamem daemon stop` / 重启 / daemon 被回收，而编辑器把 MCP server 进程
一直留着跨过了这次重启。

### 后果：把 issue 的前提修正一半

issue #21 说"未处理的 `RemoteDisconnected` 会变成一个死掉的 stdio 进程"。**在 mcp 1.28.1 上这
不成立，实测过**：`FastMCP` 的 `Tool.run`（`mcp/server/fastmcp/tools/base.py:116`）用
`except Exception` 兜底并包成 `ToolError`，lowlevel 层再转成 `isError=True` 的 CallToolResult
（`mcp/server/lowlevel/server.py:589`）。实测（`build_server(RemoteBackend(...)).call_tool(...)`
打真 socket）：

```
drain_close -> ToolError 'Error executing tool list_memories: Remote end closed connection without response'
never       -> ToolError 'Error executing tool list_memories: timed out'
process still alive
```

所以真实的伤害不是进程死掉，而是**模型收到的那句话里没有任何可执行的补救**：它说"远端关了连接"，
不说"服务没在跑，`sodamem daemon ensure`，或者 unset `SODAMEM_API_URL` 走本地"。模型能看见这句
话、会把它转述给用户，于是它是这个产品在故障时说的**唯一一句话** —— 而它现在是一句诊断，不是
一个动作。CLI 侧至少还有一个 traceback 摆在用户自己的终端里（#18 就是这么被发现的）；这里没有。

修完之后同一路径变成：

```
Error executing tool list_memories: cannot reach the SodaMem service at http://127.0.0.1:PORT:
RemoteDisconnected: Remote end closed connection without response. Start it (`sodamem daemon
ensure`) or unset SODAMEM_API_URL to run this MCP server against local stores instead.
```

这个"前提被修正"必须写进 PR 描述：issue 的严重性论证错了一环，修复的价值判断没错。

## Value

- `_call()` 按构造对"服务没在应答"是全函数（total）的，而不是"对我们想到的那几种异常是全函数的"。
  它是六个远程工具**唯一**的传输层，一处修好，八个 MCP 工具全部受益。
- 故障时模型拿到的是一句能照做的话，而不是一句 `timed out`。这是 remote 模式（我们建议所有人
  用的模式、`sodamem install` 默认写的模式）在最常见故障下的唯一输出。
- 与 #18 的 CLI 侧对齐：同一个传输缺陷不再有两个不同的答案。
- 改动是一个 except 子句 + 一个 `import`，没有新机制、没有新配置、没有新概念。

## Scope

| 文件 | 改动 |
|---|---|
| `mcp_server/backend.py` | `RemoteBackend._call()` 增加**两个** except 分支（`InvalidURL` re-raise + 兜底分支）；顶部 `import http.client` |
| `tests/test_mcp_backend.py` | AC1–AC6 的载体：真 socket 服务端 + mock 补充 + 不回归 |
| `tests/test_mcp_server.py` | AC7：工具边界断言（`ToolError` 文案里有补救动作） |

**不改：** `_explain_http_error()` 与 `HTTPError` 分支的任何行为；`URLError` 分支的任何行为
（连接被拒今天就是对的）；`RemoteBackend` 的构造 / 超时语义（`DEFAULT_TIMEOUT_S`、
`INGEST_TIMEOUT_S`）；`LocalBackend` 的任何一行；`mcp_server/main.py` 的工具函数体与
`build_server`（AC7 只是**新增测试**，不改产品代码）；`mcp_server/config.py`；
`sodamem_cli/http.py`（#18 已交付，本次一个字都不动）；`server/webhooks.py`（见「决定五」）。

**不加任何配置开关。不加任何日志调用**（见「决定四」）。

## Implementation Path

### 决定一：catch 哪些类 —— 与 #18 逐字相同的集合

新分支放在 `URLError` 分支**之后**（`HTTPError` 是 `URLError` 的子类，前两个的顺序不能动；
新分支与它们无继承关系，放最后最不容易读错）：

```
except http.client.InvalidURL:
    raise                                  # 见决定二
except (ConnectionError, TimeoutError, http.client.HTTPException) as exc:
    raise BackendError(<决定三的句子>) from exc
```

集合的每一项、以及每一项被排除的理由，**与 #18 的「决定一」完全一致，本 spec 不重新推导**，
只复述结论并标注在本文件上的复核结果：

- **`ConnectionError`** —— 覆盖上表第一、二行，外加 `BrokenPipeError`。issue 的正主。
- **`TimeoutError`** —— 上表第三行。3.10 起 `socket.timeout` 就是它的别名，本项目
  `requires-python >= 3.11`，所以**不写** `socket.timeout`。
- **`http.client.HTTPException`** —— 上表第四行（`IncompleteRead`）与 `BadStatusLine` 一族：
  "对面吐的不是一个完整 HTTP 应答"。
- **不 catch 裸 `OSError`** —— 会吞掉与网络无关的 OS 错误。
- **不 catch `ssl.SSLError`** —— 握手在 `h.request()` 里，urllib 已包成 `URLError`。
- **不动 `json.loads(raw)`** —— 它在 `try` 之外，保持原样：完整应答但不是 JSON 是服务的 bug，
  不该被伪装成"服务没在跑"。这一点在本文件上更重要：`_call()` 的六个调用方都会
  `result.get(...)`，把一个 JSON 语法错误说成"服务不可达"会让排查方向整个走偏。
- **不吞编程错误** —— `TypeError` / `AttributeError` / `ValueError` 照旧往外抛。

`import http.client` 在这个模块同样零成本：`urllib.request` 已经 import 了它。
`RemoteBackend` 的 docstring 明确把 import 成本当延迟（"spawned per editor session and, via the
hook path, per prompt"），所以这句理由要写进注释。

### 决定二：`InvalidURL` 同样 re-raise —— 但**理由与 #18 不同**，必须在注释里说清

#18 re-raise `InvalidURL` 的理由有两条：(a) 给出的补救（`sodamem daemon ensure`）对一个畸形
URL 不可能起作用；(b) 实测下游 `daemon._split()` 会以 `ValueError: Port could not be cast to
integer value` 死掉，产出一个**比原样 `InvalidURL` 更糟**的 traceback。

**(b) 在这里不成立。** 查过了：`mcp_server` 里没有 `_split()` 的对应物 ——
`RemoteBackend.__init__` 只做 `base_url.rstrip("/")`，`config.build_backend()` 不解析 URL，
`urllib.parse` 在本模块只用于 `urlencode` / `quote`。所以"下游反正会死得更难看"这条理由
**不能搬过来**，"照抄 #18"在这里是错误的直觉，结论必须重新论证。

而且这里还有一条**反方向**的理由：本文件 `URLError` 分支的句子里带着
`unset SODAMEM_API_URL to run this MCP server against local stores instead` —— 对一个畸形 URL
来说，这个补救**确实能用**。所以不能像 CLI 那样说"补救不可能起作用"。

结论仍然是 **re-raise**，理由换成三条本文件自己的：

1. **它不是应答阶段的失败。** `InvalidURL` 来自 `HTTPConnection.__init__` → `_get_hostport`，
   socket 还不存在。把它塞进"服务没在答"的分支是把一个配置错误说成一个运行时故障。
2. **转换会用一句更差的话覆盖一句更好的话。** 实测（见上表末行）：今天它到达客户端时是
   `Error executing tool list_memories: nonnumeric port: 'notaport'` —— 这句话**准确点名了坏
   掉的东西**。换成 `cannot reach the SodaMem service at http://127.0.0.1:notaport ... Start it
   (\`sodamem daemon ensure\`)` 之后，前半句在撒谎（我们从没试图连接过），"启动服务"这个错误
   补救排在"unset SODAMEM_API_URL"这个正确补救的**前面**。净损失。
3. **要给它一句更好的话，得再加一个分支和第二套文案**，为的是一个没人报过的配置错误，且它今天
   已经能被客户端看见（不是 traceback，见 Problem 一节）。不值。
   —— 如果将来要改善它，正确的位置是 `config.build_backend()` 在**启动时**校验 URL 并给出
   `McpConfigError`，那是另一个 issue，不是这个。

这条决定由 AC6 钉死（`InvalidURL` 必须仍然作为 `InvalidURL` 逃出，不得变成 `BackendError`）。
**与 #18 的偏差点**：结论相同，第 (b) 条理由不适用，替换为上述第 2、3 条。

### 决定三：消息文案 —— 用**本文件**的声音，不是 CLI 的

现有 `URLError` 分支的句子是：

```
cannot reach the SodaMem service at {self._base}: {exc.reason}. Start it
(`sodamem daemon ensure`) or unset SODAMEM_API_URL to run this MCP server against
local stores instead.
```

它比 CLI 那句多一个东西：**local 模式这条退路**。这是本文件独有的，因为这个进程真的能不连服务
就工作。新分支必须**保留这个属性**，不许改用 CLI 的措辞。建议句式（属性不可调，措辞可调）：

```
cannot reach the SodaMem service at {self._base}: {type(exc).__name__}: {exc}. Start it
(`sodamem daemon ensure`) or unset SODAMEM_API_URL to run this MCP server against local
stores instead.
```

必须含的四个属性，由 AC5 逐条断言：
1. `self._base`；
2. `type(exc).__name__`（不能只贴 `{exc}`：`str(TimeoutError())` 是空串，实测本路径上
   urllib 给的是 `TimeoutError('timed out')` 还算能读，但这是 urllib 的实现细节不是契约，
   #18 已经为此付过一次代价，照它办）；
3. 子串 `` `sodamem daemon ensure` ``；
4. 子串 `SODAMEM_API_URL`。

两个分支的句子刻意保持同一形状：对调用方来说"连不上"和"连上了但没答完"是同一个答案，
说成两种话只会让人以为是两回事。

### 决定四：**不加日志**

问过了"stdio 场景要不要 log 而不是只 raise"，答案是不要，三条理由：

1. **异常已经到达一个用户真的会看的地方**：`isError=True` 的工具结果，模型能读、能转述。
   stderr 才是"用户不会去看的地方"——一个被编辑器 spawn 的进程的 stderr，默认 level 还是
   `WARNING`（`main.py:318`），大多数客户端直接丢掉。为一个已经送达的消息再写一份到丢弃管道里，
   收益为零。
2. **会把同一个故障家族拆成两种噪音**：现有 `HTTPError` / `URLError` 分支都不 log。只给新分支
   加日志，等于说"连不上不值得记，连上了没答完值得记"，这个区分不存在。
3. **stdout 是 JSON-RPC 通道**，任何日志配置错误都是协议损坏。`main.py` 已经为此写了一段注释
   把 handler 显式钉在 stderr 上。不新增日志点 = 不新增这个风险面。

### 决定五：工具边界"故障 vs 空结果"—— 今天就已经区分开了，只补一条 gate

查过八个工具的实现：故障路径上 `_call()` 一律 raise，没有任何一个工具会 `except` 之后返回
`{"memories": [], ...}`。所以"后端死了"和"库里没东西"在协议层已经是 `isError=True` 与
`isError=False` 两种结果，**不需要改产品代码**。

但这个属性今天没有测试守着，而它正是本 issue 最坏的想象（"memory 悄悄不工作了"）。所以补一条
测试（AC7）把它钉住：真 socket + `build_server(RemoteBackend(...))` + `await call_tool(...)`，
断言抛 `ToolError` 且文案里带补救动作 —— **不是**返回一个空 `memories` 列表。

### 决定六：`mcp_server/` 里还有没有同一形状？没有；仓库里第三处也已确认安全

`grep -rn "urlopen(" --include='*.py'` 全仓库只有三处：

| 位置 | 状态 |
|---|---|
| `sodamem_cli/http.py:53` | #18 已修，本次不动 |
| `mcp_server/backend.py:365` | **本 issue** |
| `server/webhooks.py:64` | 安全：`WebhookDispatcher._deliver`（`webhooks.py:154`）用 `except Exception` 包着并计入 `failed`。**out of scope** |

`mcp_server/` 内除 `backend.py:365` 外没有第二处网络调用（`urllib.parse.quote` 在 `:507` 是
路径转义，不是传输）。范围就是这一个函数。

### 测试环境（两个坑，都实测过）

- **这个 worktree 没有 venv。** 用绝对路径的
  `/Users/aaron.w/Desktop/SodaMem-worktrees/issue-9/.venv/bin/python`。
- **那个 venv 的 editable 安装指向主仓库**：`python -c "import mcp_server"` 解析到
  `/Users/aaron.w/Desktop/SodaMem/mcp_server/__init__.py`，**不是** worktree。
  但**在 worktree 目录里跑 pytest 是对的**：`tests/` 是包，pytest 会把 rootdir 前插进
  `sys.path`，实测解析到
  `/Users/aaron.w/Desktop/SodaMem-worktrees/issue-21/mcp_server/__init__.py`。
  → **所有 pytest 命令必须 `cd` 到 worktree 再跑**，且交付说明里要贴一次
  `print(mcp_server.__file__)` 之类的证据，证明跑的是本分支的代码。

## Acceptance Criteria

- [ ] **AC1（硬 gate，真 socket，第一半）**：`tests/test_mcp_backend.py` 里新增一个测试，起一个
      **真的** `socket` 服务端（`socket.socket` + `listen`，不是 uvicorn，不是 mock），accept 后
      **读完请求字节**再直接 `close()`。`RemoteBackend(url, key).list_memories(...)` 必须抛
      `BackendError`，且断言 `cause = exc.__cause__` 同时满足
      `isinstance(cause, http.client.RemoteDisconnected)` **和**
      `not isinstance(cause, urllib.error.URLError)`。第二条断言是承重的：修复前的代码能转成
      `BackendError` 的异常**全部**是 `HTTPError` 或 `URLError`，少了它，这个测试可以在某天
      悄悄改跑一条本来就通过的路径而不被发现（这个洞在 #18 里被发现并堵上，不许重开）。
      服务端线程 `daemon=True`，结尾 `join` 并断言线程没泄漏。
- [ ] **AC2（硬 gate，真 socket，第二半）**：同上，但服务端 accept 后**不读请求**直接 `close()`
      （产出 `ConnectionResetError` 而非 `RemoteDisconnected`）。断言 `BackendError` +
      `isinstance(cause, ConnectionResetError)` + `not isinstance(cause, http.client.RemoteDisconnected)`
      + `not isinstance(cause, urllib.error.URLError)`。AC1 与 AC2 必须**同时存在**：上表说明
      只留一条会漏掉"关停中"的另一半。若实现里需要 `time.sleep` 让请求先落进没人读的缓冲区，
      注释必须说明那不是补丁而是构造条件（提前 close 会落在 `h.request()` 里，那是修复前就
      已经通过的 `URLError` 路径）。
- [ ] **AC3（硬 gate，真 socket，超时）**：服务端 accept 后**不作任何应答**并挂住；
      `RemoteBackend(url, key, timeout=<小值>)` 必须抛 `BackendError`，断言
      `isinstance(cause, TimeoutError)` + `not isinstance(cause, urllib.error.URLError)`。
      **这是本 spec 相对 #18 的加严**：#18 的 `TimeoutError` 只有 mock 覆盖（其 AC3），这里
      要求真 socket，因为 issue 里"daemon 卡死但端口还在"是编辑器长驻场景下最容易撞上的一种。
      超时值要小（≤2s）以免拖慢全量测试。
- [ ] **AC4（硬 gate，真 socket，半截应答）**：服务端读完请求后回
      `HTTP/1.1 200 OK` + `Content-Length: 100` + 一个更短的 body 再 close；断言
      `BackendError` + `isinstance(cause, http.client.IncompleteRead)` +
      `not isinstance(cause, urllib.error.URLError)`。（可选补充、**不得单独充当 gate**：
      mock `urllib.request.urlopen` 抛 `BadStatusLine` 之类，直接断言 catch 集合。）
- [ ] **AC5（文案）**：AC1 的 `BackendError` 文本同时满足四条属性断言（不是整句 `==`）：
      含 `base_url`；含 `type(cause).__name__`（如 `RemoteDisconnected`）；含子串
      `` `sodamem daemon ensure` ``；含子串 `SODAMEM_API_URL`。最后一条是本文件与 CLI 的
      区别点，必须被钉住（决定三）。
- [ ] **AC6（不回归）**：
      (a) 没人监听的端口上 `list_memories` 仍抛 `BackendError`，消息里仍是
      `Connection refused`，且 `isinstance(__cause__, urllib.error.URLError)` 为真 ——
      现有测试 `test_remote_backend_explains_an_unreachable_service` 必须原样通过，
      并**新增**对 `__cause__` 是 `URLError` 的断言（今天它没断言这一点）。
      (b) `HTTPError` 路径逐字不变：`test_remote_backend_surfaces_the_services_error_envelope`
      通过。
      (c) `SODAMEM_API_URL` 畸形（如 `http://127.0.0.1:notaport`）时，
      `http.client.InvalidURL` 仍然**原样逃出**，**不是** `BackendError` —— 一条显式测试
      钉住决定二。
      (d) `tests/test_mcp_backend.py::test_local_and_remote_return_the_same_shape` 通过。
      (e) 编程错误不被吞：mock `urlopen` 抛 `ValueError`，它必须原样逃出。
- [ ] **AC7（工具边界）**：`tests/test_mcp_server.py` 新增一个 `@pytest.mark.asyncio` 测试：
      真 socket（AC1 的形状）+ `build_server(RemoteBackend(...))` + `await
      server.call_tool("list_memories", {"user_id": "alice"})`。必须抛 `ToolError`，且
      `str(exc)` 里含 `cannot reach the SodaMem service` 与 `` `sodamem daemon ensure` `` ——
      即"后端死了"在工具边界上**与空结果可区分**，且带补救动作。
      **不得**通过修改 `mcp_server/main.py` 来满足这条。
- [ ] **AC8（每个 gate 都要证明会失败）**：AC1–AC5、AC6(c)、AC7 各自在**没有修复**的代码上跑
      一遍并记录输出。做法**必须**是从 main 取回原文件：
      ```
      cd /Users/aaron.w/Desktop/SodaMem-worktrees/issue-21
      git show origin/main:mcp_server/backend.py > mcp_server/backend.py   # 证伪
      /Users/aaron.w/Desktop/SodaMem-worktrees/issue-9/.venv/bin/python -m pytest \
          tests/test_mcp_backend.py tests/test_mcp_server.py -x
      git checkout -- mcp_server/backend.py                                # 恢复
      ```
      **不许用 `git stash` 对着 HEAD 做** —— 提交之后 stash 什么都不会 stash，测试照样绿，
      "证伪"变成走过场。这个坑在 #18 里已经吃掉一轮，不许再犯。
      预期失败形态：AC1–AC4 看到 `RemoteDisconnected` / `ConnectionResetError` / `TimeoutError`
      / `IncompleteRead` 作为 **error 而非 `BackendError`** 逃出；AC7 看到 `ToolError` 文案是
      `Error executing tool list_memories: Remote end closed connection without response`
      （不含补救动作）；AC6(c) 在证伪版本上**照样通过**（它守的是不变量，不是新行为，
      如实说明即可）。修复前后两次输出都贴进交付说明。
- [ ] **AC9（全量不回归）**：`cd` 到 worktree 后跑
      `/Users/aaron.w/Desktop/SodaMem-worktrees/issue-9/.venv/bin/python -m pytest -q`，
      基线 **857 passed, 1 skipped**，新增测试后为 **857+N passed, 1 skipped**，
      no failures / no errors。同时贴出证据表明跑的是 worktree 的源码而非
      `/Users/aaron.w/Desktop/SodaMem` 主仓库（见「测试环境」）。
- [ ] **AC10（交付说明纠正 issue 的前提）**：PR 描述里写明：issue #21 断言未捕获异常会导致
      "dead stdio process"，在 mcp 1.28.1 上**不成立**（`FastMCP.Tool.run` 的 `except Exception`
      → `ToolError` → `isError=True`），实测输出附上；本次修复的真实价值是**故障时那句话
      带不带可执行的补救**，而不是防止进程死亡。附 `mcp` 版本号与解释器版本。

## Open Questions

无。
