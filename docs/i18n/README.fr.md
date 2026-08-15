<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/logo-dark.webp">
  <img src="../assets/logo.webp" alt="SodaMem" width="260">
</picture>

**Une couche de mémoire agentique et auto-évolutive pour agents IA.**

La plupart des systèmes de mémoire stockent ce que vous avez dit et s'arrêtent là — juste aujourd'hui, silencieusement faux dès que votre vie change. SodaMem évolue avec votre agent : les faits sont remplacés au lieu d'être écrasés, les profils d'entités se reconstruisent à la demande au lieu de dériver silencieusement, et chaque réponse remonte toujours jusqu'au tour de conversation exact dont elle provient. La récupération ne coûte aucun appel LLM, donc la même question obtient toujours la même réponse.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](../../LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](../../pyproject.toml)
[![LongMemEval](https://img.shields.io/badge/LongMemEval-92.8%25-brightgreen.svg)](../../benchmarking/artifacts/)
[![LoCoMo](https://img.shields.io/badge/LoCoMo-86.88%25-brightgreen.svg)](../../benchmarking/README.md#locomo-cat-1-4)
[![Discussions](https://img.shields.io/github/discussions/SodaMem/SodaMem?logo=github&label=discussions)](https://github.com/SodaMem/SodaMem/discussions)

<!-- langs -->
[English](../../README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · **Français** · [Español](README.es.md) · [Deutsch](README.de.md) · [Português](README.pt-BR.md)
<!-- /langs -->

[Intégrations agents](#intégrations-agents) · [Benchmark](#benchmark) · [Démarrage rapide](#démarrage-rapide) · [Pourquoi une couche mémoire de plus](#pourquoi-une-couche-mémoire-de-plus) · [Installation](#installation) · [Utilisable depuis n'importe où](#utilisable-depuis-nimporte-où) · [Outils de codage](#outils-de-codage) · [Auto-hébergement](#auto-hébergement)

<img src="../assets/benchmark-cost-accuracy.webp" alt="Cost-accuracy trade-off on LongMemEval-S" width="760">

*Précision en fonction du coût API estimé par question. Le quadrant qui compte est en haut à gauche.*

</div>

---

## Intégrations agents

| Runtime | Comment | Guide |
|---|---|---|
| **Hermes Agent** | MCP | [`integrations/hermes/README.md`](../../integrations/hermes/README.md) |
| **DeepSeek Harness** | MCP | [`integrations/deepseek-harness/README.md`](../../integrations/deepseek-harness/README.md) |
| **Générique / tout client MCP** | MCP | [`mcp_server/README.md`](../../mcp_server/README.md) |
| **LangGraph** | adaptateur Python | [`adapters/README.md`](../../adapters/README.md) |
| **CrewAI** | adaptateur Python | [`adapters/README.md`](../../adapters/README.md) |
| **OpenAI Agents SDK** | adaptateur Python | [`adapters/README.md`](../../adapters/README.md) |
| **Vercel AI SDK** | adaptateur TS | [`sdk-ts/`](../../sdk-ts/) |
| **Claude Code, Cursor et autres clients de codage** | CLI + hooks | voir [Outils de codage](#outils-de-codage) |

Index complet, avec les schémas des outils MCP et le détail des adaptateurs : [`integrations/README.md`](../../integrations/README.md).

---

## Benchmark

<div align="center">
  <img src="../assets/benchmark-longmemeval.webp" alt="LongMemEval: SodaMem 92.8%, Hindsight 91.4%, Mem0 OSS 91.0%" width="720">
</div>

**92,8 % (464/500)** sur LongMemEval.

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

<div align="center">
  <img src="../assets/benchmark-locomo.webp" alt="LoCoMo: SodaMem 86.88%, MemMachine 91.69%, Hindsight 89.61%, MIRIX 85.38%, Memobase 75.78%, Mem0 OSS 66.88%" width="720">
</div>

**86,88 % (1338/1540)** sur LoCoMo, catégories 1-4 — la catégorie 5
(adversarial) est exclue, soit 1 540 des 1 986 questions. Exactitude QA de bout
en bout, notation par LLM-as-judge.

| | |
|---|---|
| reader / planner / judge | `deepseek-v4-flash` |
| prompts de notation | les gabarits du benchmark LongMemEval, copiés à l'octet près |
| store | `locomo10_Hobs`, 10 stores utilisateur / 2 905 fact events |
| code | une build pré-version — l'historique publié commence à v0.1.0 |

**Aucun artefact par question n'est publié pour LoCoMo** — pas de réponses, pas
de contexte récupéré, pas de répertoire de run. Ce qui est publié, c'est
[la section LoCoMo de `benchmarking/README.md`](../../benchmarking/README.md#locomo-cat-1-4) :
la ventilation par catégorie, la dispersion par conversation, la provenance et
les étapes de reproduction.

---

## Démarrage rapide

Ceci est le chemin Python. Vous intégrez un framework d'agents ou un client MCP ? Voir [Intégrations agents](#intégrations-agents). Vous l'appelez depuis TypeScript/Node ? Voir [Utilisable depuis n'importe où](#utilisable-depuis-nimporte-où). Vous le faites tourner comme service partagé ? Voir [Auto-hébergement](#auto-hébergement).

### Exemple

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


| la question | la réponse habituelle | SodaMem |
|---|---|---|
| D'où vient ce souvenir ? | un score de similarité et quelques métadonnées | `FactEvent → SourceSpan → RawTurn`, une chaîne de clés étrangères jusqu'au tour exact |
| L'utilisateur s'est ravisé — et maintenant ? | on écrase ; l'ancienne valeur disparaît | ajout seul, plus une arête `SUPERSEDES` ; l'ancienne version se ferme par un `valid_until` et reste lisible |
| « J'ai déménagé à Chicago l'an dernier » vs « je déménage l'an prochain » | un seul horodatage | quatre axes temporels : survenu / valide / dit / stocké |
| Combien coûte une récupération ? | un appel LLM par récupération | `build_context` n'en fait **aucun** et renvoie un bloc prêt à l'emploi, avec ses citations |
| Deux fois la même requête — la même réponse ? | cela dépend de l'échantillonnage du modèle | fusion déterministe : même store, même requête, même résultat |
| Pourquoi a-t-il oublié X ? | aucune réponse | `/v1/events` consigne chaque ajout, remplacement et suppression, avec son motif |

Deux méritent qu'on s'y attarde — le reste, le tableau l'a déjà dit.

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

`FactEvent → SourceSpan → RawTurn` est une chaîne de clés étrangères, pas un
score de similarité, donc « pourquoi pensez-vous cela de moi ? » a une réponse.

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

## Outils de codage

**Étape 1.** Démarrez le daemon — le seul processus qui possède les stores :

```
sodamem daemon ensure
```

**Étape 2.** Connectez-y un client :

```
sodamem install claude-code
```

Chaque client obtient la surface d'outils MCP. Quatre obtiennent aussi des
**hooks**, pour que la mémoire soit récupérée et retenue sans que le modèle
ait à décider d'appeler un outil — ce qu'il ne fait la plupart du temps pas
en session de codage, occupé à lire des fichiers.

Ce que les hooks peuvent faire n'est pas uniforme, parce que les systèmes de
hooks ne le sont pas. Voici ce que chaque client supporte réellement —
`sodamem clients` affiche la même chose :

| Client | Récupération | Rétention |
|---|---|---|
| Claude Code | à chaque prompt | à chaque tour + fin de session |
| GitHub Copilot CLI | à chaque prompt | à chaque tour |
| Cursor | au démarrage de la session (brief projet) | — |
| Codex CLI | au démarrage de la session (brief projet) | — |
| Claude Desktop, VS Code, Windsurf, Zed, OpenCode | outils MCP uniquement | outils MCP uniquement |

Le `beforeSubmitPrompt` de Cursor peut lire un prompt mais n'injecte rien (sa
documentation liste exactement trois événements qui le peuvent, et ce n'en
est pas un), et ni Cursor ni Codex ne passent de chemin de transcript à un
hook — il n'y a donc rien à lire pour un hook de rétention. Ces deux-là
reçoivent un brief projet au démarrage de la session et écrivent via l'outil
`add_memories`. On n'installe pas un hook qui ne peut jamais rien faire.

Trois choses à savoir avant de lancer la commande :

**Un daemon, plusieurs éditeurs.** Les stores par utilisateur sont du SQLite
sans WAL, donc un seul processus peut les ouvrir à la fois (ADR 0001 §2).
`install` pointe donc chaque client vers un service déjà en cours plutôt que
de laisser chacun lancer le sien — et si vous choisissez délibérément un
store local (`--local-store`), un second client refuse désormais de démarrer
au lieu de corrompre silencieusement les données du premier.

**Les souvenirs sont scopés au dépôt.** `install` dérive un `project_id` à
partir de la racine git (un `git worktree` résout vers son dépôt parent, donc
une branche par tâche n'équivaut pas à une banque de mémoire par tâche).
C'est un rétrécissement, pas un cloisonnement : tout ce que vous avez dit à
SodaMem en dehors d'un projet reste visible dans chaque projet, et retirer la
clé répond à « comment ai-je corrigé ça dans l'autre dépôt ? ».

**La rétention a besoin d'identifiants d'extraction.** La récupération est
zéro-LLM et fonctionne sans eux ; stocker des faits, non. `sodamem daemon
ensure` le signale d'emblée plutôt que d'accepter chaque écriture et de faire
échouer le job après coup.

```
sodamem install claude-code --dry-run      # affiche ce qui changerait
sodamem install cursor vscode zed          # plusieurs clients à la fois
sodamem daemon status                      # ce qui répond réellement
```

La configuration existante est fusionnée, pas remplacée — les autres
serveurs MCP, les autres réglages et les commentaires TOML écrits à la main
survivent — et la première écriture de chaque fichier laisse une sauvegarde
`.sodamem-backup` à côté.

---

## Auto-hébergement

Une seule commande :

```
cp .env.example .env      # puis définir SODAMEM_API_KEY
docker compose up -d
```

**Authentification active par défaut.** `docker-compose.yml` ne définit
jamais `SODAMEM_AUTH_DISABLED` — le serveur refuse de démarrer si
`SODAMEM_API_KEY` n'est pas défini (voir `server/settings.py`), donc pas de
déploiement accidentellement ouvert. Définissez la clé dans `.env` avant le
premier `docker compose up`.

**Un seul worker, exactement.** `--workers 1` est une contrainte de
correction, pas un réglage de débit : les stores par utilisateur sont des
bases SQLite ouvertes sans WAL, et deux processus qui écrivent dans le store
d'un même utilisateur le corrompent. Le `CMD` livré l'indique explicitement,
et le serveur prend un verrou exclusif sur sa racine de données au démarrage
— un second processus pointé vers le même répertoire refuse de démarrer avec
`data_root_locked` plutôt que de corrompre silencieusement les données. La
montée en charge horizontale nécessite d'abord un job store externe
(`docs/adr/0001-control-plane-db.md`).

La référence complète des opérations — appeler l'API, endpoints d'admin,
métriques, maintenance, sauvegardes, montées de version — vit dans
[`docs/self-hosting.md`](../../docs/self-hosting.md) (document disponible
uniquement en anglais pour l'instant).

---

## Documentation

| | |
|---|---|
| [Méthode de benchmark](../../benchmarking/README.md) | comment les chiffres de benchmark ont été produits |

---

## Remerciements

Les contributions initiales de [@sunjiajunsunjiajun](https://github.com/sunjiajunsunjiajun) and [@Lum1104](https://github.com/Lum1104) ont façonné le travail dont ce projet est
issu. Merci.

## Licence

Apache-2.0. Voir [LICENSE](../../LICENSE) et [NOTICE](../../NOTICE).
