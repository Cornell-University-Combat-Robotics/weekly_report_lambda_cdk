"""Semester planner: turns the Cornell academic calendar into a 16-week plan.

See REDESIGN.md section 3. The planner is the only writer of the "shape" of a
semester; the dispatcher just reads today's entry and acts on it.

The module is split so the interesting parts are pure and testable offline:

  * ``SemesterCalendar``  — the handful of dates a semester actually depends on
  * ``classify_week``     — the general holiday rule (no hardcoded break names)
  * ``build_plan``        — always exactly 16 numbered Mon-Sun weeks
  * ``fetch_calendar``    — PDF parse with a hardcoded fallback (I/O lives here)
  * ``write_plan``        — idempotent, override-aware DynamoDB write

Nothing below week 1 or above week 16 is ever emitted, so summer, winter break
and the pre/post-semester gaps are silent by construction.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

WEEKS_PER_SEMESTER = 16

CALENDAR_PDF_URL = (
    "https://courses.cornell.edu/enrollment-credit-requirements/"
    "academic-calendar/academic-calendar.pdf"
)

# Modes. Moved weeks are named after the weekday their deadline lands on, which
# is derived (section 3 rule 4) rather than pinned to a particular break.
MODE_NORMAL = "NORMAL"
MODE_SKIP = "SKIP"
_WEEKDAY_MODE = {
    0: "MONDAY",
    1: "TUESDAY",
    2: "WEDNESDAY",
    3: "THURSDAY",
    4: "FRIDAY",
}

# An inclusive [start, end] span of days with no class.
DateRange = Tuple[dt.date, dt.date]


@dataclass(frozen=True)
class SemesterCalendar:
    """The dates a semester plan depends on.

    ``no_class`` holds inclusive ranges covering both multi-day breaks and
    single-day holidays (a holiday is just ``(day, day)``). The study/exam
    period is deliberately *not* listed: week 16 lands there and must stay a
    normal reporting week.
    """

    term: str  # "Fa" or "Sp"
    year: int  # calendar year the semester starts in
    instruction_begins: dt.date
    no_class: Tuple[DateRange, ...] = ()
    last_instruction_day: Optional[dt.date] = None

    @property
    def sem(self) -> str:
        """DynamoDB partition key, e.g. "Fa25" / "Sp26"."""
        return f"{self.term}{self.year % 100:02d}"


# ---------------------------------------------------------------------------
# The general classification algorithm (REDESIGN.md section 3)
# ---------------------------------------------------------------------------

def monday_of(day: dt.date) -> dt.date:
    """The Monday of the Mon-Sun week containing ``day``."""
    return day - dt.timedelta(days=day.weekday())


def is_no_class(calendar: SemesterCalendar, day: dt.date) -> bool:
    """True if ``day`` has no classes.

    Days before instruction begins count as no-class, which is what makes a
    late-starting semester (Spring 2026 starts Tuesday because Monday is MLK)
    fall out of the same rule rather than needing a special case.
    """
    if day < calendar.instruction_begins:
        return True
    return any(start <= day <= end for start, end in calendar.no_class)


def classify_week(
    calendar: SemesterCalendar, week_start: dt.date
) -> Tuple[str, Optional[dt.date]]:
    """Classify one Mon-Sun week.

    Returns ``(mode, deadline_day)``; ``deadline_day`` is None for SKIP weeks.
    """
    weekdays = [week_start + dt.timedelta(days=i) for i in range(5)]  # Mon-Fri
    sunday = week_start + dt.timedelta(days=6)

    in_session = [day for day in weekdays if not is_no_class(calendar, day)]

    # Rule 3 - nothing in session all week: skip, but keep the number.
    if not in_session:
        return MODE_SKIP, None

    # Rule 4 - the Sunday deadline is inside a break: move it back to the last
    # weekday that actually had class. A Saturday-start break resolves to
    # Friday, a Wednesday-start break to Tuesday -- derived, not hardcoded.
    if is_no_class(calendar, sunday):
        deadline = in_session[-1]
        return _WEEKDAY_MODE[deadline.weekday()], deadline

    # Rule 5 - business as usual.
    return MODE_NORMAL, sunday


def _at(day: dt.date, hour: int, minute: int = 0) -> str:
    """Local (America/New_York) wall-clock timestamp, ISO 8601, no offset."""
    return dt.datetime.combine(day, dt.time(hour, minute)).isoformat()


def build_week(
    calendar: SemesterCalendar, week: int, week_start: dt.date
) -> Dict:
    """Build one plan item. Times follow the mode table in REDESIGN.md."""
    mode, deadline = classify_week(calendar, week_start)

    item: Dict = {
        "sem": calendar.sem,
        "week": week,
        "mode": mode,
        "week_start": week_start.isoformat(),
        "skip": mode == MODE_SKIP,
        "overridden": False,
    }

    if mode == MODE_SKIP:
        item.update(
            {"post_at": None, "remind_at": [], "ping_at": None, "due": None}
        )
        return item

    assert deadline is not None  # non-SKIP modes always have a deadline

    if mode == MODE_NORMAL:
        # Thread goes up the preceding Friday evening -- which is the Friday of
        # this same Mon-Sun tile, since the deadline is the tile's Sunday.
        post_day, post_hour = week_start + dt.timedelta(days=4), 19
    else:
        # Moved weeks post at 08:00 on the deadline day itself, so people get
        # the whole day.
        post_day, post_hour = deadline, 8

    item.update(
        {
            "post_at": _at(post_day, post_hour),
            "remind_at": [_at(deadline, 18), _at(deadline, 21)],
            "ping_at": _at(deadline, 23),
            "due": _at(deadline, 23, 59),
        }
    )
    return item


def build_plan(calendar: SemesterCalendar) -> List[Dict]:
    """Always exactly 16 numbered Mon-Sun weeks from the instruction-start week."""
    first_monday = monday_of(calendar.instruction_begins)
    return [
        build_week(calendar, week, first_monday + dt.timedelta(weeks=week - 1))
        for week in range(1, WEEKS_PER_SEMESTER + 1)
    ]


def build_meta(calendar: SemesterCalendar) -> Dict:
    """Sidecar item (week 0) holding the parsed dates, for GUI confirmation."""
    return {
        "sem": calendar.sem,
        "week": 0,
        "kind": "meta",
        "term": calendar.term,
        "year": calendar.year,
        "instruction_begins": calendar.instruction_begins.isoformat(),
        "last_instruction_day": (
            calendar.last_instruction_day.isoformat()
            if calendar.last_instruction_day
            else None
        ),
        "no_class": [[s.isoformat(), e.isoformat()] for s, e in calendar.no_class],
    }


# ---------------------------------------------------------------------------
# Calendar source (REDESIGN.md section 4): PDF first, hardcoded fallback second
# ---------------------------------------------------------------------------

# Verified against the registrar tables quoted in REDESIGN.md section 3. Used
# when the PDF fetch/parse fails or returns something implausible. Add a new
# entry here if Cornell ever changes the PDF format faster than we fix the
# parser -- the GUI can also correct these dates without a code change.
FALLBACK_CALENDARS: Dict[str, SemesterCalendar] = {
    "Fa25": SemesterCalendar(
        term="Fa",
        year=2025,
        instruction_begins=dt.date(2025, 8, 25),
        no_class=(
            (dt.date(2025, 9, 1), dt.date(2025, 9, 1)),    # Labor Day
            (dt.date(2025, 10, 11), dt.date(2025, 10, 14)),  # Fall break / IPD
            (dt.date(2025, 11, 26), dt.date(2025, 11, 30)),  # Thanksgiving
        ),
        last_instruction_day=dt.date(2025, 12, 8),
    ),
    "Sp26": SemesterCalendar(
        term="Sp",
        year=2026,
        instruction_begins=dt.date(2026, 1, 20),
        no_class=(
            (dt.date(2026, 1, 19), dt.date(2026, 1, 19)),  # MLK Day
            (dt.date(2026, 2, 14), dt.date(2026, 2, 17)),  # February break
            (dt.date(2026, 3, 28), dt.date(2026, 4, 5)),   # Spring break
        ),
        last_instruction_day=dt.date(2026, 5, 5),
    ),
    # Fa26 / Sp27 transcribed from the published 2026-2027 calendar PDF.
    # Break ranges run from "Break Begins" to the day before "Instruction
    # Resumes", which is how the PDF expresses them.
    "Fa26": SemesterCalendar(
        term="Fa",
        year=2026,
        instruction_begins=dt.date(2026, 8, 24),
        no_class=(
            (dt.date(2026, 9, 7), dt.date(2026, 9, 7)),      # Labor Day
            (dt.date(2026, 10, 10), dt.date(2026, 10, 13)),  # Fall break
            (dt.date(2026, 11, 25), dt.date(2026, 11, 29)),  # Thanksgiving
        ),
        last_instruction_day=dt.date(2026, 12, 7),
    ),
    "Sp27": SemesterCalendar(
        term="Sp",
        year=2027,
        instruction_begins=dt.date(2027, 1, 25),
        no_class=(
            (dt.date(2027, 1, 18), dt.date(2027, 1, 18)),  # MLK Day
            (dt.date(2027, 2, 13), dt.date(2027, 2, 16)),  # February break
            (dt.date(2027, 3, 27), dt.date(2027, 4, 4)),   # Spring break
        ),
        last_instruction_day=dt.date(2027, 5, 11),
    ),
}


def current_sem(today: Optional[dt.date] = None) -> Tuple[str, int]:
    """Which semester a planner run is planning for.

    The annual crons fire Aug 1 and Jan 1, so anything in the back half of the
    year plans the fall and anything in the front half plans the spring.
    """
    today = today or dt.date.today()
    term = "Fa" if today.month >= 7 else "Sp"
    return term, today.year


_MONTHS = (
    "january february march april may june july "
    "august september october november december"
).split()
_MONTH_RE = "|".join(_MONTHS)


def _parse_date(text: str, year: int) -> Optional[dt.date]:
    """Parse "August 25" / "Monday, August 25, 2025" out of a fragment."""
    match = re.search(rf"({_MONTH_RE})\s+(\d{{1,2}})", text, re.IGNORECASE)
    if not match:
        return None
    month = _MONTHS.index(match.group(1).lower()) + 1
    day = int(match.group(2))
    explicit_year = re.search(r"\b(20\d{2})\b", text)
    try:
        return dt.date(int(explicit_year.group(1)) if explicit_year else year, month, day)
    except ValueError:
        return None


def _parse_day_sequence(text: str, year: int) -> List[dt.date]:
    """Every date in a fragment, carrying the month forward across a range.

    Handles the three shapes the calendar uses for a no-class span:
    "October 11-14", "March 28 - April 5" and a bare "September 1".
    """
    text = re.sub(r"\b20\d{2}\b", " ", text)  # drop years so they aren't read as days
    dates: List[dt.date] = []
    month: Optional[int] = None

    for token in re.finditer(rf"({_MONTH_RE})\s+(\d{{1,2}})|(\d{{1,2}})", text, re.IGNORECASE):
        if token.group(1):
            month = _MONTHS.index(token.group(1).lower()) + 1
            day = int(token.group(2))
        elif month is None:
            continue  # a number before any month name tells us nothing
        else:
            day = int(token.group(3))
        try:
            dates.append(dt.date(year, month, day))
        except ValueError:
            continue

    return dates


_SECTION_RE = re.compile(r"\b(fall|spring|winter)\s+(20\d{2})", re.IGNORECASE)


def _section_lines(text: str, term: str, year: int) -> Optional[List[str]]:
    """Just the lines belonging to one semester's table.

    The PDF holds a whole academic year -- "Fall 2026", "Winter 2026-2027",
    "Spring 2027" -- so scoping to the right section is what stops a spring
    parse from picking up the fall start date.
    """
    wanted = "fall" if term == "Fa" else "spring"
    lines = text.splitlines()
    start: Optional[int] = None

    for index, line in enumerate(lines):
        match = _SECTION_RE.search(line)
        if not match:
            continue
        if start is None:
            if match.group(1).lower() == wanted and int(match.group(2)) == year:
                start = index
        else:
            return lines[start:index]  # the next section header ends ours

    return lines[start:] if start is not None else None


def parse_calendar_text(text: str, term: str, year: int) -> Optional[SemesterCalendar]:
    """Best-effort parse of the calendar PDF's extracted text.

    Returns None the moment the essentials are missing, so the caller falls
    back rather than planning a semester from half-read dates.

    Breaks are published as a *pair* of rows -- "Fall Break Begins" on one line
    and "Instruction Resumes" on another -- so the no-class range runs from the
    first up to the day before the second. The older single-line form
    ("Fall break October 11-14") is still accepted.
    """
    lines = _section_lines(text, term, year)
    if lines is None:
        return None

    instruction_begins: Optional[dt.date] = None
    last_instruction_day: Optional[dt.date] = None
    no_class: List[DateRange] = []
    break_start: Optional[dt.date] = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()

        if "instruction begins" in lowered:
            if instruction_begins is None:
                instruction_begins = _parse_date(line, year)
        elif "break begins" in lowered:
            break_start = _parse_date(line, year)
        elif "instruction resumes" in lowered:
            resumes = _parse_date(line, year)
            if break_start and resumes:
                no_class.append((break_start, resumes - dt.timedelta(days=1)))
            break_start = None
        elif "last day of instruction" in lowered or "instruction ends" in lowered:
            if last_instruction_day is None:
                last_instruction_day = _parse_date(line, year)
        elif "no classes" in lowered or "holiday" in lowered:
            holiday = _parse_date(line, year)
            if holiday:
                no_class.append((holiday, holiday))
        elif "break" in lowered:
            # Single-line range form, e.g. "Fall break October 11-14".
            parsed = _parse_day_sequence(line, year)
            if parsed:
                start, end = parsed[0], parsed[-1]
                no_class.append((start, max(start, end)))

    if break_start:  # a break that never announced a resume date
        no_class.append((break_start, break_start))

    if instruction_begins is None:
        return None

    return SemesterCalendar(
        term=term,
        year=year,
        instruction_begins=instruction_begins,
        no_class=tuple(sorted(no_class)),
        last_instruction_day=last_instruction_day,
    )


def _is_plausible(calendar: SemesterCalendar, term: str, year: int) -> bool:
    """Reject a parse that clearly drifted (wrong term, wrong year, no breaks)."""
    begins = calendar.instruction_begins
    if begins.year != year:
        return False
    if term == "Fa" and begins.month not in (8, 9):
        return False
    if term == "Sp" and begins.month not in (1, 2):
        return False
    return bool(calendar.no_class)


def fetch_calendar(term: str, year: int, url: str = CALENDAR_PDF_URL) -> SemesterCalendar:
    """Parsed calendar if we can get one, hardcoded fallback otherwise."""
    sem = f"{term}{year % 100:02d}"
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            payload = response.read()
        from pypdf import PdfReader  # imported lazily: only the planner needs it
        import io

        text = "\n".join(
            page.extract_text() or "" for page in PdfReader(io.BytesIO(payload)).pages
        )
        parsed = parse_calendar_text(text, term, year)
        if parsed and _is_plausible(parsed, term, year):
            return parsed
        print(f"[planner] calendar parse implausible for {sem}; using fallback")
    except Exception as exc:  # noqa: BLE001 - any failure must fall back, not crash
        print(f"[planner] calendar fetch/parse failed for {sem}: {exc!r}; using fallback")

    if sem not in FALLBACK_CALENDARS:
        raise RuntimeError(
            f"No calendar for {sem}: PDF parse failed and no fallback entry exists. "
            "Add one to FALLBACK_CALENDARS or fix the dates in the GUI."
        )
    return FALLBACK_CALENDARS[sem]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def write_plan(table, plan: Sequence[Dict], meta: Optional[Dict] = None) -> Dict[str, int]:
    """Write the plan, leaving human-edited weeks alone.

    Idempotent: re-running recomputes every week but skips any item a human
    marked ``overridden`` through the GUI.
    """
    written = skipped = 0

    if meta is not None:
        table.put_item(Item=meta)

    for item in plan:
        existing = table.get_item(
            Key={"sem": item["sem"], "week": item["week"]}
        ).get("Item")

        if existing and existing.get("overridden"):
            skipped += 1
            continue

        # Rosters are owned by the refresher, not the planner -- never clobber.
        if existing and existing.get("rosters"):
            item = {
                **item,
                "rosters": existing["rosters"],
                "roster_updated_at": existing.get("roster_updated_at"),
            }

        table.put_item(Item=item)
        written += 1

    return {"written": written, "skipped_overridden": skipped}


def lambda_handler(event, context):
    """Entry point for the Aug 1 / Jan 1 schedules.

    Accepts optional overrides for manual re-runs:
    ``{"term": "Sp", "year": 2026, "dry_run": true}``.
    """
    event = event or {}
    default_term, default_year = current_sem()
    term = event.get("term", default_term)
    year = int(event.get("year", default_year))

    calendar = fetch_calendar(term, year)
    plan = build_plan(calendar)
    meta = build_meta(calendar)

    if event.get("dry_run"):
        return {
            "statusCode": 200,
            "body": json.dumps({"sem": calendar.sem, "meta": meta, "weeks": plan}),
        }

    import boto3  # imported lazily so the pure logic stays importable offline

    table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    result = write_plan(table, plan, meta)

    print(f"[planner] {calendar.sem}: {result}")
    return {
        "statusCode": 200,
        "body": json.dumps({"sem": calendar.sem, **result}),
    }
