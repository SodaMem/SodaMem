"""Offline smoke checks for sodamem_opt (no API / no store)."""
from __future__ import annotations

from sodamem_opt.det_count import DetCountResult, is_set_aggregation, aggregation_mode
from sodamem_opt.intent import classify_intent
from sodamem_opt.patches import apply, is_applied
from sodamem_opt.timewords import resolve_time_window


def main() -> int:
    apply()
    assert is_applied()

    # months-ago band for q039-class
    w = resolve_time_window(
        "I mentioned visiting a museum two months ago. Did I visit with a friend?",
        current_date="2023/03/11 (Sat) 05:28",
    )
    assert w is not None, "two months ago must resolve"
    assert w["from_date"] <= "2023-01-11" <= w["to_date"] or (
        w["from_date"] <= "2023-01-10" and w["to_date"] >= "2023-01-12"
    ), w
    print("ok time window:", w["expression"], w["from_date"], "→", w["to_date"])

    # intents for the 7 misses
    cases = {
        "q028": ("What is the order of airlines I flew with from earliest to latest before today?",
                 dict(temporal_order=True, needs_timeline=True)),
        "q034": ("What is the order of the six museums I visited from earliest to latest?",
                 dict(temporal_order=True, needs_timeline=True)),
        "q035": ("Which streaming service did I start using most recently?",
                 dict(temporal_recent=True, needs_timeline=True)),
        "q039": ("I mentioned visiting a museum two months ago. Did I visit with a friend or not?",
                 dict(temporal_relative=True, needs_timeline=True)),
        "q007": ("How much is the painting of a sunset worth in terms of the amount I paid for it?",
                 dict(value_lookup=True)),
        "q055": ("Did I receive a higher percentage discount on my first order from HelloFresh, compared to my first UberEats order?",
                 dict(comparison=True, value_lookup=True)),
        "q116": ("What speed is my new internet plan?",
                 dict(new_attribute=True, value_lookup=True)),
        "q173": ("How many times did I bake something in the past two weeks?",
                 dict(aggregation=True, needs_enumeration=True)),
        "q211": ("Can you recommend a show or movie for me to watch tonight?",
                 dict(preference=True)),
    }
    for eid, (q, expect) in cases.items():
        intent = classify_intent(q)
        for k, v in expect.items():
            if k == "needs_timeline":
                got = intent.needs_timeline
            elif k == "needs_enumeration":
                got = intent.needs_enumeration
            else:
                got = getattr(intent, k)
            assert got == v, f"{eid}.{k}: expected {v}, got {got} ({intent})"
        print(f"ok intent {eid}: {intent}")

    # patches wire into sodamem modules
    import sodamem.answer.loop as loop_mod
    import sodamem.answer.rules as rules_mod
    import sodamem.prompts.reader as reader_mod

    from sodamem_opt.reader_addenda import OPT_READER_GUIDANCE

    assert "Temporal discipline" in reader_mod.READER_GUIDANCE
    assert OPT_READER_GUIDANCE in reader_mod.READER_GUIDANCE

    calls = rules_mod.initial_calls(rules_mod.DEFAULT_RULES, cases["q007"][0])
    assert len(calls) >= 2, calls
    assert any("flea market" in str((c.get("args") or {}).get("query", "")) for c in calls)
    print("ok initial_calls value rewrite:", [c["args"].get("query") for c in calls])

    # fake state for missing families
    class _S:
        objective = "Gather sufficient evidence to answer: " + cases["q028"][0]
        question_classification = None
        successful_tool_capabilities: set = set()

    missing = loop_mod._missing_capability_families(_S())
    assert any(f == "timeline_family" for f, _ in missing), missing
    print("ok missing timeline for q028:", missing)

    assert is_set_aggregation("How many times did I bake something in the past two weeks?")
    assert aggregation_mode("How many times did I bake something in the past two weeks?") == "count"
    assert not is_set_aggregation("What speed is my new internet plan?")
    assert not is_set_aggregation("How many points do I need to earn to redeem a free skincare product at Sephora?")
    assert not is_set_aggregation("How much will I save by taking the bus instead of a taxi?")
    import os
    os.environ["SODAMEM_OPT_DET_COUNT_HARD"] = "1"
    det = DetCountResult(mode="count", value=4, confidence="high", answer_text="4")
    assert det.should_override
    os.environ["SODAMEM_OPT_DET_COUNT_HARD"] = "0"
    assert not DetCountResult(mode="count", value=4, confidence="high", answer_text="4").should_override
    print("ok det_count gates")

    print("\nUNIT SMOKE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
