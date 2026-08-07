<div align="center">

# SodaMem

**Memória temporal e rastreável para agentes de IA.**

Cada memória sabe de qual turno da conversa veio, e a partir de quando deixou de ser verdade.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](../../LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](../../pyproject.toml)
[![LongMemEval](https://img.shields.io/badge/LongMemEval-92.8%25-brightgreen.svg)](../../benchmarking/artifacts/)
[![LoCoMo](https://img.shields.io/badge/LoCoMo-86.88%25-brightgreen.svg)](../../benchmarking/README.md#locomo-cat-1-4)

<!-- langs -->
[English](../../README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Français](README.fr.md) · [Español](README.es.md) · [Deutsch](README.de.md) · **Português**
<!-- /langs -->

</div>

---

```bash
pip install "sodamem[chroma,llm]"
```

```python
from sodamem import SodaMem
from sodamem.llm import create_provider_from_env      # SODAMEM_LLM_API_KEY
from sodamem.memory.ingest.extractor import FactEventExtractorV2

# Escrever precisa de um modelo para extrair fatos; ler, nunca.
mem = SodaMem.open("./data", extractor=FactEventExtractorV2(create_provider_from_env()))

mem.ingest(
    [{"role": "user", "content": "Na verdade mudei de Kauai para Oahu."}],
    user_id="u1", session_id="s1", session_time="2023-05-25",
)

block = mem.build_context("onde vou me hospedar?", user_id="u1", token_budget=1000)
print(block.text)        # pronto para colar no prompt — zero chamadas ao LLM
print(block.citations)   # a evidência por trás de cada linha
```

`SodaMem.open()` cria `./data` se ela não existir. Só `.ingest()` precisa do
extrator — omita esse argumento para um store somente-leitura, e `search` /
`build_context` funcionam exatamente igual.

**Nada seu sai da máquina.** Sem telemetria, sem analytics, sem callback — a
única requisição de saída que a instalação padrão faz é o download único do
modelo de embedding MiniLM (90 MB) para `~/.cache/chroma/`; depois disso ela
conversa apenas com o seu disco. Pré-carregue esse cache e ela roda offline.

---

## Por que mais uma camada de memória

A maioria dos sistemas de memória guarda **o que** foi dito. As perguntas que os
quebram são **desde quando deixou de ser verdade** e **de onde veio** — as duas
são questões de modelo de dados, não de um índice vetorial maior.

### Cada memória carrega o seu comprovante

Uma memória recuperada não é uma string solta. Ela aponta para o turno que a
produziu:

```
evidence_id  = ev_fact:fact_6ada707b…
support      = "Pode recomendar uma praia pouco cheia em Oahu?"
predicate    = o usuário quer uma praia tranquila em Oahu
entities     = location=Oahu | occasion=aniversário
source       = session_40 / turn_10          ← este turno exato, não "alguma conversa"
date         = 2023-05-25
```

`FactEvent → SourceSpan → RawTurn` é uma cadeia real de chaves estrangeiras, não
um escore de similaridade. Quando o usuário pergunta "por que você acha isso de
mim?", existe resposta. Quando o compliance pergunta de onde veio um fato
armazenado, existe uma linha.

### Quatro eixos de tempo, não um timestamp

| campo | pergunta que responde |
|---|---|
| `occurred_start` / `occurred_end` | quando o evento **aconteceu** |
| `valid_from` / `valid_until` | em que período o fato **era verdade** |
| `document_time` | quando o usuário **disse** |
| `created_at` | quando **armazenamos** |

Com um único timestamp não dá para separar "ano passado eu **me mudei** para
Chicago" de "ano que vem eu **vou me mudar** para Chicago", nem representar um
fato que deixou de valer.

Correções são **ADD-only**: uma nova versão mais uma aresta `SUPERSEDES`, nunca
reescrita no lugar. `PATCH /v1/memories/{id}` fecha a versão antiga com um
`valid_until` e **a mantém legível** — essa é toda a diferença para o `DELETE`.

### Duas camadas de recuperação, e a barata é gratuita de verdade

| camada | chamadas ao LLM | para |
|---|---|---|
| `search` / `build_context` | **zero** | o caminho padrão: fusão determinística de BM25 + vetorial + entidades |
| `answer` | laço do planner | perguntas multi-salto que valem os tokens |

`build_context` devolve **um bloco pronto para prompt, com citações**, e não
chama o modelo nenhuma vez. A maioria dos sistemas entrega uma lista de
registros e deixa a montagem, o orçamento de tokens e a deduplicação por sua
conta.

Há um terceiro nível intermediário: `build_context(organizer=...)` roda um
organizador apoiado em LLM (value-board, enumeration-sweep) sobre o conjunto
recuperado, para perguntas do tipo "liste todos os X que você sabe sobre
mim". É deliberadamente só de Python — `/v1/context` nunca aceita um
organizador, então a garantia de zero LLM daquela rota não pode ser virada
por um parâmetro da requisição.

### Recuperação auditável

Mesma consulta, mesmo store, mesmo resultado, sempre. `/v1/events` registra cada
inclusão, substituição e remoção com o motivo: "por que o agente esqueceu X?"
tem resposta depois do fato.

---

## Benchmark

**92,8% (464/500)** no LongMemEval.

| | |
|---|---|
| reader / planner / judge | `deepseek-v4-flash` |
| prompts de avaliação | os templates `evaluate_qa.py` do próprio benchmark, idênticos byte a byte |
| store | `longmemeval_s_500_Hobs_entitysubj`, 500 usuários / 235.840 fatos |

**Cada resposta e cada memória recuperada estão publicadas** em
[`benchmarking/artifacts/`](../../benchmarking/artifacts/) — 500 respostas na
íntegra e 8.427 evidências. Reavalie com o judge que preferir, ou entregue o
contexto que recuperamos ao seu próprio reader e veja o que acontece com o
número. Nenhum dos dois exige acesso a nada nosso.

**86,88% (1338/1540)** no LoCoMo, categorias 1-4 — a categoria 5 (adversarial)
fica de fora, ou seja, 1.540 das 1.986 perguntas. Acurácia de QA ponta a ponta,
avaliada por LLM-as-judge.

| | |
|---|---|
| reader / planner / judge | `deepseek-v4-flash` |
| prompts de avaliação | os templates do próprio benchmark LongMemEval, copiados byte a byte |
| store | `locomo10_Hobs`, 10 stores de usuário / 2.905 fact events |
| code | uma build pré-lançamento — o histórico publicado começa em v0.1.0 |

**Nenhum artefato por pergunta é publicado para o LoCoMo** — sem respostas, sem
contexto recuperado, sem diretório de execução. O que está publicado é
[a seção LoCoMo do `benchmarking/README.md`](../../benchmarking/README.md#locomo-cat-1-4):
o detalhamento por categoria, a dispersão por conversa, a procedência e os
passos de reprodução.

---

## Instalação

| extra | o que adiciona |
|---|---|
| *(base)* | modelo de dados, armazenamento, busca BM25, ingestão — **quatro dependências, nenhuma pesada** |
| `chroma` | busca vetorial + embedder ONNX local (necessário para `SodaMem.open()`) |
| `llm` | provedores compatíveis com OpenAI (OpenAI / DeepSeek / Gemini, mesmo protocolo) |
| `anthropic` | o provedor Anthropic (SDK próprio) |
| `answer` | o caminho de resposta planner + reader |
| `server` | o serviço HTTP (FastAPI + uvicorn — três pacotes, deliberadamente) |
| `mcp` | a superfície de servidor MCP |

A instalação base traz `pydantic`, `numpy`, `rank-bm25` e `python-dateutil`.
Nada além disso — e há um gate no CI que quebra o build se essa lista crescer
sem querer.



---

## Use de qualquer lugar

**HTTP** — `add` / `search` / `context` / `answer`, além de escrita em lote,
substituição, eventos, métricas e consumo de tokens:

```bash
curl -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  localhost:8000/v1/context \
  -d '{"user_id":"u1","query":"o que ela prefere?","token_budget":1000}'
```

`/v1/context` e `/v1/search` aceitam corpo JSON; `/v1/context` também
responde a um GET simples com parâmetros de query, por ser uma leitura pura.

**SDKs** — TypeScript sobre HTTP ([`sdk-ts/`](../../sdk-ts/), zero
dependências de runtime, ESM + CJS). Python fala direto com a biblioteca —
`import sodamem` e você já está aquém da rede.

**Frameworks de agentes** — LangGraph, CrewAI, OpenAI Agents SDK, Vercel AI SDK.
O escopo é fixado na construção das ferramentas e **nunca aparece no schema que
o modelo vê**: um `user_id` que o modelo pode escolher é um `user_id` que ele
pode alucinar.

**MCP** — 8 ferramentas, incluindo `entity_timeline` (o histórico de uma
entidade em ordem, cada item ainda apontando para sua origem) e
`explore_memory` (percorrer o grafo). Seis são leituras e estão sempre
disponíveis; as duas que alteram (`add_memories`, `delete_memory`) só
aparecem sob `SODAMEM_MCP_ALLOW_WRITE=true`, que o `sodamem install` escreve
para você na configuração de cliente que gera.

**Console web** — navegar e inspecionar memórias por tenant, incluso na imagem.

---

## Auto-hospedagem

```bash
cp .env.example .env      # defina SODAMEM_API_KEY
docker compose up -d
```

Autenticação ligada por padrão. O isolamento entre tenants é **físico**: um
arquivo SQLite e uma coleção vetorial por `user_id`, então "apagar este usuário"
é apagar um diretório.

`/v1/admin/*` responde ao que de outra forma exigiria um shell dentro do
contêiner: configuração efetiva (segredos reportados como "definido / não
definido" e nunca impressos), chaves de API nomeadas, log rotativo de
requisições, estado de disco e carga.

Observabilidade: `/v1/metrics` (percentis de latência), `/v1/usage` (tokens,
separando ingestão e resposta), `/metrics` (formato Prometheus), `/v1/events`
(toda mudança de memória) e webhooks de saída — fila limitada, assinados em
HMAC, inativos enquanto não houver URL configurada.

Os perfis de entidade são reconstruídos sob demanda, nunca por temporizador:
`POST /v1/maintenance/dream` (idempotente, retomável; uma chamada concorrente
devolve `already_running`). Quando gastar esses tokens é decisão do deploy,
por isso o SodaMem não traz nenhum agendador.

Detalhes na versão em inglês: [Self-hosting](../../README.md#self-hosting).

---

## Documentação

| | |
|---|---|
| [Ferramentas de código](../../README.md#coding-tools) | Claude Code, Cursor e outros clientes MCP |
| [Método do benchmark](../../benchmarking/README.md) | como os números de benchmark foram produzidos |

---

## Agradecimentos

As contribuições iniciais de [@sunjiajunsunjiajun](https://github.com/sunjiajunsunjiajun) and [@Lum1104](https://github.com/Lum1104) moldaram o trabalho do qual este projeto
nasceu. Obrigado.

## Licença

Apache-2.0. Veja [LICENSE](../../LICENSE) e [NOTICE](../../NOTICE).
