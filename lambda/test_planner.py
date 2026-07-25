"""Planner tests.

The Fall 2025 and Spring 2026 tables in REDESIGN.md section 3 are the fixtures:
if the general algorithm is right, it reproduces them exactly -- including week
16 landing in study/exams as a normal week, and Spring's numbered-but-skipped
week 11.
"""

import datetime as dt
import pathlib

import pytest

import planner
from planner import (
    MODE_NORMAL,
    MODE_SKIP,
    SemesterCalendar,
    build_plan,
    build_week,
    classify_week,
    current_sem,
    fetch_calendar,
    is_no_class,
    monday_of,
    parse_calendar_text,
    write_plan,
)

FA25 = planner.FALLBACK_CALENDARS["Fa25"]
SP26 = planner.FALLBACK_CALENDARS["Sp26"]


def _date(iso: str) -> dt.date:
    return dt.date.fromisoformat(iso)


# (week, week_start, mode, deadline) straight off the REDESIGN.md tables.
FA25_TABLE = [
    (1, "2025-08-25", MODE_NORMAL, "2025-08-31"),
    (2, "2025-09-01", MODE_NORMAL, "2025-09-07"),
    (3, "2025-09-08", MODE_NORMAL, "2025-09-14"),
    (4, "2025-09-15", MODE_NORMAL, "2025-09-21"),
    (5, "2025-09-22", MODE_NORMAL, "2025-09-28"),
    (6, "2025-09-29", MODE_NORMAL, "2025-10-05"),
    (7, "2025-10-06", "FRIDAY", "2025-10-10"),
    (8, "2025-10-13", MODE_NORMAL, "2025-10-19"),
    (9, "2025-10-20", MODE_NORMAL, "2025-10-26"),
    (10, "2025-10-27", MODE_NORMAL, "2025-11-02"),
    (11, "2025-11-03", MODE_NORMAL, "2025-11-09"),
    (12, "2025-11-10", MODE_NORMAL, "2025-11-16"),
    (13, "2025-11-17", MODE_NORMAL, "2025-11-23"),
    (14, "2025-11-24", "TUESDAY", "2025-11-25"),
    (15, "2025-12-01", MODE_NORMAL, "2025-12-07"),
    (16, "2025-12-08", MODE_NORMAL, "2025-12-14"),
]

SP26_TABLE = [
    (1, "2026-01-19", MODE_NORMAL, "2026-01-25"),
    (2, "2026-01-26", MODE_NORMAL, "2026-02-01"),
    (3, "2026-02-02", MODE_NORMAL, "2026-02-08"),
    (4, "2026-02-09", "FRIDAY", "2026-02-13"),
    (5, "2026-02-16", MODE_NORMAL, "2026-02-22"),
    (6, "2026-02-23", MODE_NORMAL, "2026-03-01"),
    (7, "2026-03-02", MODE_NORMAL, "2026-03-08"),
    (8, "2026-03-09", MODE_NORMAL, "2026-03-15"),
    (9, "2026-03-16", MODE_NORMAL, "2026-03-22"),
    (10, "2026-03-23", "FRIDAY", "2026-03-27"),
    (11, "2026-03-30", MODE_SKIP, None),
    (12, "2026-04-06", MODE_NORMAL, "2026-04-12"),
    (13, "2026-04-13", MODE_NORMAL, "2026-04-19"),
    (14, "2026-04-20", MODE_NORMAL, "2026-04-26"),
    (15, "2026-04-27", MODE_NORMAL, "2026-05-03"),
    (16, "2026-05-04", MODE_NORMAL, "2026-05-10"),
]


# ---------------------------------------------------------------------------
# The two golden tables
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "calendar,table,sem",
    [(FA25, FA25_TABLE, "Fa25"), (SP26, SP26_TABLE, "Sp26")],
    ids=["fall2025", "spring2026"],
)
def test_plan_matches_redesign_table(calendar, table, sem):
    plan = build_plan(calendar)

    assert len(plan) == 16
    assert [item["week"] for item in plan] == list(range(1, 17))
    assert {item["sem"] for item in plan} == {sem}

    for item, (week, week_start, mode, deadline) in zip(plan, table):
        assert item["week"] == week
        assert item["week_start"] == week_start, f"week {week} start"
        assert item["mode"] == mode, f"week {week} mode"
        if deadline is None:
            assert item["due"] is None
        else:
            assert item["due"] == f"{deadline}T23:59:00", f"week {week} due"


def test_fall_2025_collects_16_reports():
    plan = build_plan(FA25)
    assert sum(1 for item in plan if not item["skip"]) == 16


def test_spring_2026_collects_15_reports_but_still_numbers_16_weeks():
    plan = build_plan(SP26)
    assert len(plan) == 16
    assert sum(1 for item in plan if not item["skip"]) == 15


# ---------------------------------------------------------------------------
# Week 16 and the SKIP week get their own checks -- both are easy to get wrong
# ---------------------------------------------------------------------------

def test_spring_week_11_is_skipped_but_keeps_its_number():
    week11 = build_plan(SP26)[10]

    assert week11["week"] == 11
    assert week11["mode"] == MODE_SKIP
    assert week11["skip"] is True
    assert week11["post_at"] is None
    assert week11["ping_at"] is None
    assert week11["due"] is None
    assert week11["remind_at"] == []


def test_week_after_skip_resumes_normally_at_week_12():
    week12 = build_plan(SP26)[11]
    assert (week12["week"], week12["mode"]) == (12, MODE_NORMAL)
    assert week12["due"] == "2026-04-12T23:59:00"


@pytest.mark.parametrize(
    "calendar,post_at,due",
    [
        (FA25, "2025-12-12T19:00:00", "2025-12-14T23:59:00"),
        (SP26, "2026-05-08T19:00:00", "2026-05-10T23:59:00"),
    ],
    ids=["fall2025", "spring2026"],
)
def test_week_16_is_normal_and_threads_the_preceding_friday(calendar, post_at, due):
    """Week 16 sits in study/exams but is still an ordinary reporting week."""
    week16 = build_plan(calendar)[15]

    assert week16["week"] == 16
    assert week16["mode"] == MODE_NORMAL
    assert week16["skip"] is False
    assert week16["post_at"] == post_at
    assert week16["due"] == due


def test_nothing_is_scheduled_past_week_16():
    plan = build_plan(FA25)
    latest = max(item["due"] for item in plan if item["due"])
    assert latest == "2025-12-14T23:59:00"


# ---------------------------------------------------------------------------
# Mode timing table (REDESIGN.md section 2)
# ---------------------------------------------------------------------------

def test_normal_week_threads_preceding_friday_and_reminds_sunday():
    week = build_plan(FA25)[0]  # Aug 25-31, ordinary week

    assert week["post_at"] == "2025-08-29T19:00:00"  # Friday 19:00
    assert week["remind_at"] == ["2025-08-31T18:00:00", "2025-08-31T21:00:00"]
    assert week["ping_at"] == "2025-08-31T23:00:00"
    assert week["due"] == "2025-08-31T23:59:00"


def test_moved_week_posts_at_0800_on_the_deadline_day():
    week7 = build_plan(FA25)[6]  # fall break -> Friday Oct 10

    assert week7["mode"] == "FRIDAY"
    assert week7["post_at"] == "2025-10-10T08:00:00"
    assert week7["remind_at"] == ["2025-10-10T18:00:00", "2025-10-10T21:00:00"]
    assert week7["ping_at"] == "2025-10-10T23:00:00"
    assert week7["due"] == "2025-10-10T23:59:00"


def test_thanksgiving_week_moves_to_tuesday_not_friday():
    week14 = build_plan(FA25)[13]

    assert week14["mode"] == "TUESDAY"
    assert week14["post_at"] == "2025-11-25T08:00:00"
    assert week14["due"] == "2025-11-25T23:59:00"


def test_week_with_break_days_but_a_clear_sunday_stays_normal():
    """Fall 2025 week 8 has Mon-Tue in the break, but its Sunday is fine."""
    week8 = build_plan(FA25)[7]

    assert is_no_class(FA25, _date("2025-10-13"))  # Monday, still in break
    assert week8["mode"] == MODE_NORMAL
    assert week8["due"] == "2025-10-19T23:59:00"


def test_monday_holiday_alone_does_not_move_a_deadline():
    """Labor Day / MLK knock out a Monday but leave the Sunday deadline alone."""
    labor_day_week = build_plan(FA25)[1]  # Sep 1-7, Labor Day Mon Sep 1
    assert labor_day_week["mode"] == MODE_NORMAL
    assert labor_day_week["due"] == "2025-09-07T23:59:00"

    mlk_week = build_plan(SP26)[0]  # Jan 19-25, MLK Mon Jan 19
    assert mlk_week["mode"] == MODE_NORMAL
    assert mlk_week["due"] == "2026-01-25T23:59:00"


def test_late_semester_start_still_tiles_from_its_monday():
    """Spring 2026 starts Tuesday; week 1's tile is still the Mon-Sun week."""
    assert SP26.instruction_begins == _date("2026-01-20")
    assert build_plan(SP26)[0]["week_start"] == "2026-01-19"
    assert is_no_class(SP26, _date("2026-01-19"))


# ---------------------------------------------------------------------------
# The rule itself, on synthetic break shapes (section 3 must not be date-pinned)
# ---------------------------------------------------------------------------

def _calendar_with(no_class):
    """A plain semester starting Mon 2030-01-07, plus the given no-class ranges."""
    return SemesterCalendar(
        term="Sp",
        year=2030,
        instruction_begins=_date("2030-01-07"),
        no_class=tuple((_date(s), _date(e)) for s, e in no_class),
    )


@pytest.mark.parametrize(
    "break_range,expected_mode,expected_deadline",
    [
        # Sat-start break (fall/February shape) -> last in-session day is Friday.
        (("2030-01-12", "2030-01-15"), "FRIDAY", "2030-01-11"),
        # Wed-start break (Thanksgiving shape) -> Tuesday.
        (("2030-01-09", "2030-01-13"), "TUESDAY", "2030-01-08"),
        # A break starting Thursday resolves to Wednesday -- nothing is pinned
        # to the two shapes Cornell happens to use today.
        (("2030-01-10", "2030-01-14"), "WEDNESDAY", "2030-01-09"),
        # Sunday alone off still moves the deadline back to Friday.
        (("2030-01-13", "2030-01-13"), "FRIDAY", "2030-01-11"),
    ],
    ids=["sat_start", "wed_start", "thu_start", "sunday_only"],
)
def test_move_rule_is_derived_from_break_shape(
    break_range, expected_mode, expected_deadline
):
    calendar = _calendar_with([break_range])
    mode, deadline = classify_week(calendar, _date("2030-01-07"))

    assert mode == expected_mode
    assert deadline == _date(expected_deadline)


def test_week_with_no_in_session_weekday_is_skipped():
    calendar = _calendar_with([("2030-01-07", "2030-01-13")])
    mode, deadline = classify_week(calendar, _date("2030-01-07"))

    assert mode == MODE_SKIP
    assert deadline is None


def test_saturday_off_alone_does_not_move_anything():
    calendar = _calendar_with([("2030-01-12", "2030-01-12")])
    mode, deadline = classify_week(calendar, _date("2030-01-07"))

    assert mode == MODE_NORMAL
    assert deadline == _date("2030-01-13")


def test_monday_of_is_identity_on_mondays():
    assert monday_of(_date("2025-08-25")) == _date("2025-08-25")
    assert monday_of(_date("2025-08-31")) == _date("2025-08-25")  # Sunday
    assert monday_of(_date("2026-01-20")) == _date("2026-01-19")  # Tuesday


# ---------------------------------------------------------------------------
# Semester keys
# ---------------------------------------------------------------------------

def test_sem_key_is_derived_from_the_calendar_not_hardcoded():
    assert FA25.sem == "Fa25"
    assert SP26.sem == "Sp26"
    assert SemesterCalendar("Fa", 2031, _date("2031-08-25")).sem == "Fa31"


@pytest.mark.parametrize(
    "today,expected",
    [
        ("2026-08-01", ("Fa", 2026)),  # the fall cron
        ("2026-01-01", ("Sp", 2026)),  # the spring cron
        ("2027-01-01", ("Sp", 2027)),  # and again next year, unattended
        ("2026-12-15", ("Fa", 2026)),
    ],
)
def test_current_sem(today, expected):
    assert current_sem(_date(today)) == expected


# ---------------------------------------------------------------------------
# Calendar sourcing: parse when possible, fall back rather than guess
# ---------------------------------------------------------------------------

def test_parse_calendar_text_reads_a_plain_listing():
    text = "\n".join(
        [
            "Fall 2025",
            "Instruction begins Monday, August 25, 2025",
            "Fall break October 11-14",
            "Thanksgiving break November 26-30",
            "Last day of instruction Monday, December 8",
        ]
    )
    calendar = parse_calendar_text(text, "Fa", 2025)

    assert calendar is not None
    assert calendar.instruction_begins == _date("2025-08-25")
    assert calendar.last_instruction_day == _date("2025-12-08")
    assert (_date("2025-10-11"), _date("2025-10-14")) in calendar.no_class


def test_parse_calendar_text_reads_a_break_that_spans_two_months():
    text = "\n".join(
        [
            "Spring 2026",
            "Instruction begins Tuesday, January 20, 2026",
            "Spring break March 28 - April 5",
        ]
    )
    calendar = parse_calendar_text(text, "Sp", 2026)

    assert calendar is not None
    assert calendar.no_class == ((_date("2026-03-28"), _date("2026-04-05")),)


def test_a_parsed_calendar_plans_the_same_semester_as_the_fallback():
    """End to end: parsed text in, REDESIGN.md's Spring 2026 table out."""
    text = "\n".join(
        [
            "Spring 2026",
            "Instruction begins Tuesday, January 20, 2026",
            "Martin Luther King Jr. Day holiday January 19",
            "February break February 14-17",
            "Spring break March 28 - April 5",
        ]
    )
    calendar = parse_calendar_text(text, "Sp", 2026)

    assert calendar is not None
    modes = [item["mode"] for item in build_plan(calendar)]
    assert modes == [item["mode"] for item in build_plan(SP26)]


# --- against the real published PDF text, not a synthetic approximation ------

REAL_PDF_TEXT = (
    pathlib.Path(__file__).parent / "testdata_calendar_2026_2027.txt"
).read_text()


def test_real_pdf_break_rows_become_full_ranges_not_single_days():
    """The PDF says "Break Begins" and "Instruction Resumes" on separate rows.

    Reading only the "Begins" row would mark one day off instead of four, and
    the weeks either side would then be classified wrongly.
    """
    calendar = parse_calendar_text(REAL_PDF_TEXT, "Fa", 2026)

    assert calendar is not None
    assert (_date("2026-10-10"), _date("2026-10-13")) in calendar.no_class
    assert (_date("2026-11-25"), _date("2026-11-29")) in calendar.no_class


def test_real_pdf_fall_parse_matches_the_fallback_entry():
    parsed = parse_calendar_text(REAL_PDF_TEXT, "Fa", 2026)
    assert parsed == planner.FALLBACK_CALENDARS["Fa26"]


def test_real_pdf_spring_is_scoped_to_its_own_section():
    """Spring must not inherit the fall start date from the same document."""
    calendar = parse_calendar_text(REAL_PDF_TEXT, "Sp", 2027)

    assert calendar is not None
    assert calendar.instruction_begins == _date("2027-01-25")  # not August
    assert calendar.last_instruction_day == _date("2027-05-11")
    assert all(d.year == 2027 for span in calendar.no_class for d in span)


def test_real_pdf_study_and_exam_periods_are_not_treated_as_no_class():
    """Week 16 lives in study/exams and must stay an ordinary week."""
    for term, year in (("Fa", 2026), ("Sp", 2027)):
        calendar = parse_calendar_text(REAL_PDF_TEXT, term, year)
        week16 = build_plan(calendar)[15]
        assert week16["mode"] == MODE_NORMAL, f"{term}{year}"
        assert week16["skip"] is False


@pytest.mark.parametrize(
    "term,year,expected",
    [
        # Same shapes as 2025-26: a Saturday-start break -> Friday, Thanksgiving
        # -> Tuesday, and spring's fully-in-break week -> SKIP.
        ("Fa", 2026, {7: "FRIDAY", 14: "TUESDAY"}),
        ("Sp", 2027, {3: "FRIDAY", 9: "FRIDAY", 10: MODE_SKIP}),
    ],
    ids=["fall2026", "spring2027"],
)
def test_real_pdf_produces_the_expected_moved_and_skipped_weeks(term, year, expected):
    calendar = parse_calendar_text(REAL_PDF_TEXT, term, year)
    plan = build_plan(calendar)

    actual = {w["week"]: w["mode"] for w in plan if w["mode"] != MODE_NORMAL}
    assert actual == expected
    assert len(plan) == 16


def test_a_semester_absent_from_the_pdf_parses_as_none():
    """The PDF only carries one academic year; older ones must fall back."""
    assert parse_calendar_text(REAL_PDF_TEXT, "Fa", 2025) is None


def test_parse_calendar_text_returns_none_without_a_start_date():
    assert parse_calendar_text("Fall 2025\nsomething unrelated", "Fa", 2025) is None


def test_fetch_calendar_falls_back_when_the_pdf_is_unreachable(monkeypatch):
    monkeypatch.setattr(
        planner.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(OSError("network down")),
    )
    assert fetch_calendar("Fa", 2025) == FA25


def test_fetch_calendar_raises_when_there_is_no_fallback_either(monkeypatch):
    monkeypatch.setattr(
        planner.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(OSError("network down")),
    )
    with pytest.raises(RuntimeError, match="No calendar for Fa99"):
        fetch_calendar("Fa", 2099)


def test_implausible_parse_is_rejected_in_favour_of_the_fallback():
    # Right shape, wrong year -- exactly the "start date in the past" drift
    # REDESIGN.md section 4 warns about.
    stale = SemesterCalendar(
        term="Fa",
        year=2025,
        instruction_begins=_date("2019-08-25"),
        no_class=((_date("2019-10-11"), _date("2019-10-14")),),
    )
    assert not planner._is_plausible(stale, "Fa", 2025)
    assert planner._is_plausible(FA25, "Fa", 2025)


def test_a_parse_with_no_breaks_at_all_is_implausible():
    bare = SemesterCalendar("Fa", 2025, _date("2025-08-25"))
    assert not planner._is_plausible(bare, "Fa", 2025)


# ---------------------------------------------------------------------------
# Writing: idempotent and override-aware
# ---------------------------------------------------------------------------

class FakeTable:
    """Minimal stand-in for a boto3 DynamoDB Table."""

    def __init__(self, items=None):
        self.items = {}
        for item in items or []:
            self.items[(item["sem"], item["week"])] = item
        self.puts = []

    def get_item(self, Key):
        item = self.items.get((Key["sem"], Key["week"]))
        return {"Item": item} if item else {}

    def put_item(self, Item):
        self.items[(Item["sem"], Item["week"])] = Item
        self.puts.append(Item)


def test_write_plan_writes_every_week_plus_meta_on_a_fresh_table():
    table = FakeTable()
    plan = build_plan(FA25)

    result = write_plan(table, plan, planner.build_meta(FA25))

    assert result == {"written": 16, "skipped_overridden": 0}
    assert len(table.puts) == 17  # 16 weeks + meta
    assert table.items[("Fa25", 0)]["kind"] == "meta"


def test_write_plan_is_idempotent():
    table = FakeTable()
    plan = build_plan(FA25)

    write_plan(table, plan)
    first = dict(table.items)
    write_plan(table, plan)

    assert table.items == first


def test_write_plan_never_clobbers_a_human_edited_week():
    edited = {
        "sem": "Fa25",
        "week": 7,
        "mode": MODE_NORMAL,
        "due": "2025-10-12T23:59:00",
        "overridden": True,
    }
    table = FakeTable([edited])

    result = write_plan(table, build_plan(FA25))

    assert result == {"written": 15, "skipped_overridden": 1}
    assert table.items[("Fa25", 7)] == edited


def test_write_plan_preserves_rosters_written_by_the_refresher():
    rosters = {"TL": ["U123"], "Kinetic": ["U456"]}
    table = FakeTable(
        [
            {
                "sem": "Fa25",
                "week": 3,
                "rosters": rosters,
                "roster_updated_at": "2025-09-08T03:00:00Z",
                "overridden": False,
            }
        ]
    )

    write_plan(table, build_plan(FA25))

    week3 = table.items[("Fa25", 3)]
    assert week3["rosters"] == rosters
    assert week3["roster_updated_at"] == "2025-09-08T03:00:00Z"
    assert week3["mode"] == MODE_NORMAL  # still replanned


def test_dry_run_returns_the_plan_without_touching_dynamodb(monkeypatch):
    import json

    monkeypatch.setattr(
        planner.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(OSError("offline test")),
    )
    response = planner.lambda_handler(
        {"term": "Fa", "year": 2025, "dry_run": True}, None
    )
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert body["sem"] == "Fa25"
    assert len(body["weeks"]) == 16
    assert body["meta"]["instruction_begins"] == "2025-08-25"
