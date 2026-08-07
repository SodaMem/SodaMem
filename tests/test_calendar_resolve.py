"""resolve_date unit coverage — regression for canonical forms + the
day-precision additions (batch 1a): ordinal suffix, with-year absolute,
day-first, weekend, and "a/an <unit> ago".

Anchor 2023-06-15 is a Thursday. resolve_date returns YYYY-MM-DD or None.
"""

from sodamem.memory.ingest.calendar_resolve import resolve_date, resolve_range, iso_precision, _to_date

A = "2023-06-15"  # Thursday, June


# --- regression: the REAL production anchor format, not the clean-ISO test
# fixture. client.py passes session_date straight through to the extractor as
# "YYYY/MM/DD (Day) HH:MM" (e.g. "2023/03/04 (Sat) 16:45"). dateutil.parse chokes
# on the "(Day)" parenthetical and _to_date silently returned None for EVERY such
# anchor — meaning resolve_date/resolve_range were 100% dead in every real ingest
# run despite 16+ passing tests here, because those tests never exercised this
# format. This is the anchor format the extractor actually uses in production —
# do not silently regress it back to only testing clean ISO anchors.
PROD_ANCHOR = "2023/03/04 (Sat) 16:45"


def test_to_date_parses_production_benchmark_format():
    assert _to_date(PROD_ANCHOR) == __import__("datetime").date(2023, 3, 4)


def test_resolve_date_works_with_production_anchor_format():
    assert resolve_date("today", PROD_ANCHOR) == "2023-03-04"
    # anchor Thu 2023-03-09 (Day) format -> "last Saturday" = 2023-03-04 (real calendar)
    assert resolve_date("last Saturday", "2023/03/09 (Thu) 15:47") == "2023-03-04"


def test_resolve_range_works_with_production_anchor_format():
    assert resolve_range("the past month", PROD_ANCHOR) == ("2023-02-04", "2023-03-04")
    assert resolve_range("for 10 years", PROD_ANCHOR) == ("2013-03-04", "2023-03-04")


# --- named holidays (user directive 2026-07-01): fixed-date + rule-based only.
# Seasons and couple/few/several are explicitly OUT of scope (real-corpus false
# positives for seasons; couple/few folded into the unresolved recently/lately
# bucket) — do not add them here without a fresh decision.

def test_holiday_fixed_date():
    assert resolve_date("Christmas", "2023-06-22") == "2022-12-25"  # bare -> nearest past
    assert resolve_date("Christmas Day", "2023-06-22") == "2022-12-25"
    assert resolve_date("New Year's Day", "2023-06-22") == "2023-01-01"
    assert resolve_date("New Year's Eve", "2023-06-22") == "2022-12-31"
    assert resolve_date("Halloween", "2023-06-22") == "2022-10-31"
    assert resolve_date("Valentine's day", "2023-06-22") == "2023-02-14"
    assert resolve_date("Valentines day", "2023-06-22") == "2023-02-14"  # no apostrophe
    assert resolve_date("Independence Day", "2023-06-22") == "2022-07-04"


def test_holiday_rule_based_matches_real_calendar():
    # Real-world Thanksgiving: 2022-11-24, 2023-11-23 (weekday shifts year to year;
    # this cross-checks the Nth-weekday-of-month computation against known facts).
    assert resolve_date("Thanksgiving", "2023-06-22") == "2022-11-24"   # bare -> nearest past
    assert resolve_date("last Thanksgiving", "2023-06-22") == "2022-11-24"
    assert resolve_date("next Thanksgiving", "2023-06-22") == "2023-11-23"
    assert resolve_date("Mother's day", "2023-06-22") == "2023-05-14"
    assert resolve_date("Father's day", "2023-06-22") == "2023-06-18"
    assert resolve_date("Labor Day", "2023-06-22") == "2022-09-05"
    assert resolve_date("Memorial Day", "2023-06-22") == "2023-05-29"
    assert resolve_date("MLK Day", "2023-06-22") == "2023-01-16"


def test_weak_this_year_never_shadows_a_specific_co_occurring_trigger():
    # 2026-07-01 real-corpus finding: "last/next/this week|month|year" was
    # checked EARLY in resolve_date and its "this" branch just echoes the
    # anchor back (zero new information) — when co-occurring with a more
    # specific trigger in the same text, it used to silently win and return
    # today's date instead of the actually-relevant one. Real example:
    # "I'm glad I made the decision to host Thanksgiving dinner at my place
    # this year" used to resolve to the anchor (2023-09-15), not Thanksgiving.
    A = "2023-09-15"  # Friday
    result = resolve_date(
        "I'm glad I made the decision to host Thanksgiving dinner at my place "
        "this year, as it encouraged me to take better care of my skin", A,
    )
    assert result != A, "must not silently fall back to today's date via the weak 'this year' trigger"
    assert result == "2022-11-24"  # the holiday match wins; bare mention -> nearest past
    # Single-trigger "this/last/next month|year" — regression guard on the
    # reorder itself, updated for the partial-ISO precision fix (2026-07-01
    # fix #2): month/year convey exactly that much precision, no invented day.
    assert resolve_date("this month", A) == "2023-09"
    assert resolve_date("last year", A) == "2022"
    assert resolve_date("next month", A) == "2023-10"
    # "week" is a range concept (ISO calendar week), not a resolve_date point —
    # see test_week_is_iso_calendar_week below.
    assert resolve_date("this week", A) is None


def test_holiday_embedded_in_full_sentence():
    # Real corpus example: "...moisturize more regularly since Thanksgiving, when
    # I first noticed..." — holidays use re.search like yesterday/last-<weekday>,
    # not re.fullmatch like today/now, since holiday names are specific enough
    # not to false-positive on ordinary text.
    assert resolve_date(
        "I've been doing X since Thanksgiving, when I first noticed Y", "2023-12-18"
    ) == "2023-11-23"


def test_season_and_couple_few_remain_unresolved_by_design():
    # Explicitly NOT implemented — do not add without a fresh user decision.
    assert resolve_date("last summer", "2023-06-22") is None
    assert resolve_date("a few weeks ago", "2023-06-22") is None
    assert resolve_date("a couple of days ago", "2023-06-22") is None


# --- Chinese holidays (user directive 2026-07-01): fixed Gregorian + lunar.
# The lunar-holiday expected values are real, independently-verifiable public
# historical dates (not derived from our own code) — this is a genuine
# cross-check of the vendored 1900-2100 lunar table + conversion, the same
# pattern used for the US Thanksgiving cross-check above.

def test_cn_fixed_gregorian_holidays():
    assert resolve_date("元旦", "2023-06-22") == "2023-01-01"
    assert resolve_date("劳动节", "2023-06-22") == "2023-05-01"
    assert resolve_date("National Day", "2023-06-22") == "2022-10-01"  # bare -> nearest past
    assert resolve_date("儿童节", "2023-06-22") == "2023-06-01"
    assert resolve_date("May Day", "2023-06-22") == "2023-05-01"


def test_cn_lunar_holidays_match_real_calendar():
    # Real, publicly-verifiable 2023 lunar holiday dates.
    assert resolve_date("春节", "2023-06-22") == "2023-01-22"
    assert resolve_date("Chinese New Year", "2023-06-22") == "2023-01-22"
    assert resolve_date("Spring Festival", "2023-06-22") == "2023-01-22"
    assert resolve_date("元宵节", "2023-06-22") == "2023-02-05"
    assert resolve_date("端午节", "2023-06-22") == "2023-06-22"
    assert resolve_date("Dragon Boat Festival", "2023-06-22") == "2023-06-22"
    assert resolve_date("中秋节", "2023-12-01") == "2023-09-29"
    assert resolve_date("Mid-Autumn Festival", "2023-12-01") == "2023-09-29"
    assert resolve_date("重阳节", "2023-12-01") == "2023-10-23"
    assert resolve_date("腊八节", "2024-02-01") == "2024-01-18"
    assert resolve_date("除夕", "2023-06-22") == "2023-01-21"  # day before 春节


def test_cn_holiday_embedded_in_english_sentence():
    assert resolve_date(
        "We celebrated 春节 together with the whole family", "2023-06-22"
    ) == "2023-01-22"
    assert resolve_date("I love Dragon Boat Festival food", "2023-06-22") == "2023-06-22"


def test_cn_holiday_last_next_qualifiers():
    assert resolve_date("last Spring Festival", "2023-06-22") == "2023-01-22"
    assert resolve_date("next Spring Festival", "2023-06-22") == "2024-02-10"


# --- regression: canonical forms must keep working ------------------------

def test_regression_canonical_forms():
    assert resolve_date("today", A) == "2023-06-15"
    assert resolve_date("yesterday", A) == "2023-06-14"
    assert resolve_date("tomorrow", A) == "2023-06-16"
    assert resolve_date("2 weeks ago", A) == "2023-06-01"
    assert resolve_date("in 3 days", A) == "2023-06-18"
    assert resolve_date("last month", A) == "2023-05"  # month precision (2026-07-01 fix)
    # 2026-07-01 fix: was "2023-06-13" (nearest-past-Tuesday from Thursday,
    # which lands in THIS week, not "last week" — see
    # test_weekday_iso_week_bound_matches_week_range for the full bug story).
    assert resolve_date("last Tuesday", A) == "2023-06-06"
    assert resolve_date("end of next month", A) == "2023-07-31"
    # bare "Month day" with no year/ordinal (the pre-existing path)
    assert resolve_date("March 14", A) == "2023-03-14"


# --- NEW: ordinal suffix (was the 183/190 miss bug) -----------------------

def test_ordinal_suffix_month_day():
    assert resolve_date("June 10th", A) == "2023-06-10"
    assert resolve_date("February 10th", A) == "2023-02-10"
    assert resolve_date("January 15th", A) == "2023-01-15"
    assert resolve_date("the meeting is on August 3rd", A) == "2023-08-03"


# --- NEW: explicit year + day-first ---------------------------------------

def test_absolute_with_year_and_day_first():
    assert resolve_date("June 10, 2023", A) == "2023-06-10"
    assert resolve_date("June 10th 2022", A) == "2022-06-10"
    assert resolve_date("10th of June", A) == "2023-06-10"
    assert resolve_date("3rd of March 2021", A) == "2021-03-03"


# --- NEW: weekend ---------------------------------------------------------

def test_weekend():
    assert resolve_date("last weekend", A) == "2023-06-10"   # Sat before Thu 6/15
    assert resolve_date("this weekend", A) == "2023-06-17"   # upcoming Sat


# --- NEW: "a/an <unit> ago" (no digit, n=1) -------------------------------

def test_a_unit_ago():
    assert resolve_date("a month ago", A) == "2023-05-15"
    assert resolve_date("a week ago", A) == "2023-06-08"
    assert resolve_date("a year ago", A) == "2022-06-15"


# --- negatives: fuzzy / out-of-scope must stay None -----------------------

def test_fuzzy_and_out_of_scope_stay_none():
    assert resolve_date("recently", A) is None
    assert resolve_date("last summer", A) is None
    assert resolve_date("a few weeks ago", A) is None   # "few" between a/unit -> fuzzy
    assert resolve_date("an hour ago", A) is None        # hour is not a date unit
    assert resolve_date("", A) is None
    assert resolve_date("once upon a time", A) is None


# --- 1b: month / year precision (partial ISO) -----------------------------

def test_month_and_year_precision():
    assert resolve_date("June 2023", A) == "2023-06"        # Month YYYY -> month
    assert resolve_date("last March", A) == "2023-03"       # already passed this year
    assert resolve_date("next March", A) == "2024-03"       # future -> next year
    assert resolve_date("this December", A) == "2023-12"
    assert resolve_date("in 2019", A) == "2019"             # bare year
    assert resolve_date("2020", A) == "2020"                # standalone year


def test_bare_month_year_no_longer_fabricates_a_day():
    # 2026-07-01 fix #2: "last/this/next month|year" used to fabricate a full
    # day-precision date (echoing the anchor's day-of-month) even though the
    # phrase itself only conveys month/year granularity — inconsistent with
    # "last March" (a named month), which already correctly gave month
    # precision with no invented day. A is "2023-06-15".
    assert resolve_date("this month", A) == "2023-06"
    assert resolve_date("last month", A) == "2023-05"
    assert resolve_date("next month", A) == "2023-07"
    assert resolve_date("this year", A) == "2023"
    assert resolve_date("last year", A) == "2022"
    assert resolve_date("next year", A) == "2024"


def test_week_is_iso_calendar_week():
    # 2026-07-01, user directive: unify last/this/next week to the ISO calendar
    # week (Monday-Sunday) via resolve_range — "week" is inherently a range, not
    # a single day, and this makes bare "last week" consistent with "this/next
    # week" (which have no sensible single-point reading at all). resolve_date
    # no longer resolves bare "week" phrases (they return None there).
    A = "2023-09-15"  # Friday; this ISO week = Mon 09-11 .. Sun 09-17
    assert resolve_range("last week", A) == ("2023-09-04", "2023-09-10")
    assert resolve_range("this week", A) == ("2023-09-11", "2023-09-17")
    assert resolve_range("next week", A) == ("2023-09-18", "2023-09-24")
    assert resolve_date("last week", A) is None
    assert resolve_date("this week", A) is None
    assert resolve_date("next week", A) is None
    # A multi-week DURATION ("last 3 weeks") is a genuinely different concept
    # (a rolling window, not a single calendar week) and must be unaffected.
    assert resolve_range("last 3 weeks", A) == ("2023-08-25", "2023-09-15")
    assert resolve_range("the past 2 weeks", A) == ("2023-09-01", "2023-09-15")


def test_bare_ordinal_day_uses_anchor_month():
    assert resolve_date("the 15th", A) == "2023-06-15"
    assert resolve_date("on the 3rd", A) == "2023-06-03"


def test_clock_only_anchors_to_session_date():
    assert resolve_date("at 7pm", A) == "2023-06-15T19:00"
    assert resolve_date("9:16", A) == "2023-06-15T09:16"
    assert resolve_date("at 12pm", A) == "2023-06-15T12:00"   # noon
    assert resolve_date("at 12am", A) == "2023-06-15T00:00"   # midnight


def test_iso_precision():
    assert iso_precision("2019") == "year"
    assert iso_precision("2023-06") == "month"
    assert iso_precision("2023-06-14") == "day"
    assert iso_precision("2023-06-15T19:00") == "minute"


def test_resolve_range():
    assert resolve_range("the past 3 months", A) == ("2023-03-15", "2023-06-15")
    assert resolve_range("in the past month", A) == ("2023-05-15", "2023-06-15")
    assert resolve_range("for 10 years", A) == ("2013-06-15", "2023-06-15")
    assert resolve_range("last summer", A) is None


# --- P0 corrective sweep: slash dates, bare weekday, tonight, period endpoints ---

def test_slash_numeric_dates():
    assert resolve_date("2/14", A) == "2023-02-14"        # US M/D
    assert resolve_date("04/07", A) == "2023-04-07"
    assert resolve_date("13/4", A) == "2023-04-13"        # 13>12 -> day (D/M)
    assert resolve_date("3/4/24", A) == "2024-03-04"      # 2-digit year -> 20YY
    assert resolve_date("12/25", A) == "2023-12-25"


def test_hyphen_numeric_dates():
    assert resolve_date("04-07", A) == "2023-04-07"       # US M-D
    assert resolve_date("4-7", A) == "2023-04-07"
    assert resolve_date("13-04", A) == "2023-04-13"       # 13>12 -> day (D-M)
    assert resolve_date("04-07-24", A) == "2024-04-07"    # 2-digit year
    assert resolve_date("on 12-25", A) == "2023-12-25"
    # MUST NOT misread an ISO date's month-day as a bare M-D
    assert resolve_date("2023-04-07", A) is None
    assert resolve_date("2023-04", A) is None


def test_bare_weekday_nearest_past():
    # anchor 2023-06-15 is a Thursday
    assert resolve_date("on Tuesday", A) == "2023-06-13"
    assert resolve_date("on Saturday", A) == "2023-06-10"


def test_tonight_is_anchor_day():
    assert resolve_date("tonight", A) == "2023-06-15"


def test_period_endpoints_year_and_week():
    assert resolve_date("end of the year", A) == "2023-12-31"
    assert resolve_date("start of the year", A) == "2023-01-01"
    assert resolve_date("end of this week", A) == "2023-06-18"    # Sunday
    assert resolve_date("start of this week", A) == "2023-06-12"  # Monday


# --- Chinese relative-time grammar (2026-07-01, user directive: full parity
# with the English coverage above). CN_A is a Friday so weekday-direction
# tests ("last/next Tuesday" style) aren't degenerate the way a same-day
# anchor would make them; PROD_ANCHOR (with its "(Day)" parenthetical) is
# reused in a couple of cases to guard the _to_date bug on the Chinese path too.

CN_A = "2023/09/15 (Fri) 12:00"  # ISO week = Mon 09-11 .. Sun 09-17


def test_cn_fixed_point_words():
    assert resolve_date("今天", CN_A) == "2023-09-15"
    assert resolve_date("昨天", CN_A) == "2023-09-14"
    assert resolve_date("前天", CN_A) == "2023-09-13"
    assert resolve_date("明天", CN_A) == "2023-09-16"
    assert resolve_date("后天", CN_A) == "2023-09-17"
    assert resolve_date("今晚", CN_A) == "2023-09-15"
    assert resolve_date("现在", CN_A) == "2023-09-15"


def test_cn_n_units_ago_and_later_arabic_and_cn_numeral():
    assert resolve_date("一天前", CN_A) == "2023-09-14"
    assert resolve_date("三天前", CN_A) == "2023-09-12"
    assert resolve_date("3天前", CN_A) == "2023-09-12"
    assert resolve_date("两周前", CN_A) == "2023-09-01"
    assert resolve_date("三个月前", CN_A) == "2023-06-15"
    assert resolve_date("3个月前", CN_A) == "2023-06-15"
    assert resolve_date("一年前", CN_A) == "2022-09-15"
    assert resolve_date("十年前", CN_A) == "2013-09-15"
    assert resolve_date("一天后", CN_A) == "2023-09-16"
    assert resolve_date("三周后", CN_A) == "2023-10-06"
    assert resolve_date("两个月后", CN_A) == "2023-11-15"


def test_cn_ago_pattern_does_not_misread_a_4digit_year_or_a_monthday():
    # "2019年前" must NOT be read as "19 years ago" via a partial digit-run
    # match, and "9月10日前" ("before Sept 10") must not be read as "10 days
    # ago" — both are real collisions found and fixed during implementation.
    assert resolve_date("2019年前", CN_A) is None
    assert resolve_date("9月10日前", CN_A) is None


def test_cn_month_and_year_words_partial_iso_precision():
    # Mirrors the English "last/next/this month|year" fix: no fabricated day.
    assert resolve_date("上个月", CN_A) == "2023-08"
    assert resolve_date("这个月", CN_A) == "2023-09"
    assert resolve_date("下个月", CN_A) == "2023-10"
    assert resolve_date("上月", CN_A) == "2023-08"  # bare 月 without 个 also works
    assert resolve_date("去年", CN_A) == "2022"
    assert resolve_date("今年", CN_A) == "2023"
    assert resolve_date("明年", CN_A) == "2024"


def test_cn_repeated_modifier_generalizes_the_count():
    # 2026-07-01 correction: 上上周/上上个月 (week/month BEFORE last) were
    # initially left unresolved on the theory that they have "no clean
    # single-word English equivalent" — wrong reasoning, since this is
    # deterministic calendar arithmetic, not word-matching. "上+"/"下+" is a
    # repeated-character COUNT: each extra 上/下 is one more calendar unit in
    # that direction. CN_A's ISO week is Mon 09-11..Sun 09-17.
    assert resolve_date("上上个月", CN_A) == "2023-07"   # anchor month - 2
    assert resolve_date("下下个月", CN_A) == "2023-11"   # anchor month + 2
    assert resolve_date("上上上个月", CN_A) == "2023-06"  # generalizes to N=3
    assert resolve_range("上上周", CN_A) == ("2023-08-28", "2023-09-03")  # 2 ISO weeks back
    assert resolve_range("下下周", CN_A) == ("2023-09-25", "2023-10-01")  # 2 ISO weeks forward
    assert resolve_range("上上上周", CN_A) == ("2023-08-21", "2023-08-27")  # generalizes to N=3
    # a directed weekday inside a doubled modifier is still a POINT date via
    # resolve_date (not a range) — resolve_range correctly declines it.
    assert resolve_range("上上周二", CN_A) is None
    # 2 ISO weeks before/after the *target week's* Monday+weekday-offset — see
    # test_cn_weekday_iso_week_bound below for the underlying bug this
    # composes on top of (an adversarial workflow found the pre-correction
    # values here were themselves wrong: they were derived from the buggy
    # single-count "上周一"/"下周五" via relativedelta nearest-occurrence,
    # which don't actually land in resolve_range's own "上周"/"下周" ranges).
    assert resolve_date("上上周一", CN_A) == "2023-08-28"
    assert resolve_date("下下周五", CN_A) == "2023-09-29"


def test_cn_weekday_iso_week_bound():
    # 2026-07-01: an adversarial verification workflow found a REAL bug (not
    # Chinese-specific — the identical bug existed in the English "last/next/
    # this <weekday>" block too, see test_weekday_iso_week_bound_matches_week_range
    # below): the directed-weekday resolver used relativedelta's "nearest
    # occurrence relative to the ANCHOR DATE" search, which for an anchor
    # whose own weekday falls AFTER the target (for 上) or BEFORE it (for 这)
    # within the week silently lands in the WRONG ISO week — internally
    # self-contradicting resolve_range's own "上周"/"这周"/"下周" definition
    # (e.g. the old "上周一" == anchor's own Monday-of-this-week, which isn't
    # even inside resolve_range("上周", CN_A)'s range). Fixed by computing
    # directly off the target week's Monday (this_monday + week_offset +
    # weekday_index), exactly matching resolve_range's formula — the two
    # can no longer disagree. CN_A is a Friday (this week Mon 09-11..Sun 09-17).
    assert resolve_date("上周一", CN_A) == "2023-09-04"
    assert resolve_date("上周三", CN_A) == "2023-09-06"  # inside 上周's own range
    assert resolve_date("上周五", CN_A) == "2023-09-08"  # NOT the anchor itself anymore
    assert resolve_date("下周一", CN_A) == "2023-09-18"
    assert resolve_date("下周五", CN_A) == "2023-09-22"
    assert resolve_date("下周六", CN_A) == "2023-09-23"  # inside 下周's own range
    assert resolve_date("下周日", CN_A) == "2023-09-24"
    # "这周X" stays inside the ANCHOR'S OWN week for every weekday, not just
    # ones after the anchor (the old formula jumped Monday-Thursday into next
    # week from a Friday anchor).
    assert resolve_date("这周一", CN_A) == "2023-09-11"
    assert resolve_date("这周二", CN_A) == "2023-09-12"
    assert resolve_date("这周三", CN_A) == "2023-09-13"
    assert resolve_date("这周四", CN_A) == "2023-09-14"
    # every directed result must fall inside the SAME range resolve_range
    # returns for the identical modifier — this is the actual invariant that
    # was broken; assert it directly rather than just pinning point values.
    last_start, last_end = resolve_range("上周", CN_A)
    assert last_start <= resolve_date("上周一", CN_A) <= last_end
    assert last_start <= resolve_date("上周五", CN_A) <= last_end
    next_start, next_end = resolve_range("下周", CN_A)
    assert next_start <= resolve_date("下周一", CN_A) <= next_end
    assert next_start <= resolve_date("下周日", CN_A) <= next_end
    this_start, this_end = resolve_range("这周", CN_A)
    assert this_start <= resolve_date("这周一", CN_A) <= this_end
    assert this_start <= resolve_date("这周四", CN_A) <= this_end


def test_weekday_iso_week_bound_matches_week_range():
    # English side of the same 2026-07-01 bug fix. A is a Thursday (this week
    # Mon 06-12..Sun 06-18) — chosen because Thursday-anchor examples in the
    # PROD_ANCHOR test below happen to not expose the bug (target weekday
    # before the anchor's own within the week), so this uses a case that does.
    last_start, last_end = resolve_range("last week", A)
    assert last_start <= resolve_date("last Wednesday", A) <= last_end
    assert resolve_date("last Wednesday", A) == "2023-06-07"  # not 06-14 (this week)
    this_start, this_end = resolve_range("this week", A)
    assert this_start <= resolve_date("this Monday", A) <= this_end
    assert resolve_date("this Monday", A) == "2023-06-12"  # not 06-19 (next week)
    next_start, next_end = resolve_range("next week", A)
    assert next_start <= resolve_date("next Tuesday", A) <= next_end


def test_cn_weekday_directed_and_bare():
    # CN_A is a Friday.
    assert resolve_date("上周一", CN_A) == "2023-09-04"
    assert resolve_date("下周一", CN_A) == "2023-09-18"
    assert resolve_date("这周三", CN_A) == "2023-09-13"  # stays in the anchor's own week
    assert resolve_date("星期二", CN_A) == "2023-09-12"  # bare -> nearest past
    assert resolve_date("周二", CN_A) == "2023-09-12"
    assert resolve_date("礼拜四", CN_A) == "2023-09-14"


def test_cn_bare_week_declines_weekend_and_anniversary_and_cycle():
    # 2026-07-01: an adversarial workflow found 周末 (weekend)/周年
    # (anniversary)/周期 (cycle) are distinct WORDS silently swallowed by the
    # bare-week range pattern (e.g. "下下周末" misread as the whole two-weeks-
    # out Mon-Sun range instead of declining to match a different word).
    assert resolve_range("下下周末我们出去玩", CN_A) is None
    assert resolve_range("上上周末我们出去玩", CN_A) is None
    assert resolve_range("上上周年纪念日", CN_A) is None
    assert resolve_range("这周末", CN_A) is None
    assert resolve_range("上周期", CN_A) is None
    # legitimate bare-week forms are unaffected by the exclusion.
    assert resolve_range("上周", CN_A) == ("2023-09-04", "2023-09-10")
    assert resolve_range("这周", CN_A) == ("2023-09-11", "2023-09-17")


def test_cn_repeated_modifier_never_raises_or_hangs_on_pathological_input():
    # 2026-07-01: an adversarial workflow found the (then-unbounded) "上+"/
    # "下+" quantifier let a pathological run of repeated characters (a) push
    # relativedelta/timedelta arithmetic past datetime's year range, raising
    # an uncaught ValueError/OverflowError, and (b) take tens of seconds
    # (~28s at 50,000 reps) since the underlying date arithmetic isn't O(1) in
    # the delta size. Both fixed by capping the repeat count at 10 in the
    # regex itself. This must stay fast and never raise, however large the
    # input — the module's own contract ("Returns None ... never a guess")
    # implies "never crashes," matching the existing _to_date precedent.
    import time
    huge = "上" * 200_000 + "周三"
    t0 = time.time()
    result = resolve_date(huge, CN_A)
    assert time.time() - t0 < 2.0
    assert result is not None  # the first 10 repeats still resolve normally
    huge_range = "上" * 200_000 + "周"
    t0 = time.time()
    result_range = resolve_range(huge_range, CN_A)
    assert time.time() - t0 < 2.0
    assert result_range is not None


def test_english_n_units_ago_never_raises_on_pathological_n():
    # 2026-07-01: a follow-up adversarial audit found the PRE-EXISTING
    # (English, unbounded \d+) "N days/weeks/months/years ago/later" and "in
    # N units" (resolve_date) and "the past N units" (resolve_range) patterns
    # had the identical overflow risk as the Chinese repeated-modifier bug
    # above, just via an arbitrarily large digit string instead of an
    # arbitrarily long character run — e.g. "9999999999999999 days ago"
    # raises an uncaught OverflowError, and "99999999 years ago" raises an
    # uncaught ValueError (year out of datetime's 1..9999 range). Unlike the
    # Chinese fix, capping the regex digit count would incorrectly reject
    # legitimate large-but-valid values ("3650 days ago" doesn't overflow),
    # so the fix here is a try/except guard instead, matching _to_date's
    # existing precedent.
    assert resolve_date("9999999999999999 days ago", A) is None
    assert resolve_date("99999999 years ago", A) is None
    assert resolve_date("in 99999999 years", A) is None
    assert resolve_range("the past 99999999999 days", A) is None
    # legitimate large values still work correctly (not over-restricted).
    assert resolve_date("3650 days ago", A) == "2013-06-17"
    assert resolve_date("in 4 weeks", "2026-06-17") == "2026-07-15"


def test_cn_absolute_dates():
    assert resolve_date("九月十日", CN_A) == "2023-09-10"
    assert resolve_date("9月10日", CN_A) == "2023-09-10"
    assert resolve_date("9月10号", CN_A) == "2023-09-10"
    assert resolve_date("2023年9月10日", CN_A) == "2023-09-10"
    assert resolve_date("2023年9月", CN_A) == "2023-09"
    assert resolve_date("2019年", CN_A) == "2019"
    assert resolve_date("那是在2019年发生的", CN_A) == "2019"  # CJK \b gotcha guard


def test_cn_relative_year_combined_with_absolute_monthday():
    # "去年9月10日" must keep DAY precision with the year correctly overridden,
    # not collapse to bare year precision (a real bug found during
    # implementation: the bare 去年 check alone discarded the month/day).
    assert resolve_date("去年9月10日", CN_A) == "2022-09-10"
    assert resolve_date("今年9月10日", CN_A) == "2023-09-10"
    assert resolve_date("明年9月10日", CN_A) == "2024-09-10"
    # a distant (non-adjacent) modifier is out of scope, same residual gap as
    # English's own "<Month> <day>" form with an implied external year.
    assert resolve_date("去年的时候，是9月10日发生的", CN_A) == "2023-09-10"


def test_cn_bare_week_is_iso_calendar_week():
    assert resolve_range("上周", CN_A) == ("2023-09-04", "2023-09-10")
    assert resolve_range("这周", CN_A) == ("2023-09-11", "2023-09-17")
    assert resolve_range("下周", CN_A) == ("2023-09-18", "2023-09-24")
    # a directed weekday ("上周二") is a POINT date via resolve_date, not a
    # range — resolve_range must not also claim it as a whole-week range.
    assert resolve_range("上周二", CN_A) is None


def test_cn_past_n_units_range():
    assert resolve_range("过去一周", CN_A) == ("2023-09-08", "2023-09-15")
    assert resolve_range("过去三个月", CN_A) == ("2023-06-15", "2023-09-15")
    assert resolve_range("过去两年", CN_A) == ("2021-09-15", "2023-09-15")
    assert resolve_range("过去5天", CN_A) == ("2023-09-10", "2023-09-15")
    assert resolve_range("过去三周", CN_A) == ("2023-08-25", "2023-09-15")


def test_cn_vague_phrase_stays_unresolved_by_design():
    # 最近 (recently) — same "no textually-grounded precision" bucket as the
    # English recently/lately decision, deliberately left unresolved.
    assert resolve_date("最近", CN_A) is None


def test_cn_resolution_survives_production_anchor_parenthetical():
    # Guards the _to_date fix on the Chinese path specifically, not just English.
    assert resolve_date("昨天", PROD_ANCHOR) == "2023-03-03"
    assert resolve_range("过去一个月", PROD_ANCHOR) == ("2023-02-04", "2023-03-04")


# --- 2026-07-07 directionality fix: YEARLESS month+day must roll one year
# toward the fact's preferred direction (q198 class: "I just attended ... on
# March 15-16" said 2023-02-26 used to resolve FUTURE 2023-03-15, so the $500
# component fell outside every past enumeration window).

def test_yearless_month_day_past_event_rolls_back_a_year():
    # the literal q198 shape: past narration, month+day ahead of the anchor
    assert resolve_date("March 15-16", "2023-02-26", prefer="past_event") == "2022-03-15"
    assert resolve_date("15th of March", "2023-02-26", prefer="past_event") == "2022-03-15"


def test_yearless_month_day_plain_past_does_not_roll():
    # the default/current_state signal only steers directionless forms — a
    # dated month+day keeps the anchor year ("my flight is on March 15" is
    # upcoming, not last year's)
    assert resolve_date("March 15", "2023-02-26", prefer="past") == "2023-03-15"


def test_yearless_month_day_already_past_keeps_anchor_year():
    assert resolve_date("January 10", "2023-02-26", prefer="past_event") == "2023-01-10"


def test_yearless_month_day_same_day_stays():
    assert resolve_date("February 26", "2023-02-26", prefer="past_event") == "2023-02-26"


def test_explicit_year_is_never_overridden_by_direction():
    assert resolve_date("March 15, 2023", "2023-02-26", prefer="past_event") == "2023-03-15"
    assert resolve_date("March 15 2021", "2023-02-26", prefer="future") == "2021-03-15"


def test_yearless_month_day_future_plan_rolls_forward():
    # already ahead of the anchor: keep
    assert resolve_date("March 15", "2023-02-26", prefer="future") == "2023-03-15"
    # behind the anchor: the plan means NEXT year's occurrence
    assert resolve_date("January 10", "2023-02-26", prefer="future") == "2024-01-10"


def test_future_ref_marker_blocks_past_event_rollback():
    # extractor-level guard: the fact is past ("received an invitation") but the
    # date refers to a FUTURE sub-event — direction flips to "future", which for
    # a month+day ahead of the anchor keeps the anchor year.
    from sodamem.memory.ingest.extractor import _resolve_date_value
    item = {
        "modality": "past_event",
        "predicate_raw": "user received an invitation to a high school reunion scheduled for August 15th",
        "support_text": "I got the invite yesterday — the reunion is scheduled for August 15th.",
        "occurred_date": {"expr": "August 15th", "anchor": "session_date"},
    }
    assert _resolve_date_value(item, "occurred", None, "2023-05-27") == "2023-08-15"


def test_plain_past_event_still_rolls_without_future_marker():
    from sodamem.memory.ingest.extractor import _resolve_date_value
    item = {
        "modality": "past_event",
        "predicate_raw": "user attended a two-day digital marketing workshop",
        "support_text": "I just attended a digital marketing workshop on March 15-16. I paid $500.",
        "occurred_date": {"expr": "March 15-16", "anchor": "session_date"},
    }
    assert _resolve_date_value(item, "occurred", None, "2023-02-26") == "2022-03-15"
