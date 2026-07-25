# Weekly Report Bot

Slack bot that collects weekly reports from CRC subteams, on a **date-driven
plan** rather than recurring crons. See [REDESIGN.md](REDESIGN.md) for the full
design rationale.

Each semester is stored in DynamoDB as exactly **16 numbered Mon–Sun weeks**.
The plan is the single source of truth: holidays, moved deadlines, skipped
weeks and one-off human edits are all just data, so the AWS schedule set stays
tiny and fixed.

## How it works

| Lambda | Trigger | Does |
|---|---|---|
| `planner.py` | Aug 1 & Jan 1 (annual) | Reads the Cornell calendar, computes the 16-week plan, writes it |
| `refresher.py` | Thursdays 03:00 ET | Pulls Slack IDs from the member sheet into upcoming weeks |
| `dispatcher.py` | Daily 08:00, 18:00, 19:00, 21:00, 23:00 ET | Acts on whatever the plan says is due right now |
| `api.py` | API Gateway | `GET /plan`, `POST /plan/week`, `POST /plan/reset` for the dashboard |

Each week is classified generically from the parsed break dates, never from
hardcoded holiday names:

| Mode | Thread posts | Reminders | Ping | Due |
|---|---|---|---|---|
| `NORMAL` | preceding Fri 19:00 | Sun 18:00, 21:00 | Sun 23:00 | Sun 23:59 |
| `FRIDAY` / `TUESDAY` / … | that day 08:00 | that day 18:00, 21:00 | that day 23:00 | that day 23:59 |
| `SKIP` | — | — | — | — |

A week whose Sunday falls in a break moves to the last in-session weekday; a
week with no in-session weekday is skipped but keeps its number. Outside the
semester there is simply no plan entry, so every cron is a no-op.

## Deploy

**1. Bundle Python dependencies into the Lambda asset**

```bash
cd lambda && pip install -r requirements.txt -t . && cd ..
```

**2. Configure `.env` in the repo root**

```
SLACK_TOKEN=xoxb-…
SLACK_CHANNEL=tasks-weekly-report
SLACK_CHANNEL_ID=C03UYNDBUPQ      # channel ID, not name — see below
MEMBER_SHEET_ID=…                 # the /d/<ID>/ part of the sheet URL
MEMBER_SHEET_GRID=0               # gid of the tab to read
SUBTEAMS=                         # optional allowlist; blank = use the sheet's
EDIT_PASSPHRASE=…                 # gates dashboard edits; do not reuse an old value
PAGES_ORIGIN=https://<org>.github.io
```

`SLACK_CHANNEL_ID` must be the **ID** (e.g. `C03UYNDBUPQ`), because
`conversations_history` / `conversations_members` do not accept channel names.
Get it from the channel details or "Copy link" in Slack.

**3. Deploy**

```bash
npm install
npm run build && npx cdk synth && npx cdk deploy
```

First time in a fresh account, run `npx cdk bootstrap` first.

**4. Seed the current semester**

The planner only fires on Aug 1 / Jan 1, so seed it once by hand:

```bash
aws lambda invoke --function-name <PlannerLambda> \
  --payload '{"term":"Fa","year":2025}' /dev/stdout
```

Add `"dry_run": true` to see the computed plan without writing it.

**5. Publish the dashboard**

Set `API_BASE` in `docs/index.html` to the `ApiUrl` stack output, then enable
GitHub Pages on the `docs/` folder. Confirm the parsed calendar dates in the
banner — that is the one ~30-second human step per semester.

## The member sheet

The refresher supports two ways of reading the roster, chosen by whether
`GOOGLE_SA_SECRET` is set:

**Private sheet (current setup).** The sheet is read through the Sheets API as a
service account. The key lives in Secrets Manager and is fetched at runtime, so
it is never in the deployment bundle:

```bash
aws secretsmanager create-secret \
  --name weekly-report/google-service-account \
  --secret-string file://lambda/service_account.json
```

Then set `GOOGLE_SA_SECRET=weekly-report/google-service-account` in `.env`, and
**share the sheet (Viewer) with the service account's `client_email`** — a
private sheet the account cannot see returns 403, not an empty roster.

**Public sheet.** Leave `GOOGLE_SA_SECRET` unset and make the sheet
link-viewable; the refresher then reads its CSV export and needs no credentials
at all.

Either way the roster columns are found by name, accepting both `Slack ID` /
`Subteam` and `SlackID` / `Team`, with the header row located by scanning rather
than by a fixed offset.

## Secrets

Credentials must **never** be committed or bundled. The CDK asset explicitly
excludes `.env`, `service_account.json` and local data artifacts, so they are not
uploaded with the function code; everything else is passed as environment
variables.

If a token has ever been committed, rotate it — removing the file does not
remove it from git history.

## Tests

```bash
cd lambda && python -m pytest -q
```

The planner tests assert the Fall 2025 and Spring 2026 tables from
REDESIGN.md §3 exactly, and the dispatcher tests check that every timestamp the
planner emits is caught by exactly one cron hour — that seam is where the two
halves would otherwise silently drift apart.

## Other commands

* `npx cdk diff` — compare against the deployed stack
* `npx cdk destroy` — tear down (the plan table is `RETAIN`, so it survives)
