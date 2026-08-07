"""Deterministic relative-date resolution (PR-3.1).

Date arithmetic ("two weeks ago", "last Tuesday", "end of next month") is a
deterministic computation that LLMs do unreliably — wrong occurred_start tanks
temporal-reasoning, and wrong valid_from corrupts knowledge-update ordering. This
module resolves a relative date phrase against an anchor date using real calendar
arithmetic (dateutil.relativedelta: leap years, month-end, year rollover), so the
LLM only has to surface the phrase, not compute the date.

`resolve_date(expr, anchor_date) -> "YYYY-MM-DD" | None`.
Returns None (never a guess) when the phrase is not a recognized relative form.

Ported byte-for-byte from the predecessor implementation (Phase 1,
Task 5). R3 line-by-line audit found this file clean: zero
`_cfg.*` call-time config reads and zero broad `except Exception` — every
`except` clause here is already a narrow, specifically-typed arithmetic-overflow
guard with its own explanatory comment (see e.g. `resolve_date`'s "N units ago"
block). Nothing needed fixing; this port changes no behavior and no bytes
beyond this docstring note and the ingest-package-relative status of the
symbols it exports (`resolve_date`/`resolve_range`/`iso_precision`/
`_to_date` are ingest-only — see the ruling on
why `calendar_resolve` does not need a shared/retrieval-visible home)."""

from __future__ import annotations

import calendar
import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional

from dateutil import parser as _dtparser
from dateutil.relativedelta import relativedelta, MO, TU, WE, TH, FR, SA, SU

logger = logging.getLogger(__name__)

_WEEKDAYS = {
    "monday": MO, "tuesday": TU, "wednesday": WE, "thursday": TH,
    "friday": FR, "saturday": SA, "sunday": SU,
}
# Monday=0..Sunday=6, for ISO-week-bound weekday math (see the "last/next/this
# <weekday>" block below) — distinct from _WEEKDAYS above, which is only used
# by the bare "on <weekday>" nearest-occurrence form.
_WEEKDAY_INDEX = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
_UNIT_DELTA = {
    "day": lambda n: relativedelta(days=n),
    "week": lambda n: relativedelta(weeks=n),
    "month": lambda n: relativedelta(months=n),
    "year": lambda n: relativedelta(years=n),
}
_CN_WEEKDAY = {"一": MO, "二": TU, "三": WE, "四": TH, "五": FR, "六": SA,
               "日": SU, "天": SU}
# Monday=0..Sunday=6, for the ISO-week-bound directed-weekday math below —
# only used by the 上/下/这-directed form; the bare form still uses _CN_WEEKDAY
# (relativedelta nearest-past-occurrence, a deliberately different semantic).
_CN_WEEKDAY_INDEX = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5,
                      "日": 6, "天": 6}
_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")

# Chinese numeral -> int, covering the range daily relative-time expressions
# actually use (0-99: "三"=3, "十五"=15, "二十"=20, "二十三"=23). Arabic digits
# pass through unchanged ("3天前"). Returns None for anything else (never a guess).
_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_number(s: str) -> Optional[int]:
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s in _CN_DIGIT:
        return _CN_DIGIT[s]
    if s == "十":
        return 10
    if len(s) == 2 and s[0] == "十" and s[1] in _CN_DIGIT:   # 十X = 10+X
        return 10 + _CN_DIGIT[s[1]]
    if len(s) == 2 and s[1] == "十" and s[0] in _CN_DIGIT:   # X十 = X*10
        return _CN_DIGIT[s[0]] * 10
    if len(s) == 3 and s[1] == "十" and s[0] in _CN_DIGIT and s[2] in _CN_DIGIT:  # X十Y
        return _CN_DIGIT[s[0]] * 10 + _CN_DIGIT[s[2]]
    return None


_CN_NUM_RE = r"(?:\d+|[零一二两三四五六七八九十]{1,3})"

# English word numbers for relative expressions ("two weeks ago", "in a few
# weeks"). Digits pass through. "a"/"an" = 1. DELIBERATELY excludes the fuzzy
# quantifiers "few"/"several"/"couple": "a few weeks ago" has no single N a
# reader could defend (several could be 5, 6, ...), so it stays None rather than
# fabricate a precision the source never gave. Shared with the extractor's
# quantity-grounding check so a word-number quantity ("three times a week") is
# not mis-flagged as an ungrounded/hallucinated value.
_EN_NUMBER = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "a": 1, "an": 1,
}
_EN_NUM_RE = (r"(?:\d+|eleven|twelve|one|two|three|four|five|six|seven|eight|"
              r"nine|ten|an|a)")


def en_number(s: str) -> Optional[int]:
    """English word/digit -> int, or None. Never guesses (fuzzy words absent)."""
    if not s:
        return None
    s = s.strip().lower()
    if s.isdigit():
        return int(s)
    return _EN_NUMBER.get(s)

# Shared negative-lookahead fragment: a trailing 前/后 turns an absolute date
# into a DIRECTION ("9月10日前" = "before Sept 10"), not a point — resolve_date
# only ever returns points, so any absolute-date pattern below must decline to
# match rather than silently drop the direction and return the date itself as
# if it were the point in question (same fabricated-precision failure mode as
# the month/year-without-day fix). Applied everywhere a Chinese absolute date
# (bare year, year-month, year-month-day, month-day) is resolved.
_CN_NO_DIRECTION_SUFFIX = r"(?!\s?(?:前|以前|之前|后|以后|之后))"

# Chinese unit word -> the same canonical key used by _UNIT_DELTA above. "个"
# (a bare measure word) is stripped by callers before this lookup, since it is
# optional/interchangeable for 月/周/星期/礼拜 ("三个月前" == "三月前" in meaning,
# though the latter is rare because "三月" alone usually reads as the month
# name "March" — see the (?<![月年]) exclusion on the ago/later regex below).
_CN_UNIT_MAP = {"天": "day", "日": "day", "周": "week", "星期": "week",
                "礼拜": "week", "月": "month", "年": "year"}

# Named US holidays (§ user directive 2026-07-01): fixed calendar dates are exact;
# "rule" holidays (Nth weekday of a month, or last weekday) are equally
# deterministic via dateutil.relativedelta's weekday(+n)/weekday(-1). Matched
# holiday names are normalized (collapsed whitespace, matched regex group) before
# lookup here — see the callers in resolve_date.
_FIXED_HOLIDAYS = {
    "christmas": (12, 25), "christmas day": (12, 25),
    "new year's day": (1, 1), "new years day": (1, 1), "new year day": (1, 1),
    "new year's eve": (12, 31), "new years eve": (12, 31),
    "new year's": (1, 1), "new years": (1, 1),  # bare "new year's" defaults to Day, not Eve
    "halloween": (10, 31),
    "independence day": (7, 4),
    "valentine's day": (2, 14), "valentines day": (2, 14), "valentine day": (2, 14),
}
_RULE_HOLIDAYS = {
    # (weekday_class, nth_occurrence_in_month_or_-1_for_last, month)
    "thanksgiving": (TH, 4, 11),
    "mother's day": (SU, 2, 5), "mothers day": (SU, 2, 5),
    "father's day": (SU, 3, 6), "fathers day": (SU, 3, 6),
    "labor day": (MO, 1, 9),
    "memorial day": (MO, -1, 5),
    "mlk day": (MO, 3, 1),
    "martin luther king jr day": (MO, 3, 1), "martin luther king jr. day": (MO, 3, 1),
    "martin luther king day": (MO, 3, 1),
}


def _HOLIDAY_DATE(name: str, year: int) -> Optional[date]:
    """A named holiday's date in a given year. Fixed-date holidays are exact;
    rule-based ones (e.g. Thanksgiving = 4th Thursday of November) use dateutil's
    weekday(+n)/weekday(-1) — equally deterministic, just not a fixed month/day."""
    fixed = _FIXED_HOLIDAYS.get(name)
    if fixed:
        mon, day = fixed
        return date(year, mon, day)
    rule = _RULE_HOLIDAYS.get(name)
    if rule:
        wd_class, nth, month = rule
        anchor_day = 1 if nth > 0 else calendar.monthrange(year, month)[1]
        return date(year, month, anchor_day) + relativedelta(weekday=wd_class(nth))
    cn = _CN_HOLIDAY_DATE(name, year)
    if cn is not None:
        return cn
    return None


# ---------------------------------------------------------------------------
# Chinese holidays (user directive 2026-07-01: full coverage incl. lunar ones,
# data table vendored in-repo — no new pip dependency, same public 1900-2100
# lunar-calendar encoding used by essentially every Chinese-calendar library).
# ---------------------------------------------------------------------------

# Gregorian-fixed PRC holidays (no lunar conversion needed).
_CN_FIXED_HOLIDAYS = {
    "元旦": (1, 1), "new year's day (china)": (1, 1),  # same date as western New Year
    "劳动节": (5, 1), "五一": (5, 1), "labor day (china)": (5, 1),
    "international workers' day": (5, 1), "may day": (5, 1),
    "国庆节": (10, 1), "十一": (10, 1), "national day": (10, 1), "national day (china)": (10, 1),
    "青年节": (5, 4), "youth day": (5, 4),
    "儿童节": (6, 1), "children's day": (6, 1), "children's day (china)": (6, 1),
    "教师节": (9, 10), "teachers' day": (9, 10),
    "建军节": (8, 1), "army day": (8, 1),
}

# "从1900年到2100年的农历月份数据代码，20位二进制代码表示一个年份的数据"：
# high 4 bits = leap month length (1=30 days), middle 12 bits = months 1-12
# size (1=30, 0=29), low 4 bits = which month is a leap month (0=no leap month
# that year). Public standard table — same data used by zhdate/lunarcalendar/
# sxtwl and effectively every Chinese-calendar implementation. Vendored here
# (not a pip dependency) per user directive: small, auditable, zero new deps.
_CN_YEAR_CODE = [
    19416,
    19168, 42352, 21717, 53856, 55632, 91476, 22176, 39632,
    21970, 19168, 42422, 42192, 53840, 119381, 46400, 54944,
    44450, 38320, 84343, 18800, 42160, 46261, 27216, 27968,
    109396, 11104, 38256, 21234, 18800, 25958, 54432, 59984,
    92821, 23248, 11104, 100067, 37600, 116951, 51536, 54432,
    120998, 46416, 22176, 107956, 9680, 37584, 53938, 43344,
    46423, 27808, 46416, 86869, 19872, 42416, 83315, 21168,
    43432, 59728, 27296, 44710, 43856, 19296, 43748, 42352,
    21088, 62051, 55632, 23383, 22176, 38608, 19925, 19152,
    42192, 54484, 53840, 54616, 46400, 46752, 103846, 38320,
    18864, 43380, 42160, 45690, 27216, 27968, 44870, 43872,
    38256, 19189, 18800, 25776, 29859, 59984, 27480, 23232,
    43872, 38613, 37600, 51552, 55636, 54432, 55888, 30034,
    22176, 43959, 9680, 37584, 51893, 43344, 46240, 47780,
    44368, 21977, 19360, 42416, 86390, 21168, 43312, 31060,
    27296, 44368, 23378, 19296, 42726, 42208, 53856, 60005,
    54576, 23200, 30371, 38608, 19195, 19152, 42192, 118966,
    53840, 54560, 56645, 46496, 22224, 21938, 18864, 42359,
    42160, 43600, 111189, 27936, 44448, 84835, 37744, 18936,
    18800, 25776, 92326, 59984, 27296, 108228, 43744, 37600,
    53987, 51552, 54615, 54432, 55888, 23893, 22176, 42704,
    21972, 21200, 43448, 43344, 46240, 46758, 44368, 21920,
    43940, 42416, 21168, 45683, 26928, 29495, 27296, 44368,
    84821, 19296, 42352, 21732, 53600, 59752, 54560, 55968,
    92838, 22224, 19168, 43476, 41680, 53584, 62034, 54560,
]
# Gregorian date of lunar new year (正月初一) for each lunar year 1900-2100.
_CN_NEW_YEAR = [
    "19000131",
    "19010219", "19020208", "19030129", "19040216", "19050204",
    "19060125", "19070213", "19080202", "19090122", "19100210",
    "19110130", "19120218", "19130206", "19140126", "19150214",
    "19160203", "19170123", "19180211", "19190201", "19200220",
    "19210208", "19220128", "19230216", "19240205", "19250124",
    "19260213", "19270202", "19280123", "19290210", "19300130",
    "19310217", "19320206", "19330126", "19340214", "19350204",
    "19360124", "19370211", "19380131", "19390219", "19400208",
    "19410127", "19420215", "19430205", "19440125", "19450213",
    "19460202", "19470122", "19480210", "19490129", "19500217",
    "19510206", "19520127", "19530214", "19540203", "19550124",
    "19560212", "19570131", "19580218", "19590208", "19600128",
    "19610215", "19620205", "19630125", "19640213", "19650202",
    "19660121", "19670209", "19680130", "19690217", "19700206",
    "19710127", "19720215", "19730203", "19740123", "19750211",
    "19760131", "19770218", "19780207", "19790128", "19800216",
    "19810205", "19820125", "19830213", "19840202", "19850220",
    "19860209", "19870129", "19880217", "19890206", "19900127",
    "19910215", "19920204", "19930123", "19940210", "19950131",
    "19960219", "19970207", "19980128", "19990216", "20000205",
    "20010124", "20020212", "20030201", "20040122", "20050209",
    "20060129", "20070218", "20080207", "20090126", "20100214",
    "20110203", "20120123", "20130210", "20140131", "20150219",
    "20160208", "20170128", "20180216", "20190205", "20200125",
    "20210212", "20220201", "20230122", "20240210", "20250129",
    "20260217", "20270206", "20280126", "20290213", "20300203",
    "20310123", "20320211", "20330131", "20340219", "20350208",
    "20360128", "20370215", "20380204", "20390124", "20400212",
    "20410201", "20420122", "20430210", "20440130", "20450217",
    "20460206", "20470126", "20480214", "20490202", "20500123",
    "20510211", "20520201", "20530219", "20540208", "20550128",
    "20560215", "20570204", "20580124", "20590212", "20600202",
    "20610121", "20620209", "20630129", "20640217", "20650205",
    "20660126", "20670214", "20680203", "20690123", "20700211",
    "20710131", "20720219", "20730207", "20740127", "20750215",
    "20760205", "20770124", "20780212", "20790202", "20800122",
    "20810209", "20820129", "20830217", "20840206", "20850126",
    "20860214", "20870203", "20880124", "20890210", "20900130",
    "20910218", "20920207", "20930127", "20940215", "20950205",
    "20960125", "20970212", "20980201", "20990121", "21000209",
]
_CN_YEAR_MIN, _CN_YEAR_MAX = 1900, 2100


def _cn_lunar_month_days(year_code: int) -> list[int]:
    """Decode a year_code into a per-month day-count list (12 entries, or 13 in
    a leap year with the leap month's length inserted at its position)."""
    month_days = []
    for i in range(5, 17):
        month_days.insert(0, 30 if (year_code >> (i - 1)) & 1 else 29)
    leap_month = year_code & 0xF
    if leap_month:
        month_days.insert(leap_month, 30 if (year_code >> 16) else 29)
    return month_days


def _lunar_to_gregorian(lunar_year: int, lunar_month: int, lunar_day: int) -> Optional[date]:
    """Convert a (non-leap-month) lunar date to its Gregorian date. Only regular
    months are needed for the named holidays below (none fall in a leap month)."""
    if not (_CN_YEAR_MIN <= lunar_year <= _CN_YEAR_MAX and 1 <= lunar_month <= 12):
        return None
    year_code = _CN_YEAR_CODE[lunar_year - _CN_YEAR_MIN]
    month_days = _cn_lunar_month_days(year_code)
    leap_month = year_code & 0xF
    # If a leap month falls BEFORE our target month, its days count toward the
    # offset too (it occupies a slot in month_days at index `leap_month`).
    months_before = lunar_month - 1
    if leap_month and leap_month < lunar_month:
        months_before += 1
    days_passed = sum(month_days[:months_before]) + (lunar_day - 1)
    new_year = datetime.strptime(_CN_NEW_YEAR[lunar_year - _CN_YEAR_MIN], "%Y%m%d").date()
    return new_year + timedelta(days=days_passed)


# (lunar_month, lunar_day); 除夕 (Chinese New Year's Eve) is a special case —
# the day BEFORE lunar new year, which is the last day of the 12th lunar month
# (29 or 30 depending on the year, so it's cheapest to compute directly from
# the New Year table rather than the month-length table).
_CN_LUNAR_HOLIDAYS = {
    "春节": (1, 1), "chinese new year": (1, 1), "spring festival": (1, 1),
    "lunar new year": (1, 1),
    "元宵节": (1, 15), "lantern festival": (1, 15),
    "端午节": (5, 5), "dragon boat festival": (5, 5),
    "中秋节": (8, 15), "mid-autumn festival": (8, 15), "mid autumn festival": (8, 15),
    "重阳节": (9, 9), "double ninth festival": (9, 9),
    "腊八节": (12, 8), "laba festival": (12, 8),
}
_CN_NEW_YEARS_EVE_NAMES = {"除夕", "chinese new year's eve", "chinese new years eve"}


def _CN_HOLIDAY_DATE(name: str, year: int) -> Optional[date]:
    fixed = _CN_FIXED_HOLIDAYS.get(name)
    if fixed:
        mon, day = fixed
        return date(year, mon, day)
    if name in _CN_NEW_YEARS_EVE_NAMES:
        if not (_CN_YEAR_MIN <= year <= _CN_YEAR_MAX):
            return None
        new_year = datetime.strptime(_CN_NEW_YEAR[year - _CN_YEAR_MIN], "%Y%m%d").date()
        return new_year - timedelta(days=1)
    lunar = _CN_LUNAR_HOLIDAYS.get(name)
    if lunar:
        return _lunar_to_gregorian(year, lunar[0], lunar[1])
    return None


# Benchmark/session-date anchors carry a weekday parenthetical the LLM/harness
# writes for readability, e.g. "2023/03/04 (Sat) 16:45". dateutil.parse cannot
# parse "(Sat)" mixed into the string and raises — this is the REAL production
# anchor format (client.py passes it straight through to extract_session), so an
# unstripped anchor made every resolve_date/resolve_range call fail silently in
# every real ingest run. Strip it before parsing.
_WEEKDAY_PAREN = re.compile(r"\s*\([A-Za-z]{3,9}\)\s*")


def _to_date(anchor: str) -> Optional[date]:
    if not anchor:
        return None
    cleaned = _WEEKDAY_PAREN.sub(" ", str(anchor)).strip()
    try:
        return _dtparser.parse(cleaned).date()
    except (ValueError, OverflowError, TypeError):
        # A non-empty anchor that fails to parse silently disables every relative-
        # date resolution downstream (extraction falls back to the LLM's own,
        # unverified date guess) — this must be visible, never silent.
        logger.warning(
            "calendar_resolve._to_date: failed to parse anchor %r (cleaned=%r) — "
            "relative-date resolution is DISABLED for this call", anchor, cleaned,
        )
        return None


def resolve_date(expr: str, anchor_date: str, prefer: str = "past") -> Optional[str]:
    """Resolve a relative date phrase to YYYY-MM-DD against anchor_date.

    Recognized forms: today/yesterday/tomorrow; "N days|weeks|months|years
    ago|later" (N = digit OR English word two/three/...); "in N units"; bare
    "<Month>" (nearest past occurrence, month precision); bare "<weekday>";
    "last|next|this week|month|year|<weekday>"; "end|start of [last|next]
    month". None when unrecognized.

    `prefer` disambiguates DIRECTIONLESS forms (bare weekday) by the fact's
    modality: "past" (default; past_event/current_state) -> nearest past
    occurrence, "future" (future_plan/intent) -> nearest future. It never
    overrides an explicit direction already in the phrase."""
    anchor = _to_date(anchor_date)
    if anchor is None or not expr:
        return None
    s = expr.strip().lower()

    # Keyword forms use word-boundary search so resolve_date also works when the
    # phrase is embedded in a longer support_text (PR-3.1 null-fill).
    if re.search(r"\byesterday\b", s):
        return (anchor - relativedelta(days=1)).isoformat()
    if re.search(r"\btomorrow\b", s):
        return (anchor + relativedelta(days=1)).isoformat()
    if re.fullmatch(r"\s*(today|now)\s*", s):
        return anchor.isoformat()
    if re.search(r"\btonight\b", s):
        return anchor.isoformat()  # same date as the anchor (day precision)

    # Chinese fixed-point day words. No \b (CJK has no word boundaries, see the
    # holiday-matching note above) — plain substring match, safe: these are
    # specific 2-char compounds. Checked before the ago/later numeral pattern
    # below since they carry no digit and can't collide with it. Order among
    # themselves matters: "前天"/"后天" must be checked before a hypothetical
    # bare "天" anywhere else, but none of the other Chinese branches match a
    # bare "天", so there is no shadowing risk here.
    if "昨天" in s:
        return (anchor - relativedelta(days=1)).isoformat()
    if "前天" in s:
        return (anchor - relativedelta(days=2)).isoformat()
    if "明天" in s:
        return (anchor + relativedelta(days=1)).isoformat()
    if "后天" in s:
        return (anchor + relativedelta(days=2)).isoformat()
    if "今晚" in s:
        return anchor.isoformat()
    # "今天/今日/现在/目前" require a FULLMATCH (the whole trimmed expr, not a
    # substring), mirroring the English "today|now" block above exactly —
    # found by a follow-up audit (2026-07-01): unlike 昨天/前天/明天/后天/今晚
    # (unambiguous date words, safe to substring-match anywhere in a longer
    # string), "现在"/"目前" are heavily overloaded in real sentences to mean
    # "currently" (a valid_time qualifier: "我现在的预算" = "my CURRENT
    # budget"), not "the literal date today". A substring match here caused
    # query_plan.py's parse_time_constraint to call resolve_date on a whole
    # raw query and get back today's date as a fabricated hard single-day
    # window for a question that was actually asking for the current VALUE of
    # something, not something that happened today. The English side already
    # avoids this exact trap for "now" via fullmatch; this brings Chinese to
    # the same standard instead of inventing a new one.
    if re.fullmatch(r"\s*(今天|今日|现在|目前)\s*", s):
        return anchor.isoformat()

    # "N days/weeks/months/years ago" or "... later/from now/ahead". N is
    # unbounded (\d+, no digit cap — unlike the Chinese repeated-modifier
    # patterns, a legitimate "3650 days ago" needs more than 2 digits, so the
    # fix here is a try/except guard, not a regex-level bound) — found by
    # adversarial testing (2026-07-01) that a pathological N (e.g.
    # "9999999999999999 days ago") overflows date arithmetic and raises an
    # uncaught OverflowError/ValueError, propagating all the way through
    # query_plan.py's unguarded callers to the reader stage.
    # N is a digit OR an English word (two/three/.../twelve, a/an=1). The fuzzy
    # "a few/several/couple" are absent from _EN_NUM_RE and stay None (no single
    # defensible N). An optional "about/around/roughly" marker is stripped — the
    # anchor N is still exact ("about three weeks ago" = 3 weeks). The prior
    # digit-only regex silently returned None on every word-number phrase
    # (2026-07-04 log audit: 816 "N units ago" failures were all word numbers).
    m = re.search(r"\b(?:(?:about|around|roughly|approximately)\s+)?(" + _EN_NUM_RE +
                  r")\s+(day|week|month|year)s?\s+(ago|later|from now|ahead|earlier)\b", s)
    if m:
        n, unit, direction = en_number(m.group(1)), m.group(2), m.group(3)
        if n is not None:
            sign = -1 if direction in ("ago", "earlier") else 1
            try:
                return (anchor + _UNIT_DELTA[unit](sign * n)).isoformat()
            except (OverflowError, ValueError):
                logger.warning(
                    "calendar_resolve.resolve_date: date arithmetic overflowed for "
                    "expr=%r anchor=%r — returning None instead of raising", expr, anchor_date,
                )
                return None

    # "in N days/weeks/months/years" — N digit OR English word (same overflow
    # guard and same word-number fix as the "N ago" block above).
    m = re.search(r"\bin\s+(" + _EN_NUM_RE + r")\s+(day|week|month|year)s?\b", s)
    if m:
        n = en_number(m.group(1))
        if n is not None:
            try:
                return (anchor + _UNIT_DELTA[m.group(2)](n)).isoformat()
            except (OverflowError, ValueError):
                logger.warning(
                    "calendar_resolve.resolve_date: date arithmetic overflowed for "
                    "expr=%r anchor=%r — returning None instead of raising", expr, anchor_date,
                )
                return None

    # Chinese "N天/日/(个)周/(个)星期/(个)礼拜/个月/年 + 前/以前/之前/后/以后/之后"
    # (N units ago/later). Two deliberate exclusions:
    # (1) (?<![月年]) lookbehind on the number stops this from misreading the
    #     day-of-month inside an absolute date like "9月10日前" ("before Sept
    #     10") as "10 days ago" — the character right before the digit run in
    #     that construct is always 月, so excluding it structurally prevents
    #     the collision regardless of check order (unlike the "this year" bug,
    #     this doesn't rely on ordering the two branches).
    # (2) "个" is MANDATORY before 月 (only "个月" matches, never bare 月): bare
    #     "三月前" is real, ordinary Chinese for "before March", not "three
    #     months ago" (that reading needs 个: "三个月前") — making 个 optional
    #     here would silently misresolve every "before <month>" mention that
    #     happens to have a 1-2 digit month number spelled as a numeral.
    # Numbers capped at 2 Arabic digits so a 4-digit year like "2019年" (see
    # the bare-year block far below) can never be misread as "2019 years ago".
    m = re.search(
        r"(?<![月年\d])(\d{1,2}|[零一二两三四五六七八九十]{1,3})\s*"
        r"(天|日|个?(?:周|星期|礼拜)|个月|年)\s*(以前|之前|前|以后|之后|后)", s)
    if m:
        n = _cn_number(m.group(1))
        unit_word = m.group(2)[1:] if m.group(2).startswith("个") else m.group(2)
        unit = _CN_UNIT_MAP.get(unit_word)
        if n is not None and unit:
            sign = -1 if m.group(3) in ("以前", "之前", "前") else 1
            return (anchor + _UNIT_DELTA[unit](sign * n)).isoformat()

    # "end|start/beginning of [last|next|this] month|year|week" — checked BEFORE
    # the generic "last/next month" form so "end of next month" isn't swallowed.
    m = re.search(r"\b(end|start|beginning)\s+of\s+(?:(last|next|this|the)\s+)?(month|year|week)\b", s)
    if m:
        which, rel, unit = m.group(1), m.group(2), m.group(3)
        rel = "this" if rel in (None, "the") else rel
        base = anchor + _UNIT_DELTA[unit]({"last": -1, "next": 1, "this": 0}[rel])
        if unit == "month":
            return (base + relativedelta(day=31 if which == "end" else 1)).isoformat()
        if unit == "year":
            return base.replace(month=12, day=31).isoformat() if which == "end" \
                else base.replace(month=1, day=1).isoformat()
        # week: Monday start .. Sunday end (ISO week)
        return (base + relativedelta(weekday=SU(+1))).isoformat() if which == "end" \
            else (base + relativedelta(weekday=MO(-1))).isoformat()

    # "last/next/this weekend" and "last/next/this <weekday>" — both anchored
    # to the ISO calendar week (Monday=0..Sunday=6), NOT a "nearest occurrence"
    # search relative to the anchor date. This was a REAL bug found by
    # adversarial testing (2026-07-01): the old relativedelta(weekday=wd(-1))
    # form finds the nearest PAST occurrence of the target weekday, which for
    # an anchor whose own weekday falls AFTER the target within the same week
    # lands in the CURRENT week, not the previous one — e.g. from a Friday
    # anchor, "last Wednesday" gave this week's Wednesday, which is not even
    # inside "last week"'s own range (an internal self-contradiction against
    # resolve_range's ISO-week definition of "last/this/next week", directly
    # below). Symmetrically, "this <weekday>" for a weekday earlier than the
    # anchor's jumped into NEXT week instead of staying in the anchor's own
    # week. Fixed by computing directly off the target week's Monday, exactly
    # mirroring resolve_range's math — the two can no longer disagree about
    # what "last/this/next week" means, because they're now the same formula.
    m = re.search(r"\b(last|next|this)\s+weekend\b", s)
    if m:
        this_monday = anchor - timedelta(days=anchor.weekday())
        week_offset = {"last": -1, "next": 1, "this": 0}[m.group(1)]
        return (this_monday + timedelta(weeks=week_offset, days=5)).isoformat()  # Saturday

    m = re.search(r"\b(last|next|this)\s+(" + "|".join(_WEEKDAYS) + r")\b", s)
    if m:
        rel, wd_idx = m.group(1), _WEEKDAY_INDEX[m.group(2)]
        this_monday = anchor - timedelta(days=anchor.weekday())
        week_offset = {"last": -1, "next": 1, "this": 0}[rel]
        try:
            return (this_monday + timedelta(weeks=week_offset, days=wd_idx)).isoformat()
        except (OverflowError, ValueError):
            logger.warning(
                "calendar_resolve.resolve_date: date arithmetic overflowed for "
                "expr=%r anchor=%r — returning None instead of raising", expr, anchor_date,
            )
            return None

    # bare "on <weekday>" (no last/next/this) -> nearest PAST occurrence (incl.
    # anchor day). Past-tense narration: "I saw it on Tuesday" = the recent one.
    m = re.search(r"\bon\s+(" + "|".join(_WEEKDAYS) + r")\b", s)
    if m:
        return (anchor + relativedelta(weekday=_WEEKDAYS[m.group(1)](-1))).isoformat()

    # bare "<weekday>" with NO on/last/next/this direction -> modality-directed
    # nearest occurrence: prefer="past" (past_event/current_state) the most
    # recent one, prefer="future" (future_plan/intent) the next upcoming. The
    # lookbehinds keep it from re-matching the tail of the directed forms already
    # handled above. Was a resolver gap (2026-07-04 log: 114 bare-weekday None).
    m = re.search(r"(?<!last )(?<!next )(?<!this )(?<!on )\b(" +
                  "|".join(_WEEKDAYS) + r")\b", s)
    if m:
        step = 1 if prefer == "future" else -1
        return (anchor + relativedelta(weekday=_WEEKDAYS[m.group(1)](step))).isoformat()

    # Chinese "上/下/这 + 周|星期|礼拜 + weekday-char" (last/next/this <weekday>),
    # ISO-week-bound exactly like the English block above (same 2026-07-01 bug
    # fix, same reason: a relativedelta "nearest occurrence" search relative to
    # the anchor date can silently land in the wrong ISO week whenever the
    # anchor's own weekday falls after — for 上 — or before — for 这 — the
    # target weekday within that week, contradicting resolve_range's ISO-week
    # definition of "上/这/下周"). "上+"/"下+" (repeated-character count) then
    # composes cleanly on top: count-1 extra weeks is exactly resolve_range's
    # own week_offset math, just expressed as a repetition length instead of a
    # number — "上上周二" is 2 ISO weeks back, same formula as "上周二" (1 week
    # back), same formula the English side uses for "last/this/next <weekday>".
    # The repeat count is capped at 10 (real Chinese essentially never goes
    # past "上上上", 3x) — found by adversarial testing (2026-07-01): an
    # unbounded "+" both (a) lets relativedelta/timedelta arithmetic overflow
    # datetime's year range on a pathological input, raising an uncaught
    # ValueError/OverflowError, and (b) is genuinely SLOW at scale (~28s for a
    # 50,000-character run, since the underlying date arithmetic is not O(1)
    # in the delta size) — a bounded quantifier fixes both at the regex level,
    # which is strictly better than a try/except: it also caps the work done,
    # not just the exception.
    m = re.search(r"(上{1,10}|下{1,10}|这)\s?(?:周|星期|礼拜)\s?([一二三四五六日天])", s)
    if m:
        rel, wd_idx = m.group(1), _CN_WEEKDAY_INDEX[m.group(2)]
        this_monday = anchor - timedelta(days=anchor.weekday())
        if rel[0] == "上":
            week_offset = -len(rel)
        elif rel[0] == "下":
            week_offset = len(rel)
        else:
            week_offset = 0  # 这<weekday>
        try:
            return (this_monday + timedelta(weeks=week_offset, days=wd_idx)).isoformat()
        except (OverflowError, ValueError):
            logger.warning(
                "calendar_resolve.resolve_date: date arithmetic overflowed for "
                "expr=%r anchor=%r — returning None instead of raising", expr, anchor_date,
            )
            return None

    # bare "星期二"/"周二"/"礼拜二" (no 上/下/这) -> nearest PAST occurrence,
    # matching the English bare "on <weekday>" convention above. Also guarded
    # against being preceded by 上/下/这 (so it never independently re-matches
    # the tail of "上上周二" after the directed pattern above declines it).
    m = re.search(r"(?<![上下这])(?:周|星期|礼拜)([一二三四五六日天])", s)
    if m:
        return (anchor + relativedelta(weekday=_CN_WEEKDAY[m.group(1)](-1))).isoformat()

    # "<Month> <day>[st|nd|rd|th][, year]" or "<day>[st..th] [of] <Month> [year]".
    # Ordinal suffix tolerated (the old \d{1,2}\b rejected "10th"); year used when
    # present, else inferred from the anchor. Day-precision absolute date.
    mon = day = yr = None
    m = re.search(
        r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b", s)
    if m:
        mon, day, yr = m.group(1), m.group(2), m.group(3)
    else:
        m = re.search(
            r"\b(\d{1,2})(?:st|nd|rd|th)?\s+of\s+(" + "|".join(_MONTHS) + r")(?:,?\s+(\d{4}))?\b", s)
        if m:
            day, mon, yr = m.group(1), m.group(2), m.group(3)
    if mon:
        try:
            d = _dtparser.parse(f"{mon} {day} {yr or anchor.year}").date()
        except (ValueError, OverflowError):
            return None
        # Directionality for YEARLESS month+day (2026-07-07, q198 class): with
        # no explicit year, "I attended ... on March 15-16" said on 2023-02-26
        # used to resolve into the anchor's FUTURE (2023-03-15), dropping the
        # component from every past enumeration window. Roll one year toward
        # the fact's direction — but ONLY on the strong signals:
        #   prefer="past_event"  (narrated past)   -> future date rolls back
        #   prefer="future"      (plan/intent)     -> past date rolls forward
        # The default prefer="past" does NOT roll: it only disambiguates
        # directionless forms (bare weekday/month). current_state facts keep
        # the anchor year ("my flight is on March 15" must stay upcoming).
        # An EXPLICIT year is never overridden; same-day stays.
        if yr is None:
            try:
                if prefer == "future":
                    if d < anchor:
                        d = d.replace(year=anchor.year + 1)
                elif prefer == "past_event" and d > anchor:
                    d = d.replace(year=anchor.year - 1)
            except ValueError:
                # Feb 29 rolled into a non-leap year — keep the anchor-year date
                # rather than fabricating an invalid one.
                pass
        return d.isoformat()

    # Chinese "YYYY年M月D日/号" (full absolute date, most specific — checked
    # before the year-month and month-day-only forms below so a full date
    # keeps its day). Chinese OR Arabic numerals for month/day.
    m = re.search(
        r"((?:19|20)\d{2})\s?年\s?(\d{1,2}|[零一二两三四五六七八九十]{1,3})\s?月\s?"
        r"(\d{1,2}|[零一二两三四五六七八九十]{1,3})\s?(?:日|号)" + _CN_NO_DIRECTION_SUFFIX, s)
    if m:
        mon, day = _cn_number(m.group(2)), _cn_number(m.group(3))
        if mon and day:
            try:
                return date(int(m.group(1)), mon, day).isoformat()
            except ValueError:
                return None

    # Chinese "YYYY年M月" (year+month, no day) -> month precision.
    m = re.search(
        r"((?:19|20)\d{2})\s?年\s?(\d{1,2}|[零一二两三四五六七八九十]{1,3})\s?月" + _CN_NO_DIRECTION_SUFFIX, s)
    if m:
        mon = _cn_number(m.group(2))
        if mon and 1 <= mon <= 12:
            return f"{int(m.group(1)):04d}-{mon:02d}"

    # Chinese "去年/今年/明年" immediately adjacent to "M月D日/号" (last/this/
    # next year's specific month-day, e.g. "去年9月10日") — checked before the
    # bare "M月D日" (anchor-year) form below so the year is correctly
    # overridden instead of silently assumed as the anchor's. Only the
    # ADJACENT combo is handled, matching this file's plain-regex philosophy
    # elsewhere: a distant modifier ("去年的时候，是9月10日发生的") falls
    # through to the anchor-year assumption below — the same residual gap the
    # English "<Month> <day>" form already has with an implied external year.
    m = re.search(
        r"(去年|今年|明年)\s?(\d{1,2}|[零一二两三四五六七八九十]{1,3})\s?月\s?"
        r"(\d{1,2}|[零一二两三四五六七八九十]{1,3})\s?(?:日|号)" + _CN_NO_DIRECTION_SUFFIX, s)
    if m:
        delta = {"去年": -1, "今年": 0, "明年": 1}[m.group(1)]
        mon, day = _cn_number(m.group(2)), _cn_number(m.group(3))
        if mon and day:
            year = (anchor + _UNIT_DELTA["year"](delta)).year
            try:
                return date(year, mon, day).isoformat()
            except ValueError:
                return None

    # Chinese "M月D日/号" (month+day, no year -> anchor's year). The (?<!年)
    # lookbehind stops this from re-deriving a day off an UNHANDLED relative-
    # year + absolute-date combo (e.g. a distant-modifier case the block above
    # doesn't catch) using the WRONG (anchor's) year — better to leave that
    # combination unresolved (None) than silently return the wrong year.
    m = re.search(
        r"(?<!年)(\d{1,2}|[零一二两三四五六七八九十]{1,3})\s?月\s?"
        r"(\d{1,2}|[零一二两三四五六七八九十]{1,3})\s?(?:日|号)" + _CN_NO_DIRECTION_SUFFIX, s)
    if m:
        mon, day = _cn_number(m.group(1)), _cn_number(m.group(2))
        if mon and day:
            try:
                return date(anchor.year, mon, day).isoformat()
            except ValueError:
                return None

    # "<Month> <year>" e.g. "June 2023" -> month precision (checked AFTER the
    # day forms so "3rd of March 2021" keeps its day).
    m = re.search(r"\b(" + "|".join(_MONTHS) + r")\s+((?:19|20)\d{2})\b", s)
    if m:
        return f"{int(m.group(2)):04d}-{_MONTHS.index(m.group(1)) + 1:02d}"

    # "last/next/this <month name>" e.g. "last March" -> month precision "YYYY-MM".
    m = re.search(r"\b(last|next|this)\s+(" + "|".join(_MONTHS) + r")\b", s)
    if m:
        rel, mon_idx = m.group(1), _MONTHS.index(m.group(2)) + 1
        year = anchor.year
        cand = date(year, mon_idx, 1)
        if rel == "last" and cand > anchor:
            year -= 1
        elif rel == "next" and cand <= anchor:
            year += 1
        return f"{year:04d}-{mon_idx:02d}"

    # Named holidays (user directive 2026-07-01: deterministic for BOTH fixed-date
    # and rule-based US holidays; explicitly OUT of scope: seasons — real-corpus
    # audit showed the word "season"/spring/summer/fall/winter is dominated by
    # non-temporal false positives (poetry, podcast names, generic discussion) —
    # and couple/few/several+ago — folded into the same "no reliable duration"
    # bucket as recently/lately, left unresolved by design.
    m = re.search(
        r"\b(last|next|this)?\s?("
        r"valentine'?s?\s*day|christmas(?:\s+day)?|new\s*year'?s?\s*(?:day|eve)?"
        r"|halloween|independence\s+day|thanksgiving|mother'?s\s+day|father'?s\s+day"
        r"|labor\s+day|memorial\s+day|mlk\s+day|martin\s+luther\s+king.*?day"
        r")\b", s)
    if m:
        rel, name = m.group(1), re.sub(r"\s+", " ", m.group(2)).strip()
        this_year_date = _HOLIDAY_DATE(name, anchor.year)
        if this_year_date is not None:
            if rel == "last":
                d = this_year_date if this_year_date < anchor else _HOLIDAY_DATE(name, anchor.year - 1)
            elif rel == "next":
                d = this_year_date if this_year_date > anchor else _HOLIDAY_DATE(name, anchor.year + 1)
            elif rel == "this":
                d = this_year_date
            else:
                # bare mention, no qualifier: nearest occurrence not after anchor
                # (past-tense-narration bias, matching the bare-weekday convention).
                d = this_year_date if this_year_date <= anchor else _HOLIDAY_DATE(name, anchor.year - 1)
            if d is not None:
                return d.isoformat()

    # Chinese holidays: matched separately from the US block above because CJK
    # text has no whitespace word-boundaries — Python's \b is a \w/non-\w
    # transition, and adjacent Chinese characters are ALL \w, so "\b春节\b" would
    # NOT match inside a pure-Chinese sentence (only when flanked by ASCII/
    # punctuation, e.g. embedded in English text). The Chinese-character
    # alternatives below match as plain substrings (safe: these are specific
    # 2-4 character compounds, not single chars that could false-positive); the
    # English-alias alternatives keep \b since English DOES tokenize on
    # whitespace and needs the boundary protection.
    m = (re.search(r"(last|next|this)?\s?(除夕|春节|元宵节|端午节|中秋节|重阳节|腊八节"
                    r"|元旦|劳动节|五一|国庆节|十一|青年节|儿童节|教师节|建军节)", s)
         or re.search(
        r"\b(last|next|this)?\s?("
        r"chinese\s+new\s+year'?s?\s*eve|chinese\s+new\s+year|lunar\s+new\s+year"
        r"|spring\s+festival|lantern\s+festival|dragon\s+boat\s+festival"
        r"|mid-?\s?autumn\s+festival|double\s+ninth\s+festival|laba\s+festival"
        r"|labor\s+day\s*\(china\)|national\s+day(?:\s*\(china\))?"
        r"|international\s+workers'?\s+day|may\s+day|youth\s+day"
        r"|children'?s\s+day(?:\s*\(china\))?|teachers'?\s+day|army\s+day"
        r")\b", s))
    if m:
        rel, name = m.group(1), re.sub(r"\s+", " ", m.group(2)).strip()
        this_year_date = _CN_HOLIDAY_DATE(name, anchor.year)
        if this_year_date is not None:
            if rel == "last":
                d = this_year_date if this_year_date < anchor else _CN_HOLIDAY_DATE(name, anchor.year - 1)
            elif rel == "next":
                d = this_year_date if this_year_date > anchor else _CN_HOLIDAY_DATE(name, anchor.year + 1)
            elif rel == "this":
                d = this_year_date
            else:
                d = this_year_date if this_year_date <= anchor else _CN_HOLIDAY_DATE(name, anchor.year - 1)
            if d is not None:
                return d.isoformat()

    # bare "<Month>" / "mid-|early|late <Month>", NO year and NO last/next/this
    # -> nearest PAST occurrence, month precision (YYYY-MM), same reduced-
    # precision contract as "<Month> <year>". Checked AFTER every dated/directed
    # month form AND after the holiday names — so a month word inside a holiday
    # ("May Day" -> Labor Day 05-01) resolves as the holiday first, and only a
    # truly bare month falls through here. Safe for "May"/"March" because this
    # only runs on an expr the extractor already tagged as a DATE field (not free
    # text), so the modal-verb / verb readings never reach it. Was a gap
    # (2026-07-04 log: 335 bare-month None). No "next" default — a bare month in
    # past-tense narration ("we went in April") is the most recent past one.
    m = re.search(r"\b(?:mid-|early |late )?(" + "|".join(_MONTHS) + r")\b", s)
    if m:
        mon_idx = _MONTHS.index(m.group(1)) + 1
        year = anchor.year if mon_idx <= anchor.month else anchor.year - 1
        return f"{year:04d}-{mon_idx:02d}"

    # "last/next/this month|year" — deliberately checked LATE (2026-07-01 fix
    # #1): this is the LOWEST-information pattern in the whole function ("this"
    # literally just echoed the anchor date back — zero new information), so if
    # checked early it would silently "win" and shadow any more specific,
    # more relevant co-occurring pattern in the same text — verified on real
    # corpus text: "I'm glad I made the decision to host Thanksgiving dinner at
    # my place this year" used to resolve to the anchor date (via "this year")
    # instead of the actual Thanksgiving date, because "this year" was checked
    # BEFORE the holiday-name pattern. General principle: order checks from most
    # to least specific, so co-occurring triggers resolve to the more meaningful
    # one regardless of which appears first in the text.
    #
    # 2026-07-01 fix #2: "month"/"year" convey exactly that much information —
    # NOT a specific day. The old code fabricated day-precision (e.g. "last
    # month" -> the exact same day-of-month as the anchor, one month back) even
    # though "last March" (a named month) already correctly returned MONTH
    # precision ("2023-03") with no invented day — an inconsistency for
    # semantically identical inputs. Now consistent: partial-ISO, §0-honest.
    # "week" is handled by resolve_range below instead (no clean ISO-week
    # representation exists here, and a week is inherently a range, not a point).
    m = re.search(r"\b(last|next|this)\s+(month|year)\b", s)
    if m:
        rel, unit = m.group(1), m.group(2)
        delta = {"last": -1, "next": 1, "this": 0}[rel]
        target = anchor + _UNIT_DELTA[unit](delta)
        return f"{target.year:04d}" if unit == "year" else f"{target.year:04d}-{target.month:02d}"

    # Chinese "上/下/这 + 个月" (last/next/this month) — same late position and
    # same partial-ISO-precision rule as the English block directly above (no
    # fabricated day). "个" is mandatory here for the same reason it's
    # mandatory in the N-units-ago block: bare "上月"/"下月" without 个 also
    # occur in formal/written Chinese, so accepting bare 月 costs nothing here
    # (unlike the ago/later case, there's no competing "month name" reading to
    # collide with — 上/下/这 are never month-name prefixes) but 个 is included
    # as an alternative rather than assumed away in case both forms appear.
    # "上+"/"下+" (repeated-character count, see the weekday block above for
    # the same generalization): "上上个月" (month before last) is exactly as
    # deterministic as "上个月", just delta=-2 instead of -1. Capped at 10
    # repeats for the same reason as the weekday block: bounds both the work
    # (relativedelta with a huge months delta is slow, not O(1)) and the
    # result (can't overflow datetime's year range).
    m = re.search(r"(上{1,10}|下{1,10}|这)\s?个?月", s)
    if m:
        rel = m.group(1)
        delta = -len(rel) if rel[0] == "上" else (len(rel) if rel[0] == "下" else 0)
        try:
            target = anchor + _UNIT_DELTA["month"](delta)
        except (OverflowError, ValueError):
            logger.warning(
                "calendar_resolve.resolve_date: date arithmetic overflowed for "
                "expr=%r anchor=%r — returning None instead of raising", expr, anchor_date,
            )
            return None
        return f"{target.year:04d}-{target.month:02d}"

    # Chinese 去年/今年/明年 (last/this/next year) — fixed words, no 上/下/这
    # modifier, so no doubled-modifier collision to guard against. Same late
    # position as the English/上下这 month|year blocks above, for the same
    # "this year" shadowing reason (a bare "今年" carries no information beyond
    # the anchor's year and must not out-compete a more specific co-occurring
    # trigger, e.g. a holiday mentioned in the same sentence).
    if "去年" in s or "今年" in s or "明年" in s:
        delta = -1 if "去年" in s else (1 if "明年" in s else 0)
        target = anchor + _UNIT_DELTA["year"](delta)
        return f"{target.year:04d}"

    # bare year: standalone "2019" or "in/since/around 2019" -> year precision.
    m = re.fullmatch(r"\s*((?:19|20)\d{2})\s*", s) or re.search(
        r"\b(?:in|since|around|by|during|from|back in)\s+((?:19|20)\d{2})\b", s)
    if m:
        return m.group(1)

    # Chinese bare year "2019年" -> year precision (analogous to the English
    # bare-year block directly above). No \b: the CJK \b gotcha (see the
    # holiday-matching note above) would block this whenever the year is
    # embedded in a longer Chinese sentence (e.g. "那是在2019年发生的") since
    # neither side of the digit run necessarily borders a non-\w character —
    # the 4-digit-run-immediately-followed-by-年 shape is specific enough on
    # its own to not need a boundary guard. _CN_NO_DIRECTION_SUFFIX excludes
    # "2019年前/后" ("before/after 2019", a direction not a point) — mirrors
    # the English bare-year pattern, which likewise doesn't treat
    # "before"/"after" as a trigger word.
    m = re.search(r"((?:19|20)\d{2})\s?年" + _CN_NO_DIRECTION_SUFFIX, s)
    if m:
        return m.group(1)

    # bare ordinal day "the 15th" / "on the 3rd" -> anchor month/year, day precision.
    m = re.search(r"\b(?:on\s+)?the\s+(\d{1,2})(?:st|nd|rd|th)\b", s)
    if m:
        day = int(m.group(1))
        if 1 <= day <= 31:
            try:
                return date(anchor.year, anchor.month, day).isoformat()
            except ValueError:
                return None

    # slash numeric date "2/14" / "3/4/24" (US M/D[/Y]). Day disambiguation:
    # if the first field >12 it must be the day (D/M). Day precision.
    m = re.search(r"(?<!\d)(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?!\d)", s)
    if m:
        a, b, yr = int(m.group(1)), int(m.group(2)), m.group(3)
        mon, day = (b, a) if (a > 12 and b <= 12) else (a, b)
        year = anchor.year if not yr else (int(yr) + 2000 if int(yr) < 100 else int(yr))
        try:
            return date(year, mon, day).isoformat()
        except ValueError:
            return None

    # hyphen numeric date "04-07" / "4-7-24" (US M-D[-Y]). Day disambiguation as for
    # slash. The `(?<!\d{4}-)` guard excludes the M-D inside an ISO date (2023-04-07 /
    # 2023-04), which must not be misread as a bare M-D.
    m = re.search(r"(?<!\d)(?<!\d{4}-)(\d{1,2})-(\d{1,2})(?:-(\d{2,4}))?(?!\d)", s)
    if m:
        a, b, yr = int(m.group(1)), int(m.group(2)), m.group(3)
        mon, day = (b, a) if (a > 12 and b <= 12) else (a, b)
        year = anchor.year if not yr else (int(yr) + 2000 if int(yr) < 100 else int(yr))
        try:
            return date(year, mon, day).isoformat()
        except ValueError:
            return None

    # clock-only "at 7pm" / "9:16" with no date -> anchor date + time (minute prec).
    m = re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", s)
    if not m:
        m = re.search(r"\b(\d{1,2}):(\d{2})\b", s)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2) or 0)
        ampm = m.group(3) if (m.lastindex or 0) >= 3 else None
        if ampm == "pm" and hh != 12:
            hh += 12
        elif ampm == "am" and hh == 12:
            hh = 0
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{anchor.isoformat()}T{hh:02d}:{mm:02d}"

    return None


def iso_precision(iso: str) -> str:
    """Granularity implied by a (possibly partial) ISO string, per §0:
    'YYYY'->year, 'YYYY-MM'->month, 'YYYY-MM-DD'->day, with a time part->minute."""
    if not iso:
        return "day"
    if "T" in iso:
        return "second" if iso.count(":") >= 2 else "minute"
    n = len(iso)
    if n == 4:
        return "year"
    if n == 7:
        return "month"
    return "day"


def resolve_range(expr: str, anchor_date: str) -> Optional[tuple[str, str]]:
    """Resolve a relative RANGE phrase to (start_iso, end_iso) against the anchor.

    Forms: "the/over the/in the past|last [N] day|week|month|year(s)" and
    "for [N|a] day|week|month|year(s)" (a state's running duration). N defaults
    to 1. End is the anchor (now). "last/this/next week" (bare, no explicit N)
    is the ISO calendar week (Monday-Sunday) — see below. None when unrecognized."""
    anchor = _to_date(anchor_date)
    if anchor is None or not expr:
        return None
    s = expr.strip().lower()

    # Bare "last/this/next week" (2026-07-01, user directive: unify all three
    # to the ISO calendar week). Checked BEFORE the rolling-window pattern below
    # so it isn't swallowed by it; the \bweek\b boundary (no digit in between)
    # means "last 3 weeks" does NOT match here and still gets the rolling
    # multi-week window from the generic pattern (a genuine duration, unlike a
    # single named week, which has no natural "3 calendar weeks ago" reading).
    m = re.search(r"\b(last|next|this)\s+week\b", s)
    if m:
        this_monday = anchor - timedelta(days=anchor.weekday())  # Monday=0
        offset = {"last": -7, "next": 7, "this": 0}[m.group(1)]
        start = this_monday + timedelta(days=offset)
        end = start + timedelta(days=6)
        return (start.isoformat(), end.isoformat())

    # Chinese bare "上/下/这 + 周|星期|礼拜" (last/next/this week), unified to
    # the ISO calendar week exactly like the English block above. "上+"/"下+"
    # is a repeated-character COUNT, not a fixed single-vs-not case: "上上周"
    # (the week before last) is exactly as deterministic as "上周" — same
    # calendar-week math, one more week back (see the resolve_date directed-
    # weekday block for the identical generalization and its rationale).
    # Still requires the modifier NOT be immediately followed by a weekday
    # char (negative lookahead) so this never fires on "上周二" (a POINT date
    # handled by resolve_date instead of a range) — specific-beats-general,
    # same principle used throughout. Also excludes 末/年/期 immediately after
    # "周": those form different WORDS entirely — 周末 (weekend, a 2-day
    # sub-range not the whole week), 周年 (anniversary, not a week reference
    # at all), 周期 (cycle/period, likewise unrelated) — found by adversarial
    # testing (2026-07-01): "下下周末" was silently misread as "the whole
    # Mon-Sun range 2 weeks out" instead of declining to match a distinct word.
    # Repeat count capped at 10 for the same overflow/performance reason as
    # resolve_date's weekday and month blocks above.
    m = re.search(r"(上{1,10}|下{1,10}|这)\s?(?:周|星期|礼拜)(?!\s?[一二三四五六日天末年期])", s)
    if m:
        this_monday = anchor - timedelta(days=anchor.weekday())
        rel = m.group(1)
        offset = -7 * len(rel) if rel[0] == "上" else (7 * len(rel) if rel[0] == "下" else 0)
        try:
            start = this_monday + timedelta(days=offset)
        except OverflowError:
            logger.warning(
                "calendar_resolve.resolve_range: date arithmetic overflowed for "
                "expr=%r anchor=%r — returning None instead of raising", expr, anchor_date,
            )
            return None
        end = start + timedelta(days=6)
        return (start.isoformat(), end.isoformat())

    # N is unbounded here too (\d+) — same overflow risk and same try/except
    # fix as resolve_date's "N units ago" blocks (found by adversarial
    # testing 2026-07-01: "the past 99999999999 days" overflows uncaught).
    m = re.search(
        r"\b(?:over\s+|in\s+|for\s+)?(?:the\s+)?(?:past|last)\s+(\d+)?\s*(day|week|month|year)s?\b", s)
    if not m:
        m = re.search(r"\bfor\s+(?:(\d+)|an?|several|many)\s+(day|week|month|year)s?\b", s)
    if m:
        n = int(m.group(1)) if m.group(1) else 1
        try:
            start = anchor + _UNIT_DELTA[m.group(2)](-n)
        except (OverflowError, ValueError):
            logger.warning(
                "calendar_resolve.resolve_range: date arithmetic overflowed for "
                "expr=%r anchor=%r — returning None instead of raising", expr, anchor_date,
            )
            return None
        return (start.isoformat(), anchor.isoformat())

    # Chinese "过去N天/日/(个)周/(个)星期/(个)礼拜/(个)月/年" (rolling-window
    # duration, mirrors the English "the past/last N units" pattern directly
    # above — a genuine DURATION, distinct from the bare calendar-week form
    # checked earlier). N defaults to 1 when omitted ("过去一个月" == 1 month),
    # same convention as the English "for a/an <unit>" fallback above. "个" is
    # optional for every unit here (unlike resolve_date's ago/later block,
    # there's no competing "month name" reading for bare 月 to collide with,
    # since 过去 only ever introduces a duration, never a month name).
    m = re.search(
        r"过去\s?(\d{1,2}|[零一二两三四五六七八九十]{1,3})?\s?个?(天|日|周|星期|礼拜|月|年)", s)
    if m:
        n = _cn_number(m.group(1)) if m.group(1) else 1
        unit = _CN_UNIT_MAP.get(m.group(2))
        if n is not None and unit:
            start = anchor + _UNIT_DELTA[unit](-n)
            return (start.isoformat(), anchor.isoformat())
    return None
