"""The extraction prompt is domain-neutral (PRD R2.7).

It used to be tuned to LongMemEval's subject matter. An audit found five live
traces: `event_type` led with `flight`; `quantity_unit`'s first three options
were `flight_segment|trip_count|ride_count`; the ONLY worked example for
`entity_roles` was an airline; an entire rule covered round-trip segment
counting; and the rolling-window example echoed the question style.

Two reasons that had to go, in order of weight:

1. **It was benchmark contamination inside the product.** Publishing a
   LongMemEval number while the extractor names LongMemEval's own vocabulary
   invites exactly the question that sank mem0's published scores.
2. **It cost every other user.** Few-shot examples steer the model, and the
   single worked example was an airline — so a deployment storing medical
   appointments or code reviews spent the prompt's scarce instruction budget,
   and its only example, on air travel.

There is one prompt and no switch. A profile mechanism was built and then
removed deliberately: a knob that selects what a store CONTAINS is a knob that
can be set wrong, and I6 would then stamp the resulting store as legitimate.
One honest default is worth more than a switch nobody should touch.
"""
from __future__ import annotations

import pytest

from sodamem.prompts.extraction import DETERMINISM_RULES, EXTRACT_SYSTEM_PROMPT

# Drawn from the audit — every domain term that used to be in the prompt.
_DOMAIN_TERMS = ("flight_segment", "trip_count", "ride_count", "flight",
                 "United Airlines", "airline", "round trip", "outbound",
                 "in the last 3 months")


@pytest.mark.parametrize("term", _DOMAIN_TERMS)
def test_prompt_carries_no_domain_vocabulary(term):
    assert term.lower() not in EXTRACT_SYSTEM_PROMPT.lower(), (
        f"extraction prompt still mentions {term!r} — a deployment that never "
        "asked for air travel must not get an extractor tuned to it"
    )


def test_prompt_still_teaches_the_schema_and_the_grounding_rules():
    """Removing the domain must not remove the job. These parts are what make
    extraction work at all, and none of them are domain-specific."""
    for essential in ('"kind"', '"predicate_canonical"', '"modality"',
                      '"source_span_ids"', '"support_text"', "anchor",
                      "quantity_value"):
        assert essential in EXTRACT_SYSTEM_PROMPT, f"prompt lost {essential}"
    # The single most load-bearing instruction: without it the model computes
    # relative dates itself and invents absolute ones.
    assert "Do not calculate relative dates yourself" in EXTRACT_SYSTEM_PROMPT


def test_quantity_units_are_general_purpose():
    """The enum used to lead with three travel units. What replaces them has
    to actually cover ordinary memories, not just be shorter."""
    line = next(ln for ln in EXTRACT_SYSTEM_PROMPT.splitlines()
                if '"quantity_unit"' in ln)
    for unit in ("money", "duration", "count", "percent", "none"):
        assert unit in line


def test_there_is_no_profile_switch():
    """A knob that selects what a store CONTAINS is a knob that can be set
    wrong — and I6 would then stamp the resulting store as legitimate, making
    the mistake permanent and invisible. One default, no switch."""
    import sodamem.prompts.extraction as ex
    for removed in ("DOMAIN_PROFILES", "build_extraction_prompt",
                    "fingerprint_prompts", "list_domain_profiles"):
        assert not hasattr(ex, removed), f"{removed} came back"

    from sodamem.memory.ingest.config import IngestConfig
    assert not hasattr(IngestConfig(), "domain_profile")


# --- the variant that is built by substitution ------------------------------

# Three tests for EXTRACT_SYSTEM_PROMPT_ENTITY_SUBJECT lived here. That
# variant had zero consumers — the `entity_subject` config flag is a PARSER
# switch (honour roles["subject"] instead of hardcoding entity_user), not a
# prompt swap, so the two only ever shared a name. Variant, its
# `_require_replace` guard, and these tests were deleted 0806.
#
# The guard was good design for what it protected: a `.replace()` whose target
# has drifted returns the string unchanged, so an A/B between "variant" and
# base silently compares two identical prompts. If a prompt variant is ever
# reintroduced, reintroduce that guard with it.


def test_fingerprint_inputs_are_exactly_what_the_extractor_sends():
    """I6: fingerprinting text that never reaches the LLM would let two
    behaviourally different stores share a fingerprint."""
    from sodamem.memory.ingest.extractor import FactEventExtractorV2
    from tests.test_ingest_extractor import _SystemCapturingProvider

    provider = _SystemCapturingProvider()
    FactEventExtractorV2(provider)._extract_single("window")
    assert provider.systems[0] == EXTRACT_SYSTEM_PROMPT + DETERMINISM_RULES
