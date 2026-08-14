"""Extract asserted cardinality from the question (six museums → 6)."""
from __future__ import annotations

import re
from typing import Optional

_WORD = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


def extract_cardinality(question: str) -> Optional[int]:
    q = question or ""
    # "the six museums" / "three trips" / "how many ... " without number → None
    m = re.search(
        r"\b(?:the|these|those|my)?\s*(one|two|three|four|five|six|seven|"
        r"eight|nine|ten|eleven|twelve|\d{1,2})\s+"
        r"(?:different\s+)?(?:types?\s+of\s+)?[a-z]",
        q,
        re.I,
    )
    if m:
        raw = m.group(1).lower()
        if raw.isdigit():
            n = int(raw)
            return n if 1 <= n <= 20 else None
        return _WORD.get(raw)
    # "I replaced or fixed five items" style is in gold not question — skip
    return None
