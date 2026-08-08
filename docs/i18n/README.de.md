<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/logo-dark.webp">
  <img src="../assets/logo.webp" alt="SodaMem" width="260">
</picture>

**Zeitlich fundiertes, belegbares Gedächtnis für KI-Agenten.**

Jede Erinnerung weiß, aus welchem Gesprächszug sie stammt — und ab wann sie nicht mehr galt.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](../../LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](../../pyproject.toml)
[![LongMemEval](https://img.shields.io/badge/LongMemEval-92.8%25-brightgreen.svg)](../../benchmarking/artifacts/)
[![LoCoMo](https://img.shields.io/badge/LoCoMo-86.88%25-brightgreen.svg)](../../benchmarking/README.md#locomo-cat-1-4)
[![Discussions](https://img.shields.io/github/discussions/SodaMem/SodaMem?logo=github&label=discussions)](https://github.com/SodaMem/SodaMem/discussions)

<!-- langs -->
[English](../../README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Français](README.fr.md) · [Español](README.es.md) · **Deutsch** · [Português](README.pt-BR.md)
<!-- /langs -->

<img src="../assets/benchmark-cost-accuracy.webp" alt="Cost-accuracy trade-off on LongMemEval-S" width="760">

*Genauigkeit gegen geschätzte API-Kosten pro Frage. Der Quadrant, auf den es ankommt, liegt oben links.*

</div>

---

## Benchmark

<div align="center">
  <img src="../assets/benchmark-longmemeval.webp" alt="LongMemEval: SodaMem 92.8%, Hindsight 91.4%, Mem0 OSS 91.0%" width="720">
</div>

**92,8 % (464/500)** auf LongMemEval.

| | |
|---|---|
| reader / planner / judge | `deepseek-v4-flash` |
| Bewertungs-Prompts | die `evaluate_qa.py`-Vorlagen des Benchmarks selbst, byte-identisch |
| Store | `longmemeval_s_500_Hobs_entitysubj`, 500 Nutzer / 235.840 Fakten |

**Jede Antwort und jede abgerufene Erinnerung ist veröffentlicht** unter
[`benchmarking/artifacts/`](../../benchmarking/artifacts/) — 500 Antworten im
Wortlaut, 8.427 Belege. Bewerten Sie sie mit einem Judge Ihrer Wahl neu, oder
geben Sie unseren abgerufenen Kontext Ihrem eigenen Reader und sehen Sie, was
die Zahl macht. Beides ohne Zugriff auf irgendetwas von uns.

<div align="center">
  <img src="../assets/benchmark-locomo.webp" alt="LoCoMo: SodaMem 86.88%, MemMachine 91.69%, Hindsight 89.61%, MIRIX 85.38%, Memobase 75.78%, Mem0 OSS 66.88%" width="720">
</div>

**86,88 % (1338/1540)** auf LoCoMo, Kategorien 1-4 — Kategorie 5 (adversarial)
ist ausgeschlossen, das sind 1.540 der 1.986 Fragen. End-to-End-QA-Genauigkeit,
bewertet per LLM-as-judge.

| | |
|---|---|
| reader / planner / judge | `deepseek-v4-flash` |
| Bewertungs-Prompts | die Vorlagen des LongMemEval-Benchmarks selbst, byteweise kopiert |
| Store | `locomo10_Hobs`, 10 Nutzer-Stores / 2.905 Fact Events |
| Code | ein Pre-Release-Build — die veröffentlichte Historie beginnt bei v0.1.0 |

**Für LoCoMo sind keinerlei Artefakte pro Frage veröffentlicht** — keine
Antworten, kein abgerufener Kontext, kein Run-Verzeichnis. Veröffentlicht ist
[der LoCoMo-Abschnitt in `benchmarking/README.md`](../../benchmarking/README.md#locomo-cat-1-4):
die Aufschlüsselung nach Kategorie, die Streuung über die Konversationen, die
Provenance und die Schritte zur Reproduktion.

---

## Schnellstart

```bash
pip install "sodamem[chroma,llm]"
```

```python
from sodamem import SodaMem
from sodamem.llm import create_provider_from_env      # SODAMEM_LLM_API_KEY
from sodamem.memory.ingest.extractor import FactEventExtractorV2

# Schreiben braucht ein Modell zum Extrahieren; Lesen nie.
mem = SodaMem.open("./data", extractor=FactEventExtractorV2(create_provider_from_env()))

mem.ingest(
    [{"role": "user", "content": "Ich bin doch von Kauai nach Oahu gewechselt."}],
    user_id="u1", session_id="s1", session_time="2023-05-25",
)

block = mem.build_context("wo übernachte ich?", user_id="u1", token_budget=1000)
print(block.text)        # direkt in den Prompt — null LLM-Aufrufe
print(block.citations)   # der Beleg hinter jeder Zeile
```

`SodaMem.open()` legt `./data` an, falls es fehlt. Nur `.ingest()` braucht
den Extractor — ohne dieses Argument bekommen Sie einen Nur-Lese-Store, und
`search` / `build_context` arbeiten exakt gleich.

**Nichts von Ihnen verlässt die Maschine.** Keine Telemetrie, keine
Analytics, kein Callback — die einzige ausgehende Anfrage der
Standardinstallation ist der einmalige Download des 90 MB großen
MiniLM-Embedding-Modells nach `~/.cache/chroma/`; danach spricht sie nur mit
Ihrer Festplatte. Füllen Sie diesen Cache vorab, läuft sie ohne Netz.

---

## Warum noch eine Gedächtnisschicht

Die meisten Gedächtnissysteme speichern, **was** gesagt wurde. Woran sie
scheitern, sind die Fragen **seit wann es nicht mehr stimmt** und **woher es
kommt** — beides Fragen des Datenmodells, nicht eines größeren Vektorindex.


| die Frage | die übliche Antwort | SodaMem |
|---|---|---|
| Woher stammt diese Erinnerung? | ein Ähnlichkeitswert und etwas Metadaten | `FactEvent → SourceSpan → RawTurn` — eine Fremdschlüsselkette bis zum genauen Turn |
| Die Nutzerin hat es sich anders überlegt — und jetzt? | überschreiben; der alte Wert ist weg | nur anfügen, dazu eine `SUPERSEDES`-Kante; die alte Version schließt mit `valid_until` und bleibt lesbar |
| „Ich bin letztes Jahr nach Chicago gezogen“ vs. „ich ziehe nächstes Jahr“ | ein Zeitstempel | vier Zeitachsen: geschehen / gültig / gesagt / gespeichert |
| Was kostet ein Abruf? | ein LLM-Aufruf pro Abruf | `build_context` macht **keinen** und liefert einen fertigen Prompt-Block samt Belegen |
| Zweimal dieselbe Anfrage — dieselbe Antwort? | hängt vom Sampling des Modells ab | deterministische Fusion: gleicher Store, gleiche Anfrage, gleiches Ergebnis |
| Warum hat es X vergessen? | keine Antwort | `/v1/events` protokolliert jedes Anlegen, Ersetzen und Löschen — mit Begründung |

Jede Zeile wird unten ausgeführt, und jede lässt sich in diesem Repository nachprüfen, statt geglaubt werden zu müssen.

### Jede Erinnerung bringt ihren Beleg mit

Eine abgerufene Erinnerung ist keine freischwebende Zeichenkette. Sie zeigt auf
den Gesprächszug, aus dem sie entstand:

```
evidence_id  = ev_fact:fact_6ada707b…
support      = "Kannst du mir einen nicht überfüllten Strand auf Oahu empfehlen?"
predicate    = Nutzer sucht einen ruhigen Strand auf Oahu
entities     = location=Oahu | occasion=Geburtstag
source       = session_40 / turn_10          ← genau dieser Zug, nicht „irgendein Chat“
date         = 2023-05-25
```

`FactEvent → SourceSpan → RawTurn` ist eine echte Fremdschlüsselkette, kein
Ähnlichkeitswert. Fragt ein Nutzer „warum denkst du das über mich?“, gibt es
eine Antwort. Fragt die Compliance, woher ein gespeicherter Fakt stammt, gibt
es eine Zeile.

### Vier Zeitachsen statt eines Zeitstempels

| Feld | beantwortet |
|---|---|
| `occurred_start` / `occurred_end` | wann das Ereignis **stattfand** |
| `valid_from` / `valid_until` | in welchem Zeitraum der Fakt **galt** |
| `document_time` | wann der Nutzer es **sagte** |
| `created_at` | wann wir es **gespeichert** haben |

Mit einem einzigen Zeitstempel lässt sich „ich **bin** letztes Jahr nach Chicago
**gezogen**“ nicht von „ich **ziehe** nächstes Jahr nach Chicago“ unterscheiden —
und ein Fakt, der nicht mehr gilt, ist gar nicht darstellbar.

Korrekturen laufen **ADD-only**: eine neue Version plus eine `SUPERSEDES`-Kante,
nie ein Überschreiben. `PATCH /v1/memories/{id}` schließt die alte Version mit
einem `valid_until` und **lässt sie lesbar** — genau darin unterscheidet sie
sich von `DELETE`.

### Zwei Abrufstufen, und die günstige ist wirklich kostenlos

| Stufe | LLM-Aufrufe | wofür |
|---|---|---|
| `search` / `build_context` | **null** | der Standardweg: deterministische Fusion aus BM25 + Vektor + Entitäten |
| `answer` | Planner-Schleife | Mehrschritt-Fragen, die die Tokens wert sind |

`build_context` liefert **einen prompt-fertigen Block samt Zitaten** und ruft
kein Modell auf. Die meisten Systeme geben eine Trefferliste zurück und
überlassen Ihnen das Zusammensetzen, das Token-Budget und die Deduplizierung.

Dazwischen liegt eine dritte Stufe: `build_context(organizer=...)` lässt
einen LLM-gestützten Organizer (value-board, enumeration-sweep) über die
Trefferliste laufen — für Fragen wie „zähl alles auf, was du an X über mich
weißt“. Bewusst nur in Python — `/v1/context` nimmt niemals einen Organizer
entgegen, damit die Null-LLM-Zusage dieser Route nicht per Request-Parameter
gekippt werden kann.

### Nachvollziehbarer Abruf

Gleiche Anfrage, gleicher Store, gleiches Ergebnis — jedes Mal. `/v1/events`
protokolliert jedes Hinzufügen, Ersetzen und Löschen samt Begründung: „warum
hat der Agent X vergessen?“ ist im Nachhinein beantwortbar.

---

## Installation

| Extra | was es hinzufügt |
|---|---|
| *(base)* | Datenmodell, Speicher, BM25-Suche, Ingest — **vier Abhängigkeiten, keine schwere** |
| `chroma` | Vektorsuche + lokaler ONNX-Embedder (von `SodaMem.open()` benötigt) |
| `llm` | OpenAI-kompatible Anbieter (OpenAI / DeepSeek / Gemini, gleiches Protokoll) |
| `anthropic` | der Anthropic-Anbieter (eigenes SDK) |
| `answer` | der Antwortpfad aus Planner + Reader |
| `server` | der HTTP-Dienst (FastAPI + uvicorn — bewusst drei Pakete) |
| `mcp` | die MCP-Server-Oberfläche |

Die Basisinstallation zieht `pydantic`, `numpy`, `rank-bm25`,
`python-dateutil` — sonst nichts. Ein CI-Gate lässt den Build scheitern, falls
diese Liste versehentlich wächst.



---

## Überall einsetzbar

**HTTP** — `add` / `search` / `context` / `answer`, dazu Batch-Schreiben,
Ersetzen, Events, Metriken und Token-Verbrauch:

```bash
curl -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  localhost:8000/v1/context \
  -d '{"user_id":"u1","query":"was bevorzugt die Person?","token_budget":1000}'
```

`/v1/context` und `/v1/search` nehmen beide einen JSON-Body; `/v1/context`
beantwortet zusätzlich ein schlichtes GET mit Query-Parametern, denn es ist
ein reiner Lesezugriff.

**SDKs** — TypeScript über HTTP ([`sdk-ts/`](../../sdk-ts/), keine
Laufzeitabhängigkeiten, ESM + CJS). Python spricht direkt mit der Bibliothek
— `import sodamem`, und Sie sind bereits unterhalb des Netzwerks.

**Agenten-Frameworks** — LangGraph, CrewAI, OpenAI Agents SDK, Vercel AI SDK.
Der Scope wird beim Erzeugen der Tools gebunden und **taucht nie in dem Schema
auf, das das Modell sieht**: eine `user_id`, die das Modell wählen kann, ist
eine `user_id`, die es halluzinieren kann.

**MCP** — 8 Tools, darunter `entity_timeline` (die Historie einer Entität in
zeitlicher Ordnung, jeder Eintrag weiterhin mit Quellverweis) und
`explore_memory` (den Graphen nach außen ablaufen). Sechs davon sind Lesezugriffe und immer
verfügbar; die beiden ändernden (`add_memories`, `delete_memory`) erscheinen
nur unter `SODAMEM_MCP_ALLOW_WRITE=true`, das `sodamem install` für Sie in die
erzeugte Client-Konfiguration schreibt.

**Web-Konsole** — Erinnerungen pro Mandant durchsehen und prüfen, im Image
enthalten.

---

## Self-Hosting

```bash
cp .env.example .env      # SODAMEM_API_KEY setzen
docker compose up -d
```

Authentifizierung standardmäßig an. Die Mandantentrennung ist **physisch**: eine
SQLite-Datei und eine Vektor-Collection pro `user_id` — „diesen Nutzer löschen“
heißt ein Verzeichnis löschen.

`/v1/admin/*` beantwortet, wofür man sonst eine Shell im Container bräuchte:
effektive Konfiguration (Geheimnisse werden als „gesetzt / nicht gesetzt“
gemeldet und nie ausgegeben), benannte API-Schlüssel, rollierendes
Anfrage-Log, Platten- und Lastzustand.

Observability: `/v1/metrics` (Latenz-Perzentile), `/v1/usage` (Tokenverbrauch,
getrennt nach Ingest und Answer), `/metrics` (Prometheus-Format), `/v1/events`
(jede Gedächtnisänderung) sowie ausgehende Webhooks — begrenzte Queue,
HMAC-signiert, ohne konfigurierte URL vollständig inaktiv.

Entitätsprofile werden auf Anforderung neu gebaut, nie per Timer:
`POST /v1/maintenance/dream` (idempotent, fortsetzbar; ein paralleler Aufruf
liefert `already_running`). Wann diese Tokens ausgegeben werden, ist eine
Deployment-Entscheidung — deshalb bringt SodaMem keinen Scheduler mit.

Details in der englischen Fassung: [Self-hosting](../../README.md#self-hosting).

---

## Dokumentation

| | |
|---|---|
| [Coding-Tools](../../README.md#coding-tools) | Claude Code, Cursor und andere MCP-Clients |
| [Benchmark-Methode](../../benchmarking/README.md) | wie die Benchmark-Zahlen entstanden |

---

## Danksagung

Frühe Beiträge von [@sunjiajunsunjiajun](https://github.com/sunjiajunsunjiajun) and [@Lum1104](https://github.com/Lum1104) haben die Arbeit geprägt, aus der dieses Projekt
hervorgegangen ist. Vielen Dank.

## Lizenz

Apache-2.0. Siehe [LICENSE](../../LICENSE) und [NOTICE](../../NOTICE).
