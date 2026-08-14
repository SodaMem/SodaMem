"""v1.2 light SUM predicate gate — drop amounts whose context mismatches question."""
from __future__ import annotations

import re
from typing import Any

from protocol_v1.schema import QuestionSchema

_CHARITY = re.compile(
    r"\b(charit|donat|fundrais|raised?|pledge|nonprofit|go-?fundme|"
    r"bake sale|benefit concert|food bank|animal shelter)\b",
    re.I,
)
_HOUSE = re.compile(
    r"\b(house|mortgage|down payment|home (?:loan|price)|property|rent)\b", re.I
)
_OTHER_SPEND = re.compile(
    r"\b(workshop|tuition|salary|paycheck|grocer|amazon|uber|"
    r"subscription|membership fee)\b",
    re.I,
)


def sum_gate_advisory(
    schema: QuestionSchema,
    admitted: list[dict[str, Any]],
    *,
    question: str = "",
) -> str:
    ql = (
        (schema.predicate or "")
        + " "
        + " ".join(schema.notes)
        + " "
        + (question or "")
    ).lower()
    charity_q = any(k in ql for k in ("charit", "rais", "donat", "fundrais"))
    if schema.task != "SUM" and not charity_q:
        return ""
    if not charity_q:
        return ""
    bad: list[str] = []
    good_amt: list[str] = []
    for it in admitted:
        text = str(it.get("content") or "")
        amts = re.findall(r"\$\s?([\d,]+(?:\.\d+)?)", text)
        if not amts:
            continue
        role = "unknown"
        if _CHARITY.search(text):
            role = "raise/donate"
            good_amt.extend(amts[:2])
        elif _HOUSE.search(text):
            role = "housing/finance"
            bad.append(f"${amts[0]} (role={role})")
        elif _OTHER_SPEND.search(text):
            role = "other-spend"
            bad.append(f"${amts[0]} (role={role})")
        else:
            # Unlabeled large amounts are suspicious for charity totals
            try:
                v = float(amts[0].replace(",", ""))
                if v >= 4000 and not _CHARITY.search(text):
                    bad.append(f"${amts[0]} (unlabeled large; verify charity cue)")
            except ValueError:
                pass
    lines = [
        "protocol_v1.3 sum_money_role_gate:",
        "- Tag each dollar amount with a role: raise/donate vs housing vs other-spend.",
        "- Question is charity/fundraising total → sum ONLY raise/donate roles.",
        "- EXCLUDE housing/mortgage/down-payment and unlabeled large non-charity amounts.",
    ]
    if bad:
        lines.append("- Likely exclude: " + ", ".join(bad[:6]))
    if good_amt:
        lines.append(
            "- Charity-linked amounts seen: " + ", ".join("$" + a for a in good_amt[:8])
        )
    return "\n".join(lines)
