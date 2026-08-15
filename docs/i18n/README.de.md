<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/logo-dark.webp">
  <img src="../assets/logo.webp" alt="SodaMem" width="260">
</picture>

**Eine sich selbst weiterentwickelnde, agentische Gedächtnisschicht für KI-Agenten.**

Die meisten Gedächtnissysteme speichern, was gesagt wurde, und belassen es dabei — heute richtig, im nächsten Moment leise falsch, sobald sich etwas ändert. SodaMem entwickelt sich mit dem Agenten mit: Fakten werden ersetzt statt überschrieben, Entitätsprofile werden bei Bedarf neu aufgebaut statt unbemerkt zu veralten, und jede Antwort lässt sich bis zum genauen Gesprächszug zurückverfolgen, aus dem sie stammt. Der Abruf kostet null LLM-Aufrufe — dieselbe Frage liefert also immer dieselbe Antwort.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](../../LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](../../pyproject.toml)
[![LongMemEval](https://img.shields.io/badge/LongMemEval-92.8%25-brightgreen.svg)](../../benchmarking/artifacts/)
[![LoCoMo](https://img.shields.io/badge/LoCoMo-86.88%25-brightgreen.svg)](../../benchmarking/README.md#locomo-cat-1-4)
[![Discussions](https://img.shields.io/github/discussions/SodaMem/SodaMem?logo=github&label=discussions)](https://github.com/SodaMem/SodaMem/discussions)

<!-- langs -->
[English](../../README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Français](README.fr.md) · [Español](README.es.md) · **Deutsch** · [Português](README.pt-BR.md)
<!-- /langs -->

[Agent-Integrationen](#agent-integrationen) · [Benchmark](#benchmark) · [Schnellstart](#schnellstart) · [Warum noch eine Gedächtnisschicht](#warum-noch-eine-gedächtnisschicht) · [Installation](#installation) · [Überall einsetzbar](#überall-einsetzbar) · [Coding-Tools](#coding-tools) · [Self-Hosting](#self-hosting) · [Dokumentation](#dokumentation)

<img src="../assets/benchmark-cost-accuracy.webp" alt="Cost-accuracy trade-off on LongMemEval-S" width="760">

*Genauigkeit gegen geschätzte API-Kosten pro Frage. Der Quadrant, auf den es ankommt, liegt oben links.*

</div>

---

## Agent-Integrationen

| Runtime | Wie | Anleitung |
|---|---|---|
| **Hermes Agent** | MCP | [`integrations/hermes/README.md`](../../integrations/hermes/README.md) |
| **DeepSeek Harness** | MCP | [`integrations/deepseek-harness/README.md`](../../integrations/deepseek-harness/README.md) |
| **Generisch / jeder MCP-Client** | MCP | [`mcp_server/README.md`](../../mcp_server/README.md) |
| **LangGraph** | Python-Adapter | [`adapters/README.md`](../../adapters/README.md) |
| **CrewAI** | Python-Adapter | [`adapters/README.md`](../../adapters/README.md) |
| **OpenAI Agents SDK** | Python-Adapter | [`adapters/README.md`](../../adapters/README.md) |
| **Vercel AI SDK** | TS-Adapter | [`sdk-ts/`](../../sdk-ts/) |
| **Claude Code, Cursor und andere Coding-Clients** | CLI + Hooks | siehe [Coding-Tools](#coding-tools) |

Vollständiger Index inklusive MCP-Tool-Schemas und Adapter-Details: [`integrations/README.md`](../../integrations/README.md).

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
die Zahl macht — der Reader wechselt, der Score bewegt sich, und genau
deshalb sind die Artefakte der Punkt, nicht die 92,8 % für sich. Beides ohne
Zugriff auf irgendetwas von uns.

<div align="center">
  <img src="../assets/benchmark-locomo.webp" alt="LoCoMo: SodaMem 86.88%, MemMachine 91.69%, Hindsight 89.61%, MIRIX 85.38%, Memobase 75.78%, Mem0 OSS 66.88%" width="720">
</div>

**86,88 % (1338/1540)** auf LoCoMo. End-to-End-QA-Genauigkeit, bewertet per
LLM-as-judge.

| | |
|---|---|
| reader / planner / judge | `deepseek-v4-flash` |
| Bewertungs-Prompts | die Vorlagen des LongMemEval-Benchmarks selbst, byteweise kopiert |
| Store | `locomo10_Hobs`, 10 Nutzer-Stores / 2.905 Fact Events |
| Code | ein Pre-Release-Build — die veröffentlichte Historie dieses Repositorys beginnt bei v0.1.0 |

**Für LoCoMo sind keinerlei Artefakte pro Frage veröffentlicht** — keine
Antworten, kein abgerufener Kontext, kein Run-Verzeichnis. Veröffentlicht ist
[der LoCoMo-Abschnitt in `benchmarking/README.md`](../../benchmarking/README.md#locomo-cat-1-4):
die Aufschlüsselung nach Kategorie, die Streuung über die Konversationen, die
Provenance und die Schritte zur Reproduktion.

---

## Schnellstart

Das hier ist der Python-Weg. Einbindung in ein Agent-Framework oder einen
MCP-Client? Siehe [Agent-Integrationen](#agent-integrationen). Aufruf aus
TypeScript/Node? Siehe [Überall einsetzbar](#überall-einsetzbar). Betrieb als
gemeinsamer Dienst? Siehe [Self-Hosting](#self-hosting).

### Beispiel

```bash
pip install "sodamem[chroma,llm]"
```

```python
from sodamem import SodaMem
from sodamem.llm import create_provider_from_env      # SODAMEM_LLM_API_KEY
from sodamem.memory.ingest.extractor import FactEventExtractorV2

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
| „Ich bin letztes Jahr nach Chicago gezogen" vs. „ich ziehe nächstes Jahr" | ein Zeitstempel | vier Zeitachsen: geschehen / gültig / gesagt / gespeichert |
| Was kostet ein Abruf? | ein LLM-Aufruf pro Abruf | `build_context` macht **keinen** und liefert einen fertigen Prompt-Block samt Belegen |
| Zweimal dieselbe Anfrage — dieselbe Antwort? | hängt vom Sampling des Modells ab | deterministische Fusion: gleicher Store, gleiche Anfrage, gleiches Ergebnis |
| Warum hat es X vergessen? | keine Antwort | `/v1/events` protokolliert jedes Anlegen, Ersetzen und Löschen — mit Begründung |

Zwei davon lohnen einen genaueren Blick — der Rest steht schon in der Tabelle.

### Jede Erinnerung bringt ihren Beleg mit

Eine abgerufene Erinnerung ist keine freischwebende Zeichenkette. Sie zeigt auf
den Gesprächszug, aus dem sie entstand:

```
evidence_id  = ev_fact:fact_6ada707b…
support      = "Kannst du mir einen nicht überfüllten Strand auf Oahu empfehlen?"
predicate    = Nutzer sucht einen ruhigen Strand auf Oahu
entities     = location=Oahu | occasion=Geburtstag
source       = session_40 / turn_10          ← genau dieser Zug, nicht „irgendein Chat"
date         = 2023-05-25
```

`FactEvent → SourceSpan → RawTurn` ist eine echte Fremdschlüsselkette, kein
Ähnlichkeitswert — deshalb hat *„warum denkst du das über mich?"* eine
Antwort.

### Vier Zeitachsen statt eines Zeitstempels

| Feld | beantwortet |
|---|---|
| `occurred_start` / `occurred_end` | wann das Ereignis **stattfand** |
| `valid_from` / `valid_until` | in welchem Zeitraum der Fakt **galt** |
| `document_time` | wann der Nutzer es **sagte** |
| `created_at` | wann wir es **gespeichert** haben |

Mit einem einzigen Zeitstempel lässt sich „ich **bin** letztes Jahr nach Chicago
**gezogen**" nicht von „ich **ziehe** nächstes Jahr nach Chicago" unterscheiden —
und ein Fakt, der nicht mehr gilt, ist gar nicht darstellbar.

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
ein reiner Lesezugriff. Die einzige Python-exklusive Ausnahme ist
`build_context(organizer=...)`, das für Fragen wie „zähl alles auf, was du
über mich weißt" einen LLM-gestützten Organizer über die Trefferliste laufen
lässt — `/v1/context` nimmt niemals einen Organizer entgegen, damit die
Null-LLM-Zusage über HTTP nicht per Request-Parameter gekippt werden kann.

**SDKs** — TypeScript über HTTP ([`sdk-ts/`](../../sdk-ts/), keine
Laufzeitabhängigkeiten, ESM + CJS):

```bash
npm i sodamem
```

```typescript
import { SodaMemClient } from "sodamem";

const mem = new SodaMemClient({ baseUrl: "http://localhost:8000", apiKey: process.env.SODAMEM_API_KEY! });
const block = await mem.context({ user_id: "u1", query: "was bevorzugt die Person?", token_budget: 1000 });
```

Python spricht direkt mit der Bibliothek — `import sodamem`, und Sie sind
bereits unterhalb des Netzwerks.

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

## Coding-Tools

**Schritt 1.** Den Daemon starten — der eine Prozess, dem die Stores gehören:

```
sodamem daemon ensure
```

**Schritt 2.** Einen Client damit verbinden:

```
sodamem install claude-code
```

Jeder Client bekommt die MCP-Tool-Oberfläche. Vier bekommen zusätzlich
**Hooks**, sodass Erinnerungen abgerufen und gespeichert werden, ohne dass
das Modell sich dafür entscheiden muss, ein Tool aufzurufen — was es in
einer Coding-Session meist ohnehin nicht tut, weil es mit dem Lesen von
Dateien beschäftigt ist.

Was Hooks leisten können, ist nicht einheitlich, weil die Hook-Systeme es
nicht sind. Das hier ist, was jeder Client tatsächlich unterstützt —
`sodamem clients` gibt dasselbe aus:

| Client | Abrufen | Speichern |
|---|---|---|
| Claude Code | bei jedem Prompt | bei jedem Turn + Sitzungsende |
| GitHub Copilot CLI | bei jedem Prompt | bei jedem Turn |
| Cursor | Sitzungsstart (Projekt-Briefing) | — |
| Codex CLI | Sitzungsstart (Projekt-Briefing) | — |
| Claude Desktop, VS Code, Windsurf, Zed, OpenCode | nur MCP-Tools | nur MCP-Tools |

Cursors `beforeSubmitPrompt` kann einen Prompt lesen, aber nichts einfügen
(die Dokumentation nennt genau drei Events, die das können, und dieses
gehört nicht dazu), und weder Cursor noch Codex übergeben einem Hook einen
Transkript-Pfad — es gibt also nichts, was ein Retain-Hook lesen könnte.
Beide bekommen stattdessen ein Projekt-Briefing beim Sitzungsstart und
schreiben über das `add_memories`-Tool. Wir installieren keinen Hook, der
ohnehin nichts tun könnte.

Drei Dinge, die man vorher wissen sollte:

**Ein Daemon, viele Editoren.** Pro-Nutzer-Stores sind SQLite ohne WAL, also
darf genau ein Prozess sie öffnen (ADR 0001 §2). `install` richtet deshalb
jeden Client standardmäßig auf einen laufenden Dienst aus, statt jeden
seinen eigenen starten zu lassen — und wer sich bewusst für einen lokalen
Store entscheidet (`--local-store`), bekommt bei einem zweiten Client eine
Startverweigerung statt einer still korrumpierten Datenbank.

**Erinnerungen sind auf das Repo begrenzt.** `install` leitet eine
`project_id` aus der Git-Root ab (ein `git worktree` löst zum übergeordneten
Repo auf, ein Branch pro Aufgabe ist also keine eigene Gedächtnisbank pro
Aufgabe). Das grenzt ein, statt zu trennen: Was Sie SodaMem außerhalb eines
Projekts erzählt haben, taucht weiterhin in jedem Projekt auf, und der
Schlüssel beantwortet „wie habe ich das im anderen Repo gelöst?".

**Speichern braucht Extraktions-Credentials.** Abrufen ist Null-LLM und
funktioniert ohne sie; Fakten speichern nicht. `sodamem daemon ensure` sagt
das im Voraus, statt jeden Write anzunehmen und den Job erst danach
scheitern zu lassen.

```
sodamem install claude-code --dry-run      # zeigt, was sich ändern würde
sodamem install cursor vscode zed          # mehrere auf einmal
sodamem daemon status                      # was tatsächlich antwortet
```

Bestehende Konfiguration wird zusammengeführt, nicht ersetzt — andere
MCP-Server, andere Einstellungen und handgeschriebene TOML-Kommentare
bleiben erhalten —, und beim ersten Schreiben einer Datei entsteht daneben
ein `.sodamem-backup`.

---

## Self-Hosting

Ein Befehl:

```
cp .env.example .env      # danach SODAMEM_API_KEY setzen
docker compose up -d
```

**Authentifizierung ist standardmäßig aktiv.** `docker-compose.yml` setzt
niemals `SODAMEM_AUTH_DISABLED` — der Server verweigert den Start, wenn
`SODAMEM_API_KEY` nicht gesetzt ist (siehe `server/settings.py`), es gibt
also kein versehentlich offenes Deployment. Setzen Sie den Schlüssel in
`.env`, bevor Sie `docker compose up` zum ersten Mal ausführen.

**Genau ein Worker.** `--workers 1` ist eine Korrektheitsbedingung, keine
Durchsatz-Einstellung: Pro-Nutzer-Stores sind SQLite-Datenbanken ohne WAL,
und zwei Prozesse, die in den Store desselben Nutzers schreiben, korrumpieren
ihn. Das mitgelieferte `CMD` sagt das explizit, und der Server nimmt beim
Start eine exklusive Sperre auf seine Datenwurzel — ein zweiter Prozess, der
auf dasselbe Verzeichnis zeigt, verweigert den Start mit `data_root_locked`,
statt still Daten zu korrumpieren. Horizontale Skalierung braucht zuerst
einen externen Job-Store (`docs/adr/0001-control-plane-db.md`).

Die vollständige Betriebsreferenz — API-Aufrufe, Admin-Endpunkte, Metriken,
Wartung, Backups, Upgrades — liegt in
[`docs/self-hosting.md`](../../docs/self-hosting.md). Diese ausführliche
Dokumentation gibt es bisher nur auf Englisch.

---

## Dokumentation

| | |
|---|---|
| [Benchmark-Methode](../../benchmarking/README.md) | wie die Benchmark-Zahlen entstanden |
| [Self-Hosting-Referenz](../../docs/self-hosting.md) | vollständige Betriebsdokumentation (Englisch) |

---

## Danksagung

Frühe Beiträge von [@sunjiajunsunjiajun](https://github.com/sunjiajunsunjiajun) und [@Lum1104](https://github.com/Lum1104) haben die Arbeit geprägt, aus der dieses Projekt
hervorgegangen ist. Vielen Dank.

## Lizenz

Apache-2.0. Siehe [LICENSE](../../LICENSE) und [NOTICE](../../NOTICE).
