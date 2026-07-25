"""Roster refresher: pulls Slack IDs from the member sheet into upcoming weeks.

Runs weekly (Thursday) and self-gates: if this semester has no thread going up
in the next few days, it does nothing. Writing the roster *ahead* of the post
means the message the dispatcher sends is deterministic and a human can eyeball
or fix it in the GUI before it fires.

Consolidates the old `load_member_info.py` and `member_slackid.py`:

  * the module-level script in `load_member_info.py` is gone -- it ran on
    import, which is wrong in a Lambda
  * pandas is gone (it was imported but never declared in requirements.txt);
    the stdlib csv module is enough and far lighter
  * the two files disagreed on column names ("Slack ID"/"Subteam" versus
    "SlackID"/"Team"), so both spellings are accepted
  * reading the sheet's public CSV export means no Google service-account key
    has to be bundled with the function
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import re
import urllib.request
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from planner import current_sem

ET = ZoneInfo("America/New_York")

# How far ahead to populate. Five days covers the Friday thread of the current
# week plus a following Tuesday-move week, both of which are reachable from a
# Thursday run.
DEFAULT_HORIZON_DAYS = 5

CSV_EXPORT_URL = "https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"

# Header spellings seen across the two predecessor scripts.
_SLACK_ID_HEADERS = {"slackid", "slack id", "slack_id", "slack"}
_SUBTEAM_HEADERS = {"subteam", "team", "sub team", "sub-team"}


# ---------------------------------------------------------------------------
# Reading the sheet
# ---------------------------------------------------------------------------

def _normalise(header: str) -> str:
    return re.sub(r"\s+", " ", (header or "").strip().lower())


def _find_header_row(rows: Sequence[Sequence[str]]) -> int:
    """Index of the row that actually holds the column names.

    The sheet carries a title line above the headers (the old code hardcoded
    ``skiprows=1``); scanning for the header row instead survives that line
    being added or removed.
    """
    for index, row in enumerate(rows):
        normalised = {_normalise(cell) for cell in row}
        if normalised & _SLACK_ID_HEADERS and normalised & _SUBTEAM_HEADERS:
            return index
    raise ValueError(
        "member sheet has no row containing both a Slack-ID and a subteam column"
    )


def rows_to_members(rows: Sequence[Sequence[str]]) -> List[Dict[str, str]]:
    """Turn raw sheet rows into ``{"slack_id": ..., "subteam": ...}`` records.

    Shared by both read paths: the CSV export returns rows via csv.reader, the
    Sheets API returns them as a list of lists, and neither should have its own
    idea of where the header is or what the columns are called.
    """
    rows = [list(row) for row in rows]
    if not rows:
        return []

    header_index = _find_header_row(rows)
    headers = [_normalise(cell) for cell in rows[header_index]]

    slack_col = next(i for i, h in enumerate(headers) if h in _SLACK_ID_HEADERS)
    subteam_col = next(i for i, h in enumerate(headers) if h in _SUBTEAM_HEADERS)

    members = []
    for row in rows[header_index + 1:]:
        if len(row) <= max(slack_col, subteam_col):
            continue
        slack_id = clean_slack_id(row[slack_col])
        subteam = (row[subteam_col] or "").strip()
        if slack_id and subteam:
            members.append({"slack_id": slack_id, "subteam": subteam})
    return members


def parse_member_csv(text: str) -> List[Dict[str, str]]:
    """Parse the sheet's CSV export."""
    return rows_to_members(list(csv.reader(io.StringIO(text))))


def clean_slack_id(raw: str) -> str:
    """Bare user ID from whatever the sheet holds.

    The old code stored mentions pre-wrapped as ``<@U123>``; the plan schema
    stores bare IDs and the dispatcher does the wrapping, so unwrap here.
    """
    value = (raw or "").strip()
    match = re.fullmatch(r"<@([^|>]+)(?:\|[^>]*)?>", value)
    if match:
        value = match.group(1)
    return value.lstrip("@").strip()


def fetch_member_rows_csv(sheet_id: str, gid: str = "0") -> List[Dict[str, str]]:
    """Read the sheet's public CSV export. Only works if the sheet is
    link-viewable; a private sheet answers 401."""
    url = CSV_EXPORT_URL.format(sheet_id=sheet_id, gid=gid)
    with urllib.request.urlopen(url, timeout=20) as response:
        return parse_member_csv(response.read().decode("utf-8", errors="replace"))


def _service_account_info() -> Dict:
    """Service-account JSON from Secrets Manager.

    Kept out of the deployment bundle deliberately -- the function reads it at
    runtime so no private key is ever baked into the code asset.
    """
    import boto3

    secret_id = os.environ["GOOGLE_SA_SECRET"]
    payload = boto3.client("secretsmanager").get_secret_value(SecretId=secret_id)
    return json.loads(payload["SecretString"])


def _a1(tab: str) -> str:
    """Quote a tab name for A1 notation (an apostrophe is doubled)."""
    return "'" + tab.replace("'", "''") + "'!A:Z"


def expected_tab_name(term: str, year: int) -> str:
    """The tab this semester's roster should live in, e.g. "Fall 2026"."""
    return f"{'Fall' if term == 'Fa' else 'Spring'} {year}"


def choose_tab(titles: Sequence[str], term: str, year: int,
               preferred: Optional[str] = None) -> str:
    """Which tab holds this semester's roster.

    The workbook keeps one tab per semester ("Fall 2025", "Spring 2026", ...),
    so the right one changes every term and no fixed gid or range can track it.
    Prefer an exact name match for the semester being planned; fall back to the
    first tab, which is where the newest term lives.
    """
    if not titles:
        raise ValueError("spreadsheet has no tabs")
    if preferred and preferred in titles:
        return preferred

    wanted = expected_tab_name(term, year)
    if wanted in titles:
        return wanted

    print(
        f"[refresher] no tab named {wanted!r}; falling back to {titles[0]!r}. "
        "Add the tab, or set MEMBER_SHEET_TAB, to be explicit."
    )
    return titles[0]


def fetch_member_rows_api(
    sheet_id: str, term: str, year: int, preferred_tab: Optional[str] = None
) -> Tuple[List[Dict[str, str]], str, bool]:
    """Read the sheet through the Sheets API as the service account.

    Used when the sheet is private. The sheet must be shared (Viewer is enough)
    with the service account's client_email.
    """
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession

    credentials = service_account.Credentials.from_service_account_info(
        _service_account_info(),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    session = AuthorizedSession(credentials)
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"

    meta = session.get(
        base, params={"fields": "sheets.properties(title,index)"}, timeout=20
    )
    meta.raise_for_status()
    titles = [
        sheet["properties"]["title"]
        for sheet in sorted(
            meta.json().get("sheets", []), key=lambda s: s["properties"]["index"]
        )
    ]

    tab = choose_tab(titles, term, year, preferred_tab)
    exact = tab in (expected_tab_name(term, year), preferred_tab)
    print(f"[refresher] reading tab {tab!r} (exact match: {exact})")

    response = session.get(f"{base}/values/{_a1(tab)}", timeout=20)
    response.raise_for_status()
    return rows_to_members(response.json().get("values", [])), tab, exact


def fetch_member_rows(
    sheet_id: str,
    term: str,
    year: int,
    gid: str = "0",
    preferred_tab: Optional[str] = None,
) -> Tuple[List[Dict[str, str]], str, bool]:
    """Read the roster, preferring the authenticated path when it's configured.

    Returns the members plus which tab they came from and whether that tab was
    the one actually wanted -- the caller records both so a stale roster is
    visible in the dashboard instead of only in the logs.
    """
    if os.environ.get("GOOGLE_SA_SECRET"):
        return fetch_member_rows_api(sheet_id, term, year, preferred_tab)
    return fetch_member_rows_csv(sheet_id, gid), f"gid {gid}", True


def build_rosters(
    members: Iterable[Dict[str, str]], allowed: Optional[Sequence[str]] = None
) -> Dict[str, List[str]]:
    """Group Slack IDs by subteam, preserving sheet order and de-duplicating.

    Subteams are taken from the sheet rather than hardcoded, so a roster reorg
    needs no code change. Set SUBTEAMS to pin an explicit allowlist.
    """
    allowed_set = {a.strip().lower() for a in allowed} if allowed else None
    rosters: Dict[str, List[str]] = {}

    for member in members:
        subteam = member["subteam"]
        if allowed_set is not None and subteam.lower() not in allowed_set:
            continue
        bucket = rosters.setdefault(subteam, [])
        if member["slack_id"] not in bucket:
            bucket.append(member["slack_id"])

    return rosters


# ---------------------------------------------------------------------------
# Choosing which weeks to populate
# ---------------------------------------------------------------------------

def weeks_needing_rosters(
    weeks: Iterable[Dict],
    now: dt.datetime,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> List[Dict]:
    """Upcoming reporting weeks whose thread goes up within the horizon.

    The horizon is whole calendar days, not an exact multiple of 24 hours: a
    03:00 Thursday run has to reach a Tuesday 08:00 thread five days out, and
    an exact window would fall five hours short of it.
    """
    last_day = now.date() + dt.timedelta(days=horizon_days)
    horizon = dt.datetime.combine(last_day, dt.time.max).replace(tzinfo=ET)
    upcoming = []

    for week in weeks:
        if week.get("week", 0) < 1 or week.get("skip") or not week.get("post_at"):
            continue
        if week.get("rosters_overridden"):
            continue  # a human curated this week's roster; leave it alone
        post_at = dt.datetime.fromisoformat(week["post_at"]).replace(tzinfo=ET)
        if now <= post_at <= horizon:
            upcoming.append(week)

    return sorted(upcoming, key=lambda w: w["post_at"])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    """Sync rosters into the next thread(s). A no-op outside the semester.

    Accepts ``{"now": "...", "horizon_days": 5, "dry_run": true}`` for testing.
    """
    event = event or {}
    now = (
        dt.datetime.fromisoformat(event["now"]).replace(tzinfo=ET)
        if event.get("now")
        else dt.datetime.now(ET)
    )
    horizon_days = int(event.get("horizon_days", DEFAULT_HORIZON_DAYS))

    term, year = current_sem(now.date())
    sem = f"{term}{year % 100:02d}"

    import boto3
    from boto3.dynamodb.conditions import Key

    table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    weeks = table.query(KeyConditionExpression=Key("sem").eq(sem)).get("Items", [])
    targets = weeks_needing_rosters(weeks, now, horizon_days)

    if not targets:
        print(f"[refresher] {sem} {now.isoformat()}: no thread due within "
              f"{horizon_days} days")
        return {"statusCode": 200, "body": "nothing upcoming"}

    sheet_id = os.environ["MEMBER_SHEET_ID"]
    gid = os.environ.get("MEMBER_SHEET_GRID", "0")
    preferred_tab = os.environ.get("MEMBER_SHEET_TAB") or None
    allowed = [s for s in os.environ.get("SUBTEAMS", "").split(",") if s.strip()]

    members, tab, exact = fetch_member_rows(sheet_id, term, year, gid, preferred_tab)
    rosters = build_rosters(members, allowed or None)
    if not rosters:
        print(f"[refresher] {sem}: sheet produced no rosters; leaving weeks alone")
        return {"statusCode": 200, "body": "sheet empty, no write"}

    updated = [week["week"] for week in targets]
    if event.get("dry_run"):
        return {
            "statusCode": 200,
            "body": (
                f"would write {sorted(rosters)} from tab {tab!r} "
                f"(exact={exact}) into weeks {updated}"
            ),
        }

    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    for week in targets:
        table.update_item(
            Key={"sem": sem, "week": week["week"]},
            UpdateExpression=(
                "SET rosters = :r, roster_updated_at = :t, "
                "roster_tab = :tab, roster_is_fallback = :fb"
            ),
            ExpressionAttributeValues={
                ":r": rosters,
                ":t": stamp,
                ":tab": tab,
                ":fb": not exact,
            },
        )

    total = sum(len(ids) for ids in rosters.values())
    print(f"[refresher] {sem}: wrote {total} members across {len(rosters)} "
          f"subteams into weeks {updated}")
    return {"statusCode": 200, "body": f"updated weeks {updated}"}
