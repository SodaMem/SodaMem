<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/logo-dark.webp">
  <img src="../assets/logo.webp" alt="SodaMem" width="260">
</picture>

**Una capa de memoria agéntica que evoluciona por sí sola para agentes de IA.**

La mayoría de los sistemas de memoria guardan lo que dijiste y ahí se quedan: correctos hoy, silenciosamente equivocados en cuanto tu vida cambia. SodaMem evoluciona junto a tu agente: los hechos se sustituyen en vez de sobrescribirse, los perfiles de entidad se reconstruyen bajo demanda en vez de quedarse obsoletos sin avisar, y cada respuesta sigue remontándose al turno exacto del que salió. Recuperar cuesta cero llamadas al LLM, así que la misma pregunta obtiene siempre la misma respuesta.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](../../LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](../../pyproject.toml)
[![LongMemEval](https://img.shields.io/badge/LongMemEval-92.8%25-brightgreen.svg)](../../benchmarking/artifacts/)
[![LoCoMo](https://img.shields.io/badge/LoCoMo-86.88%25-brightgreen.svg)](../../benchmarking/README.md#locomo-cat-1-4)
[![Discussions](https://img.shields.io/github/discussions/SodaMem/SodaMem?logo=github&label=discussions)](https://github.com/SodaMem/SodaMem/discussions)

<!-- langs -->
[English](../../README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Français](README.fr.md) · **Español** · [Deutsch](README.de.md) · [Português](README.pt-BR.md)
<!-- /langs -->

[Integraciones de agentes](#integraciones-de-agentes) · [Benchmark](#benchmark) · [Inicio rápido](#inicio-rápido) · [Por qué otra capa de memoria](#por-qué-otra-capa-de-memoria) · [Instalación](#instalación) · [Úsalo desde donde quieras](#úsalo-desde-donde-quieras) · [Herramientas de codificación](#herramientas-de-codificación) · [Autoalojamiento](#autoalojamiento) · [Documentación](#documentación)

<img src="../assets/benchmark-cost-accuracy.webp" alt="Cost-accuracy trade-off on LongMemEval-S" width="760">

*Precisión frente al coste estimado de API por pregunta. El cuadrante que importa es arriba a la izquierda.*

</div>

---

## Integraciones de agentes

| Runtime | Cómo | Guía |
|---|---|---|
| **Hermes Agent** | MCP | [`integrations/hermes/README.md`](../../integrations/hermes/README.md) |
| **DeepSeek Harness** | MCP | [`integrations/deepseek-harness/README.md`](../../integrations/deepseek-harness/README.md) |
| **Genérico / cualquier cliente MCP** | MCP | [`mcp_server/README.md`](../../mcp_server/README.md) |
| **LangGraph** | adaptador Python | [`adapters/README.md`](../../adapters/README.md) |
| **CrewAI** | adaptador Python | [`adapters/README.md`](../../adapters/README.md) |
| **OpenAI Agents SDK** | adaptador Python | [`adapters/README.md`](../../adapters/README.md) |
| **Vercel AI SDK** | adaptador TS | [`sdk-ts/`](../../sdk-ts/) |
| **Claude Code, Cursor y otros clientes de codificación** | CLI + hooks | ver [Herramientas de codificación](#herramientas-de-codificación) |

Índice completo, con esquemas de herramientas MCP y detalles de los adaptadores: [`integrations/README.md`](../../integrations/README.md).

---

## Benchmark

<div align="center">
  <img src="../assets/benchmark-longmemeval.webp" alt="LongMemEval: SodaMem 92.8%, Hindsight 91.4%, Mem0 OSS 91.0%" width="720">
</div>

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

<div align="center">
  <img src="../assets/benchmark-locomo.webp" alt="LoCoMo: SodaMem 86.88%, MemMachine 91.69%, Hindsight 89.61%, MIRIX 85.38%, Memobase 75.78%, Mem0 OSS 66.88%" width="720">
</div>

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

## Inicio rápido

Esta es la ruta en Python. ¿Vas a integrarlo en un framework de agentes o un cliente MCP? Consulta [Integraciones de agentes](#integraciones-de-agentes). ¿Lo vas a llamar desde TypeScript/Node? Consulta [Úsalo desde donde quieras](#úsalo-desde-donde-quieras). ¿Lo vas a ejecutar como servicio compartido? Consulta [Autoalojamiento](#autoalojamiento).

### Ejemplo

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


| la pregunta | la respuesta habitual | SodaMem |
|---|---|---|
| ¿De dónde salió este recuerdo? | una puntuación de similitud y algunos metadatos | `FactEvent → SourceSpan → RawTurn`, una cadena de claves foráneas hasta el turno exacto |
| El usuario cambió de opinión, ¿y ahora? | se sobrescribe; el valor anterior desaparece | solo se añade, más una arista `SUPERSEDES`; la versión anterior se cierra con `valid_until` y sigue siendo legible |
| «Me mudé a Chicago el año pasado» vs «me mudo el año que viene» | una sola marca de tiempo | cuatro ejes temporales: ocurrido / válido / dicho / almacenado |
| ¿Cuánto cuesta una recuperación? | una llamada al LLM por recuperación | `build_context` no hace **ninguna** y devuelve un bloque listo para el prompt, con sus citas |
| ¿La misma consulta dos veces da lo mismo? | depende del muestreo del modelo | fusión determinista: mismo store, misma consulta, mismo resultado |
| ¿Por qué olvidó X? | sin respuesta | `/v1/events` registra cada alta, sustitución y borrado, con su motivo |

Dos de ellas merecen un vistazo más de cerca — el resto ya lo dice la tabla.

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

`FactEvent → SourceSpan → RawTurn` es una cadena de claves foráneas, no una
puntuación de similitud, así que «¿por qué piensas eso de mí?» tiene
respuesta.

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
además a un GET con parámetros de consulta, porque es una lectura pura. La
única excepción exclusiva de Python es `build_context(organizer=...)`, que
ejecuta un organizador respaldado por LLM sobre el conjunto recuperado para
preguntas como «enumera todo lo que sabes de mí» — `/v1/context` nunca acepta
uno, así que la garantía de cero LLM por HTTP no se puede desactivar con un
parámetro de la petición.

**SDKs** — TypeScript sobre HTTP ([`sdk-ts/`](../../sdk-ts/), cero
dependencias en tiempo de ejecución, ESM + CJS):

```bash
npm i sodamem
```

```typescript
import { SodaMemClient } from "sodamem";

const mem = new SodaMemClient({ baseUrl: "http://localhost:8000", apiKey: process.env.SODAMEM_API_KEY! });
const block = await mem.context({ user_id: "u1", query: "¿qué prefiere?", token_budget: 1000 });
```

Python habla directamente con la biblioteca — `import sodamem` y ya estás
por debajo de la red.

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

## Herramientas de codificación

**Paso 1.** Arranca el daemon — el único proceso dueño de los almacenes:

```
sodamem daemon ensure
```

**Paso 2.** Conecta un cliente a él:

```
sodamem install claude-code
```

Todos los clientes reciben la superficie de herramientas MCP. Cuatro también
reciben **hooks**, para que la memoria se recupere y se guarde sin que el
modelo tenga que decidir llamar a una herramienta — cosa que en una sesión de
codificación casi nunca hace, porque está ocupado leyendo archivos.

Lo que los hooks pueden hacer no es uniforme, porque los sistemas de hooks
tampoco lo son. Esto es lo que cada cliente soporta de verdad, y `sodamem
clients` imprime lo mismo:

| Cliente | Recall | Retain |
|---|---|---|
| Claude Code | cada prompt | cada turno + fin de sesión |
| GitHub Copilot CLI | cada prompt | cada turno |
| Cursor | inicio de sesión (resumen del proyecto) | — |
| Codex CLI | inicio de sesión (resumen del proyecto) | — |
| Claude Desktop, VS Code, Windsurf, Zed, OpenCode | solo herramientas MCP | solo herramientas MCP |

El `beforeSubmitPrompt` de Cursor puede leer un prompt pero no puede inyectar
nada (su documentación enumera exactamente tres eventos que sí pueden, y este
no es uno), y ni Cursor ni Codex le pasan a un hook una ruta al transcript —
así que no hay nada que un hook de retain pueda leer. Esos dos reciben un
resumen del proyecto al inicio de sesión y escriben a través de la
herramienta `add_memories`. No instalamos un hook que solo puede no hacer
nada.

Tres cosas que conviene saber antes de ejecutarlo:

**Un daemon, muchos editores.** Los almacenes por usuario son SQLite sin
WAL, así que solo un proceso puede abrirlos (ADR 0001 §2). `install` por eso
apunta cada cliente a un servicio en ejecución por defecto, en lugar de dejar
que cada uno lance el suyo — y si eliges deliberadamente un almacén local
(`--local-store`), un segundo cliente ahora se niega a arrancar en vez de
corromper en silencio los datos del primero.

**Los recuerdos están acotados al repo.** `install` deriva un `project_id`
de la raíz de git (un `git worktree` resuelve a su repo padre, así que una
rama por tarea no es un banco de memoria por tarea). Es un acotamiento, no
una partición: lo que le contaste a SodaMem fuera de un proyecto sigue
apareciendo dentro de todos los proyectos, y quitar la clave responde a
«¿cómo arreglé esto en el otro repo?».

**Retain necesita credenciales de extracción.** Recall es cero-LLM y
funciona sin ellas; guardar hechos no. `sodamem daemon ensure` lo avisa por
adelantado en vez de aceptar cada escritura y fallar el trabajo después.

```
sodamem install claude-code --dry-run      # imprime qué cambiaría
sodamem install cursor vscode zed          # varios a la vez
sodamem daemon status                      # qué está respondiendo de verdad
```

La configuración existente se fusiona, no se reemplaza — otros servidores
MCP, otros ajustes y comentarios TOML escritos a mano sobreviven — y la
primera escritura de cualquier archivo deja un `.sodamem-backup` al lado.

---

## Autoalojamiento

Un comando:

```bash
cp .env.example .env      # luego define SODAMEM_API_KEY
docker compose up -d
```

**La autenticación está activada por defecto.** `docker-compose.yml` nunca
define `SODAMEM_AUTH_DISABLED` — el servidor se niega a arrancar si
`SODAMEM_API_KEY` no está definida (ver `server/settings.py`), así que no hay
manera de desplegarlo accidentalmente abierto. Define la clave en `.env`
antes del primer `docker compose up`.

**Ejecuta exactamente un worker.** `--workers 1` es una restricción de
corrección, no de rendimiento: los almacenes por usuario son bases SQLite
abiertas sin WAL, y dos procesos escribiendo en el store del mismo usuario lo
corrompen. El `CMD` de la imagen lo dice explícitamente, y el servidor toma
un bloqueo exclusivo sobre su directorio de datos al arrancar — un segundo
proceso apuntando al mismo directorio se niega a arrancar con
`data_root_locked` en lugar de corromper los datos en silencio. Escalar
horizontalmente necesita antes un job store externo
(`docs/adr/0001-control-plane-db.md`).

La referencia completa de operaciones — llamadas a la API, endpoints de
administración, métricas, mantenimiento, backups, actualizaciones — vive en
[`docs/self-hosting.md`](../../docs/self-hosting.md), disponible por ahora
solo en inglés.

---

## Documentación

| | |
|---|---|
| [Método de benchmark](../../benchmarking/README.md) | cómo se produjeron las cifras de benchmark |

---

## Agradecimientos

Las contribuciones tempranas de [@sunjiajunsunjiajun](https://github.com/sunjiajunsunjiajun) and [@Lum1104](https://github.com/Lum1104) dieron forma al trabajo del que nació
este proyecto. Gracias.

## Licencia

Apache-2.0. Véanse [LICENSE](../../LICENSE) y [NOTICE](../../NOTICE).
