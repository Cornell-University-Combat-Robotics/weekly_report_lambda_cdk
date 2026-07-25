"""Dispatcher tests.

The plans built by the planner are the fixtures here, so these tests check the
two halves actually meet: every timestamp the planner writes must be picked up
by exactly one dispatcher run, and nothing outside the semester may fire.
"""

import datetime as dt

import pytest

import dispatcher
import planner
from dispatcher import (
    ACTION_PING,
    ACTION_POST,
    ACTION_REMIND,
    ET,
    find_actions,
    render_reminder,
    render_thread_messages,
    roster_user_ids,
    submission_window,
    subteams_of,
)

FA25 = planner.FALLBACK_CALENDARS["Fa25"]
SP26 = planner.FALLBACK_CALENDARS["Sp26"]

FA25_PLAN = planner.build_plan(FA25)
SP26_PLAN = planner.build_plan(SP26)

# The five daily crons the CDK stack creates.
CRON_HOURS = (8, 18, 19, 21, 23)

ROSTERS = {
    "Kinetic": ["U_KIN1", "U_KIN2"],
    "TL": ["U_TL1"],
    "Marketing": ["U_MKT1", "U_KIN1"],  # someone on two subteams
}


def _now(iso: str) -> dt.datetime:
    return dt.datetime.fromisoformat(iso).replace(tzinfo=ET)


def _week(plan, number: int) -> dict:
    return plan[number - 1]


# ---------------------------------------------------------------------------
# Matching plan entries to the hour we're running in
# ---------------------------------------------------------------------------

def test_normal_week_posts_the_preceding_friday_evening():
    actions = find_actions(FA25_PLAN, _now("2025-08-29T19:00:00"))
    assert actions == [(ACTION_POST, _week(FA25_PLAN, 1))]


def test_normal_week_reminds_twice_and_pings_on_sunday():
    assert [a for a, _ in find_actions(FA25_PLAN, _now("2025-08-31T18:00:00"))] == [ACTION_REMIND]
    assert [a for a, _ in find_actions(FA25_PLAN, _now("2025-08-31T21:00:00"))] == [ACTION_REMIND]
    assert [a for a, _ in find_actions(FA25_PLAN, _now("2025-08-31T23:00:00"))] == [ACTION_PING]


def test_moved_week_posts_at_0800_on_the_deadline_day():
    actions = find_actions(FA25_PLAN, _now("2025-10-10T08:00:00"))
    assert [a for a, _ in actions] == [ACTION_POST]
    assert actions[0][1]["week"] == 7


def test_thanksgiving_week_runs_entirely_on_the_tuesday():
    for hour, expected in ((8, ACTION_POST), (18, ACTION_REMIND), (23, ACTION_PING)):
        actions = find_actions(FA25_PLAN, _now(f"2025-11-25T{hour:02d}:00:00"))
        assert [a for a, _ in actions] == [expected], hour
        assert actions[0][1]["week"] == 14


@pytest.mark.parametrize("hour", CRON_HOURS)
def test_skipped_week_is_silent_at_every_cron_hour(hour):
    """Spring week 11 is numbered but must produce no Slack activity."""
    for day in range(30, 32):  # Mar 30-31
        assert find_actions(SP26_PLAN, _now(f"2026-03-{day:02d}T{hour:02d}:00:00")) == []
    for day in range(1, 6):  # Apr 1-5
        assert find_actions(SP26_PLAN, _now(f"2026-04-{day:02d}T{hour:02d}:00:00")) == []


@pytest.mark.parametrize(
    "when",
    [
        "2025-07-04T08:00:00",  # summer
        "2025-08-20T19:00:00",  # before week 1
        "2025-12-25T18:00:00",  # after week 16
        "2026-06-15T23:00:00",  # after spring ends
    ],
)
@pytest.mark.parametrize("plan", [FA25_PLAN, SP26_PLAN], ids=["fa25", "sp26"])
def test_out_of_semester_is_silent(plan, when):
    assert find_actions(plan, _now(when)) == []


def test_an_ordinary_weekday_inside_the_semester_is_silent():
    """Mid-week hours have no plan entry, so the crons are no-ops."""
    for hour in CRON_HOURS:
        assert find_actions(FA25_PLAN, _now(f"2025-09-17T{hour:02d}:00:00")) == []


def test_a_matching_hour_but_wrong_day_does_not_fire():
    # Week 1 posts Fri Aug 29 at 19:00; the same hour a day later is nothing.
    assert find_actions(FA25_PLAN, _now("2025-08-30T19:00:00")) == []


# ---------------------------------------------------------------------------
# Every planned timestamp must be reachable, and only from one cron
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("plan,sem", [(FA25_PLAN, "Fa25"), (SP26_PLAN, "Sp26")], ids=["fa25", "sp26"])
def test_every_planned_timestamp_is_caught_by_exactly_one_cron_hour(plan, sem):
    """No planned event may fall in an hour the stack never wakes up in."""
    scheduled = []
    for week in plan:
        if week["skip"]:
            continue
        scheduled.append(week["post_at"])
        scheduled.extend(week["remind_at"])
        scheduled.append(week["ping_at"])

    for timestamp in scheduled:
        moment = dt.datetime.fromisoformat(timestamp)
        assert moment.hour in CRON_HOURS, f"{sem} {timestamp} has no cron"

        hits = find_actions(plan, moment.replace(tzinfo=ET))
        assert len(hits) == 1, f"{sem} {timestamp} fired {len(hits)} actions"


@pytest.mark.parametrize("plan", [FA25_PLAN, SP26_PLAN], ids=["fa25", "sp26"])
def test_each_reporting_week_gets_one_post_one_ping_and_two_reminders(plan):
    counts = {ACTION_POST: 0, ACTION_REMIND: 0, ACTION_PING: 0}

    start = dt.datetime.fromisoformat(plan[0]["week_start"])
    for offset in range(0, 7 * 17):  # the whole semester, plus slack either side
        day = start + dt.timedelta(days=offset)
        for hour in CRON_HOURS:
            when = dt.datetime.combine(day, dt.time(hour)).replace(tzinfo=ET)
            for action, _ in find_actions(plan, when):
                counts[action] += 1

    reporting_weeks = sum(1 for week in plan if not week["skip"])
    assert counts[ACTION_POST] == reporting_weeks
    assert counts[ACTION_PING] == reporting_weeks
    assert counts[ACTION_REMIND] == reporting_weeks * 2


def test_a_gui_override_to_an_odd_hour_is_honoured():
    """Times come from the item, not the cron, so overrides just work --
    provided the hour is one the stack actually wakes up in."""
    week = dict(_week(FA25_PLAN, 3), post_at="2025-09-11T21:00:00")
    actions = find_actions([week], _now("2025-09-11T21:00:00"))

    assert [a for a, _ in actions] == [ACTION_POST]


def test_meta_item_is_ignored():
    meta = planner.build_meta(FA25)
    assert find_actions([meta], _now("2025-08-29T19:00:00")) == []


# ---------------------------------------------------------------------------
# Message rendering
# ---------------------------------------------------------------------------

def test_subteams_put_tl_first_then_alphabetical():
    assert subteams_of({"rosters": ROSTERS}) == ["TL", "Kinetic", "Marketing"]


def test_subteams_without_tl_are_just_alphabetical():
    assert subteams_of({"rosters": {"Kinetic": [], "Autonomous": []}}) == [
        "Autonomous",
        "Kinetic",
    ]


def test_thread_messages_name_the_deadline_day():
    week = dict(_week(FA25_PLAN, 7), rosters=ROSTERS)  # Friday deadline
    messages = render_thread_messages(week)

    assert len(messages) == 3
    assert messages[0] == (
        "WEEK 7 TL thread for Weekly Report. DUE AT 11:59 PM ON FRIDAY. <@U_TL1>"
    )
    assert all("ON FRIDAY." in message for message in messages)


def test_thread_messages_say_sunday_for_a_normal_week():
    week = dict(_week(FA25_PLAN, 1), rosters={"TL": ["U_TL1"]})
    assert "ON SUNDAY." in render_thread_messages(week)[0]


def test_thread_messages_say_tuesday_for_thanksgiving():
    week = dict(_week(FA25_PLAN, 14), rosters={"TL": ["U_TL1"]})
    assert "ON TUESDAY." in render_thread_messages(week)[0]


def test_a_subteam_with_an_empty_roster_still_gets_a_thread():
    week = dict(_week(FA25_PLAN, 1), rosters={"TL": []})
    assert render_thread_messages(week) == [
        "WEEK 1 TL thread for Weekly Report. DUE AT 11:59 PM ON SUNDAY."
    ]


def test_a_week_with_no_rosters_yet_posts_nothing():
    assert render_thread_messages(_week(FA25_PLAN, 1)) == []


def test_reminder_names_the_week():
    assert render_reminder(_week(FA25_PLAN, 5)) == (
        "WEEK 5 Weekly Report is DUE TONIGHT. Make sure to turn it in!"
    )


def test_roster_user_ids_deduplicates_across_subteams():
    week = {"rosters": ROSTERS}
    assert roster_user_ids(week) == ["U_TL1", "U_KIN1", "U_KIN2", "U_MKT1"]


# ---------------------------------------------------------------------------
# Submission window
# ---------------------------------------------------------------------------

def test_submission_window_spans_thread_post_until_now():
    week = _week(FA25_PLAN, 1)  # posts Fri 19:00, ping Sun 23:00
    now = _now("2025-08-31T23:00:00")

    start, end = submission_window(week, now)

    assert start == _now("2025-08-29T19:00:00").timestamp()
    assert end == now.timestamp()
    assert (end - start) / 3600 == pytest.approx(52.0)  # Fri 19:00 -> Sun 23:00


def test_submission_window_for_a_moved_week_is_the_same_day():
    week = _week(FA25_PLAN, 7)  # Friday: post 08:00, ping 23:00
    start, end = submission_window(week, _now("2025-10-10T23:00:00"))

    assert (end - start) / 3600 == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# Ping path, against a fake Slack client
# ---------------------------------------------------------------------------

class FakeSlack:
    """Enough of slack_sdk.WebClient for the ping path."""

    def __init__(self, history=None, replies=None, members=None):
        self.history = history or []
        self.replies = replies or {}
        self.members = members or []
        self.posted = []

    def conversations_members(self, **kwargs):
        return {"members": self.members}

    def conversations_history(self, **kwargs):
        return {"messages": self.history}

    def conversations_replies(self, ts, **kwargs):
        return {"messages": self.replies.get(ts, [])}

    def chat_postMessage(self, channel, text):
        self.posted.append(text)
        return {"ok": True}


def _thread(ts, repliers_with_files=(), repliers_without=()):
    messages = [{"ts": ts}]
    for uid in repliers_with_files:
        messages.append({"user": uid, "files": [{"filetype": "png"}]})
    for uid in repliers_without:
        messages.append({"user": uid})
    return messages


def test_ping_calls_out_only_the_people_who_have_not_submitted():
    week = dict(_week(FA25_PLAN, 1), rosters={"TL": ["U_A", "U_B", "U_C"]})
    client = FakeSlack(
        history=[{"ts": "1.0", "reply_count": 2}],
        replies={"1.0": _thread("1.0", repliers_with_files=["U_A"], repliers_without=["U_B"])},
    )

    text = dispatcher.ping_non_submitters(client, "C123", week, _now("2025-08-31T23:00:00"))

    assert "<@U_B>" in text and "<@U_C>" in text
    assert "<@U_A>" not in text
    assert client.posted == [text]


def test_a_reply_without_a_file_does_not_count_as_a_submission():
    week = dict(_week(FA25_PLAN, 1), rosters={"TL": ["U_B"]})
    client = FakeSlack(
        history=[{"ts": "1.0", "reply_count": 1}],
        replies={"1.0": _thread("1.0", repliers_without=["U_B"])},
    )

    text = dispatcher.ping_non_submitters(client, "C123", week, _now("2025-08-31T23:00:00"))
    assert "<@U_B>" in text


def test_a_non_image_attachment_does_not_count():
    week = dict(_week(FA25_PLAN, 1), rosters={"TL": ["U_B"]})
    client = FakeSlack(
        history=[{"ts": "1.0", "reply_count": 1}],
        replies={"1.0": [{"ts": "1.0"}, {"user": "U_B", "files": [{"filetype": "zip"}]}]},
    )

    text = dispatcher.ping_non_submitters(client, "C123", week, _now("2025-08-31T23:00:00"))
    assert "<@U_B>" in text


def test_ping_celebrates_when_everyone_has_submitted():
    week = dict(_week(FA25_PLAN, 1), rosters={"TL": ["U_A"]})
    client = FakeSlack(
        history=[{"ts": "1.0", "reply_count": 1}],
        replies={"1.0": _thread("1.0", repliers_with_files=["U_A"])},
    )

    text = dispatcher.ping_non_submitters(client, "C123", week, _now("2025-08-31T23:00:00"))
    assert "everyone has turned in" in text


def test_ping_falls_back_to_channel_members_without_a_roster():
    week = _week(FA25_PLAN, 1)  # no rosters written yet
    client = FakeSlack(history=[], members=["U_X"])

    text = dispatcher.ping_non_submitters(client, "C123", week, _now("2025-08-31T23:00:00"))
    assert "<@U_X>" in text
