<div align="center">

# SodaMem

**Memoria temporal y trazable para agentes de IA.**

Cada recuerdo sabe de qué turno de conversación proviene, y desde cuándo dejó de ser cierto.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](../../LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](../../pyproject.toml)
[![LongMemEval](https://img.shields.io/badge/LongMemEval-92.8%25-brightgreen.svg)](../../benchmarking/artifacts/)
[![LoCoMo](https://img.shields.io/badge/LoCoMo-86.88%25-brightgreen.svg)](../../benchmarking/README.md#locomo-cat-1-4)

<!-- langs -->
[English](../../README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Français](README.fr.md) · **Español** · [Deutsch](README.de.md) · [Português](README.pt-BR.md)
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

# Escribir necesita un modelo que extraiga los hechos; leer, nunca.
mem = SodaMem.open("./data", extractor=FactEventExtractorV2(create_provider_from_env()))

mem.ingest(
    [{"role": "user", "content": "En realidad cambié de Kauai a Oahu."}],
    user_id="u1", session_id="s1", session_time="2023-05-25",
)

block = mem.build_context("¿dónde me voy a alojar?", user_id="u1", token_budget=1000)
print(block.text)        # listo para pegar en un prompt — cero llamadas al LLM
print(block.citations)   # la evidencia detrás de cada línea
```

`SodaMem.open()` crea `./data` si no existe. Solo `.ingest()` necesita el
extractor — omite ese argumento para un almacén de solo lectura y `search` /
`build_context` funcionan exactamente igual.

**Nada tuyo sale de la máquina.** Sin telemetría, sin analítica, sin
callbacks — la única petición saliente que hace la instalación por defecto es
la descarga única del modelo de embeddings MiniLM (90 MB) en
`~/.cache/chroma/`; después solo habla con tu disco. Precarga esa caché y
funciona sin red.

---

## Por qué otra capa de memoria

La mayoría de los sistemas de memoria guardan **qué se dijo**. Las preguntas que
los rompen son **desde cuándo dejó de ser cierto** y **de dónde salió**: dos
problemas de modelo de datos, no de un índice vectorial más grande.

### Cada recuerdo lleva su comprobante

Un recuerdo recuperado no es una cadena suelta. Apunta al turno que lo produjo:

```
evidence_id  = ev_fact:fact_6ada707b…
support      = "¿Me recomiendas una playa poco concurrida en Oahu?"
predicate    = el usuario busca una playa tranquila en Oahu
entities     = location=Oahu | occasion=cumpleaños
source       = session_40 / turn_10          ← ese turno exacto, no «alguna charla»
date         = 2023-05-25
```

`FactEvent → SourceSpan → RawTurn` es una cadena real de claves foráneas, no una
puntuación de similitud. Cuando un usuario pregunta «¿por qué piensas eso de
mí?», hay respuesta. Cuando cumplimiento pregunta de dónde salió un dato
almacenado, hay una fila.

### Cuatro ejes temporales, no una marca de tiempo

| campo | pregunta que responde |
|---|---|
| `occurred_start` / `occurred_end` | cuándo **ocurrió** el hecho |
| `valid_from` / `valid_until` | durante qué periodo **fue cierto** |
| `document_time` | cuándo lo **dijo** el usuario |
| `created_at` | cuándo lo **guardamos** |

Con una sola marca de tiempo no se distingue «el año pasado **me mudé** a
Chicago» de «el año que viene **me mudaré** a Chicago», ni se puede representar
un hecho que dejó de ser cierto.

Las correcciones son **ADD-only**: una versión nueva más una arista
`SUPERSEDES`, nunca una reescritura en el sitio. `PATCH /v1/memories/{id}` cierra
la versión anterior con un `valid_until` y **la deja legible** — esa es toda la
diferencia con `DELETE`.

### Dos niveles de recuperación, y el barato es gratis de verdad

| nivel | llamadas al LLM | para |
|---|---|---|
| `search` / `build_context` | **cero** | la ruta por defecto: fusión determinista de BM25 + vectorial + entidades |
| `answer` | bucle del planificador | preguntas multi-salto que merecen los tokens |

`build_context` devuelve **un bloque listo para el prompt, con sus citas**, y no
llama al modelo ni una vez. La mayoría de los sistemas te entregan una lista de
registros y te dejan el ensamblado, el presupuesto de tokens y la
deduplicación.

Hay un tercer nivel intermedio: `build_context(organizer=...)` ejecuta un
organizador respaldado por LLM (value-board, enumeration-sweep) sobre el
conjunto recuperado, para preguntas del tipo «enumera todos los X que sabes
de mí». Es deliberadamente solo de Python — `/v1/context` nunca acepta un
organizador, así que la garantía de cero LLM de esa ruta no puede volcarse
con un parámetro de la petición.

### Recuperación auditable

Misma consulta, mismo almacén, mismo resultado, siempre. `/v1/events` registra
cada alta, sustitución y borrado con su motivo: «¿por qué el agente olvidó X?»
tiene respuesta a posteriori.

---

## Benchmark

**92,8 % (464/500)** en LongMemEval.

| | |
|---|---|
| reader / planner / judge | `deepseek-v4-flash` |
| prompts de evaluación | las plantillas `evaluate_qa.py` del propio benchmark, idénticas byte a byte |
| almacén | `longmemeval_s_500_Hobs_entitysubj`, 500 usuarios / 235.840 hechos |

**Publicamos cada respuesta y cada recuerdo recuperado** en
[`benchmarking/artifacts/`](../../benchmarking/artifacts/): 500 respuestas
íntegras y 8 427 evidencias. Vuelve a puntuarlas con el juez que prefieras, o
pasa nuestro contexto recuperado a tu propio reader y mira qué hace el número.
Ninguna de las dos cosas requiere acceso a nada nuestro.

**86,88 % (1338/1540)** en LoCoMo, categorías 1-4: se excluye la categoría 5
(adversarial), es decir 1.540 de las 1.986 preguntas. Exactitud de QA de extremo
a extremo, evaluada con LLM-as-judge.

| | |
|---|---|
| reader / planner / judge | `deepseek-v4-flash` |
| prompts de evaluación | las plantillas del propio benchmark LongMemEval, copiadas byte a byte |
| almacén | `locomo10_Hobs`, 10 almacenes de usuario / 2.905 fact events |
| código | una build pre-release — el historial publicado empieza en v0.1.0 |

**Para LoCoMo no publicamos ningún artefacto por pregunta**: ni respuestas, ni
contexto recuperado, ni directorio de ejecución. Lo que sí está publicado es
[la sección LoCoMo de `benchmarking/README.md`](../../benchmarking/README.md#locomo-cat-1-4):
el desglose por categoría, la dispersión por conversación, la procedencia y los
pasos de reproducción.

---

## Instalación

| extra | qué añade |
|---|---|
| *(base)* | modelo de datos, almacenamiento, búsqueda BM25, ingesta — **cuatro dependencias, ninguna pesada** |
| `chroma` | búsqueda vectorial + embedder ONNX local (lo necesita `SodaMem.open()`) |
| `llm` | proveedores compatibles con OpenAI (OpenAI / DeepSeek / Gemini, mismo protocolo) |
| `anthropic` | el proveedor de Anthropic (SDK propio) |
| `answer` | la ruta de respuesta planificador + reader |
| `server` | el servicio HTTP (FastAPI + uvicorn — tres paquetes, deliberadamente) |
| `mcp` | la superficie de servidor MCP |

La instalación base trae `pydantic`, `numpy`, `rank-bm25` y `python-dateutil`.
Nada más, y hay una comprobación en CI que rompe el build si esa lista crece por
accidente.


Todavía no está en PyPI. Hasta la primera versión etiquetada, desde el

---

## Úsalo desde donde quieras

**HTTP** — `add` / `search` / `context` / `answer`, además de escritura por
lotes, sustitución, eventos, métricas y consumo de tokens:

```bash
curl -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  localhost:8000/v1/context \
  -d '{"user_id":"u1","query":"¿qué prefiere?","token_budget":1000}'
```

`/v1/context` y `/v1/search` aceptan cuerpo JSON; `/v1/context` responde
además a un GET con parámetros de consulta, porque es una lectura pura.

**SDK** — TypeScript sobre HTTP ([`sdk-ts/`](../../sdk-ts/), cero
dependencias en tiempo de ejecución, ESM + CJS). Python habla directamente
con la biblioteca — `import sodamem` y ya estás por debajo de la red.

**Frameworks de agentes** — LangGraph, CrewAI, OpenAI Agents SDK, Vercel AI SDK.
El ámbito se fija al construir las herramientas y **nunca aparece en el esquema
que ve el modelo**: un `user_id` que el modelo puede elegir es un `user_id` que
puede alucinar.

**MCP** — 8 herramientas, incluidas `entity_timeline` (el historial de una
entidad en orden, cada elemento apuntando aún a su origen) y `explore_memory`
(recorrer el grafo). Seis son de lectura y están
siempre disponibles; las dos que modifican (`add_memories`, `delete_memory`)
solo aparecen con `SODAMEM_MCP_ALLOW_WRITE=true`, que `sodamem install`
escribe por ti en la configuración de cliente que genera.

**Consola web** — explorar e inspeccionar recuerdos por inquilino, incluida en
la imagen.

---

## Autoalojamiento

```bash
cp .env.example .env      # define SODAMEM_API_KEY
docker compose up -d
```

Autenticación activa por defecto. El aislamiento entre inquilinos es **físico**:
un fichero SQLite y una colección vectorial por `user_id`, así que «borrar este
usuario» es borrar un directorio.

`/v1/admin/*` responde a lo que si no exigiría abrir una shell dentro del
contenedor: configuración efectiva (los secretos se informan como
«definido / no definido» y nunca se imprimen), claves API con nombre, registro
rodante de peticiones, estado de disco y carga.

Observabilidad: `/v1/metrics` (percentiles de latencia), `/v1/usage` (tokens,
separando ingesta y respuesta), `/metrics` (formato Prometheus), `/v1/events`
(todo cambio de memoria) y webhooks salientes — cola acotada, firmados con HMAC,
inactivos mientras no configures una URL.

Los perfiles de entidad se reconstruyen bajo demanda, nunca por temporizador:
`POST /v1/maintenance/dream` (idempotente, reanudable; una llamada concurrente
devuelve `already_running`). Cuándo gastar esos tokens es una decisión del
despliegue, así que SodaMem no incluye ningún planificador.

Detalles en la versión inglesa: [Self-hosting](../../README.md#self-hosting).

---

## Documentación

| | |
|---|---|
| [Herramientas de codificación](../../README.md#coding-tools) | Claude Code, Cursor y otros clientes MCP |
| [Método de benchmark](../../benchmarking/README.md) | cómo se produjeron las cifras de benchmark |

---

## Agradecimientos

Las contribuciones tempranas de [@sunjiajunsunjiajun](https://github.com/sunjiajunsunjiajun) and [@Lum1104](https://github.com/Lum1104) dieron forma al trabajo del que nació
este proyecto. Gracias.

## Licencia

Apache-2.0. Véanse [LICENSE](../../LICENSE) y [NOTICE](../../NOTICE).
