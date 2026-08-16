<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/logo-dark.webp">
  <img src="../assets/logo.webp" alt="SodaMem" width="260">
</picture>

**为 AI Agent 打造的自演化记忆层。**

多数记忆系统只把你说过的话存下来就不管了——今天是对的，可你的生活一变，它就悄悄错了。SodaMem 跟着 agent 一起演化：事实被取代而不是覆盖，实体档案按需重建而不会悄悄过时，每个答案依然能追溯到它出自哪一轮对话。检索零 LLM 调用，同一个问题每次都得到同一个答案。

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](../../LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](../../pyproject.toml)
[![LongMemEval](https://img.shields.io/badge/LongMemEval-92.8%25-brightgreen.svg)](../../benchmarking/artifacts/)
[![LoCoMo](https://img.shields.io/badge/LoCoMo-86.88%25-brightgreen.svg)](../../benchmarking/README.md#locomo-cat-1-4)
[![Discussions](https://img.shields.io/github/discussions/SodaMem/SodaMem?logo=github&label=discussions)](https://github.com/SodaMem/SodaMem/discussions)

<!-- langs -->
[English](../../README.md) · **简体中文** · [日本語](README.ja.md) · [한국어](README.ko.md) · [Français](README.fr.md) · [Español](README.es.md) · [Deutsch](README.de.md) · [Português](README.pt-BR.md)
<!-- /langs -->

[Agent 接入](#agent-接入) · [跑分](#跑分) · [快速开始](#快速开始) · [为什么还需要一个记忆层](#为什么还需要一个记忆层) · [安装](#安装) · [各种接入方式](#各种接入方式) · [编码工具](#编码工具) · [自托管](#自托管) · [文档](#文档)

<img src="../assets/benchmark-cost-accuracy.webp" alt="Cost-accuracy trade-off on LongMemEval-S" width="760">

*纵轴是准确率，横轴是每个问题的预估 API 成本。有意义的象限在左上角。*

</div>

---

## Agent 接入

| Runtime | 接入方式 | 指南 |
|---|---|---|
| **Hermes Agent** | MCP | [`integrations/hermes/README.md`](../../integrations/hermes/README.md) |
| **DeepSeek Harness** | MCP | [`integrations/deepseek-harness/README.md`](../../integrations/deepseek-harness/README.md) |
| **通用 / 任意 MCP 客户端** | MCP | [`mcp_server/README.md`](../../mcp_server/README.md) |
| **LangGraph** | Python adapter | [`adapters/README.md`](../../adapters/README.md) |
| **CrewAI** | Python adapter | [`adapters/README.md`](../../adapters/README.md) |
| **OpenAI Agents SDK** | Python adapter | [`adapters/README.md`](../../adapters/README.md) |
| **Vercel AI SDK** | TS adapter | [`sdk-ts/`](../../sdk-ts/) |
| **Claude Code、Cursor 等编码类客户端** | CLI + hooks | 见[编码工具](#编码工具) |

完整索引，包括 MCP 工具 schema 和 adapter 细节：[`integrations/README.md`](../../integrations/README.md)。

---

## 跑分

<div align="center">
  <img src="../assets/benchmark-longmemeval.webp" alt="LongMemEval: SodaMem 92.8%, Hindsight 91.4%, Mem0 OSS 91.0%" width="720">
</div>

LongMemEval **92.8%（464/500）**。

| | |
|---|---|
| reader / planner / judge | `deepseek-v4-flash` |
| 判分提示词 | LongMemEval 官方 `evaluate_qa.py` 模板，逐字节相同 |
| 跑分店 | `longmemeval_s_500_Hobs_entitysubj`,500 用户 / 235,840 条事实 |

**每一条答案和每一条检索到的记忆都已公开**，见
[`benchmarking/artifacts/`](../../benchmarking/artifacts/)——500 条逐字答案，
8427 条证据。你可以用任意 judge 重新判分，也可以把我们检索到的上下文喂给
你自己的 reader 看分数怎么变。两件事都不需要接触我们的任何服务。

<div align="center">
  <img src="../assets/benchmark-locomo.webp" alt="LoCoMo: SodaMem 86.88%, MemMachine 91.69%, Hindsight 89.61%, MIRIX 85.38%, Memobase 75.78%, Mem0 OSS 66.88%" width="720">
</div>

LoCoMo **86.88%（1338/1540）**。端到端问答准确率（end-to-end QA accuracy），
由 LLM-as-judge 判分。

| | |
|---|---|
| reader / planner / judge | `deepseek-v4-flash` |
| 判分提示词 | LongMemEval 官方模板，逐字节复制 |
| 跑分店 | `locomo10_Hobs`，10 个用户库 / 2,905 条 fact event |
| 代码 | 一个预发布构建 —— 本仓库公开的历史从 v0.1.0 开始 |

**LoCoMo 没有发布任何逐题产物** —— 没有 answers，没有检索上下文，没有 run 目录。
公开的是
[`benchmarking/README.md` 的 LoCoMo 一节](../../benchmarking/README.md#locomo-cat-1-4)：
分类别拆分、逐会话分布、provenance 与复现步骤。

---

## 快速开始

这是 Python 路径。要接入 agent 框架或 MCP 客户端？见 [Agent 接入](#agent-接入)。要从 TypeScript/Node 调用？见 [各种接入方式](#各种接入方式)。要作为共享服务运行？见 [自托管](#自托管)。

### 示例

```bash
pip install "sodamem[chroma,llm]"
```

```python
from sodamem import SodaMem
from sodamem.llm import create_provider_from_env      # SODAMEM_LLM_API_KEY
from sodamem.memory.ingest.extractor import FactEventExtractorV2

# 写入需要一个模型来抽取事实；读取从不需要。
mem = SodaMem.open("./data", extractor=FactEventExtractorV2(create_provider_from_env()))

mem.ingest(
    [{"role": "user", "content": "其实我从可爱岛改去欧胡岛了。"}],
    user_id="u1", session_id="s1", session_time="2023-05-25",
)

block = mem.build_context("我要住哪里？", user_id="u1", token_budget=1000)
print(block.text)        # 可直接拼进 prompt —— 零 LLM 调用
print(block.citations)   # 这段文字里每一句的证据出处
```

`SodaMem.open()` 会自动创建 `./data`。只有 `.ingest()` 需要 extractor——
不传这个参数就是一个只读库，`search` / `build_context` 完全照常工作。

**你的数据不出本机。** 无遥测、无埋点、无回调——默认安装唯一一次对外请求，
是把 90MB 的 MiniLM 嵌入模型下载到 `~/.cache/chroma/`，此后只跟你的磁盘
说话。预先放好这个缓存就能完全离线运行。

---

## 为什么还需要一个记忆层

多数记忆系统记录的是**你说过什么**。真正让它们失效的问题是**这话从哪一刻起
不再成立**和**它到底出自哪里**——而这两件事要靠数据模型解决，不是靠更大的
向量索引。


| 这个问题 | 常见做法 | SodaMem |
|---|---|---|
| 这条记忆是哪来的？ | 一个相似度分数，外加几个元数据字段 | `FactEvent → SourceSpan → RawTurn` 外键链，一路指到具体那一轮对话 |
| 用户改口了怎么办？ | 覆盖写，旧值就此消失 | 只增不改，再加一条 `SUPERSEDES` 边；旧版本以 `valid_until` 收尾，依然可读 |
| 「我去年搬去了芝加哥」和「我明年要搬」 | 同一个时间戳 | 四条时间轴：发生 / 有效 / 说出 / 存入 |
| 取一次上下文要花多少钱？ | 每检索一次就过一遍 LLM | `build_context` **零** 模型调用，直接返回带引用的成品 prompt |
| 同一个查询问两次，结果一样吗？ | 取决于模型这次怎么采样 | 确定性融合：同一个库、同一个查询、同一个结果 |
| 它为什么忘了 X？ | 没有答案 | `/v1/events` 记录每一次新增、覆盖与删除，连原因一起 |

下面每个小节展开其中一行——而且每一行都能在这个仓库里查证，不需要你选择相信。

### 每条记忆都带着凭证

检索回来的记忆不是一段浮空的文字，它指向产生它的那一轮对话：

```
evidence_id  = ev_fact:fact_6ada707b…
support      = "能推荐欧胡岛上一处不太拥挤的海滩吗？"      ← 用户原话，逐字
predicate    = 用户想在欧胡岛找一处不拥挤的海滩
entities     = location=欧胡岛 | occasion=生日
source       = session_40 / turn_10          ← 精确到哪一轮，不是"某次聊天"
date         = 2023-05-25
```

`FactEvent → SourceSpan → RawTurn` 是一条真实的外键链，不是相似度分数。
用户问「你凭什么这么认为」时有答案；合规问「这条事实哪来的」时有行可查。

### 四组时间轴，而不是一个时间戳

| 字段 | 它回答的问题 |
|---|---|
| `occurred_start` / `occurred_end` | 事情**发生**在什么时候 |
| `valid_from` / `valid_until` | 这条事实**成立**于哪段时间 |
| `document_time` | 用户**说出**它的时刻 |
| `created_at` | 我们**存下**它的时刻 |

只有一个时间戳，就分不清「我去年**搬到**了芝加哥」和「我明年**要搬去**芝加哥」，
也无法表达一条已经失效的事实。

---

## 安装

| extra | 带来什么 |
|---|---|
| *(base)* | 数据模型、存储、BM25 检索、写入 —— **四个依赖，没有一个重的** |
| `chroma` | 向量检索 + 本地 ONNX 嵌入模型（`SodaMem.open()` 需要它） |
| `llm` | OpenAI 兼容的模型服务（OpenAI / DeepSeek / Gemini 同一套协议） |
| `anthropic` | Anthropic（它自己的 SDK） |
| `answer` | planner + reader 的答题路径 |
| `server` | HTTP 服务（FastAPI + uvicorn，刻意只有三个包） |
| `mcp` | MCP 服务端 |

基础安装只拉 `pydantic`、`numpy`、`rank-bm25`、`python-dateutil`。
有一道 CI 门守着——这个列表要是被谁不小心加长了，构建直接失败。



---

## 各种接入方式

**HTTP** —— `add` / `search` / `context` / `answer`，另有批量写入、取代、
事件流、指标、token 用量：

```bash
curl -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  localhost:8000/v1/context \
  -d '{"user_id":"u1","query":"他偏好什么？","token_budget":1000}'
```

`/v1/context` 和 `/v1/search` 都收 JSON body；`/v1/context` 同时支持
用 query 参数发 GET——它本来就是一个纯读接口。唯一的 Python-only 例外是
`build_context(organizer=...)`——它会在检索结果集上跑一个 LLM 驱动的
organizer，用来回答"列出你知道的关于我的一切"这类问题；`/v1/context`
从不接受这个参数，所以 HTTP 这一侧的零 LLM 保证不会被某个请求参数翻盘。

**SDKs** —— TypeScript 走 HTTP（[`sdk-ts/`](../../sdk-ts/)，零运行时依赖，
ESM + CJS）：

```bash
npm i sodamem
```

```typescript
import { SodaMemClient } from "sodamem";

const mem = new SodaMemClient({ baseUrl: "http://localhost:8000", apiKey: process.env.SODAMEM_API_KEY! });
const block = await mem.context({ user_id: "u1", query: "what do they prefer?", token_budget: 1000 });
```

Python 直接用库本身——`import sodamem`，你已经在网络这一层之内了。

**Agent 框架** —— LangGraph、CrewAI、OpenAI Agents SDK、Vercel AI SDK。
作用域在构造工具时绑定，**绝不出现在模型看得到的 schema 里**：
一个模型能选的 `user_id`，就是模型能幻觉出来的 `user_id`。

**MCP** —— 8 个工具，含 `entity_timeline`（某个实体的完整历史，按时间排序，
每条仍指回它的出处）和 `explore_memory`（沿图向外走）。
其中 6 个是读，始终可用；两个会改数据的
（`add_memories`、`delete_memory`）只在 `SODAMEM_MCP_ALLOW_WRITE=true` 时
才注册，而 `sodamem install` 会把这一行写进它生成的客户端配置里。

**Web 控制台** —— 按租户浏览和检查单条记忆，已打包进镜像。

---

## 编码工具

**第一步。** 启动 daemon —— 这是唯一拥有各个库的进程：

```
sodamem daemon ensure
```

**第二步。** 把客户端接上去：

```
sodamem install claude-code
```

每个客户端都能拿到 MCP 工具面。其中四个还有 **hooks**，让记忆的召回和写入
不需要模型主动决定调用工具——在编码场景里它大多数时候确实不会主动调，因为
它正忙着读文件。

hooks 能做到什么并不统一，因为各家的 hook 系统本来就不一样。下面是每个
客户端实际支持的能力，`sodamem clients` 打印的是同一份信息：

| 客户端 | 召回（Recall） | 写入（Retain） |
|---|---|---|
| Claude Code | 每次 prompt | 每一轮 + session 结束 |
| GitHub Copilot CLI | 每次 prompt | 每一轮 |
| Cursor | session 开始时（项目简报） | — |
| Codex CLI | session 开始时（项目简报） | — |
| Claude Desktop、VS Code、Windsurf、Zed、OpenCode | 仅 MCP 工具 | 仅 MCP 工具 |

Cursor 的 `beforeSubmitPrompt` 能读 prompt，但不能注入任何内容（它的文档里
列出的能注入的事件只有三个，这不是其中之一）；Cursor 和 Codex 都不会把
transcript 路径传给 hook，所以写入 hook 根本无从读起。这两家改为在 session
开始时给一份项目简报，写入则通过 `add_memories` 工具完成。我们不会装一个
注定什么都做不了的 hook。

运行前有三件事值得了解：

**一个 daemon，多个编辑器。** 每用户的存储是没开 WAL 的 SQLite，只能有一个
进程打开它（ADR 0001 §2）。所以 `install` 默认会把每个客户端都指向同一个
正在运行的服务，而不是让各自都起一份——如果你特意选择本地存储
（`--local-store`），第二个客户端会直接拒绝启动，而不是悄悄把第一个的数据
写坏。

**记忆按仓库划分作用域。** `install` 会从 git root 推导出 `project_id`
（`git worktree` 会解析到它的父仓库，所以"一个任务一个分支"不等于"一个
任务一个记忆库"）。这是收窄，不是隔离：你在某个项目之外告诉 SodaMem 的事，
仍然会出现在每个项目里，去掉这个 key 就能回答"我在另一个仓库里是怎么修的
这个问题"。

**写入需要抽取用的模型凭证。** 召回零 LLM，不需要它们；但存事实需要。
`sodamem daemon ensure` 会提前把这话说清楚，而不是先接受写入，再让任务
事后失败。

```
sodamem install claude-code --dry-run      # 打印将要改动的内容
sodamem install cursor vscode zed          # 一次装好几个
sodamem daemon status                      # 看看现在实际是谁在响应
```

已有配置是合并写入，不是整体替换——其他 MCP server、其他设置、手写的 TOML
注释都会保留——第一次写任何文件时都会在旁边留一份 `.sodamem-backup`。

## 自托管

一条命令：

```
cp .env.example .env      # 然后设置 SODAMEM_API_KEY
docker compose up -d
```

**默认开启鉴权。** `docker-compose.yml` 从不设置 `SODAMEM_AUTH_DISABLED`——
如果没设 `SODAMEM_API_KEY`，服务端会直接拒绝启动（见 `server/settings.py`），
所以不存在"忘了配置就裸奔"的部署。第一次 `docker compose up` 之前，先在
`.env` 里把 key 设好。

**必须只跑一个 worker。** `--workers 1` 是正确性约束，不是吞吐量设置：
每用户的存储是没开 WAL 的 SQLite 数据库，两个进程同时写同一个用户的库会
把它写坏。镜像自带的 `CMD` 已经写死这一点，服务端启动时还会在数据根目录上
加独占锁——第二个指向同一目录的进程会以 `data_root_locked` 拒绝启动，而
不是悄悄把数据写坏。要横向扩容，得先有一个外部的任务存储
（`docs/adr/0001-control-plane-db.md`）。

完整的运维参考——调用 API、admin 接口、指标、维护、备份、升级——都在
[`docs/self-hosting.md`](../../docs/self-hosting.md)（目前只有英文版）。

---

## 文档

| | |
|---|---|
| [跑分方法](../../benchmarking/README.md) | 那些跑分数字是怎么跑出来的 |

---

## 致谢

感谢 [@sunjiajunsunjiajun](https://github.com/sunjiajunsunjiajun) 和 [@Lum1104](https://github.com/Lum1104) 在早期做出的贡献 —— 这个项目正是从那些工作里长出来的。

## 许可

Apache-2.0，见 [LICENSE](../../LICENSE) 与 [NOTICE](../../NOTICE)。
