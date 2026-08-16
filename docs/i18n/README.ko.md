<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/logo-dark.webp">
  <img src="../assets/logo.webp" alt="SodaMem" width="260">
</picture>

**AI 에이전트를 위한, 스스로 진화하는 에이전틱 메모리 레이어.**

대부분의 메모리 시스템은 사용자가 한 말을 저장하고 거기서 멈춥니다 — 오늘은 맞아도 삶이 바뀌는 순간 조용히 틀린 정보가 됩니다. SodaMem은 에이전트와 함께 진화합니다: 사실은 덮어쓰이는 대신 대체되고, 엔티티 프로필은 조용히 낡아가는 대신 필요할 때 다시 구축되며, 모든 답변은 여전히 그것이 나온 정확한 발화까지 추적됩니다. 검색(recall)은 LLM 호출이 0회이므로, 같은 질문에는 언제나 같은 답이 나옵니다.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](../../LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](../../pyproject.toml)
[![LongMemEval](https://img.shields.io/badge/LongMemEval-92.8%25-brightgreen.svg)](../../benchmarking/artifacts/)
[![LoCoMo](https://img.shields.io/badge/LoCoMo-86.88%25-brightgreen.svg)](../../benchmarking/README.md#locomo-cat-1-4)
[![Discussions](https://img.shields.io/github/discussions/SodaMem/SodaMem?logo=github&label=discussions)](https://github.com/SodaMem/SodaMem/discussions)

<!-- langs -->
[English](../../README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · **한국어** · [Français](README.fr.md) · [Español](README.es.md) · [Deutsch](README.de.md) · [Português](README.pt-BR.md)
<!-- /langs -->

[에이전트 연동](#에이전트-연동) · [벤치마크](#벤치마크) · [빠른 시작](#빠른-시작) · [왜 또 하나의 메모리 계층인가](#왜-또-하나의-메모리-계층인가) · [설치](#설치) · [어디서든 사용](#어디서든-사용) · [코딩 도구](#코딩-도구) · [셀프 호스팅](#셀프-호스팅) · [문서](#문서)

<img src="../assets/benchmark-cost-accuracy.webp" alt="Cost-accuracy trade-off on LongMemEval-S" width="760">

*세로축은 정확도, 가로축은 질문당 추정 API 비용입니다. 의미 있는 사분면은 왼쪽 위입니다.*

</div>

---

## 에이전트 연동

| 런타임 | 방식 | 가이드 |
|---|---|---|
| **Hermes Agent** | MCP | [`integrations/hermes/README.md`](../../integrations/hermes/README.md) |
| **DeepSeek Harness** | MCP | [`integrations/deepseek-harness/README.md`](../../integrations/deepseek-harness/README.md) |
| **범용 / 모든 MCP 클라이언트** | MCP | [`mcp_server/README.md`](../../mcp_server/README.md) |
| **LangGraph** | Python 어댑터 | [`adapters/README.md`](../../adapters/README.md) |
| **CrewAI** | Python 어댑터 | [`adapters/README.md`](../../adapters/README.md) |
| **OpenAI Agents SDK** | Python 어댑터 | [`adapters/README.md`](../../adapters/README.md) |
| **Vercel AI SDK** | TS 어댑터 | [`sdk-ts/`](../../sdk-ts/) |
| **Claude Code, Cursor 등 코딩 클라이언트** | CLI + hooks | [코딩 도구](#코딩-도구) 참고 |

MCP 도구 스키마와 어댑터 세부사항을 포함한 전체 목록: [`integrations/README.md`](../../integrations/README.md).

---

## 벤치마크

<div align="center">
  <img src="../assets/benchmark-longmemeval.webp" alt="LongMemEval: SodaMem 92.8%, Hindsight 91.4%, Mem0 OSS 91.0%" width="720">
</div>

LongMemEval **92.8% (464/500)**.

| | |
|---|---|
| reader / planner / judge | `deepseek-v4-flash` |
| 채점 프롬프트 | LongMemEval 공식 `evaluate_qa.py` 템플릿, 바이트 단위 동일 |
| 스토어 | `longmemeval_s_500_Hobs_entitysubj`, 500 사용자 / 235,840 팩트 |

**모든 답변과 검색된 기억을 전부 공개합니다**
([`benchmarking/artifacts/`](../../benchmarking/artifacts/)) — 500건의 답변 전문과
8,427건의 근거. 원하는 judge 로 다시 채점하거나, 우리가 검색한 컨텍스트를 여러분의
reader 에 넣어 숫자가 어떻게 달라지는지 확인할 수 있습니다. 어느 쪽도 우리 서비스에
접근할 필요가 없습니다.

<div align="center">
  <img src="../assets/benchmark-locomo.webp" alt="LoCoMo: SodaMem 86.88%, MemMachine 91.69%, Hindsight 89.61%, MIRIX 85.38%, Memobase 75.78%, Mem0 OSS 66.88%" width="720">
</div>

LoCoMo **86.88% (1338/1540)**. 엔드투엔드 QA 정확도이고 채점은
LLM-as-judge 입니다.

| | |
|---|---|
| reader / planner / judge | `deepseek-v4-flash` |
| 채점 프롬프트 | LongMemEval 공식 템플릿, 바이트 단위 복제 |
| 스토어 | `locomo10_Hobs`, 사용자 스토어 10개 / 팩트 이벤트 2,905건 |
| 코드 | 사전 릴리스 빌드 — 공개된 히스토리는 v0.1.0 부터 시작합니다 |

**LoCoMo 는 문항별 산출물을 공개하지 않습니다** — 답변도, 검색된 컨텍스트도,
run 디렉터리도 없습니다. 공개하는 것은
[`benchmarking/README.md` 의 LoCoMo 절](../../benchmarking/README.md#locomo-cat-1-4)
이며, 카테고리별 분해와 대화별 분포, provenance 및 재현 절차가 들어 있습니다.

---

## 빠른 시작

이건 Python 경로입니다. 에이전트 프레임워크나 MCP 클라이언트에 연결하려면 [에이전트 연동](#에이전트-연동)을, TypeScript/Node에서 호출하려면 [어디서든 사용](#어디서든-사용)을, 공유 서비스로 운영하려면 [셀프 호스팅](#셀프-호스팅)을 참고하세요.

### 예제

```bash
pip install "sodamem[chroma,llm]"
```

```python
from sodamem import SodaMem
from sodamem.llm import create_provider_from_env      # SODAMEM_LLM_API_KEY 등
from sodamem.memory.ingest.extractor import FactEventExtractorV2

mem = SodaMem.open("./data", extractor=FactEventExtractorV2(create_provider_from_env()))

mem.ingest(
    [{"role": "user", "content": "사실 카우아이에서 오아후로 바꿨어요."}],
    user_id="u1", session_id="s1", session_time="2023-05-25",
)

block = mem.build_context("어디에 묵을 예정이지?", user_id="u1", token_budget=1000)
print(block.text)        # 프롬프트에 그대로 넣을 수 있음 — LLM 호출 0회
print(block.citations)   # 그 문장 한 줄 한 줄의 근거
```

`SodaMem.open()` 은 `./data` 가 없으면 만듭니다. extractor 가 필요한 것은
`.ingest()` 뿐이라, 이 인자를 빼면 읽기 전용 스토어가 되고 `search` /
`build_context` 는 그대로 동작합니다.

**여러분의 데이터는 기기를 떠나지 않습니다.** 텔레메트리 없음, 분석 없음,
콜백 없음 — 기본 설치가 보내는 유일한 외부 요청은 90MB MiniLM 임베딩
모델을 `~/.cache/chroma/` 로 한 번 내려받는 것뿐이며, 그 뒤로는 디스크와만
통신합니다. 이 캐시를 미리 채워두면 완전한 오프라인으로 동작합니다.

---

## 왜 또 하나의 메모리 계층인가

대부분의 메모리 시스템이 저장하는 것은 **무엇을 말했는가**입니다. 정작 시스템을
무너뜨리는 질문은 **그것이 언제부터 참이 아니게 되었는가**와 **어디에서 왔는가**
입니다. 이 둘은 데이터 모델로 풀어야 할 문제이지, 벡터 인덱스를 키운다고
해결되지 않습니다.


| 질문 | 흔한 답 | SodaMem |
|---|---|---|
| 이 기억은 어디서 왔는가? | 유사도 점수와 메타데이터 몇 개 | `FactEvent → SourceSpan → RawTurn` 외래키 사슬이 정확히 그 턴까지 짚어 준다 |
| 사용자가 말을 바꾸면? | 덮어쓰기, 옛 값은 사라짐 | 추가만 하고 `SUPERSEDES` 간선을 건다. 옛 버전은 `valid_until`로 닫히고 계속 읽힌다 |
| "작년에 시카고로 이사했다" vs "내년에 이사한다" | 타임스탬프 하나 | 네 개의 시간 축: 발생 / 유효 / 발화 / 저장 |
| 검색 한 번의 비용은? | 검색마다 LLM 호출 | `build_context`는 모델 호출 **0회**, 인용이 붙은 완성 프롬프트를 반환 |
| 같은 질의를 두 번 하면 같은 답인가? | 모델 샘플링에 달림 | 결정적 융합: 같은 저장소, 같은 질의, 같은 결과 |
| 왜 X를 잊었는가? | 답이 없음 | `/v1/events`가 추가·대체·삭제를 이유와 함께 모두 기록 |

이 중 두 가지는 더 살펴볼 가치가 있습니다 — 나머지는 표에 이미 나온 그대로입니다.

### 모든 기억이 자기 근거를 지닌다

검색된 기억은 떠다니는 문자열이 아닙니다. 그것을 만들어낸 발화를 가리킵니다.

```
evidence_id  = ev_fact:fact_6ada707b…
support      = "오아후에서 너무 붐비지 않는 해변을 추천해 줄래요?"   ← 사용자 원문 그대로
predicate    = 사용자는 오아후의 한적한 해변을 원한다
entities     = location=오아후 | occasion=생일
source       = session_40 / turn_10          ← "어떤 대화"가 아니라 바로 그 발화
date         = 2023-05-25
```

`FactEvent → SourceSpan → RawTurn` 은 유사도 점수가 아니라 실제 외래키 사슬입니다.
사용자가 "왜 나에 대해 그렇게 생각하죠?"라고 물으면 답이 있고, 감사에서 "이 사실은
어디서 왔습니까?"라고 물으면 해당 행이 있습니다.

### 타임스탬프 하나가 아니라, 네 개의 시간축

| 필드 | 답하는 질문 |
|---|---|
| `occurred_start` / `occurred_end` | 사건이 **일어난** 시점 |
| `valid_from` / `valid_until` | 그 사실이 **성립하던** 기간 |
| `document_time` | 사용자가 **말한** 시점 |
| `created_at` | 우리가 **저장한** 시점 |

타임스탬프가 하나뿐이면 "작년에 시카고로 **이사했다**"와 "내년에 시카고로
**이사한다**"를 구분할 수 없고, 이미 참이 아니게 된 사실을 표현할 수도 없습니다.

---

## 설치

| extra | 추가되는 것 |
|---|---|
| *(base)* | 데이터 모델, 저장소, BM25 검색, 수집 — **의존성 4개, 무거운 것 없음** |
| `chroma` | 벡터 검색 + 로컬 ONNX 임베딩 (`SodaMem.open()` 에 필요) |
| `llm` | OpenAI 호환 프로바이더 (OpenAI / DeepSeek / Gemini 동일 프로토콜) |
| `anthropic` | Anthropic (자체 SDK) |
| `answer` | 플래너 + 리더 답변 경로 |
| `server` | HTTP 서비스 (FastAPI + uvicorn, 의도적으로 3개 패키지만) |
| `mcp` | MCP 서버 |

base 는 `pydantic`, `numpy`, `rank-bm25`, `python-dateutil` 만 가져옵니다.
이 목록이 실수로 길어지면 빌드를 실패시키는 CI 게이트가 있습니다.



---

## 어디서든 사용

**HTTP** — `add` / `search` / `context` / `answer`, 그리고 일괄 쓰기, 대체,
이벤트, 메트릭, 토큰 사용량:

```bash
curl -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  localhost:8000/v1/context \
  -d '{"user_id":"u1","query":"무엇을 선호하지?","token_budget":1000}'
```

`/v1/context` 와 `/v1/search` 는 모두 JSON 본문을 받습니다. `/v1/context` 는
순수 읽기이므로 쿼리 파라미터를 쓰는 GET 도 그대로 받습니다. 유일하게
Python 에만 있는 예외는 `build_context(organizer=...)` 입니다 — "나에 대해
아는 걸 전부 나열해줘" 같은 질문을 위해 검색 결과 집합 위에 LLM 기반
organizer 를 돌립니다. `/v1/context` 는 이 옵션을 절대 받지 않으므로, HTTP
위에서의 zero-LLM 보장은 요청 파라미터로 뒤집힐 수 없습니다.

**SDK** — TypeScript 는 HTTP 로([`sdk-ts/`](../../sdk-ts/), 런타임 의존성
0, ESM + CJS):

```bash
npm i sodamem
```

```typescript
import { SodaMemClient } from "sodamem";

const mem = new SodaMemClient({ baseUrl: "http://localhost:8000", apiKey: process.env.SODAMEM_API_KEY! });
const block = await mem.context({ user_id: "u1", query: "무엇을 선호하지?", token_budget: 1000 });
```

Python 은 라이브러리를 직접 씁니다 — `import sodamem` 하는
순간 이미 네트워크 안쪽입니다.

**에이전트 프레임워크** — LangGraph, CrewAI, OpenAI Agents SDK, Vercel AI SDK.
스코프는 도구를 만들 때 바인딩되며 **모델이 보는 schema 에는 절대 노출되지
않습니다**. 모델이 고를 수 있는 `user_id` 는 모델이 환각할 수 있는 `user_id`
이기 때문입니다.

**MCP** — 8개 도구. `entity_timeline`(한 엔티티의 이력을 시간순으로, 각 항목은
여전히 출처를 가리킴)과 `explore_memory`(그래프를 바깥으로 순회) 포함.
그중 6개는 읽기이며 항상 제공됩니다.
데이터를 바꾸는 두 개(`add_memories`, `delete_memory`)는
`SODAMEM_MCP_ALLOW_WRITE=true` 일 때만 등록되며, `sodamem install` 이
생성하는 클라이언트 설정에 그 줄을 대신 써 줍니다.

**웹 콘솔** — 테넌트별로 기억을 조회·점검. 이미지에 포함되어 있습니다.

---

## 코딩 도구

**1단계.** 데몬을 시작합니다 — 스토어를 소유하는 단 하나의 프로세스입니다:

```
sodamem daemon ensure
```

**2단계.** 클라이언트를 데몬에 연결합니다:

```
sodamem install claude-code
```

모든 클라이언트가 MCP 도구 표면을 얻습니다. 그중 네 개는 **hooks** 도 함께 얻어서,
모델이 도구를 호출하기로 "결정"하지 않아도 기억이 회상되고 저장됩니다 — 코딩
세션에서는 모델이 대개 파일을 읽느라 바빠서 그런 결정을 잘 내리지 않기 때문입니다.

hooks 가 할 수 있는 일은 균일하지 않습니다. hook 시스템 자체가 균일하지 않기
때문입니다. 아래가 각 클라이언트가 실제로 지원하는 것이고, `sodamem clients`
가 출력하는 내용도 동일합니다:

| 클라이언트 | 회상(Recall) | 저장(Retain) |
|---|---|---|
| Claude Code | 모든 프롬프트마다 | 모든 턴 + 세션 종료 시 |
| GitHub Copilot CLI | 모든 프롬프트마다 | 모든 턴마다 |
| Cursor | 세션 시작 시(프로젝트 개요) | — |
| Codex CLI | 세션 시작 시(프로젝트 개요) | — |
| Claude Desktop, VS Code, Windsurf, Zed, OpenCode | MCP 도구로만 | MCP 도구로만 |

Cursor 의 `beforeSubmitPrompt` 는 프롬프트를 읽을 수는 있어도 무엇을 주입할
수는 없습니다(공식 문서가 주입 가능한 이벤트로 정확히 세 개를 꼽는데, 이건 그중
하나가 아닙니다). Cursor 와 Codex 모두 hook 에 트랜스크립트 경로를 넘기지 않으므로,
저장용 hook 이 읽을 것 자체가 없습니다. 이 둘은 대신 세션 시작 시 프로젝트 개요를
받고 `add_memories` 도구를 통해 씁니다. 아무것도 할 수 없는 hook 은 설치하지
않습니다.

실행 전에 알아둘 것 세 가지:

**데몬은 하나, 에디터는 여럿.** 사용자별 스토어는 WAL 없는 SQLite 라서 정확히
하나의 프로세스만 열 수 있습니다(ADR 0001 §2). 그래서 `install` 은 기본적으로
각 클라이언트가 자기만의 프로세스를 띄우게 두지 않고 실행 중인 서비스를 가리키게
합니다 — 의도적으로 로컬 스토어(`--local-store`)를 선택한 경우, 두 번째
클라이언트는 첫 번째의 데이터를 조용히 망가뜨리는 대신 시작을 거부합니다.

**기억은 저장소(repo) 단위로 스코프됩니다.** `install` 은 git 루트에서
`project_id` 를 도출합니다(`git worktree` 는 부모 저장소로 귀결되므로, 작업마다
브랜치를 나눈다고 해서 작업마다 별도의 메모리 뱅크가 생기지 않습니다). 이건
분할이 아니라 좁히기입니다: 프로젝트 밖에서 SodaMem 에게 말한 내용은 여전히
모든 프로젝트 안에서 나타나며, 키를 빼면 "이 문제를 다른 저장소에서는 어떻게
고쳤더라?" 에 답할 수 없게 됩니다.

**저장에는 추출용 자격증명이 필요합니다.** 회상은 LLM 호출이 0회라 자격증명
없이도 동작하지만, 사실을 저장하는 데는 필요합니다. `sodamem daemon ensure`
는 이걸 미리 알려줍니다 — 모든 쓰기를 일단 받아들이고 나중에 작업을 실패시키는
대신입니다.

```
sodamem install claude-code --dry-run      # 무엇이 바뀔지 미리 출력
sodamem install cursor vscode zed          # 여러 개를 한 번에
sodamem daemon status                      # 실제로 응답하고 있는 것이 무엇인지
```

기존 설정은 교체가 아니라 병합됩니다 — 다른 MCP 서버, 다른 설정, 손으로 쓴
TOML 주석까지 그대로 남습니다 — 그리고 각 파일을 처음 쓸 때 옆에
`.sodamem-backup` 을 남깁니다.

---

## 셀프 호스팅

한 줄이면 됩니다:

```
cp .env.example .env      # SODAMEM_API_KEY 설정
docker compose up -d
```

**인증은 기본적으로 켜져 있습니다.** `docker-compose.yml` 은 `SODAMEM_AUTH_DISABLED`
를 절대 설정하지 않습니다 — `SODAMEM_API_KEY` 가 없으면 서버가 아예 시작하지
않으므로(`server/settings.py` 참고), 실수로 열린 채 배포되는 일이 없습니다.
첫 `docker compose up` 전에 `.env` 에 키를 설정하세요.

**워커는 정확히 하나만 실행하세요.** `--workers 1` 은 처리량 설정이 아니라
정합성 제약입니다: 사용자별 스토어는 WAL 없이 열리는 SQLite 데이터베이스이고,
두 프로세스가 같은 사용자의 스토어에 동시에 쓰면 손상됩니다. 기본 `CMD` 가
이를 명시하고, 서버는 시작 시 데이터 루트에 대해 배타 락을 잡습니다 — 같은
디렉터리를 가리키는 두 번째 프로세스는 데이터를 조용히 망가뜨리는 대신
`data_root_locked` 로 시작을 거부합니다. 수평 확장에는 먼저 외부 잡 스토어가
필요합니다(`docs/adr/0001-control-plane-db.md`).

API 호출, 관리자 엔드포인트, 메트릭, 유지보수, 백업, 업그레이드를 다루는 전체
운영 레퍼런스는 [`docs/self-hosting.md`](../../docs/self-hosting.md) 에
있습니다(현재 영어로만 제공됩니다).

---

## 문서

| | |
|---|---|
| [벤치마크 방법](../../benchmarking/README.md) | 벤치마크 숫자를 어떻게 냈는가 |

---

## 감사의 말

이 프로젝트가 자라난 초기 작업에 기여해 주신 [@sunjiajunsunjiajun](https://github.com/sunjiajunsunjiajun) and [@Lum1104](https://github.com/Lum1104) 께 감사드립니다.

## 라이선스

Apache-2.0. [LICENSE](../../LICENSE) 와 [NOTICE](../../NOTICE) 참조.
