<div align="center">

# SodaMem

**Mémoire temporelle et traçable pour agents IA.**

Chaque souvenir sait de quel tour de conversation il provient, et à partir de quand il a cessé d'être vrai.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](../../LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](../../pyproject.toml)
[![LongMemEval](https://img.shields.io/badge/LongMemEval--S-92.8%25-brightgreen.svg)](../../benchmarking/artifacts/)

<!-- langs -->
[English](../../README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · **Français** · [Español](README.es.md) · [Deutsch](README.de.md) · [Português](README.pt-BR.md)
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

# L'écriture a besoin d'un modèle pour extraire les faits ; la lecture, jamais.
mem = SodaMem.open("./data", extractor=FactEventExtractorV2(create_provider_from_env()))

mem.ingest(
    [{"role": "user", "content": "En fait, j'ai changé de Kauai pour Oahu."}],
    user_id="u1", session_id="s1", session_time="2023-05-25",
)

block = mem.build_context("où vais-je loger ?", user_id="u1", token_budget=1000)
print(block.text)        # prêt à coller dans un prompt — zéro appel LLM
print(block.citations)   # la preuve derrière chaque ligne
```

`SodaMem.open()` crée `./data` s'il n'existe pas. Seul `.ingest()` a besoin
de l'extracteur — omettez cet argument pour un store en lecture seule, et
`search` / `build_context` fonctionnent exactement pareil.

**Rien de vous ne quitte la machine.** Pas de télémétrie, pas d'analytics,
pas de rappel réseau — la seule requête sortante de l'installation par défaut
est le téléchargement unique du modèle d'embedding MiniLM (90 Mo) dans
`~/.cache/chroma/` ; ensuite elle ne parle qu'à votre disque. Pré-remplissez
ce cache et elle tourne hors ligne.

---

## Pourquoi une couche mémoire de plus

La plupart des systèmes de mémoire enregistrent **ce qui a été dit**. Les questions
qui les mettent en échec sont **depuis quand ce n'est plus vrai** et **d'où cela
vient** — deux problèmes de modèle de données, pas d'index vectoriel plus gros.

### Chaque souvenir porte sa preuve

Un souvenir récupéré n'est pas une chaîne flottante. Il pointe vers le tour de
conversation qui l'a produit :

```
evidence_id  = ev_fact:fact_6ada707b…
support      = "Pouvez-vous me conseiller une plage peu fréquentée à Oahu ?"
predicate    = l'utilisateur cherche une plage tranquille à Oahu
entities     = location=Oahu | occasion=anniversaire
source       = session_40 / turn_10          ← ce tour précis, pas « une conversation »
date         = 2023-05-25
```

`FactEvent → SourceSpan → RawTurn` est une véritable chaîne de clés étrangères,
pas un score de similarité. Quand un utilisateur demande « pourquoi pensez-vous
cela de moi ? », il y a une réponse. Quand la conformité demande d'où vient un
fait stocké, il y a une ligne.

### Quatre axes temporels, pas un horodatage

| champ | question à laquelle il répond |
|---|---|
| `occurred_start` / `occurred_end` | quand l'événement **a eu lieu** |
| `valid_from` / `valid_until` | pendant quelle période le fait **était vrai** |
| `document_time` | quand l'utilisateur **l'a dit** |
| `created_at` | quand nous **l'avons stocké** |

Avec un seul horodatage, impossible de distinguer « j'ai **déménagé** à Chicago
l'an dernier » de « je **vais déménager** à Chicago l'an prochain », ni de
représenter un fait devenu faux.

Les corrections sont en **ADD-only** : une nouvelle version plus une arête
`SUPERSEDES`, jamais une réécriture sur place. `PATCH /v1/memories/{id}` clôt
l'ancienne version avec un `valid_until` et **la laisse lisible** — c'est toute
la différence avec `DELETE`.

### Deux niveaux de récupération, et le moins cher est vraiment gratuit

| niveau | appels LLM | pour |
|---|---|---|
| `search` / `build_context` | **zéro** | le chemin par défaut : fusion déterministe BM25 + vectoriel + entités |
| `answer` | boucle planificateur | les questions multi-sauts qui valent les tokens |

`build_context` renvoie **un bloc prêt pour le prompt, avec ses citations**, sans
aucun appel de modèle. La plupart des systèmes rendent une liste
d'enregistrements et vous laissent l'assemblage, le budget de tokens et la
déduplication.

Il existe un troisième niveau intermédiaire : `build_context(organizer=...)`
fait tourner un organisateur adossé à un LLM (value-board, enumeration-sweep)
sur l'ensemble récupéré, pour des questions du type « liste tous les X que tu
connais de moi ». Il est volontairement réservé à Python — `/v1/context`
n'accepte jamais d'organisateur, donc la garantie zéro-LLM de cette route ne
peut pas être renversée par un paramètre de requête.

### Une récupération auditable

Même requête, même store, même résultat, à chaque fois. `/v1/events` enregistre
chaque ajout, remplacement et suppression avec son motif : « pourquoi l'agent
a-t-il oublié X ? » a une réponse après coup.

---

## Benchmark

**92,8 % (464/500)** sur LongMemEval-S est l’artifact publié. **Typed Answer Schema (TAS)** (`benchmarking/protocol_v1.0/`) est la discipline côté réponse (typage de tâches) ; remesurer après tout changement de protocole.

| | |
|---|---|
| reader / planner / judge | `deepseek-v4-flash` |
| prompts de notation | les gabarits `evaluate_qa.py` du benchmark LongMemEval, identiques à l'octet près |
| store | `longmemeval_s_500_Hobs_entitysubj`, 500 utilisateurs / 235 840 faits |

**Chaque réponse et chaque souvenir récupéré sont publiés** dans
[`benchmarking/artifacts/`](../../benchmarking/artifacts/) — 500 réponses
intégrales, 8 427 éléments de preuve. Renotez-les avec le juge de votre choix, ou
donnez notre contexte récupéré à votre propre reader et observez le chiffre.
Ni l'un ni l'autre ne nécessite d'accès à quoi que ce soit de notre côté.

---

## Installation

| extra | ce qu'il ajoute |
|---|---|
| *(base)* | modèle de données, stockage, recherche BM25, ingestion — **quatre dépendances, aucune lourde** |
| `chroma` | recherche vectorielle + embedder ONNX local (requis par `SodaMem.open()`) |
| `llm` | fournisseurs compatibles OpenAI (OpenAI / DeepSeek / Gemini, même protocole) |
| `anthropic` | le fournisseur Anthropic (SDK dédié) |
| `answer` | le chemin de réponse planificateur + reader |
| `server` | le service HTTP (FastAPI + uvicorn — trois paquets, délibérément) |
| `mcp` | la surface serveur MCP |

L'installation de base tire `pydantic`, `numpy`, `rank-bm25`, `python-dateutil`.
Rien d'autre — et une porte CI fait échouer le build si cette liste s'allonge par
accident.


Pas encore sur PyPI. En attendant la première version taguée, depuis les
sources :

```bash
pip install "git+https://github.com/xlows1206/SodaMem#egg=sodamem[chroma,llm]"
```

---

## Utilisable depuis n'importe où

**HTTP** — `add` / `search` / `context` / `answer`, plus écriture par lot,
remplacement, événements, métriques, consommation de tokens :

```bash
curl -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  localhost:8000/v1/context \
  -d '{"user_id":"u1","query":"que préfère-t-il ?","token_budget":1000}'
```

`/v1/context` et `/v1/search` acceptent tous deux un corps JSON ;
`/v1/context` répond aussi à un simple GET avec des paramètres d'URL,
puisque c'est une lecture pure.

**SDK** — TypeScript en HTTP ([`sdk-ts/`](../../sdk-ts/), zéro dépendance
d'exécution, ESM + CJS). Python s'adresse directement à la bibliothèque —
`import sodamem` et vous êtes déjà en deçà du réseau.

**Frameworks d'agents** — LangGraph, CrewAI, OpenAI Agents SDK, Vercel AI SDK.
La portée est liée à la construction des outils et **n'apparaît jamais dans le
schéma que voit le modèle** : un `user_id` que le modèle peut choisir est un
`user_id` qu'il peut halluciner.

**MCP** — 8 outils, dont `entity_timeline` (l'historique d'une entité dans
l'ordre, chaque élément pointant toujours vers sa source) et `explore_memory`
(parcours du graphe). Six sont des lectures, toujours
disponibles ; les deux qui modifient (`add_memories`, `delete_memory`)
n'apparaissent que sous `SODAMEM_MCP_ALLOW_WRITE=true`, que `sodamem install`
écrit pour vous dans la configuration client qu'il génère.

**Console web** — parcourir et inspecter les souvenirs par locataire, incluse
dans l'image.

---

## Auto-hébergement

```bash
cp .env.example .env      # définir SODAMEM_API_KEY
docker compose up -d
```

Authentification active par défaut. L'isolation entre locataires est
**physique** : un fichier SQLite et une collection vectorielle par `user_id`,
donc « supprimer cet utilisateur » revient à supprimer un répertoire.

`/v1/admin/*` répond aux questions qui exigeraient sinon un shell dans le
conteneur : configuration effective (les secrets sont signalés « défini / non
défini » et jamais imprimés), clés API nommées, journal glissant des requêtes,
état du disque et de la charge.

Observabilité : `/v1/metrics` (quantiles de latence), `/v1/usage` (tokens,
séparés entre ingestion et réponse), `/metrics` (format Prometheus),
`/v1/events` (tout changement de mémoire), et des webhooks sortants — file
bornée, signés en HMAC, inactifs tant qu'aucune URL n'est configurée.

Les profils d'entités se reconstruisent à la demande, jamais sur minuterie :
`POST /v1/maintenance/dream` (idempotent, reprenable, un appel concurrent
renvoie `already_running`). Quand dépenser ces tokens est une décision de
déploiement, donc SodaMem n'embarque aucun ordonnanceur.

Détails dans la version anglaise : [Self-hosting](../../README.md#self-hosting).

---

## Documentation

| | |
|---|---|
| [Outils de codage](../../README.md#coding-tools) | Claude Code, Cursor et autres clients MCP |
| [Méthode de benchmark](../../benchmarking/README.md) | comment le chiffre LongMemEval a été produit |

---

## Licence

Apache-2.0. Voir [LICENSE](../../LICENSE) et [NOTICE](../../NOTICE).
