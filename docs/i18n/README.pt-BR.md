<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/logo-dark.webp">
  <img src="../assets/logo.webp" alt="SodaMem" width="260">
</picture>

**Uma camada de memória agentiva e auto-evolutiva para agentes de IA.**

A maioria dos sistemas de memória guarda o que você disse e para por aí — certo hoje, silenciosamente errado assim que sua vida muda. O SodaMem evolui junto com o seu agente: fatos são substituídos, nunca sobrescritos, perfis de entidade são reconstruídos sob demanda em vez de ficarem defasados sem aviso, e toda resposta ainda remete ao turno exato de onde veio. A recuperação não custa nenhuma chamada de LLM, então a mesma pergunta sempre recebe a mesma resposta.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](../../LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](../../pyproject.toml)
[![LongMemEval](https://img.shields.io/badge/LongMemEval-92.8%25-brightgreen.svg)](../../benchmarking/artifacts/)
[![LoCoMo](https://img.shields.io/badge/LoCoMo-86.88%25-brightgreen.svg)](../../benchmarking/README.md#locomo-cat-1-4)
[![Discussions](https://img.shields.io/github/discussions/SodaMem/SodaMem?logo=github&label=discussions)](https://github.com/SodaMem/SodaMem/discussions)

<!-- langs -->
[English](../../README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Français](README.fr.md) · [Español](README.es.md) · [Deutsch](README.de.md) · **Português**
<!-- /langs -->

[Integrações de agentes](#integrações-de-agentes) · [Benchmark](#benchmark) · [Início rápido](#início-rápido) · [Por que mais uma camada de memória](#por-que-mais-uma-camada-de-memória) · [Instalação](#instalação) · [Use de qualquer lugar](#use-de-qualquer-lugar) · [Ferramentas de código](#ferramentas-de-código) · [Auto-hospedagem](#auto-hospedagem) · [Documentação](#documentação)

<img src="../assets/benchmark-cost-accuracy.webp" alt="Cost-accuracy trade-off on LongMemEval-S" width="760">

*Precisão em função do custo estimado de API por pergunta. O quadrante que importa fica no canto superior esquerdo.*

</div>

---

## Integrações de agentes

| Runtime | Como | Guia |
|---|---|---|
| **Hermes Agent** | MCP | [`integrations/hermes/README.md`](../../integrations/hermes/README.md) |
| **DeepSeek Harness** | MCP | [`integrations/deepseek-harness/README.md`](../../integrations/deepseek-harness/README.md) |
| **Genérico / qualquer cliente MCP** | MCP | [`mcp_server/README.md`](../../mcp_server/README.md) |
| **LangGraph** | adaptador Python | [`adapters/README.md`](../../adapters/README.md) |
| **CrewAI** | adaptador Python | [`adapters/README.md`](../../adapters/README.md) |
| **OpenAI Agents SDK** | adaptador Python | [`adapters/README.md`](../../adapters/README.md) |
| **Vercel AI SDK** | adaptador TS | [`sdk-ts/`](../../sdk-ts/) |
| **Claude Code, Cursor e outros clientes de código** | CLI + hooks | veja [Ferramentas de código](#ferramentas-de-código) |

Índice completo, com schemas das ferramentas MCP e detalhes dos adaptadores: [`integrations/README.md`](../../integrations/README.md).

---

## Benchmark

<div align="center">
  <img src="../assets/benchmark-longmemeval.webp" alt="LongMemEval: SodaMem 92.8%, Hindsight 91.4%, Mem0 OSS 91.0%" width="720">
</div>

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

<div align="center">
  <img src="../assets/benchmark-locomo.webp" alt="LoCoMo: SodaMem 86.88%, MemMachine 91.69%, Hindsight 89.61%, MIRIX 85.38%, Memobase 75.78%, Mem0 OSS 66.88%" width="720">
</div>

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

## Início rápido

Este é o caminho em Python. Vai integrar com um framework de agentes ou cliente MCP? Veja [Integrações de agentes](#integrações-de-agentes). Vai chamar a partir de TypeScript/Node? Veja [Use de qualquer lugar](#use-de-qualquer-lugar). Vai rodar como serviço compartilhado? Veja [Auto-hospedagem](#auto-hospedagem).

### Exemplo

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


| a pergunta | a resposta de sempre | SodaMem |
|---|---|---|
| De onde veio esta memória? | uma pontuação de similaridade e alguns metadados | `FactEvent → SourceSpan → RawTurn`, uma cadeia de chaves estrangeiras até o turno exato |
| O usuário mudou de ideia — e agora? | sobrescreve; o valor antigo some | só adiciona, mais uma aresta `SUPERSEDES`; a versão antiga fecha com `valid_until` e continua legível |
| "Me mudei para Chicago ano passado" vs "me mudo ano que vem" | um único timestamp | quatro eixos de tempo: ocorrido / válido / dito / armazenado |
| Quanto custa uma recuperação? | uma chamada de LLM por recuperação | `build_context` não faz **nenhuma** e devolve um bloco pronto para o prompt, com citações |
| A mesma consulta duas vezes dá o mesmo resultado? | depende da amostragem do modelo | fusão determinística: mesmo store, mesma consulta, mesmo resultado |
| Por que ele esqueceu X? | sem resposta | `/v1/events` registra cada inclusão, substituição e exclusão, com o motivo |

Duas delas merecem um olhar mais de perto — o resto é o que a tabela já diz.

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
A única exceção, exclusiva do Python, é `build_context(organizer=...)`, que
roda um organizador com LLM sobre o conjunto recuperado para perguntas como
"liste tudo que você sabe sobre mim" — `/v1/context` nunca aceita esse
parâmetro, então a garantia zero-LLM via HTTP não pode ser revertida por um
parâmetro de requisição.

**SDKs** — TypeScript sobre HTTP ([`sdk-ts/`](../../sdk-ts/), zero
dependências de runtime, ESM + CJS):

```bash
npm i sodamem
```

```typescript
import { SodaMemClient } from "sodamem";

const mem = new SodaMemClient({ baseUrl: "http://localhost:8000", apiKey: process.env.SODAMEM_API_KEY! });
const block = await mem.context({ user_id: "u1", query: "o que ela prefere?", token_budget: 1000 });
```

Python fala direto com a biblioteca — `import sodamem` e você já está aquém
da rede.

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

## Ferramentas de código

**Passo 1.** Suba o daemon — o único processo dono dos stores:

```
sodamem daemon ensure
```

**Passo 2.** Conecte um cliente a ele:

```
sodamem install claude-code
```

Todo cliente recebe a superfície de ferramentas MCP. Quatro também recebem
**hooks**, para que a memória seja recuperada e gravada sem o modelo precisar
decidir chamar uma ferramenta — o que numa sessão de código ele quase nunca
faz, porque está ocupado lendo arquivos.

O que os hooks conseguem fazer não é uniforme, porque os sistemas de hook
também não são. Isto é o que cada cliente realmente suporta, e `sodamem
clients` imprime a mesma coisa:

| Cliente | Recall | Retain |
|---|---|---|
| Claude Code | a cada prompt | a cada turno + fim de sessão |
| GitHub Copilot CLI | a cada prompt | a cada turno |
| Cursor | início da sessão (resumo do projeto) | — |
| Codex CLI | início da sessão (resumo do projeto) | — |
| Claude Desktop, VS Code, Windsurf, Zed, OpenCode | só ferramentas MCP | só ferramentas MCP |

O `beforeSubmitPrompt` do Cursor consegue ler um prompt, mas não consegue
injetar nada (a documentação dele lista exatamente três eventos que
conseguem, e esse não é um deles), e nem Cursor nem Codex passam para um hook
um caminho de transcript — então não há nada para um hook de retain ler.
Esses dois recebem um resumo do projeto no início da sessão e gravam via a
ferramenta `add_memories`. Não instalamos um hook que só consegue não fazer
nada.

Três coisas para saber antes de rodar:

**Um daemon, vários editores.** Os stores por usuário são SQLite sem WAL,
então só um processo pode abri-los (ADR 0001 §2). Por isso o `install`
aponta cada cliente para um serviço já rodando, em vez de deixar cada um
subir o seu — e se você escolher deliberadamente um store local
(`--local-store`), um segundo cliente agora se recusa a iniciar em vez de
corromper silenciosamente os dados do primeiro.

**Memórias são isoladas por repositório.** O `install` deriva um `project_id`
a partir da raiz do git (um `git worktree` resolve para o repositório pai,
então uma branch por tarefa não vira um banco de memória por tarefa). É um
recorte, não uma partição: o que você contou ao SodaMem fora de um projeto
continua aparecendo em todos os projetos, e é essa mesma chave que responde
"como eu resolvi isso no outro repositório?".

**Retain precisa de credenciais de extração.** Recall é zero-LLM e funciona
sem elas; gravar fatos não. `sodamem daemon ensure` avisa isso de cara, em
vez de aceitar toda escrita e falhar o job depois.

```
sodamem install claude-code --dry-run      # mostra o que mudaria
sodamem install cursor vscode zed          # vários de uma vez
sodamem daemon status                      # o que está realmente respondendo
```

A configuração existente é mesclada, não substituída — outros servidores
MCP, outras configurações e comentários TOML escritos à mão sobrevivem — e a
primeira escrita de qualquer arquivo deixa um `.sodamem-backup` ao lado.

---

## Auto-hospedagem

Um comando:

```bash
cp .env.example .env      # depois defina SODAMEM_API_KEY
docker compose up -d
```

**Autenticação ligada por padrão.** O `docker-compose.yml` nunca define
`SODAMEM_AUTH_DISABLED` — o servidor se recusa a iniciar se
`SODAMEM_API_KEY` não estiver definida (veja `server/settings.py`), então
não existe deploy aberto por acidente. Defina a chave no `.env` antes do
primeiro `docker compose up`.

**Rode exatamente um worker.** `--workers 1` é uma restrição de corretude,
não um ajuste de throughput: os stores por usuário são bancos SQLite abertos
sem WAL, e dois processos escrevendo no store do mesmo usuário o corrompem.
O `CMD` da imagem declara isso explicitamente, e o servidor toma um lock
exclusivo sobre a sua data root na inicialização — um segundo processo
apontado para o mesmo diretório se recusa a iniciar com
`data_root_locked` em vez de corromper dados silenciosamente. Escalar
horizontalmente exige primeiro um job store externo
(`docs/adr/0001-control-plane-db.md`).

Referência completa de operação — chamar a API, endpoints de admin,
métricas, manutenção, backups, upgrades — está em
[`docs/self-hosting.md`](../../docs/self-hosting.md) (por enquanto,
disponível só em inglês).

---

## Documentação

| | |
|---|---|
| [Método do benchmark](../../benchmarking/README.md) | como os números de benchmark foram produzidos |

---

## Agradecimentos

As contribuições iniciais de [@sunjiajunsunjiajun](https://github.com/sunjiajunsunjiajun) and [@Lum1104](https://github.com/Lum1104) moldaram o trabalho do qual este projeto
nasceu. Obrigado.

## Licença

Apache-2.0. Veja [LICENSE](../../LICENSE) e [NOTICE](../../NOTICE).
