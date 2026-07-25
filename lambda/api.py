"""Plan API behind API Gateway (REDESIGN.md section 5).

    GET  /plan?sem=Sp26   read the semester plan (open)
    POST /plan/week       edit one week      (passphrase-gated)
    POST /plan/reset      clear overrides    (passphrase-gated)

The passphrase is compared server-side against an env secret and is never part
of the static Pages bundle. Editing a week sets ``overridden=true`` so the next
planner run leaves it alone.
"""

from __future__ import annotations

import datetime as dt
import decimal
import hmac
import json
import os
from typing import Dict, Optional, Tuple

import planner

# Fields the GUI is allowed to write. Anything else in a request is ignored
# rather than trusted through to DynamoDB.
EDITABLE_FIELDS = {
    "mode",
    "post_at",
    "remind_at",
    "ping_at",
    "due",
    "skip",
    "rosters",
    "week_start",
}

TIME_FIELDS = ("post_at", "ping_at", "due")

VALID_MODES = {planner.MODE_NORMAL, planner.MODE_SKIP} | set(
    planner._WEEKDAY_MODE.values()
)


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------

def _cors_headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": os.environ.get("PAGES_ORIGIN", "*"),
        "Access-Control-Allow-Headers": "content-type",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    }


class _DecimalEncoder(json.JSONEncoder):
    """DynamoDB hands back Decimal; the GUI wants plain numbers."""

    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return int(o) if o == o.to_integral_value() else float(o)
        return super().default(o)


def _response(status: int, body) -> Dict:
    return {
        "statusCode": status,
        "headers": _cors_headers(),
        "body": json.dumps(body, cls=_DecimalEncoder),
    }


def _route(event) -> Tuple[str, str]:
    """(method, path) across both HTTP API payload versions."""
    context = event.get("requestContext") or {}
    http = context.get("http") or {}
    method = http.get("method") or event.get("httpMethod") or "GET"
    path = http.get("path") or event.get("rawPath") or event.get("path") or "/"
    return method.upper(), path


def _body(event) -> Dict:
    raw = event.get("body")
    if not raw:
        return {}
    if event.get("isBase64Encoded"):
        import base64

        raw = base64.b64decode(raw).decode("utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise ApiError(400, "body must be JSON")
    if not isinstance(parsed, dict):
        raise ApiError(400, "body must be a JSON object")
    return parsed


def check_passphrase(supplied: Optional[str]) -> None:
    """Constant-time passphrase check against the env secret."""
    expected = os.environ.get("EDIT_PASSPHRASE", "")
    if not expected:
        raise ApiError(500, "EDIT_PASSPHRASE is not configured")
    if not supplied or not hmac.compare_digest(str(supplied), expected):
        raise ApiError(401, "invalid passphrase")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _valid_timestamp(value) -> bool:
    try:
        dt.datetime.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


def validate_fields(fields: Dict) -> Dict:
    """Keep only editable fields, and reject malformed values outright."""
    unknown = set(fields) - EDITABLE_FIELDS
    if unknown:
        raise ApiError(400, f"unknown field(s): {', '.join(sorted(unknown))}")

    clean = dict(fields)

    if "mode" in clean and clean["mode"] not in VALID_MODES:
        raise ApiError(
            400, f"mode must be one of {', '.join(sorted(VALID_MODES))}"
        )

    for field in TIME_FIELDS:
        if field in clean and clean[field] is not None:
            if not _valid_timestamp(clean[field]):
                raise ApiError(400, f"{field} must be an ISO 8601 timestamp")

    if "remind_at" in clean:
        reminders = clean["remind_at"]
        if not isinstance(reminders, list) or not all(
            _valid_timestamp(r) for r in reminders
        ):
            raise ApiError(400, "remind_at must be a list of ISO 8601 timestamps")

    if "skip" in clean and not isinstance(clean["skip"], bool):
        raise ApiError(400, "skip must be a boolean")

    if "rosters" in clean:
        rosters = clean["rosters"]
        if not isinstance(rosters, dict) or not all(
            isinstance(v, list) and all(isinstance(i, str) for i in v)
            for v in rosters.values()
        ):
            raise ApiError(400, "rosters must be a map of subteam -> list of IDs")

    return clean


def apply_mode_defaults(week_item: Dict, fields: Dict) -> Dict:
    """Recompute standard times when the mode changes without explicit times.

    Keeps the GUI's "pick a mode, get sensible times" behaviour in one place --
    the same table the planner uses -- instead of duplicating it in JavaScript.
    """
    if "mode" not in fields:
        return fields

    explicit = {f for f in ("post_at", "remind_at", "ping_at", "due") if f in fields}
    if explicit:
        return fields  # caller supplied times; respect them

    week_start = dt.date.fromisoformat(
        fields.get("week_start") or week_item["week_start"]
    )
    mode = fields["mode"]

    if mode == planner.MODE_SKIP:
        return {
            **fields,
            "skip": True,
            "post_at": None,
            "remind_at": [],
            "ping_at": None,
            "due": None,
        }

    if mode == planner.MODE_NORMAL:
        deadline = week_start + dt.timedelta(days=6)
        post_day, post_hour = week_start + dt.timedelta(days=4), 19
    else:
        offset = next(
            day for day, name in planner._WEEKDAY_MODE.items() if name == mode
        )
        deadline = week_start + dt.timedelta(days=offset)
        post_day, post_hour = deadline, 8

    return {
        **fields,
        "skip": False,
        "post_at": planner._at(post_day, post_hour),
        "remind_at": [planner._at(deadline, 18), planner._at(deadline, 21)],
        "ping_at": planner._at(deadline, 23),
        "due": planner._at(deadline, 23, 59),
    }


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def get_plan(table, sem: str) -> Dict:
    from boto3.dynamodb.conditions import Key

    items = table.query(KeyConditionExpression=Key("sem").eq(sem)).get("Items", [])
    weeks = sorted(
        (i for i in items if int(i.get("week", 0)) >= 1),
        key=lambda i: int(i["week"]),
    )
    meta = next((i for i in items if int(i.get("week", 0)) == 0), None)
    return {"sem": sem, "meta": meta, "weeks": weeks}


def update_week(table, sem: str, week: int, fields: Dict) -> Dict:
    existing = table.get_item(Key={"sem": sem, "week": week}).get("Item")
    if not existing:
        raise ApiError(404, f"{sem} week {week} not found")

    fields = apply_mode_defaults(existing, validate_fields(fields))
    if not fields:
        raise ApiError(400, "no fields to update")

    updated = {
        **existing,
        **fields,
        "overridden": True,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    # A human editing rosters takes them out of the refresher's hands.
    if "rosters" in fields:
        updated["rosters_overridden"] = True

    table.put_item(Item=updated)
    return updated


def reset_plan(table, sem: str) -> Dict:
    """Drop overrides and re-derive the semester from the calendar."""
    calendar = planner.fetch_calendar(sem[:2], 2000 + int(sem[2:]))
    plan = planner.build_plan(calendar)

    from boto3.dynamodb.conditions import Key

    for item in table.query(KeyConditionExpression=Key("sem").eq(sem)).get("Items", []):
        if item.get("overridden") or item.get("rosters_overridden"):
            cleaned = {
                k: v
                for k, v in item.items()
                if k not in ("overridden", "rosters_overridden")
            }
            table.put_item(Item=cleaned)

    result = planner.write_plan(table, plan, planner.build_meta(calendar))
    return {"sem": sem, "reset": True, **result}


def lambda_handler(event, context):
    event = event or {}
    method, path = _route(event)

    if method == "OPTIONS":
        return {"statusCode": 204, "headers": _cors_headers(), "body": ""}

    try:
        import boto3

        table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])

        if method == "GET" and path.endswith("/plan"):
            params = event.get("queryStringParameters") or {}
            sem = params.get("sem")
            if not sem:
                term, year = planner.current_sem()
                sem = f"{term}{year % 100:02d}"
            return _response(200, get_plan(table, sem))

        if method == "POST" and path.endswith("/plan/week"):
            body = _body(event)
            check_passphrase(body.get("passphrase"))
            if "sem" not in body or "week" not in body:
                raise ApiError(400, "sem and week are required")
            return _response(
                200,
                update_week(
                    table, body["sem"], int(body["week"]), body.get("fields") or {}
                ),
            )

        if method == "POST" and path.endswith("/plan/reset"):
            body = _body(event)
            check_passphrase(body.get("passphrase"))
            if "sem" not in body:
                raise ApiError(400, "sem is required")
            return _response(200, reset_plan(table, body["sem"]))

        raise ApiError(404, f"no route for {method} {path}")

    except ApiError as exc:
        return _response(exc.status, {"error": exc.message})
    except Exception as exc:  # noqa: BLE001 - never leak a stack trace to the browser
        print(f"[api] unhandled error: {exc!r}")
        return _response(500, {"error": "internal error"})
