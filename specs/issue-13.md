# Spec: store-open 路径上的 BaseException 逃逸成未处理 500

Record: GitHub Issue #13 — https://github.com/SodaMem/SodaMem/issues/13
Branch: fix/13-store-open-panic
Worktree: /Users/aaron.w/Desktop/SodaMem-worktrees/issue-13
相关: #14（`sodamem daemon ensure` 从 PATH 解析 uvicorn）

## 撤回：本 spec 的初版前提是错的

初版 spec（同文件，commit 1647256 之后写就）把问题定义为"chromadb 1.1.1 在受影响 store 上
首次 open 必 panic，第二次成功"，并据此要求"重试一次"。**这个前提不成立，据此推导出的
AC1/AC2/AC9 已被推翻。** 留下这段而不是悄悄改写，是因为后来者需要知道 AC 为什么变了。

错在哪：

1. **不是 chroma 的 bug，是一次 chromadb 降级**，纯属测试机上的环境事故。这台机器上有
   两份 chromadb：项目 venv 里 **1.1.1**，homebrew 里 **1.5.8**。`sodamem daemon ensure`
   从 PATH 解析 `uvicorn`，某一次跑起来的守护进程用的是 homebrew 解释器，**它把测试 store
   的 schema 向前迁移了**。证据：那个"会 panic"的 store 的 chroma `sysdb` 有 **10** 条
   migration（`00010-collection-schema`），正常建出来的 store 都是 **9** 条。1.1.1 于是从
   自己 9 元素列表的 index 10 开始切片 → `rust/sqlite/src/db.rs:157` 的 panic。
   双向确认过：1.5.8 打开那个 store 干干净净；1.1.1 下新建的 1000-fact store 从不 panic，
   全新守护进程的**第一个**请求 200 且 `0 degraded`，3 次重启 3 次如此。
   所以"每次守护进程启动后第一个请求 500"**在一般情况下是假的**，chromadb 版本一致的用户
   根本遇不到。PATH 解析问题已单独立为 #14。

2. **重试必须去掉。** 检查过重试拿到的到底是什么 store：panic 之后 chroma 在该进程的
   余生里已经死了，第二次 open"成功"只是因为 `Store._init_chroma` 吞掉了后续的
   `ValueError` 并以 lexical-only 继续：

   ```
   degraded : ['vector_route_failed' × 3]
   routes   : {'bm25_fact_direct': 10, 'vector_fact_direct': 0, 'vector_span': 0, 'raw_vector': 0}
   ```

   健康 store 是 `degraded: []` 且各向量路由都有量。**重试等于拿一个响亮、可诊断的失败，
   换一个安静的 200 和被砍掉一半的召回。** 对一个版本不匹配的环境故障，这是所有可选行为
   里最差的一个。

## 第二次修订（code review 之后）

第一次修订推翻了前提；这一次修订的是 **AC8 自己写错了**，所以改的是 spec 不是代码。
留档同上：AC 变过就要说清为什么。

1. **AC8 原文「单测至少断言消息含 `chromadb` 与 `PATH`」是无条件的**，实现照做的结果是
   每一个失败都被追加 chroma hint —— 一个 `PermissionError` 也被告知"某个更新的 chromadb
   写过这个 store，去查是哪个解释器在服务"。**自信的错误线索比没有线索更贵。** 正确行为是
   把 hint 变成"挣来的"：只在失败确实是 chroma 形状时给（cause 名字含 `Panic`，或该 store
   的 sysdb migration 数超过所装 chromadb 自带的数量），其余给通用指引。AC8 已按此重写，
   并补上互补的反向要求（(ii)）。
2. **AC8 漏掉了一个本次改动【新引入】的暴露面**：改动前这条路径是 body 为空的未处理 500，
   现在它是一个有 message 的 503，而 `host` 默认 `0.0.0.0`。原始异常文本里带绝对路径。
   已补为 AC8(v)。
3. **review 另发现诊断在全局 `StoreManager._lock` 内跑**（探针读的 sqlite 可能正被另一个
   进程锁着 —— 恰恰是本诊断要描述的双解释器场景；实测被独占写者持锁时 5.2 s）。修法是结构性的
   而不是加个超时：`_open_store()` 只抛裸的类型化错误，诊断挪到 `get()` 的
   `except StoreOpenError` 里、**锁已释放之后**再跑（见 Implementation Path 第 1 节）。
   这不是新增 AC，而是 AC5「不改 LRU/借用行为」的题中之义 —— 一次 store 打不开不得让
   其他所有用户排队。代码里有 docstring 和对应用例钉住这个结构，后来的重构不要把它挪回去。

## Problem

真正的缺陷与 chroma 无关，只有一条：

**`SodaMem.open()` 抛出的 `BaseException` 会一路逃进 ASGI 栈，变成一个未处理的 500；
并且触发它的条件没有以任何人能据以行动的方式被报告出来。**

为什么逃得掉 —— 类型：

```
type       : pyo3_runtime.PanicException
Exception? : False
MRO        : ['PanicException', 'BaseException', 'object']
```

它直接继承 `BaseException`。`server/stores.py` 原来的 `except Exception:`、FastAPI 自己的
错误中间件、以及本仓库其他所有 handler，**结构上就看不见它**。任何 pyo3 后端的依赖都能
制造这个形状。客户端拿到的是一个没有 `code`、没有 message 的 500，运维拿到的是一条裸的
rust panic 文本 —— 它不会告诉你"你的 store 是被另一个 chromadb 迁移过的"。

所以本次要交付的是两件事，都不依赖 chroma 做任何改变：
**（a）让这条路径有类型、被处理；（b）让失败的消息说清该去修什么。**

## Value

- 打不开的 store 得到一个**有类型、已处理**的 503 + 机器可读 `code`，而不是裸奔到 ASGI 栈的 500。
  这对任何 pyo3 后端依赖都成立，不只是这一次的 chroma。
- 失败消息直接点名"装的是哪个 chromadb 版本 / 这个 store 的 schema 到了第几号 migration /
  最可能的原因是 `daemon ensure` 从 PATH 取 uvicorn"。**这是本次改动现实价值最大的部分** ——
  它把一个要花掉一下午的诊断变成一条读得懂的错误。
- 明确拒绝"重试把它变成 200"这条路：环境故障必须响亮地失败，不能靠降级掩盖。

## Scope

| 文件 | 改动 |
|---|---|
| `server/stores.py` | 新增 `StoreOpenError`、`_CHROMA_VERSION_HINT`、`_diagnose_store_open()`、`StoreManager._open_store()`；`get()` 改为经 `_open_store()` 打开 |
| `server/app.py` | 为 `StoreOpenError` 注册 exception handler（503 + `vector_store_unavailable`） |
| `tests/test_store_lifecycle.py` | 新增 8 个用例（AC1–AC7 的载体） |

不改：data-root 锁、LRU/淘汰逻辑、`release()`/`_pending_close` 握手、`close_all()`、
`user_dir()` 校验、任何 route 签名、`SodaMemError` 原有的 400 分支。

## Implementation Path

### 1. 一次 open，捕获 `BaseException`

`StoreManager.get()` 里那一行 `SodaMem.open()` 抽成 `_open_store(user_id, path)`：

- **只开一次。** 价值在 catch，不在 retry（理由见上文"撤回"第 2 条）。
- **先 `except (KeyboardInterrupt, SystemExit): raise`**，再 `except BaseException`。
  re-raise 清单**恰好这两个**：它们是进程控制信号，不是 store 故障；把 Ctrl-C 或
  `sys.exit()` 报成"store 坏了"是撒谎。
- 已评估并排除、需写在 docstring 里供 review 核对的两个：
  - `GeneratorExit` —— `get()` 是普通函数；`lease()` 的 GeneratorExit 只会落在它的
    `yield` 处，那时 open 早已返回。到不了这里。
  - `asyncio.CancelledError` —— 碰 store 的路由都是 `def` 不是 `async def`，跑在
    threadpool 里，取消不会注入 worker 线程；`SodaMem.open` 不 await 任何东西。到不了这里。
- 捕获后：`logger.error(..., exc_info=True)`（含 user_id、异常类型名与**原始异常文本**），
  然后 `raise StoreOpenError(...) from exc`。**进错误对象的只有类型名**，原文留在日志里
  （理由见 AC8(v)）。
- **诊断不在锁内跑。** `_open_store()` 只抛裸的类型化错误；`get()` 用
  `except StoreOpenError` 在 `with self._lock` **外面**接住它，补上诊断再抛。
  `self._lock` 串行化所有用户的 `get()/release()/close_all()`，而诊断要读一个可能正被
  别的进程锁住的 sqlite 文件；放在临界区里等于让所有无关用户排在那把文件锁后面。
  锁外重算路径是安全的：`user_dir()` 是 settings + user_id 的纯函数，不读写任何 manager 状态。

### 2. 诊断消息才是交付物

`_diagnose_store_open(path)`：读该 store 的 `chroma/chroma.sqlite3` 的 `migrations` 表
（`dir='sysdb'` 的条数与最大 filename）+ `importlib.metadata.version("chromadb")`，
拼成一句点名版本与 schema 状态的话，尾部接 `_CHROMA_VERSION_HINT`（说明"被更新的 chromadb
写过的 store 会让旧 chromadb 的 rust 绑定 panic"，以及"`sodamem daemon ensure` 从 PATH 取
uvicorn"这个坑）。

**每个探针单独 `try/except`。** 一个会抛异常的诊断会用自己的故障顶掉真正的故障 —— 这是
诊断代码最常见的死法。store 的 chroma db 读不出来时，退回静态 hint。

### 3. 类型化出口

`StoreOpenError(SodaMemError)`，`code=ErrorCode.VECTOR_STORE_UNAVAILABLE`（复用
`sodamem/errors.py:19` 已有枚举，不新造 code）。`server/app.py` 在 `SodaMemError` handler
**旁边**再注册一个 `StoreOpenError` handler → **503** + `ErrorBody(code="vector_store_unavailable")`。
Starlette 按 `type(exc).__mro__` 逐级查找 handler，更具体的先命中，原有 400 分支不受影响。

503 而不是 400：store 打不开是服务端资源不可用，客户端没做错任何事。`/v1/answer` 缺
provider 时返回 503 是既有先例（`tests/test_server_routes.py:581`）。

### 4. 失败的 open 不留痕

失败在 `self._cache[user_id] = mem` **之前**抛出，`_inflight` 自增与 `_evict_locked()` 都在
其后。所以既不入缓存、也不留借用计数、也不进 `_pending_close`。这个顺序必须保持（AC4 有测试）。

## 关于「守护进程启动时预热 store」—— 明确排除

**建议：不做。** 初版 spec 给的第一条理由（"预热不能替代重试"）随着重试被删除已经失效，
这里换成成立的理由：

1. **它解决的不是本 issue 的问题。** 触发这次 panic 的是环境里两个 chromadb（#14），
   预热只会让同一个故障更早发生，不会让它消失，也不会让消息更清楚 —— 消息已经在本次改动里
   解决了。
2. **它自带一整套本 issue 没有答案的策略问题**：预热哪些 user？枚举 data_root 下的目录的话，
   `store_cache_max` 会立刻把它们淘汰掉，收益归零；预热失败要不要拒绝启动？同步阻塞健康检查
   还是后台线程？每一个都会动 LRU/启动语义，而本次约束是不改这些。
3. **~400 ms 冷启动是性能问题，不是正确性问题**，混进 bugfix 会让 AC9 的手工证据读不出
   到底是谁修好的。

`sodamem_cli/daemon.py:104-119`（`ensure` 的健康检查循环）是现成的接缝，follow-up 可直接用。
本 spec 只记录位置，不在此使用。

## Acceptance Criteria

- [ ] **AC1（回归门）**：在 open 接缝上注入 **`BaseException` 直接子类**（自建
      `class FakePanic(BaseException)`，不依赖真实 chroma panic —— tmp_path 的 fixture store
      太小且版本正确，等真 panic 的测试有没有 fix 都会通过）。断言：`get()` 抛出
      `StoreOpenError`，`__cause__` 是注入的那个异常，open **恰好被调用 1 次**，消息里带
      user_id。去掉 `except BaseException` 后此测试不是断言失败，而是 FakePanic 直接逃出
      `get()` —— 正是真 panic 逃进 ASGI 栈的形状。
- [ ] **AC2**：普通 `Exception`（如 `RuntimeError`）走**同一条**路径 —— `BaseException`
      捕获是加宽不是替换，普通异常也必须变成 `StoreOpenError`（`__cause__` 保留），
      open 恰好 1 次，不得漏成 500。
- [ ] **AC3**：`KeyboardInterrupt` 与 `SystemExit` **不被吞** —— 分别注入时 `get()` 原样
      抛出同一类型（不是 `StoreOpenError`），open 恰好 1 次。
- [ ] **AC4**：失败的 open 不留痕 —— 失败后 `_cache` / `_inflight` / `_pending_close` 里
      都没有该 user_id；撤掉接缝后一次真实 `get()` 正常工作。
- [ ] **AC5**：LRU / 淘汰 / 借用计数行为不变 —— `tests/test_store_lifecycle.py` 原有用例
      一行不改即通过；成功打开的 store 正常入缓存，第二次 `get()` 不再调用 open。
- [ ] **AC6**：HTTP 出口是 **503** + `code="vector_store_unavailable"` 的 `ErrorBody`
      （注入失败接缝后打 `/v1/context` 的 route 级测试），不是未处理的 500，且 message 里
      带 `chromadb`。
- [ ] **AC7**：失败打 **ERROR** 级日志（不是 WARNING —— 没有重试路径了，这是一次硬失败），
      含 user_id 与被吞异常的类型名，且 `exc_info` 非空（traceback 必须留下）。
- [ ] **AC8（诊断可行动性 —— 本次价值最大的部分）**：诊断必须**有条件地**给出，
      它是挣来的、不是无脑追加的。
      - **(i) chroma 形状的失败**（cause 类型名含 `Panic`，**或**该 store 的 sysdb
        migration 数超过所装 chromadb 自带的数量）：消息必须同时点名 **(a)** 安装的
        chromadb 版本及其已知 migration 数、**(b)** 这个 store 的 schema 状态
        （sysdb migration 号 + 文件名）、**(c)** 去哪儿看（PATH / `sodamem daemon ensure`
        取 uvicorn）。单测断言消息含 `chromadb` 与 `PATH`，且
        `exc.code is ErrorCode.VECTOR_STORE_UNAVAILABLE`。
      - **(ii) 非 chroma 形状的失败必须【不】带 chroma hint。** 对 `PermissionError`
        说"检查是哪个 chromadb 在 PATH 上"是一个**自信的错误线索**，比不给线索更贵 ——
        它把读者送去查一个跟故障无关的方向。这类失败只给"完整 traceback 在服务日志里"
        这种通用指引。需要独立用例：注入 `PermissionError`，断言消息**不含** `PATH`
        与 `NEWER chromadb`，但仍含异常类型名。
      - **(iii) (b) 必须由单测覆盖，不能只靠 AC9 的人工输出**：手工在
        `<store>/chroma/chroma.sqlite3` 里造一张 `migrations` 表（`dir='sysdb'` 若干行，
        外加一行别的 `dir` 以捕获忘记过滤的探针），断言消息里出现该 migration 号与文件名。
        那次 sqlite 读是整个诊断里最脆的一环 —— 表名或列名一改它就静默退化成静态 hint，
        其余断言一个都不会红。
      - **(iv) 诊断探针自身抛异常时不得顶掉真正的错误** —— store 的 chroma db 不可读时
        仍正常抛出 `StoreOpenError` 且 `__cause__` 保持为原始异常（需要一个用例）。
      - **(v) 503 body 不得泄露文件系统路径。** 这是本次改动**新引入**的暴露面：改动前
        这条路径是一个 body 为空的未处理 500，而 `server/settings.py` 的 `host` 默认
        `0.0.0.0`。原始异常文本里有绝对路径（经 `_build_extractor` 还可能有 provider 配置），
        所以进 body 的只能是异常**类型名**，原文连同 traceback 留在日志里（`exc_info`）。
        需要独立用例：注入一个 message 里带绝对路径的异常，断言路径既不在 message 里、
        也不在 `details` 里。
- [ ] **AC9（手工验证，两侧都要贴真实输出）**：
      - **干净 store**（版本一致）：全新守护进程的**第一个** `/v1/context` 请求 **200**，
        `degraded: []`，citations 非空（已测得 24 citations）。这一侧证明本次改动没有把
        正常路径变成 503。
      - **schema-forward store**（被 1.5.8 迁移过的那个）：全新守护进程的第一个请求 **503**，
        body `code=vector_store_unavailable`，message 里同时出现 chromadb 1.1.1、migration 10、
        以及 PATH 起因 —— 此前这里是一个未处理的 500 加一条裸 rust panic。
      两侧都必须记录状态码与响应体片段。本 worktree 没有 venv，用
      `/Users/aaron.w/Desktop/SodaMem-worktrees/issue-9/.venv`；schema-forward store 在
      `/private/tmp/claude-501/-Users-aaron-w-Desktop-SodaMem/7fbf6096-fa4c-4599-ac86-c9710a5180ba/scratchpad/benchdata`
      （`user_id=bench`）。
- [ ] **AC10**：现有全量测试仍通过 —— main 基线 **820 passed / 1 skipped**，本次新增用例后
      应为 **835 passed / 1 skipped**，无新增失败。

**PARTIAL = FAIL。** AC1 / AC3 / AC8 / AC9 任一缺失即为未通过。
