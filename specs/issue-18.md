# Spec: CLI 的 HTTP 客户端只认 `HTTPError`/`URLError`，连接在应答前断掉就抛裸 traceback

Record: GitHub Issue #18 — https://github.com/SodaMem/SodaMem/issues/18
Branch: fix/18-status-connection-errors
Worktree: /Users/aaron.w/Desktop/SodaMem-worktrees/issue-18
相关: #13（已合并，同一形状：没预料到的异常类逃到用户脸上）、#14（已合并，本 bug 在其验收时被发现）

## Problem

`sodamem_cli/http.py:52-61`，`Client.call()` 里 `urlopen` 周围只有两个 except：

```python
        except urllib.error.HTTPError as exc:
        except urllib.error.URLError as exc:
```

这两个覆盖的是 **"连接建立阶段"** 的失败。urllib 的 `AbstractHTTPHandler.do_open` 只把
`h.request(...)` 抛的 `OSError` 包成 `URLError`；**`h.getresponse()` 和 `r.read()` 抛的东西
原样往外走**。也就是说：连不上被处理了，连上了之后服务没答完这件事没有被处理。

### 实测：今天有三种输入能从 `call()` 逃出去

在本仓库的解释器（`/Users/aaron.w/Desktop/SodaMem-worktrees/issue-9/.venv`，CPython 3.11.13）
上用真 socket 服务端逐个跑过，不是推测：

| 服务端行为 | 逃出来的异常 | MRO 前几层 | `isinstance(e, URLError)` |
|---|---|---|---|
| accept 后**读完请求**再 close（优雅关停的形状） | `http.client.RemoteDisconnected` | `RemoteDisconnected → ConnectionResetError → ConnectionError → OSError` | **False** |
| accept 后**不读**直接 close（macOS 发 RST） | `ConnectionResetError` | `ConnectionResetError → ConnectionError → OSError` | **False** |
| 读完请求后挂住不答（读超时） | `TimeoutError` | `TimeoutError → OSError` | **False** |
| 应答体比 `Content-Length` 短 | `http.client.IncompleteRead` | `IncompleteRead → HTTPException` | **False** |
| **没人监听**（连接被拒） | `URLError(reason=ConnectionRefusedError)` | — | **True（今天就是对的，不要动它）** |

注意第一行和第二行的区别：**同一个"关停中"场景，取决于服务端有没有先把请求读掉，抛出来的
类不一样**。这直接决定了修法——只 catch `RemoteDisconnected` 是错的，会漏掉 `ConnectionResetError`。

### 为什么这条特别值得修

`daemon.status()` 的 docstring 写得很清楚，它存在的意义就是回答"这个 URL 上有没有活着的东西"，
"死了/正在死"是它的**正常答案**，不是异常。`status()` 用 `except ServiceError` 把答案转成
`{"running": False, ...}`。而"连接在应答前断掉"恰恰是这个问题最普通的一种形态，也是唯一一种
会让它崩的输入。

用户侧的表现更糟：`daemon stop` 之后最自然的动作就是 `daemon ensure`，`ensure()` 第一行调的
就是 `status()`。**崩溃正好压在重启流程上。**

（`sodamem hook recall/retain` 路径不崩，因为 `main.py:206` 有个兜底的
`except Exception` 会打印 `hook error (RemoteDisconnected: ...)`——但那句话对用户没有可操作性，
它本该是 `URLError` 分支那种"跑 `sodamem daemon ensure`"。所以 hook 路径也是受益方，只是症状轻。）

## Value

- `status()` 变成**按构造**对"服务没在应答"是全函数（total）的，而不是"对我们想到的那几种异常是全函数的"。
- `daemon stop && daemon ensure` 这条最普通的重启路径不再有 traceback。
- hook 路径的错误信息从 `hook error (RemoteDisconnected: ...)` 升级成一句能照做的话。
- 改的是一个 except 子句，不是新机制；没有新配置、没有新概念。

## Scope

| 文件 | 改动 |
|---|---|
| `sodamem_cli/http.py` | `Client.call()` 增加**一个** except 分支；顶部 `import http.client` |
| `tests/test_http_client.py`（新建） | AC1–AC4 的载体：真 socket 服务端 + mock 两层 |
| `tests/test_daemon_command.py` | AC5：`stop` → `ensure` 端到端 |

**不改：** `_explain()` 与 `HTTPError` 分支的任何行为；`URLError` 分支的任何行为（连接被拒今天
就是对的）；`Client` 的构造/超时语义；`status()` / `ensure()` / `stop()` 的任何逻辑；
`hooks.py` 的任何 except；`mcp_server/backend.py`（理由见"决定三"）；`server/webhooks.py`。

**不加任何配置开关。**

## Implementation Path

### 决定一：catch 哪些类 —— `(ConnectionError, TimeoutError, http.client.HTTPException)`

新增分支放在 `URLError` 分支**之后**（`HTTPError` 是 `URLError` 的子类，顺序不能乱；新分支
与前两个无继承关系，放最后最不容易读错）：

```
except (ConnectionError, TimeoutError, http.client.HTTPException) as exc:
    raise ServiceError(<与 URLError 分支同款的句子>) from exc
```

逐个交代**为什么它在里面**：

- **`ConnectionError`** —— 覆盖上表第一、二行（`RemoteDisconnected`、`ConnectionResetError`），
  以及 `BrokenPipeError`（发请求发到一半对端没了）。这就是 issue 的正主。
  按 `RemoteDisconnected` 单点 catch 是错的：同一场景另一半会以 `ConnectionResetError` 出现。
- **`TimeoutError`** —— 覆盖上表第三行。服务在但卡死，与服务不在，对调用方是同一个答案。
  3.10 起 `socket.timeout` 就是 `TimeoutError` 的别名，本项目 `requires-python = ">=3.11"`，
  所以**不需要**再写 `socket.timeout`，写了是重复。
- **`http.client.HTTPException`** —— 覆盖上表第四行（`IncompleteRead`）与 `BadStatusLine`
  一族。语义是"对面吐的东西不是一个完整的 HTTP 应答"，这同样是"服务没有答上来"，不是本地 bug。

逐个交代**为什么某些东西不在里面**：

- **不 catch 裸 `OSError`。** 它确实能一网打尽，但它也会吞掉与网络无关的 OS 错误；
  上面三个类的并集已经覆盖了全部实测到的失败形状，多出来的那部分只有坏处。
- **不 catch `ssl.SSLError`。** 已实测：把一个明文端口当 https 访问，异常是
  `URLError(reason=ssl.SSLError)` —— TLS 握手发生在 `h.request()` 里，urllib 已经替我们包好了，
  今天就走既有分支。（`SODAMEM_API_URL` 没有 scheme 校验，https 事实上可用，所以这个问题必须
  回答；答案是"已经被覆盖了"。）为一个没观察到、也构造不出来的路径加分支就是过度 catch。
- **不 catch `Exception` / 不动 `json.loads`。** `json.loads(raw)` 在 try 之外，保持原样：
  服务返回了完整应答但不是 JSON，那是服务的 bug，不该被伪装成"服务没在跑"。
- **不吞编程错误。** `TypeError`、`AttributeError`、`ValueError` 一律照旧往外抛。

`import http.client` **零成本**：`urllib.request` 本来就 import 了它（`ssl` 同理），
这个模块"不 import 任何解释器里还没有的东西"的约定没有被破坏。这一点在改动的注释里要写出来，
因为模块 docstring 明确把 import 时间当成用户能感知的延迟。

### 决定二：修在 `call()`，不是修在 `status()`

`call()` 的调用方全表（`grep` 过整个 `sodamem_cli/`，没有别的）：

| 调用方 | 今天遇到新分支覆盖的异常会怎样 | 修在 `call()` 之后 |
|---|---|---|
| `daemon.status()` (`daemon.py:54`) | **裸 traceback 逃出 `ensure()`** ← 本 issue | `{"running": False, "error": ...}`，正是它的设计答案 |
| `hooks._resolve_pending()` (`hooks.py:213`) | 逃到 `main.py:206` 的兜底，打印 `hook error (...)` | 走已有的 `except ServiceError` → 视作"仍在跑"，不提交不回退。**这正是那段注释说的"唯一不该做的是猜"** |
| `hooks.recall()` (`hooks.py:335`) | 同上 | `recall skipped (cannot reach ...)` |
| `hooks.retain()` (`hooks.py:366`) | 同上 | `retain failed, will retry (...)`，turns 下次重发 |
| `main.hook_command()` (`main.py:204`) | `except Exception` 兜底 | 走 `except ServiceError`，消息更可操作 |

**Blast radius：4 个调用点，全部已经有 `except ServiceError`，全部因此变好，没有一个变差。**
把修补塞进 `status()`（比如在那里加一层 catch）反而会让 hook 的三个调用点继续留在兜底分支里 ——
同一个 bug 修一半。所以修在 `call()`。

### 决定三：`mcp_server/backend.py` 有同一个洞，本次**不改**

`mcp_server/backend.py:363-374` 的 `_call()` 是 `Client.call()` 的一份复制品，同样只 catch
`HTTPError`/`URLError`，同样会让 `RemoteDisconnected` 逃成 MCP 客户端里的 traceback。

不并进来的理由：它属于另一个包、另一个错误类型（`BackendError`）、另一套测试，本 spec 的任何
一条 AC 都到不了那行代码。**#13 和 #14 的教训恰恰是"没有 gate 的改动会通过字面验收"** ——
顺手改一个我不打算立 gate 的地方，就是重犯。

所以：**开一个 follow-up issue**（AC7），把上面这张实测表和结论贴进去，引用 #18。

其余 `urlopen` 现场核对过：`server/webhooks.py:64` 的 `_urllib_transport` 由
`WebhookDispatcher._deliver` 用 `except Exception` 包着（`webhooks.py:154-155`），没有这个洞。

### 决定四：消息文案

新分支的 `ServiceError` 文案必须和 `URLError` 分支同一质量档次，即：
**含 `self.base_url`、含这次到底发生了什么、以反引号包住的 `sodamem daemon ensure` 结尾。**
建议句式（措辞可调，属性不可调）：

```
cannot reach the SodaMem service at {self.base_url}: {type(exc).__name__}: {exc}.
Run `sodamem daemon ensure` to start one.
```

用 `type(exc).__name__` 是因为 `str(RemoteDisconnected)` 是
`"Remote end closed connection without response"`（还算能读），但 `str(TimeoutError())` 是
**空字符串** —— 只贴 `{exc}` 会产出一句半截话。这一条由 AC4 钉死。

## Acceptance Criteria

- [ ] **AC1（硬 gate，真 socket）**：`tests/test_http_client.py` 里有一个测试，起一个**真的**
      `socket` 服务端（`socket.socket` + `listen`，不是 uvicorn，不是 mock），它 accept、
      **读完请求字节**、然后不作任何应答直接 `close()`；`Client(base_url).health()` 必须抛
      `ServiceError`，且 `daemon.status(url)` 对同一端口必须返回 `{"running": False, ...}`，
      不得有异常逃出。测试须用 `free_port()` 之类拿空闲端口，服务端线程 `daemon=True` 并在
      结尾 join，不得泄漏线程。
- [ ] **AC2（硬 gate，真 socket，另一半）**：同一文件里第二个真 socket 测试，服务端 accept 后
      **不读请求**直接 `close()`（产出 `ConnectionResetError` 而非 `RemoteDisconnected`），
      同样断言 `ServiceError` / `running is False`。AC1 与 AC2 都必须存在 —— 上面的实测表说明
      只有一条会漏掉"关停中"的另一半。
- [ ] **AC3（补充单测，不得单独充当 gate）**：mock `urllib.request.urlopen` 分别抛
      `http.client.RemoteDisconnected`、`TimeoutError`、`http.client.IncompleteRead(b"")`，
      三者都得到 `ServiceError`。这三条是对"catch 集合"的直接断言，但 AC1/AC2 才是证明。
- [ ] **AC4（文案）**：AC1 的 `ServiceError` 文本同时满足：包含 `base_url`；包含引发原因的
      类名（如 `RemoteDisconnected`）；包含子串 `` `sodamem daemon ensure` ``。断言写成对这三条
      属性的断言，不是对整句话的 `==`。
- [ ] **AC5（端到端，真输出）**：用 `/Users/aaron.w/Desktop/SodaMem-worktrees/issue-9/.venv/bin`
      置于 `PATH` 最前的解释器，在隔离的 `SODAMEM_HOME`/`SODAMEM_DATA_ROOT` 下，执行
      `sodamem daemon ensure` → `sodamem daemon stop` → **立刻** `sodamem daemon ensure`，
      **连做 10 轮**；10 轮全部返回正常结果，**一次 traceback 都不许有**，退出码全为 0。
      终端真实输出（10 轮全文）贴进 PR/交付说明。这是用户看得见的症状，不可用测试替代。
- [ ] **AC6（不回归）**：没人监听的端口上，`Client.health()` 仍抛 `ServiceError` 且消息里仍是
      `Connection refused`（走的仍是 `URLError` 分支，`from` 的 `__cause__` 仍是 `URLError`）；
      `HTTPError` 路径（含 401 那句 `set SODAMEM_API_KEY ...`）行为逐字不变；成功路径不变。
      现有全量测试通过：基线 **843 passed, 1 skipped**，新增测试后为 843+N passed, 1 skipped。
- [ ] **AC7（同形状的第二处）**：为 `mcp_server/backend.py:363-374` 开一个 follow-up GitHub
      issue，引用 #18 并附上本 spec 的实测异常表；issue 号写进 PR 描述。本次**不**改该文件。
- [ ] **AC8（每个 gate 都要证明会失败）**：AC1、AC2、AC3 各自在**没有修复**的代码上跑一遍并
      记录输出。做法：`git stash push sodamem_cli/http.py` →
      `/Users/aaron.w/Desktop/SodaMem-worktrees/issue-9/.venv/bin/python -m pytest tests/test_http_client.py -x`
      → 记录失败（应看到 `RemoteDisconnected` / `ConnectionResetError` / `TimeoutError` /
      `IncompleteRead` 作为 **error 而非 ServiceError** 逃出）→ `git stash pop` → 再跑一遍通过。
      两次输出都贴出来。AC5 同理跑一轮 stash 版本；若 10 轮内没能撞上竞态窗口，如实说明，
      并以 AC1/AC2 的 stash 失败输出作为该症状的确定性证据（**不得**为了让 e2e 失败而去改产品
      代码或注入 sleep）。

## Open Questions

无。
