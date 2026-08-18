# Spec: 首次 store open 的 PanicException 逃逸导致守护进程首个请求 500

Record: GitHub Issue #13 — https://github.com/SodaMem/SodaMem/issues/13
Branch: fix/13-store-open-panic
Worktree: /Users/aaron.w/Desktop/SodaMem-worktrees/issue-13

## Problem

在受影响的 store 上，任何全新进程里的第一次 `SodaMem.open()` 会从 chromadb 1.1.1 的
rust 绑定里抛出 `pyo3_runtime.PanicException`；同一进程内第二次 open 必定成功。经 HTTP
暴露出来就是：**每次守护进程启动后的第一个请求返回未处理的 500。**

它逃过所有 handler 的原因只有一个 —— 类型：

```
type       : pyo3_runtime.PanicException
Exception? : False
MRO        : ['PanicException', 'BaseException', 'object']
```

它直接继承 `BaseException`。`server/stores.py:205` 的 `except Exception:`、FastAPI 自己的
错误中间件、以及代码库里其他所有地方用的都是 `except Exception` —— 结构上就看不见它。

已被实验排除（不要重新讨论）：store 里有没有数据、chroma tenant 缺失/损坏、持久化的
`index_metadata.pickle`。它与 store 规模相关（400 vectors/collection 正常，1000 必崩），
但删掉持久化 index metadata 并不能避开，所以机制在 chroma 的加载路径内部。**chroma 的
bug 是上游的，不在本次范围内；一个 `BaseException` 从 store-open 路径逃进 ASGI 栈，是我们
自己的缺陷。**

关键事实（issue 评论已独立复现）：**重试一次就能成功**，并给出了正确答案
（`opened on attempt 2; citations=8`）。

## Value

- 守护进程启动后的第一个请求不再是 500。这是每一个新用户第一次用 SodaMem 时看到的东西。
- 任何 pyo3 后端依赖抛 `BaseException` 时，store-open 路径都能给出**有类型、已处理**的
  错误，而不是裸奔到 ASGI 栈。
- 依赖每进程 panic 一次这件事变得**可见**（日志），一旦 chroma 行为变化能被发现。

## Scope

| 文件 | 改动 |
|---|---|
| `server/stores.py` | `StoreManager.get()` 内 open 处加一次重试 + `BaseException` 捕获；新增 `StoreOpenError` |
| `server/app.py` | 为 `StoreOpenError` 注册 exception handler（503） |
| `tests/test_store_lifecycle.py` | 新增回归测试（详见 AC1–AC3） |

不改：data-root 锁、LRU/淘汰逻辑、`release()`/`_pending_close` 握手、`close_all()`、
`user_dir()` 校验、任何 route 签名。

## Implementation Path

### 1. open 的重试（`StoreManager.get()`）

现在的代码（`server/stores.py:118-128`）在持锁状态下直接调用 `SodaMem.open()`。改成：
把这一行抽成一个私有方法 `_open_store(path)`，语义是"最多开两次"：

- 第 1 次 `SodaMem.open(path, extractor=...)`。
- 捕获 `BaseException`：
  - **立刻原样 re-raise 的类型：`KeyboardInterrupt`、`SystemExit`。** 理由：这两个是
    进程控制信号，不是 store 故障；吞掉它们会让 Ctrl-C 和 `sys.exit()` 变成一次
    莫名其妙的重试。用 `except (KeyboardInterrupt, SystemExit): raise` 放在
    `except BaseException` 之前，让类型清单在代码里一眼可查。
  - 其余一切（包括 `PanicException`、以及普通的 `Exception` 子类）→ 记一条
    `logger.warning`（含 user_id、异常类型名、`exc_info=True`），然后**重试一次**。
  - 第 2 次仍失败 → `raise StoreOpenError(...) from exc2`。
- 已评估但**不**加入 re-raise 清单的类型，写在代码注释里供 review 核对：
  - `GeneratorExit`：`get()` 是普通函数不是生成器；`lease()` 的 `GeneratorExit` 只会
    落在 `yield` 处，那时 open 早已返回。到不了这里。
  - `asyncio.CancelledError`：`/v1/context` 等路由是 `def`（同步），由 threadpool 执行，
    取消不会注入 worker 线程；`SodaMem.open` 自身也不 await 任何东西。到不了这里。
  - 结论：清单就是 `KeyboardInterrupt` + `SystemExit`，不多不少。

重试仍在 `self._lock` 内进行 —— 与现状一致，open 本来就是持锁的，重试不引入新的并发语义。

### 2. 重试为什么不需要缓存簿记

失败路径在 `self._cache[user_id] = mem` **之前**就抛出，`self._inflight` 的自增在其后，
`_evict_locked()` 也在其后。所以失败的 open 既不入缓存、也不留借用计数。本次改动必须
保持这个顺序（AC6 有对应测试）。

### 3. 二次失败的类型化出口

新增（`server/stores.py`，与 `InvalidScopeError` 并列）：

```
class StoreOpenError(SodaMemError):  # code=ErrorCode.VECTOR_STORE_UNAVAILABLE
```

复用核心 taxonomy 里已有的 `ErrorCode.VECTOR_STORE_UNAVAILABLE`（`sodamem/errors.py:19`），
不新造 code 枚举。

`server/app.py` 在现有 `@app.exception_handler(SodaMemError)` 旁再注册一个
`@app.exception_handler(StoreOpenError)`，返回 **503** +
`ErrorBody(code="vector_store_unavailable", message=...)`。
Starlette 的 handler 查找按 `type(exc).__mro__` 逐级匹配，更具体的 `StoreOpenError`
handler 先命中，`SodaMemError` 的 400 分支不受影响。

为什么是 503 而不是走现成的 400 分支：store 打不开是服务端资源不可用，客户端没做错任何事。
`/v1/answer` 缺 provider 时已经是 503 的先例（`tests/test_server_routes.py:581`）。

### 4. 日志形态

重试路径必须 `logger.warning` 而不是 debug，且必须带 `exc_info=True` —— 目的就是让
"依赖每进程 panic 一次"这件事在日志里显形。二次失败额外一条 `logger.error`。

## 关于「守护进程启动时预热 store」的建议 —— 明确排除在本次改动之外

**建议：不做，另开 follow-up issue。** 三个理由：

1. **它不能替代重试，只会把 panic 挪到更糟的位置。** 没有本次的 `BaseException` 兜底，
   启动期预热就是让 panic 打在守护进程启动路径上 —— 从"一个请求 500"升级成"进程起不来
   或者启动日志里一个没人看的裸 traceback"。重试是预热的前置条件，不是它的替代品。
2. **它自带一整套本 issue 没有答案的策略问题**：预热哪些 user？（枚举 data_root 下的
   目录？那 500 个用户的部署怎么办 —— `store_cache_max` 会立刻把它们淘汰掉，预热的收益
   归零。）预热失败要不要拒绝启动？预热是同步阻塞健康检查，还是后台线程？这些每一个都
   会改动 LRU/启动语义，而本次的约束是"不改 LRU/淘汰行为"。
3. **~400 ms 冷启动是性能问题，不是正确性问题。** 本 issue 的验收门是"第一个请求返回
   200"，重试就能达标。把性能优化混进 bugfix 会让 AC5 的手工验证读不出到底是谁修好的。

`sodamem daemon ensure` 里的健康检查（`sodamem_cli/daemon.py:104-119`）确实是现成的接缝，
follow-up 可以直接用；本 spec 记录该接缝的位置，不在此处使用它。

## Acceptance Criteria

- [ ] **AC1（回归门，缺它必挂）**：新增测试复现原始缺陷形态 —— 在 open 接缝上注入
      **首次调用抛 `BaseException` 直接子类**（自定义 `class FakePanic(BaseException)`，
      不依赖真实 chroma panic，小 fixture store 复现不出来），第二次返回正常 store。
      断言：`StoreManager.get()` 返回可用 store，且 open 被调用了 **2** 次。
      去掉 fix 后此测试必须失败（以 `BaseException` 逃逸的形式）。
- [ ] **AC2**：新增测试证明**非 panic 的真实故障不会被永久掩盖** —— open 接缝**每次都**
      抛错时，`get()` 抛出 `StoreOpenError`，open 恰好被调用 **2** 次（不是无限重试），
      且原始异常挂在 `__cause__` 上。
- [ ] **AC3**：新增测试证明 `KeyboardInterrupt` 与 `SystemExit` **不被吞** —— 首次调用
      分别抛这两个类型时，`get()` 原样抛出同一类型（不是 `StoreOpenError`），且 open
      只被调用 **1** 次（没有重试）。
- [ ] **AC4**：失败的 open 不入缓存 —— AC2 的场景之后，`StoreManager` 的 `_cache` 和
      `_inflight` 里都没有该 user_id 的条目；随后一次成功的 `get()` 正常工作。
- [ ] **AC5**：LRU/淘汰与借用计数行为不变 —— `tests/test_store_lifecycle.py` 原有用例
      全部不改动即通过；重试成功后 store 正常入缓存，第二次 `get()` **不再**调用 open。
- [ ] **AC6**：`StoreOpenError` 经 HTTP 出口为 **503** + `code="vector_store_unavailable"`
      的 `ErrorBody`（route 级测试，注入永久失败的 open 接缝打 `/v1/context`），不是未处理的 500。
- [ ] **AC7**：重试路径打出 `WARNING` 级日志，内容含 user_id 和被吞异常的类型名
      （用 `caplog` 断言）。
- [ ] **AC8**：现有全量测试仍通过 —— 基线为 main 今日的 **820 passed / 1 skipped**，
      改动后不得有新增失败（新增用例使总数上升是预期的）。
- [ ] **AC9（手工验证，必须贴真实输出）**：对真实会 panic 的 store 验证 —— 全新启动的
      守护进程对 **第一个** `/v1/context` 请求返回 **200**（今天返回 500）。
      store：`/private/tmp/claude-501/-Users-aaron-w-Desktop-SodaMem/7fbf6096-fa4c-4599-ac86-c9710a5180ba/scratchpad/benchdata`
      （`SODAMEM_DATA_ROOT` 指向它，`user_id=bench`）。
      本 worktree 没有 venv，用 `/Users/aaron.w/Desktop/SodaMem-worktrees/issue-9/.venv`。
      记录必须包含：HTTP 状态码、响应体片段（citations 非空）、以及日志里那条重试
      WARNING（证明走的是重试路径而不是碰巧没 panic）。

**PARTIAL = FAIL。** AC1 / AC3 / AC9 任一缺失即为未通过。
