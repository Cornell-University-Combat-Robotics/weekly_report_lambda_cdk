"""API tests: auth, validation, routing, and the mode-defaults behaviour."""

import decimal
import json

import pytest

import api
import planner
from api import ApiError, apply_mode_defaults, check_passphrase, validate_fields

FA25 = planner.FALLBACK_CALENDARS["Fa25"]
FA25_PLAN = planner.build_plan(FA25)

PASSPHRASE = "correct horse battery staple"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("EDIT_PASSPHRASE", PASSPHRASE)
    monkeypatch.setenv("PAGES_ORIGIN", "https://example.github.io")
    monkeypatch.setenv("TABLE_NAME", "test-table")


class FakeTable:
    """Minimal stand-in for a boto3 DynamoDB Table."""

    def __init__(self, items=None):
        self.items = {(i["sem"], i["week"]): dict(i) for i in (items or [])}

    def query(self, KeyConditionExpression=None, **kwargs):
        return {"Items": [dict(v) for v in self.items.values()]}

    def get_item(self, Key):
        item = self.items.get((Key["sem"], Key["week"]))
        return {"Item": dict(item)} if item else {}

    def put_item(self, Item):
        self.items[(Item["sem"], Item["week"])] = dict(Item)


def _seeded_table():
    return FakeTable(FA25_PLAN + [planner.build_meta(FA25)])


def _event(method, path, body=None, query=None):
    return {
        "requestContext": {"http": {"method": method, "path": path}},
        "rawPath": path,
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
    }


def _call(monkeypatch, event, table=None):
    """Invoke the handler with boto3 stubbed out."""
    table = table if table is not None else _seeded_table()

    class FakeResource:
        def Table(self, name):
            return table

    fake_boto3 = type("boto3", (), {"resource": staticmethod(lambda svc: FakeResource())})
    conditions = type("conditions", (), {"Key": lambda self=None, *a, **k: None})

    monkeypatch.setitem(__import__("sys").modules, "boto3", fake_boto3)
    monkeypatch.setitem(
        __import__("sys").modules,
        "boto3.dynamodb.conditions",
        type("m", (), {"Key": lambda *a, **k: type("K", (), {"eq": lambda s, v: None})()}),
    )
    return api.lambda_handler(event, None), table


# ---------------------------------------------------------------------------
# Passphrase
# ---------------------------------------------------------------------------

def test_correct_passphrase_is_accepted():
    check_passphrase(PASSPHRASE)  # does not raise


@pytest.mark.parametrize("supplied", [None, "", "wrong", PASSPHRASE + "x", PASSPHRASE.upper()])
def test_wrong_passphrase_is_rejected(supplied):
    with pytest.raises(ApiError) as exc:
        check_passphrase(supplied)
    assert exc.value.status == 401


def test_an_unconfigured_passphrase_fails_closed(monkeypatch):
    """A blank env secret must never mean 'everyone is authorised'."""
    monkeypatch.setenv("EDIT_PASSPHRASE", "")
    with pytest.raises(ApiError) as exc:
        check_passphrase("")
    assert exc.value.status == 500


def test_editing_requires_a_passphrase(monkeypatch):
    response, table = _call(
        monkeypatch,
        _event("POST", "/plan/week", {"sem": "Fa25", "week": 3, "fields": {"skip": True}}),
    )
    assert response["statusCode"] == 401
    assert table.items[("Fa25", 3)]["skip"] is False  # unchanged


def test_reset_requires_a_passphrase(monkeypatch):
    response, _ = _call(monkeypatch, _event("POST", "/plan/reset", {"sem": "Fa25"}))
    assert response["statusCode"] == 401


def test_reading_the_plan_needs_no_passphrase(monkeypatch):
    response, _ = _call(monkeypatch, _event("GET", "/plan", query={"sem": "Fa25"}))
    assert response["statusCode"] == 200


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_unknown_fields_are_rejected():
    with pytest.raises(ApiError, match="unknown field"):
        validate_fields({"overridden": False})


def test_rejecting_unknown_fields_blocks_writing_the_primary_key():
    for field in ("sem", "week"):
        with pytest.raises(ApiError, match="unknown field"):
            validate_fields({field: "nonsense"})


@pytest.mark.parametrize("mode", ["NORMAL", "FRIDAY", "TUESDAY", "WEDNESDAY", "SKIP"])
def test_valid_modes_are_accepted(mode):
    assert validate_fields({"mode": mode})["mode"] == mode


@pytest.mark.parametrize("mode", ["normal", "SATURDAY", "", None, 7])
def test_invalid_modes_are_rejected(mode):
    with pytest.raises(ApiError, match="mode must be"):
        validate_fields({"mode": mode})


@pytest.mark.parametrize("value", ["not a date", "2025-13-45T00:00:00", 12345, []])
def test_malformed_timestamps_are_rejected(value):
    with pytest.raises(ApiError, match="ISO 8601"):
        validate_fields({"due": value})


def test_a_null_timestamp_is_allowed():
    assert validate_fields({"due": None})["due"] is None


def test_remind_at_must_be_a_list_of_timestamps():
    assert validate_fields({"remind_at": ["2025-08-31T18:00:00"]})
    for bad in ("2025-08-31T18:00:00", ["nope"], 5):
        with pytest.raises(ApiError, match="remind_at"):
            validate_fields({"remind_at": bad})


def test_skip_must_be_boolean():
    with pytest.raises(ApiError, match="skip must be"):
        validate_fields({"skip": "true"})


def test_rosters_must_be_subteam_to_id_list():
    assert validate_fields({"rosters": {"TL": ["U1"]}})
    for bad in ({"TL": "U1"}, {"TL": [1]}, ["TL"]):
        with pytest.raises(ApiError, match="rosters"):
            validate_fields({"rosters": bad})


# ---------------------------------------------------------------------------
# Mode defaults
# ---------------------------------------------------------------------------

def test_changing_mode_recomputes_the_standard_times():
    week = FA25_PLAN[0]  # Aug 25-31, NORMAL
    fields = apply_mode_defaults(week, {"mode": "FRIDAY"})

    assert fields["post_at"] == "2025-08-29T08:00:00"
    assert fields["due"] == "2025-08-29T23:59:00"
    assert fields["remind_at"] == ["2025-08-29T18:00:00", "2025-08-29T21:00:00"]
    assert fields["ping_at"] == "2025-08-29T23:00:00"
    assert fields["skip"] is False


def test_switching_back_to_normal_restores_the_sunday_deadline():
    fields = apply_mode_defaults(FA25_PLAN[6], {"mode": "NORMAL"})  # week 7 was FRIDAY

    assert fields["due"] == "2025-10-12T23:59:00"
    assert fields["post_at"] == "2025-10-10T19:00:00"  # preceding Friday 19:00


def test_mode_defaults_match_what_the_planner_would_have_produced():
    """The GUI and the planner must not drift apart."""
    for week in FA25_PLAN:
        recomputed = apply_mode_defaults(week, {"mode": week["mode"]})
        for field in ("post_at", "remind_at", "ping_at", "due"):
            assert recomputed[field] == week[field], f"week {week['week']} {field}"


def test_setting_skip_clears_every_time():
    fields = apply_mode_defaults(FA25_PLAN[0], {"mode": "SKIP"})

    assert fields["skip"] is True
    assert fields["post_at"] is None
    assert fields["ping_at"] is None
    assert fields["due"] is None
    assert fields["remind_at"] == []


def test_explicit_times_win_over_the_mode_defaults():
    fields = apply_mode_defaults(
        FA25_PLAN[0], {"mode": "FRIDAY", "due": "2025-08-29T12:00:00"}
    )
    assert fields["due"] == "2025-08-29T12:00:00"
    assert "post_at" not in fields  # nothing else was invented


def test_editing_times_without_a_mode_leaves_them_alone():
    fields = apply_mode_defaults(FA25_PLAN[0], {"due": "2025-08-31T20:00:00"})
    assert fields == {"due": "2025-08-31T20:00:00"}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def test_get_plan_returns_sixteen_weeks_and_meta(monkeypatch):
    response, _ = _call(monkeypatch, _event("GET", "/plan", query={"sem": "Fa25"}))
    body = json.loads(response["body"])

    assert response["statusCode"] == 200
    assert len(body["weeks"]) == 16
    assert [w["week"] for w in body["weeks"]] == list(range(1, 17))
    assert body["meta"]["instruction_begins"] == "2025-08-25"


def test_get_plan_defaults_to_the_current_semester(monkeypatch):
    response, _ = _call(monkeypatch, _event("GET", "/plan"))
    assert response["statusCode"] == 200
    assert "sem" in json.loads(response["body"])


def test_editing_a_week_marks_it_overridden(monkeypatch):
    response, table = _call(
        monkeypatch,
        _event(
            "POST",
            "/plan/week",
            {
                "sem": "Fa25",
                "week": 3,
                "passphrase": PASSPHRASE,
                "fields": {"mode": "FRIDAY"},
            },
        ),
    )

    assert response["statusCode"] == 200
    stored = table.items[("Fa25", 3)]
    assert stored["overridden"] is True
    assert stored["mode"] == "FRIDAY"
    assert stored["due"] == "2025-09-12T23:59:00"


def test_an_overridden_week_survives_a_planner_rerun(monkeypatch):
    """The whole point of the flag: the GUI edit must stick."""
    _, table = _call(
        monkeypatch,
        _event(
            "POST",
            "/plan/week",
            {"sem": "Fa25", "week": 3, "passphrase": PASSPHRASE, "fields": {"mode": "SKIP"}},
        ),
    )

    planner.write_plan(table, planner.build_plan(FA25))
    assert table.items[("Fa25", 3)]["mode"] == "SKIP"


def test_editing_rosters_takes_them_out_of_the_refreshers_hands(monkeypatch):
    _, table = _call(
        monkeypatch,
        _event(
            "POST",
            "/plan/week",
            {
                "sem": "Fa25",
                "week": 3,
                "passphrase": PASSPHRASE,
                "fields": {"rosters": {"TL": ["U_HUMAN"]}},
            },
        ),
    )
    assert table.items[("Fa25", 3)]["rosters_overridden"] is True


def test_editing_a_missing_week_is_a_404(monkeypatch):
    response, _ = _call(
        monkeypatch,
        _event(
            "POST",
            "/plan/week",
            {"sem": "Fa25", "week": 99, "passphrase": PASSPHRASE, "fields": {"skip": True}},
        ),
    )
    assert response["statusCode"] == 404


def test_editing_without_sem_or_week_is_a_400(monkeypatch):
    response, _ = _call(
        monkeypatch,
        _event("POST", "/plan/week", {"passphrase": PASSPHRASE, "fields": {"skip": True}}),
    )
    assert response["statusCode"] == 400


def test_a_bad_field_is_a_400_not_a_500(monkeypatch):
    response, _ = _call(
        monkeypatch,
        _event(
            "POST",
            "/plan/week",
            {"sem": "Fa25", "week": 3, "passphrase": PASSPHRASE, "fields": {"due": "soon"}},
        ),
    )
    assert response["statusCode"] == 400
    assert "ISO 8601" in json.loads(response["body"])["error"]


def test_malformed_json_is_a_400(monkeypatch):
    event = _event("POST", "/plan/week")
    event["body"] = "{not json"
    response, _ = _call(monkeypatch, event)
    assert response["statusCode"] == 400


def test_unknown_route_is_a_404(monkeypatch):
    response, _ = _call(monkeypatch, _event("GET", "/nope"))
    assert response["statusCode"] == 404


def test_options_preflight_is_answered(monkeypatch):
    response, _ = _call(monkeypatch, _event("OPTIONS", "/plan/week"))
    assert response["statusCode"] == 204
    assert response["headers"]["Access-Control-Allow-Origin"] == "https://example.github.io"


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------

def test_cors_header_names_the_pages_origin(monkeypatch):
    response, _ = _call(monkeypatch, _event("GET", "/plan", query={"sem": "Fa25"}))
    assert response["headers"]["Access-Control-Allow-Origin"] == "https://example.github.io"


def test_dynamodb_decimals_serialise_as_plain_numbers(monkeypatch):
    table = FakeTable([{"sem": "Fa25", "week": decimal.Decimal(1), "n": decimal.Decimal("1.5")}])
    response, _ = _call(monkeypatch, _event("GET", "/plan", query={"sem": "Fa25"}), table)

    body = json.loads(response["body"])
    assert body["weeks"][0]["week"] == 1
    assert body["weeks"][0]["n"] == 1.5


def test_an_unexpected_error_does_not_leak_internals(monkeypatch):
    class Exploding(FakeTable):
        def query(self, **kwargs):
            raise RuntimeError("secret table detail")

    response, _ = _call(monkeypatch, _event("GET", "/plan", query={"sem": "Fa25"}), Exploding())

    assert response["statusCode"] == 500
    assert json.loads(response["body"]) == {"error": "internal error"}
