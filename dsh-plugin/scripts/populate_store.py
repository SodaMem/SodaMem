"""Populate a REAL SodaMem store with zero LLM network calls.

The extractor is the only thing that needs a model, and `ScriptedProvider`
(sodamem.llm.testing) replaces it with a pre-set response per call — so the
full ingest path runs for real (fact rows, source spans, entities, embeddings
via the cached MiniLM) without an API key.

Usage: python populate_store.py <data_root> <user_id> <n_facts>
"""
import json
import sys
from pathlib import Path

from sodamem import SodaMem
from sodamem.llm.testing import ScriptedProvider
from sodamem.memory.ingest.extractor import FactEventExtractorV2

CITIES = ["Boston", "Seattle", "Osaka", "Lisbon", "Nairobi", "Bogota", "Oslo",
          "Chennai", "Perth", "Krakow", "Quito", "Tbilisi"]
AIRLINES = ["United", "ANA", "TAP", "Kenya Airways", "Avianca", "LOT"]
EMPLOYERS = ["Acme Corp", "Initech", "Globex", "Umbrella", "Soylent", "Hooli"]
PETS = ["golden retriever", "tabby cat", "corgi", "cockatiel", "beagle"]


def make_fact(i: int) -> dict:
    """Rotate across four predicate families so retrieval has real competition
    to rank, not N copies of one fact."""
    kind = i % 4
    if kind == 0:
        city, airline = CITIES[i % len(CITIES)], AIRLINES[i % len(AIRLINES)]
        return {"kind": "fact",
                "predicate_raw": f"User flew {airline} to {city}",
                "predicate_canonical": "travel_by_airline", "event_type": "flight",
                "modality": "past_event", "occurred_start": "2023-06-10",
                "entity_roles": {"subject": "user", "airline": airline, "destination": city},
                "source_span_ids": ["irrelevant"],
                "support_text": f"I flew {airline} to {city} last week."}
    if kind == 1:
        employer = EMPLOYERS[i % len(EMPLOYERS)]
        return {"kind": "fact",
                "predicate_raw": f"User started a new job at {employer}",
                "predicate_canonical": "employed_by", "event_type": "employment",
                "modality": "past_event", "occurred_start": "2023-04-01",
                "entity_roles": {"subject": "user", "employer": employer},
                "source_span_ids": ["irrelevant"],
                "support_text": f"I started a new job at {employer}."}
    if kind == 2:
        pet = PETS[i % len(PETS)]
        return {"kind": "fact",
                "predicate_raw": f"User adopted a {pet}",
                "predicate_canonical": "owns_pet", "event_type": "pet",
                "modality": "past_event", "occurred_start": "2023-05-01",
                "entity_roles": {"subject": "user", "pet": pet},
                "source_span_ids": ["irrelevant"],
                "support_text": f"I adopted a {pet} named number {i}."}
    city = CITIES[(i * 3) % len(CITIES)]
    return {"kind": "fact",
            "predicate_raw": f"User lives in {city}",
            "predicate_canonical": "resides_in", "event_type": "residence",
            "modality": "state", "occurred_start": "2023-01-01",
            "entity_roles": {"subject": "user", "location": city},
            "source_span_ids": ["irrelevant"],
            "support_text": f"I moved to {city} and I'm staying for a while."}


def main() -> None:
    data_root, user_id, n = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    user_dir = data_root / user_id
    user_dir.mkdir(parents=True, exist_ok=True)

    facts = [make_fact(i) for i in range(n)]
    # One scripted response per ingest_session call.
    provider = ScriptedProvider([json.dumps([f]) for f in facts])
    mem = SodaMem.open(user_dir, extractor=FactEventExtractorV2(provider))

    for i, f in enumerate(facts):
        mem.ingest([{"role": "user", "content": f["support_text"]}],
                   user_id=user_id, session_id=f"s{i}",
                   session_time=f"2023-06-{(i % 28) + 1:02d}")
        if (i + 1) % 100 == 0:
            print(f"  ingested {i + 1}/{n}", flush=True)

    print(f"done: {n} facts into {user_dir}")


if __name__ == "__main__":
    main()
