"""Dispatcher: reads today's plan entry and acts on it.

Triggered by five fixed daily crons (08:00, 18:00, 19:00, 21:00, 23:00 ET).
Each run loads this semester's 16 plan items and looks for one whose scheduled
timestamp lands in the current hour:

    post_at    -> post the per-subteam thread starters
    remind_at  -> "due tonight" nudge
    ping_at    -> @ the people who haven't submitted

Matching on the stored timestamps rather than on the cron that fired means a
week edited in the GUI is honoured automatically, and everything outside the
semester is silent: no plan item for right now, nothing happens. SKIP weeks
carry null timestamps and so never match.

The Slack submission-check logic is carried over from the pre-redesign
`lambda_function.py`; the "two days prior" window is generalised to "from when
this week's thread went up until now".
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from planner import current_sem

ET = ZoneInfo("America/New_York")

ACTION_POST = "post"
ACTION_REMIND = "remind"
ACTION_PING = "ping"

# TL leads the thread list; everything else follows alphabetically. Subteams
# are read from the roster the refresher wrote, so a roster reorg needs no
# code change here.
_FIRST_SUBTEAM = "TL"


# ---------------------------------------------------------------------------
# Deciding what to do (pure)
# ---------------------------------------------------------------------------

def _matches_now(timestamp: Optional[str], now: dt.datetime) -> bool:
    """True if a stored local timestamp falls in the hour we're running in."""
    if not timestamp:
        return False
    scheduled = dt.datetime.fromisoformat(timestamp)
    return scheduled.date() == now.date() and scheduled.hour == now.hour


def find_actions(weeks: Iterable[Dict], now: dt.datetime) -> List[Tuple[str, Dict]]:
    """Every (action, week) due in the current hour. Usually zero or one."""
    actions: List[Tuple[str, Dict]] = []

    for week in weeks:
        if week.get("week", 0) < 1 or week.get("skip"):
            continue
        if _matches_now(week.get("post_at"), now):
            actions.append((ACTION_POST, week))
        if any(_matches_now(ts, now) for ts in week.get("remind_at") or []):
            actions.append((ACTION_REMIND, week))
        if _matches_now(week.get("ping_at"), now):
            actions.append((ACTION_PING, week))

    return actions


def subteams_of(week: Dict) -> List[str]:
    """Subteam names for a week, TL first then alphabetical."""
    names = sorted((week.get("rosters") or {}).keys())
    if _FIRST_SUBTEAM in names:
        names.remove(_FIRST_SUBTEAM)
        names.insert(0, _FIRST_SUBTEAM)
    return names


def _deadline_day_name(week: Dict) -> str:
    due = week.get("due")
    if not due:
        return ""
    return dt.datetime.fromisoformat(due).strftime("%A").upper()


def render_thread_messages(week: Dict) -> List[str]:
    """One thread-starter per subteam, mirroring the original message format."""
    rosters = week.get("rosters") or {}
    day = _deadline_day_name(week)
    messages = []

    for subteam in subteams_of(week):
        mentions = " ".join(f"<@{uid}>" for uid in rosters.get(subteam) or [])
        messages.append(
            f"WEEK {week['week']} {subteam} thread for Weekly Report. "
            f"DUE AT 11:59 PM ON {day}. {mentions}".rstrip()
        )

    return messages


def render_reminder(week: Dict) -> str:
    return (
        f"WEEK {week['week']} Weekly Report is DUE TONIGHT. "
        "Make sure to turn it in!"
    )


def roster_user_ids(week: Dict) -> List[str]:
    """Everyone expected to submit this week, de-duplicated."""
    seen: List[str] = []
    for subteam in subteams_of(week):
        for uid in (week.get("rosters") or {}).get(subteam) or []:
            if uid not in seen:
                seen.append(uid)
    return seen


def submission_window(week: Dict, now: dt.datetime) -> Tuple[float, float]:
    """Epoch bounds to search for submissions: thread post time until now.

    Generalises the old fixed "two days prior, 15:00-23:45" window, which only
    described a Friday thread with a Sunday deadline.
    """
    posted = dt.datetime.fromisoformat(week["post_at"]).replace(tzinfo=ET)
    return posted.timestamp(), now.timestamp()


# ---------------------------------------------------------------------------
# Slack (I/O) - carried over from the pre-redesign lambda_function.py
# ---------------------------------------------------------------------------

def _fetch_channel_members(client, channel_id: str) -> set:
    """All member IDs in the channel (paginated)."""
    members: List[str] = []
    cursor = None
    while True:
        kwargs = {"channel": channel_id}
        if cursor:
            kwargs["cursor"] = cursor
        response = client.conversations_members(**kwargs)
        members.extend(response.get("members", []))
        cursor = response.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return set(members)


def _collect_repliers_with_files(client, channel_id, ts_start, ts_end) -> set:
    """User IDs who replied in-thread with a png/jpg/jpeg/pdf in the window."""
    replied = set()
    cursor = None
    while True:
        kwargs = {
            "channel": channel_id,
            "oldest": str(ts_start),
            "latest": str(ts_end),
        }
        if cursor:
            kwargs["cursor"] = cursor
        response = client.conversations_history(**kwargs)

        for message in response.get("messages", []):
            if not message.get("reply_count"):
                continue
            thread_ts = message.get("thread_ts") or message["ts"]
            reply_cursor = None
            while True:
                reply_kwargs = {"channel": channel_id, "ts": thread_ts}
                if reply_cursor:
                    reply_kwargs["cursor"] = reply_cursor
                thread = client.conversations_replies(**reply_kwargs)
                for reply in thread.get("messages", [])[1:]:  # skip the root
                    for f in reply.get("files") or []:
                        if f.get("filetype") in ("png", "jpg", "jpeg", "pdf"):
                            if reply.get("user"):
                                replied.add(reply["user"])
                            break
                reply_cursor = thread.get("response_metadata", {}).get("next_cursor")
                if not reply_cursor:
                    break

        cursor = response.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return replied


def ping_non_submitters(client, channel_id: str, week: Dict, now: dt.datetime) -> str:
    """@ everyone who hasn't posted an image/PDF since the thread went up."""
    expected = set(roster_user_ids(week)) or _fetch_channel_members(client, channel_id)
    start, end = submission_window(week, now)
    submitted = _collect_repliers_with_files(client, channel_id, start, end)

    outstanding = expected - submitted
    if outstanding:
        mentions = " ".join(f"<@{uid}>" for uid in sorted(outstanding))
        text = f"{mentions}, folks please turn in your weekly report!"
    else:
        text = "Let's go! Seems that everyone has turned in the weekly report!"

    client.chat_postMessage(channel=channel_id, text=text)
    return text


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def load_weeks(table, sem: str) -> List[Dict]:
    from boto3.dynamodb.conditions import Key

    response = table.query(KeyConditionExpression=Key("sem").eq(sem))
    return [item for item in response.get("Items", []) if item.get("week", 0) >= 1]


def lambda_handler(event, context):
    """Act on whatever this semester's plan says is due right now.

    Accepts ``{"now": "2025-10-10T08:00:00", "dry_run": true}`` for manual
    testing without waiting for a cron.
    """
    event = event or {}
    now = (
        dt.datetime.fromisoformat(event["now"]).replace(tzinfo=ET)
        if event.get("now")
        else dt.datetime.now(ET)
    )

    term, year = current_sem(now.date())
    sem = f"{term}{year % 100:02d}"

    import boto3

    table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    weeks = load_weeks(table, sem)
    actions = find_actions(weeks, now)

    if not actions:
        print(f"[dispatcher] {sem} {now.isoformat()}: nothing due")
        return {"statusCode": 200, "body": "nothing due"}

    channel_id = os.environ.get("SLACK_CHANNEL_ID") or os.environ.get("SLACK_CHANNEL")
    planned = [(action, week["week"]) for action, week in actions]

    if event.get("dry_run"):
        return {"statusCode": 200, "body": f"would run {planned}"}

    from slack_sdk import WebClient

    client = WebClient(token=os.environ["SLACK_TOKEN"])

    for action, week in actions:
        if action == ACTION_POST:
            for message in render_thread_messages(week):
                client.chat_postMessage(channel=channel_id, text=message)
        elif action == ACTION_REMIND:
            client.chat_postMessage(channel=channel_id, text=render_reminder(week))
        elif action == ACTION_PING:
            ping_non_submitters(client, channel_id, week, now)

    print(f"[dispatcher] {sem} {now.isoformat()}: ran {planned}")
    return {"statusCode": 200, "body": f"ran {planned}"}
