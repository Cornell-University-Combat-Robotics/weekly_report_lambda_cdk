"""Refresher tests: sheet parsing, and picking the right weeks to populate."""

import datetime as dt

import pytest

import planner
import refresher
from refresher import (
    ET,
    build_rosters,
    clean_slack_id,
    choose_tab,
    parse_member_csv,
    rows_to_members,
    weeks_needing_rosters,
)

# The real workbook's tabs, newest first.
REAL_TABS = [
    "Spring 2026", "SP'26 Hold Harmless", "Fall 2025", "Spring 2025",
    "Fall 2024", "Spring 2024", "Fall 2023", "Spring 2023", "Fall 2022",
    "Spring 2023 (old)", "Avg Subteam Height", "2021-2022", "2020-2021",
]

FA25_PLAN = planner.build_plan(planner.FALLBACK_CALENDARS["Fa25"])
SP26_PLAN = planner.build_plan(planner.FALLBACK_CALENDARS["Sp26"])

# The sheet as load_member_info.py expected it: a title line, then headers.
SHEET_CSV = "\n".join(
    [
        "CRC Member Info Spring 2026,,",
        "Name,Slack ID,Subteam",
        "Ada,U_TL1,TL",
        "Grace,U_KIN1,Kinetic",
        "Alan,U_KIN2,Kinetic",
        "Edsger,U_MKT1,Marketing",
    ]
)

# The spelling member_slackid.py used instead.
ALT_SHEET_CSV = "\n".join(
    [
        "Name,SlackID,Team",
        "Ada,U_TL1,TL",
        "Grace,U_KIN1,Kinetic",
    ]
)


def _now(iso: str) -> dt.datetime:
    return dt.datetime.fromisoformat(iso).replace(tzinfo=ET)


# ---------------------------------------------------------------------------
# Sheet parsing
# ---------------------------------------------------------------------------

def test_parses_the_sheet_skipping_the_title_line():
    members = parse_member_csv(SHEET_CSV)

    assert len(members) == 4
    assert members[0] == {"slack_id": "U_TL1", "subteam": "TL"}


def test_parses_the_other_column_spelling():
    """The two predecessor scripts disagreed on header names; accept both."""
    members = parse_member_csv(ALT_SHEET_CSV)

    assert [m["slack_id"] for m in members] == ["U_TL1", "U_KIN1"]
    assert [m["subteam"] for m in members] == ["TL", "Kinetic"]


def test_header_row_is_found_regardless_of_how_many_title_lines_there_are():
    csv_text = "\n".join(["Title", "", "sub-heading,,", "Name,Slack ID,Subteam", "Ada,U_A,TL"])
    assert parse_member_csv(csv_text) == [{"slack_id": "U_A", "subteam": "TL"}]


def test_a_sheet_without_the_expected_columns_is_an_error_not_silent_garbage():
    with pytest.raises(ValueError, match="Slack-ID"):
        parse_member_csv("Name,Email\nAda,ada@example.com")


def test_rows_missing_an_id_or_subteam_are_dropped():
    csv_text = "\n".join(
        [
            "Name,Slack ID,Subteam",
            "Ada,U_A,TL",
            "Nobody,,TL",
            "Ghost,U_G,",
            "Grace,U_B,Kinetic",
        ]
    )
    assert [m["slack_id"] for m in parse_member_csv(csv_text)] == ["U_A", "U_B"]


def test_short_rows_do_not_crash_the_parse():
    csv_text = "Name,Slack ID,Subteam\nAda,U_A,TL\nTruncated\n"
    assert [m["slack_id"] for m in parse_member_csv(csv_text)] == ["U_A"]


def test_empty_sheet_yields_nothing():
    assert parse_member_csv("") == []


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("U123", "U123"),
        ("  U123  ", "U123"),
        ("<@U123>", "U123"),          # old code stored pre-wrapped mentions
        ("<@U123|ada>", "U123"),
        ("@U123", "U123"),
        ("", ""),
    ],
)
def test_clean_slack_id(raw, expected):
    assert clean_slack_id(raw) == expected


# ---------------------------------------------------------------------------
# Picking the semester's tab
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "term,year,expected",
    [("Sp", 2026, "Spring 2026"), ("Fa", 2025, "Fall 2025"), ("Sp", 2025, "Spring 2025")],
)
def test_tab_is_matched_by_semester_name(term, year, expected):
    assert choose_tab(REAL_TABS, term, year) == expected


def test_missing_tab_falls_back_to_the_newest_not_the_oldest():
    """gid 0 and worksheets()[-1] both pointed at the 2020-2021 tab."""
    chosen = choose_tab(REAL_TABS, "Fa", 2026)

    assert chosen == "Spring 2026"
    assert chosen != "2020-2021"


def test_an_explicit_tab_overrides_the_name_match():
    assert choose_tab(REAL_TABS, "Sp", 2026, preferred="Fall 2025") == "Fall 2025"


def test_an_explicit_tab_that_does_not_exist_is_ignored():
    assert choose_tab(REAL_TABS, "Sp", 2026, preferred="Nonexistent") == "Spring 2026"


@pytest.mark.parametrize(
    "term,year,expected",
    [("Fa", 2026, "Fall 2026"), ("Sp", 2027, "Spring 2027")],
)
def test_expected_tab_name(term, year, expected):
    assert refresher.expected_tab_name(term, year) == expected


def test_fallback_is_detectable_so_the_gui_can_flag_it():
    """A stale roster must be distinguishable from a correct one."""
    exact_tab = choose_tab(REAL_TABS, "Sp", 2026)
    fallback_tab = choose_tab(REAL_TABS, "Fa", 2026)

    assert exact_tab == refresher.expected_tab_name("Sp", 2026)      # exact
    assert fallback_tab != refresher.expected_tab_name("Fa", 2026)   # fell back


def test_choose_tab_needs_at_least_one_tab():
    with pytest.raises(ValueError, match="no tabs"):
        choose_tab([], "Fa", 2026)


def test_tab_names_with_apostrophes_are_quoted_for_a1_notation():
    assert refresher._a1("SP'26 Hold Harmless") == "'SP''26 Hold Harmless'!A:Z"
    assert refresher._a1("Fall 2025") == "'Fall 2025'!A:Z"


# ---------------------------------------------------------------------------
# Both read paths share one parser
# ---------------------------------------------------------------------------

def test_sheets_api_rows_parse_identically_to_the_csv_export():
    """The API returns lists of lists; the CSV export returns text. Same result.

    The two paths must not develop separate ideas about where the header is or
    what the columns are called.
    """
    api_rows = [
        ["CRC Member Info Spring 2026", "", ""],
        ["Name", "Slack ID", "Subteam"],
        ["Ada", "U_TL1", "TL"],
        ["Grace", "U_KIN1", "Kinetic"],
        ["Alan", "U_KIN2", "Kinetic"],
        ["Edsger", "U_MKT1", "Marketing"],
    ]
    assert rows_to_members(api_rows) == parse_member_csv(SHEET_CSV)


def test_sheets_api_ragged_rows_are_tolerated():
    """The Sheets API truncates trailing empty cells, so rows arrive ragged."""
    api_rows = [
        ["Name", "Slack ID", "Subteam"],
        ["Ada", "U_TL1", "TL"],
        ["Truncated"],
        ["Grace", "U_KIN1", "Kinetic"],
    ]
    assert [m["slack_id"] for m in rows_to_members(api_rows)] == ["U_TL1", "U_KIN1"]


def test_empty_api_response_yields_nothing():
    assert rows_to_members([]) == []


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

def test_rosters_group_by_subteam_in_sheet_order():
    rosters = build_rosters(parse_member_csv(SHEET_CSV))

    assert rosters == {
        "TL": ["U_TL1"],
        "Kinetic": ["U_KIN1", "U_KIN2"],
        "Marketing": ["U_MKT1"],
    }


def test_subteams_come_from_the_sheet_not_a_hardcoded_list():
    """A new subteam appears without a code change."""
    csv_text = "Name,Slack ID,Subteam\nAda,U_A,Firmware\n"
    assert build_rosters(parse_member_csv(csv_text)) == {"Firmware": ["U_A"]}


def test_an_allowlist_filters_unknown_subteams_when_you_want_one():
    rosters = build_rosters(parse_member_csv(SHEET_CSV), allowed=["TL", "Kinetic"])
    assert set(rosters) == {"TL", "Kinetic"}


def test_allowlist_matching_ignores_case():
    rosters = build_rosters(parse_member_csv(SHEET_CSV), allowed=["tl"])
    assert set(rosters) == {"TL"}


def test_duplicate_ids_within_a_subteam_are_collapsed():
    csv_text = "Name,Slack ID,Subteam\nAda,U_A,TL\nAda again,U_A,TL\n"
    assert build_rosters(parse_member_csv(csv_text)) == {"TL": ["U_A"]}


def test_someone_on_two_subteams_appears_in_both():
    csv_text = "Name,Slack ID,Subteam\nAda,U_A,TL\nAda,U_A,Kinetic\n"
    assert build_rosters(parse_member_csv(csv_text)) == {
        "TL": ["U_A"],
        "Kinetic": ["U_A"],
    }


# ---------------------------------------------------------------------------
# Which weeks get populated
# ---------------------------------------------------------------------------

def test_thursday_run_picks_up_the_friday_thread_two_days_out():
    # Week 1 posts Fri 2025-08-29 19:00.
    targets = weeks_needing_rosters(FA25_PLAN, _now("2025-08-28T03:00:00"))
    assert [w["week"] for w in targets] == [1]


def test_thursday_run_reaches_a_following_tuesday_move_week():
    """Thanksgiving posts Tue Nov 25; the prior Thursday must still catch it."""
    targets = weeks_needing_rosters(FA25_PLAN, _now("2025-11-20T03:00:00"))
    assert 14 in [w["week"] for w in targets]


def test_nothing_upcoming_outside_the_semester():
    for when in ("2025-07-03T03:00:00", "2026-01-08T03:00:00"):
        assert weeks_needing_rosters(FA25_PLAN, _now(when)) == []


def test_a_skipped_week_is_never_populated():
    """Spring week 11 has no thread, so it must not be a target."""
    for day in range(23, 31):
        targets = weeks_needing_rosters(SP26_PLAN, _now(f"2026-03-{day:02d}T03:00:00"))
        assert 11 not in [w["week"] for w in targets]


def test_a_past_thread_is_not_repopulated():
    # Week 1 posted Fri Aug 29; by Saturday it is behind us.
    targets = weeks_needing_rosters(FA25_PLAN, _now("2025-08-30T03:00:00"))
    assert 1 not in [w["week"] for w in targets]


def test_a_human_curated_roster_is_left_alone():
    weeks = [dict(FA25_PLAN[0], rosters_overridden=True)]
    assert weeks_needing_rosters(weeks, _now("2025-08-28T03:00:00")) == []


def test_the_meta_item_is_never_a_target():
    meta = planner.build_meta(planner.FALLBACK_CALENDARS["Fa25"])
    assert weeks_needing_rosters([meta], _now("2025-08-28T03:00:00")) == []


def test_targets_come_back_in_chronological_order():
    targets = weeks_needing_rosters(FA25_PLAN, _now("2025-08-25T03:00:00"), horizon_days=30)
    posts = [w["post_at"] for w in targets]
    assert posts == sorted(posts)


def test_horizon_is_configurable():
    far = weeks_needing_rosters(FA25_PLAN, _now("2025-08-25T03:00:00"), horizon_days=30)
    near = weeks_needing_rosters(FA25_PLAN, _now("2025-08-25T03:00:00"), horizon_days=1)
    assert len(far) > len(near)


def test_every_reporting_week_is_reachable_from_some_thursday_run():
    """No week may be missed by the weekly cadence."""
    covered = set()
    start = dt.date.fromisoformat(FA25_PLAN[0]["week_start"]) - dt.timedelta(days=7)

    for offset in range(0, 7 * 20, 7):  # every Thursday across the semester
        thursday = start + dt.timedelta(days=offset + 3)
        when = dt.datetime.combine(thursday, dt.time(3)).replace(tzinfo=ET)
        covered.update(w["week"] for w in weeks_needing_rosters(FA25_PLAN, when))

    expected = {w["week"] for w in FA25_PLAN if not w["skip"]}
    assert covered == expected
