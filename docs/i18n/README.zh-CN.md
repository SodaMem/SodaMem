<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/logo-dark.webp">
  <img src="../assets/logo.webp" alt="SodaMem" width="260">
</picture>

**给 AI Agent 的证据可溯、带时间的记忆层。**

每一条记忆都说得出自己出自哪一轮对话，也知道自己从哪一刻起不再成立。

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](../../LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](../../pyproject.toml)
[![LongMemEval](https://img.shields.io/badge/LongMemEval-92.8%25-brightgreen.svg)](../../benchmarking/artifacts/)
[![LoCoMo](https://img.shields.io/badge/LoCoMo-86.88%25-brightgreen.svg)](../../benchmarking/README.md#locomo-cat-1-4)
[![Discussions](https://img.shields.io/github/discussions/SodaMem/SodaMem?logo=github&label=discussions)](https://github.com/SodaMem/SodaMem/discussions)

<!-- langs -->
[English](../../README.md) · **简体中文** · [日本語](README.ja.md) · [한국어](README.ko.md) · [Français](README.fr.md) · [Español](README.es.md) · [Deutsch](README.de.md) · [Português](README.pt-BR.md)
<!-- /langs -->

<img src="../assets/benchmark-cost-accuracy.webp" alt="Cost-accuracy trade-off on LongMemEval-S" width="760">

*纵轴是准确率，横轴是每个问题的预估 API 成本。有意义的象限在左上角。*

</div>

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

修正走 **ADD-only**：新版本 + 一条 `SUPERSEDES` 边，绝不原地改写。
`PATCH /v1/memories/{id}` 给旧版本盖上 `valid_until` 并**保留它可读**——
这正是它和 `DELETE` 的全部区别。

### 两档检索，而且便宜那档是真的免费

| 档位 | LLM 调用 | 适用 |
|---|---|---|
| `search` / `build_context` | **零** | 默认路径：BM25 + 向量 + 实体的确定性融合 |
| `answer` | planner 循环 | 值得花 token 的多跳难题 |

`build_context` 直接返回**带引用、可拼进 prompt 的文本块**，全程不调模型。
多数系统只给你一列记录，拼装、token 预算、去重都留给你自己做。

还有一个居中的第三档：`build_context(organizer=...)` 会在检索结果上跑一个
LLM 编排器（value-board / enumeration-sweep），用来回答「把你知道的所有 X
列出来」这类问题。它刻意只在 Python 侧开放——`/v1/context` 永不接受
organizer，所以那条路由的零 LLM 保证不可能被某个请求参数翻掉。

### 检索结果可审计

同一个查询、同一个库，每次结果都一样。`/v1/events` 记录每一次新增、取代、
删除及其原因——「agent 为什么忘了 X」是事后查得到的，不是耸耸肩。

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
用 query 参数发 GET——它本来就是一个纯读接口。

**SDK** —— TypeScript 走 HTTP（[`sdk-ts/`](../../sdk-ts/)，零运行时依赖，
ESM + CJS）。Python 直接用库本身——`import sodamem`，你已经在网络这一层之内了。

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

## 自托管

```bash
cp .env.example .env      # 设置 SODAMEM_API_KEY
docker compose up -d
```

默认开鉴权。租户隔离是**物理隔离**——每个 `user_id` 一个独立 SQLite 文件
和一个独立向量集合，所以「删除这个用户」就是删一个目录。

`/v1/admin/*` 提供那些原本需要钻进容器才能看的东西：生效配置（密钥只报
「已设置/未设置」，绝不打印）、具名 API key、滚动请求日志、磁盘与负载形状。

可观测性：`/v1/metrics`（延迟分位）、`/v1/usage`（token 花费，按 ingest 与
answer 分开）、`/metrics`（Prometheus）、`/v1/events`（每一次记忆变更），
以及出站 webhook——有界队列、HMAC 签名、不配 URL 就完全不启用。

实体档案按需重建，不走定时器：`POST /v1/maintenance/dream`
（幂等、可续跑、并发调用返回 `already_running`）。何时花这份 token 是
部署方的决定，所以 SodaMem 自己不带调度器。

完整说明见英文版 [Self-hosting](../../README.md#self-hosting)。

---

## 文档

| | |
|---|---|
| [编码工具接入](../../README.md#coding-tools) | Claude Code、Cursor 等 MCP 客户端 |
| [跑分方法](../../benchmarking/README.md) | 那些跑分数字是怎么跑出来的 |

---

## 致谢

感谢 [@sunjiajunsunjiajun](https://github.com/sunjiajunsunjiajun) 和 [@Lum1104](https://github.com/Lum1104) 在早期做出的贡献 —— 这个项目正是从那些工作里长出来的。

## 许可

Apache-2.0，见 [LICENSE](../../LICENSE) 与 [NOTICE](../../NOTICE)。
