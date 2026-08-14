"""Offline smoke tests for structural extraction / slots (no API)."""
from __future__ import annotations

from sodamem_struct.candidates import Candidate
from sodamem_struct.classify import route_question
from sodamem_struct.extract import extract_events
from sodamem_struct.slots import try_slot


def main() -> int:
    r = route_question("How many dinner parties have I attended in the past month?")
    assert r.kind == "set_count" and r.template == "dinner_party", r

    r2 = route_question("What speed is my new internet plan?")
    assert r2.kind == "slot_new_speed", r2

    r3 = route_question(
        "How many points do I need to earn to redeem a free skincare product at Sephora?"
    )
    assert r3.kind == "slot_redeem_points", r3

    cands = [
        Candidate(
            "1",
            "I'm looking for Italian recipe ideas for a dinner party I'm hosting soon. "
            "I attended a lovely Italian feast at Sarah's place last week.",
            "2023-05-15",
        ),
        Candidate(
            "2",
            "I've also had experience with dinner parties that are more low-key, like the ones "
            "we had at Alex's place yesterday, where we had a potluck and tried out different "
            "cuisines, and also at Mike's place, where we had a BBQ.",
            "2023-05-20",
        ),
        Candidate(
            "3",
            "I've also had a great experience with a BBQ theme, like the one we had at Mike's "
            "place two weeks ago.",
            "2023-05-21",
        ),
        Candidate(
            "4",
            "I went to David's birthday party on 2023-05-08",
            "2023-05-08",
        ),
        Candidate(
            "6",
            "Now, about board games for a dinner party... I'm looking for suggestions",
            "2023-05-29",
        ),
    ]
    q = "How many dinner parties have I attended in the past month?"
    events = extract_events("dinner_party", cands, question=q)
    keys = sorted(e.key for e in events)
    assert keys == ["dinner:alex", "dinner:mike", "dinner:sarah"], keys

    wed = [
        Candidate(
            "w1",
            "I just got back from a friend's wedding — Jen and Tom had a rustic barn ceremony.",
            "2023-10-07",
        ),
        Candidate(
            "w2",
            "I attended my cousin Rachel and Mike's wedding at a vineyard in August.",
            "2023-08-01",
        ),
        Candidate(
            "w3",
            "Emily and Sarah's wedding had a rooftop garden ceremony.",
            "2023-06-01",
        ),
        Candidate(
            "w4",
            "I was the maid of honor at my sister's wedding.",
            "2023-07-01",
        ),
        Candidate(
            "w5",
            "I'm planning my own wedding and need venue ideas.",
            "2023-09-01",
        ),
    ]
    we = extract_events("wedding", wed, question="How many weddings have I attended in this year?")
    assert len(we) == 3, [e.key for e in we]

    proj = [
        Candidate(
            "p1",
            "Our company is currently migrating our applications to the cloud, "
            "and I'm responsible for leading the migration effort.",
        ),
        Candidate(
            "p2",
            "I'm planning to launch a new product feature in June and I need a Gantt chart.",
        ),
        Candidate(
            "p3",
            "Congratulations on your promotion to senior software engineer and leading a team of five.",
        ),
    ]
    pe = extract_events("project_lead", proj, question="How many projects have I led?")
    assert len(pe) == 1, [e.key for e in pe]

    class Ev:
        records = [
            {"content": "I upgraded to 500 Mbps about three weeks ago", "date": "2023-05-24"},
            {"content": "My internet speed is 1 Gbps", "date": "2023-05-30"},
        ]
        observations = []

    slot = try_slot("What speed is my new internet plan?", Ev())
    assert slot.ok and "500" in slot.answer, slot

    print("sodamem_struct unit_smoke OK", f"dinner={len(events)} wedding={len(we)} proj={len(pe)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
