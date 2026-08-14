"""Zero-LLM retrieval diff for the entity_subject flag (star-graph fix).

Question this answers: if `ExtractConfig.entity_subject` had been ON when the
frozen S500 stores were built, would deterministic retrieval have returned
different evidence for the benchmark questions?

Why this is a valid stand-in for a rebuild: the flag does not change what the
LLM emits — its answer is already persisted in `fact_entity_roles`
(role='subject', entity_id already canonicalized). The hardcoded literal only
discarded it when writing `fact_events.subject_entity_id`. Rewriting that one
column on a COPY of a store reproduces exactly what the flag would have
stored. BM25/chroma documents are untouched by construction:
`fact_search_document` serializes predicate/event_type/modality/units/roles
and never the subject_entity_id column.

So the only retrieval surfaces that can move are fusion's identity key
(dedup) and the hop-2 entity expansion. If top-k evidence is identical across
arms for every sampled question, the reader's input is identical, and the
flag is score-neutral BY CONSTRUCTION on the read path — no reader, no judge,
no self-judging concern.

Limitation, stated up front: the benchmark's answer path is an autonomous
planner that can also call refine/entity_timeline with its own arguments; a
single-shot search diff does not cover every planner trajectory. It covers
the first-order retrieval surface those calls share.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent))

import paths as _paths  # noqa: E402 - env-var contract, see paths.py

FROZEN = _paths.store_root()
QUESTIONS = _paths.data_dir() / "questions_slim.json"
SCRATCH = pathlib.Path(__file__).parent / "results" / "flip_stores"
N_USERS = int(__import__("os").environ.get("SODAMEM_PROBE_USERS", "50"))
TOP_K = 10

# Measured 2026-08-04, N=50 on the frozen Hobs store:
#   47/50 questions: byte-identical top-10 evidence
#    3/50: one substitution or a rank swap (overlap 9/10, 9/10, 10/10)
#   flip rate: 22.5% of facts change subject; 'assistant' is 22.3% of flips
# 0804 acceptance run (exclusion list with 'assistant', FULL 500 questions):
#   486/500 (97.2%): byte-identical top-10 evidence, 0 errors
#    14/500: 8 rank-order-only (10/10 shared), 5 with one substitution,
#            1 with two (q478)
#   Manual review of all 6 substitution questions: every swapped-OUT item was
#   off-topic noise (skincare packaging copy on a points question, a diabetes
#   explainer on a handbag question, lexical semantics on a devices question);
#   none contained the gold answer. Several swapped-IN items were MORE
#   relevant (q053 gained the verbatim Sephora-points exchange). q443's
#   swapped-out item mentions the entity (Rachel) but not the gold value,
#   and the other 9/10 evidence rows are unchanged.
#   Verdict: the flip is retrieval-neutral-to-mildly-positive; flag default
#   went True on this evidence (see ExtractConfig.entity_subject).


def flip_subjects(db_path: pathlib.Path) -> int:
    """Apply exactly the flag's logic retroactively: subject_entity_id takes
    the canonical id the LLM already wrote into the roles table, unless the
    role value is empty/'user'/'assistant' (the flag's own exclusion list —
    'assistant' added 0804: a conversational role, not a knowledge entity,
    and 22.3% of would-flip subjects; honoring it builds a second fake hub)."""
    conn = sqlite3.connect(str(db_path))
    changed = conn.execute(
        """
        UPDATE fact_events SET subject_entity_id = (
            SELECT r.entity_id FROM fact_entity_roles r
            WHERE r.fact_id = fact_events.fact_id AND r.role = 'subject'
              AND LOWER(TRIM(r.entity_name)) NOT IN ('', 'user', 'assistant')
        )
        WHERE EXISTS (
            SELECT 1 FROM fact_entity_roles r
            WHERE r.fact_id = fact_events.fact_id AND r.role = 'subject'
              AND LOWER(TRIM(r.entity_name)) NOT IN ('', 'user', 'assistant')
        )
        """
    ).rowcount
    conn.commit()
    conn.close()
    return changed


def evidence_ids(store, user_id: str, query: str) -> list[str]:
    from sodamem.memory.retrieval.search import search
    result = search(query, user_id=user_id, store=store)
    ids = []
    for ev in list(result.evidence)[:TOP_K]:
        ids.append(ev.get("evidence_id") or ev.get("id") or ev.get("fact_id") or str(ev)[:60])
    return ids


_EMBEDDER = None


def open_readonly(user_dir: pathlib.Path, user_id: str):
    """Open a COPY, satisfying I6 by echoing the store's own recorded
    fingerprint back at it. Legitimate here and only here: the probe changes
    no prompt-derived content — it rewrites one column from data the same
    store already holds. (Also: one shared embedder; 50 ONNX sessions is a
    probe-killer.)"""
    global _EMBEDDER
    import sodamem.memory.storage.store as store_mod
    from sodamem.embedding.onnx_minilm import OnnxMiniLmEmbedder
    if _EMBEDDER is None:
        _EMBEDDER = OnnxMiniLmEmbedder()
    conn = sqlite3.connect(str(user_dir / "memory.db"))
    row = conn.execute(
        "SELECT value FROM store_meta WHERE key='prompt_fingerprint'").fetchone()
    conn.close()
    recorded = row[0] if row else None
    original = store_mod.prompt_fingerprint
    store_mod.prompt_fingerprint = lambda prompts: recorded  # type: ignore[assignment]
    try:
        return store_mod.open_store(user_dir / "memory.db",
                                    prompts={"frozen": "frozen"},
                                    embedder=_EMBEDDER)
    finally:
        store_mod.prompt_fingerprint = original


def main() -> None:
    questions = {q["user_id"]: q for q in json.load(open(QUESTIONS))}
    users = sorted(d.name for d in FROZEN.iterdir()
                   if d.name.startswith("lme_q") and (d / "memory.db").exists())[:N_USERS]
    SCRATCH.mkdir(exist_ok=True)

    same = diff = errors = 0
    diffs = []
    for uid in users:
        q = questions.get(uid)
        if q is None:
            continue
        work = SCRATCH / uid
        if not work.exists():
            shutil.copytree(FROZEN / uid, work)
        try:
            store_a = open_readonly(work, uid)
            ids_a = evidence_ids(store_a, uid, q["question"])
            store_a.close()

            flipped = flip_subjects(work / "memory.db")

            store_b = open_readonly(work, uid)
            ids_b = evidence_ids(store_b, uid, q["question"])
            store_b.close()
        except Exception as exc:  # noqa: BLE001 - report, keep sampling
            errors += 1
            print(f"  {uid}: ERROR {type(exc).__name__}: {exc}")
            continue
        finally:
            shutil.rmtree(work, ignore_errors=True)

        if ids_a == ids_b:
            same += 1
        else:
            diff += 1
            overlap = len(set(ids_a) & set(ids_b))
            diffs.append((uid, flipped, overlap, len(ids_a)))
            print(f"  {uid}: DIFF flipped={flipped} overlap={overlap}/{len(ids_a)}")

    print(f"\n=== {same + diff} questions compared (errors: {errors}) ===")
    print(f"identical top-{TOP_K} evidence: {same}")
    print(f"different: {diff}")
    for uid, flipped, overlap, k in diffs:
        print(f"  {uid}: {overlap}/{k} shared, {flipped} facts flipped")


if __name__ == "__main__":
    main()
