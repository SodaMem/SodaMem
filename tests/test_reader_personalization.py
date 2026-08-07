"""Recommendation answers must lead with the person, not with the process.

SodaMem's own 0729 full-500: `single-session-preference` is 23/30 — a 23%
failure rate against 10.6% overall, the worst of any question type. All seven
misses open the same way, with the reader's scratch work presented as the
answer:

    "Based on the provided history chats, I can extract the following
     relevant information... **Step 1: Extract Relevant Information**"

and then land on advice that would fit anyone. The official judge for this
type asks one thing — "the response is correct as long as it recalls and
utilizes the user's personal information correctly" — and a wall of generic
tips does not, no matter how much personal context was quoted above it.

Two sentences, and they are one unit. Leading with personalization without
also checking against stated dislikes is how q212 happens: the user said
screens keep them awake and the reader recommended a meditation app. The
positive half without the negative half just produces confident wrong
suggestions faster.

This is the one place mem0's answer prompt has something worth taking. Their
`Lead with personalization, don't pad with generic alternatives` and
`Respect anti-preferences — check every suggestion against known dislikes`
are general rules about what a good recommendation is — unlike the same
prompt's `chandelier counts as jewelry`, they carry to any corpus.
"""
from __future__ import annotations

from sodamem.prompts.reader import (
    READER_GUIDANCE,
    READER_GUIDANCE_PERSONALIZATION_ADDENDUM,
)


def test_the_addendum_pairs_leading_with_checking():
    """Never ship half of this — see q212."""
    text = READER_GUIDANCE_PERSONALIZATION_ADDENDUM.lower()
    assert "generic" in text
    assert "avoid" in text or "dislike" in text


def test_the_base_guidance_does_not_already_carry_it():
    """If it were already there the arm would be measuring nothing."""
    assert READER_GUIDANCE_PERSONALIZATION_ADDENDUM not in READER_GUIDANCE


# ---------------------------------------------------------------------------
# Wiring. Scoring-path change, so default-OFF like every other arm here.
# ---------------------------------------------------------------------------

from sodamem.answer.reader import (  # noqa: E402
    ReaderConfig,
    ReaderContext,
    answer as reader_answer,
)
from sodamem.llm.testing import EchoProvider  # noqa: E402


class _CapturingProvider(EchoProvider):
    """EchoProvider that keeps the prompt it was handed."""

    def __init__(self):
        super().__init__()
        self.prompts: list[str] = []

    def complete(self, **kw) -> str:
        self.prompts.append(kw["messages"][-1]["content"])
        return super().complete(**kw)


def _prompt_with(*, personalization_bias: bool) -> str:
    provider = _CapturingProvider()
    reader_answer(
        "Any documentary recommendations?",
        ReaderContext(key_evidence=[{"evidence_id": "ev_fact:a",
                                     "support_text": "user likes nature docs"}],
                      citations=["ev_fact:a"]),
        current_date="2023-05-30", provider=provider, config=ReaderConfig(),
        personalization_bias=personalization_bias,
    )
    return provider.prompts[-1]


def test_the_addendum_reaches_the_reader_prompt_when_the_arm_is_on():
    assert READER_GUIDANCE_PERSONALIZATION_ADDENDUM in _prompt_with(
        personalization_bias=True)


def test_the_personalization_arm_is_off_by_default():
    assert READER_GUIDANCE_PERSONALIZATION_ADDENDUM not in _prompt_with(
        personalization_bias=False)
