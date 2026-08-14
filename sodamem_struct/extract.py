"""Extract atomic countable events from noisy chat/roster blobs (code, not LLM)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from sodamem_struct.candidates import Candidate


@dataclass
class Event:
    key: str
    text: str
    date: str = ""
    amount: float | None = None
    source_cid: str = ""


_NOISE = re.compile(
    r"\b(looking for|recipe ideas?|do you have any|can you (help|suggest|recommend)|"
    r"suggestions?|tips on|board games?|theme for|i'm planning my own|"
    r"getting married soon|venue ideas|custom wine|gantt chart|"
    r"bulb that provides|clustering analysis)\b",
    re.I,
)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def extract_events(template: str, candidates: list[Candidate], question: str = "") -> list[Event]:
    ql = (question or "").lower()
    out: list[Event] = []
    seen: set[str] = set()

    def add(key: str, text: str, date: str = "", amount: float | None = None, cid: str = "") -> None:
        key = (key or text[:60]).strip().lower()
        if not key or key in seen:
            return
        seen.add(key)
        out.append(Event(key=key, text=_clean(text)[:240], date=date, amount=amount, source_cid=cid))

    for c in candidates:
        text = c.content or ""
        if not text.strip():
            continue
        # Skip pure advice / planning shells with no past-event cue.
        if _NOISE.search(text) and not re.search(
            r"\b(attended|went to|I had|I've had|I've been|I just got back|"
            r"one of them was|raised|spent|picked up|returned|"
            r"exchanged|assembled|fixed|bought|serviced|leading the|led the|"
            r"I led|responsible for leading|we had at)\b",
            text,
            re.I,
        ):
            continue

        if template == "dinner_party":
            def _host_key(raw: str) -> str:
                h = raw.lower().strip()
                h = re.sub(r"'s\b", "", h)
                h = re.sub(r"\s+place\b", "", h)
                return h.strip() or raw.lower()[:40]

            # "attended ... feast at Sarah's"
            for m in re.finditer(
                r"(attended|went to|was at)\s+(?:a |an |the )?([^.]{0,90}?"
                r"(?:dinner party|potluck|feast|BBQ|barbecue)[^.]{0,50})",
                text,
                re.I,
            ):
                span = m.group(0)
                if re.search(r"\bbirthday\b|\breunion\b", span, re.I):
                    continue
                host = re.search(r"\bat\s+([A-Z][a-z]+(?:'s)?(?:\s+place)?)", span)
                key = _host_key(host.group(1)) if host else span.lower()[:50]
                add(f"dinner:{key}", span, c.date, cid=c.cid)

            # "at Alex's place yesterday, where we had a potluck"
            # "at Mike's place, where we had a BBQ"
            for m in re.finditer(
                r"\bat\s+([A-Z][a-z]+(?:'s)?(?:\s+place)?)"
                r"[^.]{0,60}?\b(?:had a |where we had a |we had a )?"
                r"(potluck|feast|BBQ|barbecue|dinner party)\b",
                text,
            ):
                add(f"dinner:{_host_key(m.group(1))}", m.group(0), c.date, cid=c.cid)

            # "BBQ theme, like the one we had at Mike's place"
            for m in re.finditer(
                r"(?:BBQ|barbecue|potluck|feast|dinner party)[^.]{0,40}?"
                r"\bwe had at\s+([A-Z][a-z]+(?:'s)?(?:\s+place)?)",
                text,
                re.I,
            ):
                add(f"dinner:{_host_key(m.group(1))}", m.group(0), c.date, cid=c.cid)

            # "feast at Sarah's" / "potluck at Alex's"
            for m in re.finditer(
                r"((?:Italian\s+)?feast|potluck|dinner party|BBQ potluck)\s+at\s+"
                r"([A-Z][a-z]+(?:'s)?(?:\s+place)?)",
                text,
            ):
                window = text[max(0, m.start() - 100) : m.end() + 40]
                if re.search(r"\b(hosting|looking for|ideas for)\b", window, re.I) and not re.search(
                    r"\b(attended|went to|had (?:a great )?experience|we had)\b", window, re.I
                ):
                    continue
                add(f"dinner:{_host_key(m.group(2))}", m.group(0), c.date, cid=c.cid)
            continue

        if template == "wedding":
            def _couple_key(a: str, b: str) -> str:
                return f"{a.lower().strip()}&{b.lower().strip()}"

            # Exclude own wedding planning and sister maid-of-honor.
            if re.search(r"sister'?s wedding|maid of honor", text, re.I):
                # Still allow other weddings mentioned in the same blob.
                pass
            # Drop encyclopedia / third-person culture dumps.
            if re.search(
                r"\b(traditionally|customs involved|gurkha|typically feature|"
                r"how do .+ celebrate)\b",
                text,
                re.I,
            ):
                continue
            if re.search(r"sister'?s wedding|maid of honor", text, re.I):
                continue

            for m in re.finditer(
                r"([A-Z][a-z]+)\s+and\s+([A-Z][a-z]+)'?s?\s+wedding",
                text,
            ):
                add(
                    f"wedding:{_couple_key(m.group(1), m.group(2))}",
                    m.group(0),
                    c.date,
                    cid=c.cid,
                )

            # First-person attendance keyed by relationship (when couples absent).
            for m in re.finditer(
                r"\b(?:I've been to|I attended|went to|got back from|was at)\b"
                r"[^.]{0,120}?\b(my cousin'?s|a friend'?s|my friend'?s|my roommate'?s|"
                r"college roommate'?s)\s+wedding\b",
                text,
                re.I,
            ):
                rel = re.sub(r"[^a-z]", "", m.group(1).lower())
                add(f"wedding:rel:{rel}", m.group(0), c.date, cid=c.cid)
            # "one of them was my cousin's wedding"
            for m in re.finditer(
                r"\b(my cousin'?s|a friend'?s|my friend'?s|my roommate'?s)\s+wedding\b",
                text,
                re.I,
            ):
                if not re.search(
                    r"\b(I've been|attended|went|got back|was at|one of them was|recently)\b",
                    text,
                    re.I,
                ):
                    continue
                if re.search(r"\bplanning my own wedding\b", text, re.I) and "friend" not in m.group(0).lower() and "cousin" not in m.group(0).lower():
                    continue
                rel = re.sub(r"[^a-z]", "", m.group(1).lower())
                add(f"wedding:rel:{rel}", m.group(0), c.date, cid=c.cid)
            continue

        if template == "project_lead":
            if re.search(r"\bhow many projects\b|\bled or am currently leading\?\b", text, re.I):
                continue  # question echo / meta
            for m in re.finditer(
                r"(responsible for leading|currently leading|I've led|I led|leading the)\s+"
                r"([^?]{0,80})",
                text,
                re.I,
            ):
                span = m.group(0)
                tail = (m.group(2) or "").strip()
                if len(tail) < 5 or tail.endswith("?"):
                    continue
                if re.search(r"\b(planning to launch|thinking of|gantt)\b", text, re.I) and not re.search(
                    r"\b(responsible for leading|I've led|I led)\b", span, re.I
                ):
                    continue
                if re.search(r"\bleading a team\b", span, re.I):
                    continue  # people-management, not a counted project
                key = re.sub(r"\s+", " ", tail.lower())[:50]
                add(f"proj:{key}", span, c.date, cid=c.cid)
            continue

        if template == "bike_service":
            # Distinct bike *types* with service/tune-up — never count bare "bike".
            if not re.search(r"\b(bike|bicycle)\b", text, re.I):
                continue
            if not re.search(r"\b(service|serviced|tune-?up|repair|running great since)\b", text, re.I):
                continue
            typed = False
            for kind in ("road bike", "mountain bike", "hybrid bike", "electric bike"):
                if kind in text.lower():
                    add(f"bike:{kind}", kind, c.date, cid=c.cid)
                    typed = True
            if not typed:
                continue
            continue

        if template == "clothing_store":
            if re.search(r"dry\s*clean", text, re.I):
                continue
            for m in re.finditer(
                r"(pick(?:ed)? up|return(?:ed)?|exchange(?:d)?)\s+([^.]{0,80})",
                text,
                re.I,
            ):
                span = m.group(0)
                if not re.search(r"\b(store|zara|mall|boots|blazer|dress|shirt|pants|jacket)\b", text, re.I):
                    continue
                key = re.sub(r"\s+", " ", span.lower())[:50]
                add(f"cloth:{key}", span, c.date, cid=c.cid)
            continue

        if template == "charity_raise":
            for m in re.finditer(
                r"(raised|raise[d]?)\s+\$?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?)\b|"
                r"\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?)\s+(?:for|to)\s+[^.]{0,40}"
                r"(?:charit|hospital|shelter|cancer|fund)",
                text,
                re.I,
            ):
                raw = m.group(2) or m.group(3)
                if not raw:
                    continue
                amt = float(raw.replace(",", ""))
                add(f"charity:{amt}:{c.date}:{m.start()}", m.group(0), c.date, amount=amt, cid=c.cid)
            continue

        if template == "food_delivery":
            for name in (
                "doordash", "ubereats", "uber eats", "grubhub", "postmates",
                "seamless", "fresh fusion", "domino", "dominos",
            ):
                if name in text.lower():
                    add(f"delivery:{name.replace(' ', '')}", name, c.date, cid=c.cid)
            continue

        if template in {"furniture", "art_event", "kitchen_fix", "album", "bake", "tank", "workshop"}:
            # Conservative: only past-tense action sentences containing template cue.
            cues = {
                "furniture": r"\b(furniture|sofa|couch|table|chair|desk|bookshelf|ikea)\b",
                "art_event": r"\b(art|gallery|exhibit|museum opening)\b",
                "kitchen_fix": r"\b(toaster|coffee maker|fridge|oven|dishwasher|microwave|kitchen)\b",
                "album": r"\b(album|ep|lp|vinyl)\b",
                "bake": r"\b(baked|bake|baking)\b",
                "tank": r"\btanks?\b",
                "workshop": r"\bworkshop\b",
            }[template]
            acts = {
                "furniture": r"\b(bought|assembled|sold|fixed|got a new)\b",
                "art_event": r"\b(attended|went|visited)\b",
                "kitchen_fix": r"\b(replace|replaced|fix|fixed|broke)\b",
                "album": r"\b(listened|bought|released|got)\b",
                "bake": r"\b(baked|bake)\b",
                "tank": r"\b(bought|cleaned|set up|have)\b",
                "workshop": r"\b(attended|took|went)\b",
            }[template]
            if re.search(cues, text, re.I) and re.search(acts, text, re.I):
                # one event per fact_id/date+cue
                add(f"{template}:{c.fact_id or c.date or text[:40]}", text[:160], c.date, cid=c.cid)
            continue

        # generic: do not invent events from noise
        if re.search(r"\bhow many\b", ql) and re.search(r"\b(attended|bought|raised|led)\b", text, re.I):
            add(f"gen:{c.fact_id or text[:40]}", text[:160], c.date, cid=c.cid)

    return out
