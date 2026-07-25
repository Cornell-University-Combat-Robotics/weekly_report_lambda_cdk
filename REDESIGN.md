# Weekly Report Bot — Redesign Spec

A date-driven rebuild of the existing `weekly_report_lambda_cdk` repo. This is the
marching-orders document: commit it to the repo and feed it to Claude Code.

Repo: https://github.com/Cornell-University-Combat-Robotics/weekly_report_lambda_cdk

---

## 0. Decisions locked

1. **Build approach:** iterate on the existing repo with Claude Code. Keep the Slack
   posting path, the "who hasn't submitted" reminder, and the deploy harness. Replace
   the scheduling abstraction (recurring crons + governor toggling) with a date-driven
   plan + dispatcher.
2. **Spring-break lead-in week** (classes Mon–Fri but Sunday is inside the break):
   treat like Feb/fall break — move to **Friday**. The fully-in-break week is
   **skipped** (no Slack activity) but still consumes a week number.
3. **Week counting around a skip:** the skipped week keeps its number.
4. **GUI editing:** small **password-protected API** (API Gateway + Lambda). The static
   GitHub Pages site reads the plan and posts edits; the passphrase is entered at edit
   time, never embedded in the page bundle.
5. **Always 16 numbered weeks.** Week 16 is the Mon–Sun week after week 15 and lands in
   the study/exam period; it still counts and is the **last** week. Nothing is ever
   scheduled past week 16. Fall therefore collects 16 reports; spring collects 15 (week
   11 skipped but numbered).

## 0.1 The 16-week rule

The planner always produces exactly 16 numbered weeks, tiled Mon–Sun from the
instruction-start week, with holiday rules applied (§3). Week 16 falls in study/exams
and is NORMAL-mode (thread posted the preceding Friday). It is the final week — the
dispatcher takes no action before week 1 or after week 16. If you ever want week 16's
thread posted earlier than its preceding Friday, change it in the GUI (per-week
override); the default is consistent NORMAL timing.

---

## 1. Architecture

The current model pre-creates recurring weekly cron schedules per team per weekday and
toggles "groups" on/off weekly via a governor. That fights date facts (holidays, skips,
one-off edits). The redesign makes **DynamoDB the single source of truth** and reduces
AWS schedules to a handful of static triggers.

```
                         ┌────────────────────────────────────────────┐
   Aug 1 / Jan 1 cron ──▶│ Planner Lambda                             │
                         │  • fetch Cornell calendar (start + breaks) │
                         │  • compute 16-wk plan w/ holiday rules     │
                         │  • write weeks to DynamoDB (skip overridden)│
                         └───────────────┬────────────────────────────┘
                                         │ writes
                                         ▼
   Thursday cron ───────▶┌─────────────────────────────┐   ┌──────────────────────────┐
   (gated to in-window)  │ Roster Refresher Lambda     │   │   DynamoDB: Plan table   │
                         │ • poll Google Sheet         │──▶│  PK sem (e.g. Sp26)      │
                         │ • per-subteam @mentions     │   │  weeks[] = source of truth│
                         │ • write into upcoming week  │   └─────────┬────────┬───────┘
                         └─────────────────────────────┘        read │        │ read/write
                                                                      ▼        ▼
   08:00 / 18:00 ───────▶┌─────────────────────────────┐   ┌──────────────────────────┐
   21:00 / 23:00 crons   │ Dispatcher Lambda           │   │ API Lambda (API Gateway) │
                         │ • read today's plan entry   │   │  GET  /plan   (read)     │
                         │ • post thread / remind /    │   │  POST /plan/week (edit,  │
                         │   ping (submission check)   │   │        passphrase-gated) │
                         │ • post to Slack             │   └─────────────┬────────────┘
                         └───────────────┬─────────────┘                │ fetch (CORS)
                                         │ Slack API                    ▼
                                         ▼                   ┌──────────────────────────┐
                                   ┌───────────┐             │ GitHub Pages (static)    │
                                   │   Slack   │             │  semester dashboard +    │
                                   └───────────┘             │  edit-one-week modal     │
                                                             └──────────────────────────┘
```

**Why out-of-range silence is free:** no plan entry for today → dispatcher does nothing.
Summer, winter break, before week 1, and after week 16 are silent automatically. Skipped
weeks have `skip=true` and are likewise no-ops.

### Components

- **Planner Lambda** — triggers: two annual EventBridge schedules (Aug 1, Jan 1). Reads
  the calendar, computes the 16-week plan, writes weeks that are not `overridden=true`.
- **Roster Refresher Lambda** — trigger: weekly Thursday cron, but it self-gates to do
  nothing outside the active window. Polls the Google Sheet, builds per-subteam mention
  strings, writes them into the next thread-post event(s) due in the next ~5 days
  (covers Friday-this-week and a following Tuesday-move week populated the prior
  Thursday). Writing ahead means the post is deterministic and a human can eyeball/fix
  it in the GUI before it fires.
- **Dispatcher Lambda** — triggers: four fixed daily crons at 08:00, 18:00, 21:00, 23:00
  America/New_York. Each run loads today's plan entry and acts based on the time-of-day:
  08:00 = post threads (Friday/Tuesday-move weeks), 18:00 & 21:00 = general reminders,
  23:00 = ping non-submitters. (NORMAL-week threads post the preceding Friday at 19:00 —
  add a 19:00 cron, or fold it into the 18:00 run.) Absorbs the existing
  `lambda_function.py` logic.
- **API Lambda + API Gateway (HTTP API)** — `GET /plan`, `POST /plan/week`. Passphrase
  checked server-side against an env secret. CORS allows the Pages origin.
- **GitHub Pages site** — static dashboard; reads via GET, edits via passphrase-gated
  POST.

---

## 2. Data model (DynamoDB)

Single table, partition key `sem` (e.g. `Fa25`, `Sp26`). One item per semester holding
the whole plan, or one item per (sem, week) — per-week items are easier for partial
edits. Recommended: **per-week items**, PK `sem`, SK `week` (number).

```json
{
  "sem": "Sp26",
  "week": 4,
  "mode": "FRIDAY",                 // NORMAL | FRIDAY | TUESDAY | SKIP
  "week_start": "2026-02-09",       // Monday of the week (informational)
  "due": "2026-02-13T23:59:00",     // local ET
  "post_at": "2026-02-13T08:00:00",
  "remind_at": ["2026-02-13T18:00:00", "2026-02-13T21:00:00"],
  "ping_at": "2026-02-13T23:00:00",
  "skip": false,
  "overridden": false,              // true → planner won't touch it
  "rosters": {                      // written by the refresher; editable in GUI
    "TL":         ["U048E6QP8C8", "U047HT1JFAA"],
    "Marketing":  ["..."],
    "Autonomous": ["..."],
    "Kinetic":    ["..."],
    "Sportsman":  ["..."],
    "Infinity":   ["..."]
  },
  "roster_updated_at": "2026-02-12T03:00:00Z"
}
```

A small `meta` item per `sem` can store the parsed calendar (`instruction_begins`,
break ranges, last instruction day) for the GUI to display and for human confirmation.

Modes set the standard times; the GUI can override any individual field (which flips
`overridden=true` so the next planner run leaves it alone).

| mode | post | reminders | ping | due | day |
|---|---|---|---|---|---|
| NORMAL | Fri 19:00 (preceding Friday) | Sun 18:00, 21:00 | Sun 23:00 | Sun 23:59 | Sunday |
| FRIDAY | Fri 08:00 (same day) | Fri 18:00, 21:00 | Fri 23:00 | Fri 23:59 | Friday |
| TUESDAY | Tue 08:00 (same day) | Tue 18:00, 21:00 | Tue 23:00 | Tue 23:59 | Tuesday |
| SKIP | — | — | — | — | — |

> NORMAL preserves the current behavior (thread posted the *preceding* Friday evening,
> reminders Sunday). Move weeks post at 08:00 on the deadline day itself so people get
> the full day. FRIDAY and TUESDAY are not hardcoded special cases — they are the two
> outcomes the generic move rule (§3) produces for Cornell's break shapes.

---

## 3. Holiday rules — a general algorithm

Do **not** hardcode "February break = Friday move." Classify each week generically from
the parsed break/holiday ranges. This is what makes the bot survive Cornell shifting
break dates year to year (see §9). Run per week, in order:

1. Take the week's seven days (Mon–Sun) and its normal Sunday deadline `D`.
2. Mark each day `no-class` if it falls in a break range or a no-class holiday.
3. **If every weekday Mon–Fri is `no-class`** → **SKIP**. The week keeps its number; no
   Slack activity.
4. **Else if the Sunday `D` is `no-class`** → **MOVED**: set the deadline to the **latest
   in-session weekday** of that week (the last of Mon–Fri that is not `no-class`). Post
   08:00 that day; reminders 18:00 & 21:00; ping 23:00; due 23:59. A Saturday-start break
   resolves to **Friday**; a Wednesday-start break resolves to **Tuesday** — derived, not
   pinned to dates.
5. **Else** → **NORMAL**: thread the preceding Friday 19:00; reminders Sun 18:00 & 21:00;
   ping Sun 23:00; due Sun 23:59.
6. Always emit exactly **16** numbered weeks. Week 16 is the 16th Mon–Sun tile from the
   instruction-start week; it lands in study/exams, is NORMAL-mode, and is the last week.
   No event is ever scheduled before week 1 or after week 16.

### The named breaks, as worked examples of the rule

- **Fall break / Indigenous Peoples' Day** — break Sat–Tue, resume Wed. The week whose
  Sunday is in the break has classes Mon–Fri → rule 4 → last in-session weekday = Friday.
- **Thanksgiving** — break Wed–Sun, resume Mon. The week whose Sunday is in the break has
  classes Mon–Tue → rule 4 → last in-session weekday = Tuesday.
- **February break** — break Sat–Tue, resume Wed → same as fall break → Friday.
- **Spring break** — spans two Sundays. Lead-in week has classes Mon–Fri → rule 4 →
  Friday. The fully-in-break week has no in-session weekday → rule 3 → SKIP (numbered).
- **Single Monday holidays (Labor Day, MLK):** rules 3–4 don't fire (Sunday is in
  session), so the week is NORMAL. **But** they can shift the *instruction-start day*:
  Spring 2026 begins **Tuesday Jan 20** because Monday Jan 19 is MLK. The planner reads
  the real start date; week 1 is then a short Tue–Sun week with an unaffected Sunday
  deadline.

### Computed plan — Fall 2025 (verify against registrar)

Instruction begins **Mon Aug 25**; fall break Oct 11–14 (IPD Mon Oct 13, resume Wed
Oct 15); Thanksgiving Nov 26–30 (resume Mon Dec 1); last instruction day Mon Dec 8;
study Dec 9–11; exams begin Dec 12.

| wk | week range | mode | due |
|---|---|---|---|
| 1 | Aug 25–31 | NORMAL | Sun Aug 31 |
| 2 | Sep 1–7 | NORMAL | Sun Sep 7 |
| 3 | Sep 8–14 | NORMAL | Sun Sep 14 |
| 4 | Sep 15–21 | NORMAL | Sun Sep 21 |
| 5 | Sep 22–28 | NORMAL | Sun Sep 28 |
| 6 | Sep 29–Oct 5 | NORMAL | Sun Oct 5 |
| 7 | Oct 6–12 | **FRIDAY** | **Fri Oct 10** |
| 8 | Oct 13–19 | NORMAL | Sun Oct 19 |
| 9 | Oct 20–26 | NORMAL | Sun Oct 26 |
| 10 | Oct 27–Nov 2 | NORMAL | Sun Nov 2 |
| 11 | Nov 3–9 | NORMAL | Sun Nov 9 |
| 12 | Nov 10–16 | NORMAL | Sun Nov 16 |
| 13 | Nov 17–23 | NORMAL | Sun Nov 23 |
| 14 | Nov 24–30 | **TUESDAY** | **Tue Nov 25** |
| 15 | Dec 1–7 | NORMAL | Sun Dec 7 |
| 16 | Dec 8–14 (study/exams) | NORMAL | Sun Dec 14 |

16 weeks, 16 reports. Week 8's Mon–Tue are in break but its Sunday is fine. Week 16's
thread posts the preceding Friday (Dec 12, first exam day).

### Computed plan — Spring 2026 (verify against registrar)

Instruction begins **Tue Jan 20** (MLK Mon Jan 19); Feb break Feb 14–17 (resume Wed
Feb 18); spring break Mar 28–Apr 5 (resume Mon Apr 6); last instruction day Tue May 5;
study May 6–8; exams begin May 9.

| wk | week range | mode | due |
|---|---|---|---|
| 1 | Jan 20–25 (Tue start) | NORMAL | Sun Jan 25 |
| 2 | Jan 26–Feb 1 | NORMAL | Sun Feb 1 |
| 3 | Feb 2–8 | NORMAL | Sun Feb 8 |
| 4 | Feb 9–15 | **FRIDAY** | **Fri Feb 13** |
| 5 | Feb 16–22 | NORMAL | Sun Feb 22 |
| 6 | Feb 23–Mar 1 | NORMAL | Sun Mar 1 |
| 7 | Mar 2–8 | NORMAL | Sun Mar 8 |
| 8 | Mar 9–15 | NORMAL | Sun Mar 15 |
| 9 | Mar 16–22 | NORMAL | Sun Mar 22 |
| 10 | Mar 23–29 | **FRIDAY** | **Fri Mar 27** |
| 11 | Mar 30–Apr 5 | **SKIP** | — (numbered, no report) |
| 12 | Apr 6–12 | NORMAL | Sun Apr 12 |
| 13 | Apr 13–19 | NORMAL | Sun Apr 19 |
| 14 | Apr 20–26 | NORMAL | Sun Apr 26 |
| 15 | Apr 27–May 3 | NORMAL | Sun May 3 |
| 16 | May 4–10 (study/exams) | NORMAL | Sun May 10 |

16 numbered weeks, week 11 skipped → 15 reports collected. Week 16's thread posts the
preceding Friday (May 8, last study day).

These tables double as unit-test fixtures for the planner.

---

## 4. Calendar source & robustness

The registrar page (`registrar.cornell.edu/academic-calendar`) is a JS-rendered Drupal
app — the existing BeautifulSoup `<table>`/`<tr>` scrape will not find rows. Use a more
stable source and fail safe:

1. **Primary:** parse the published PDF at
   `courses.cornell.edu/enrollment-credit-requirements/academic-calendar/academic-calendar.pdf`
   — plain text, lists Instruction Begins, each break, last instruction day.

   **Verified format (fetched 2026-07-25).** Two details cost us a silent bug, so
   they are written down here:

   - The PDF holds **one academic year at a time** (currently 2026–2027), split
     into `Fall <year>` / `Winter <y>-<y+1>` / `Spring <year+1>` sections. A
     parser must scope to the right section, or Spring inherits Fall's start
     date. Past semesters are simply absent — they only survive in the fallback
     table.
   - Breaks are **not** a date range on one line. They are a *pair* of rows:

     ```
     October 10 Fall Break Begins
     October 14 Instruction Resumes
     ```

     so the no-class span runs from the "Begins" date to the day *before*
     "Instruction Resumes" (Oct 10–13). Reading only the "Begins" row yields a
     one-day break, which quietly misclassifies the weeks either side — and
     still passes a naive plausibility check.

   Study/exam rows (`Study Period`, `Scheduled Exams`) must **not** be treated as
   no-class, or week 16 stops being a normal reporting week.
2. **Fallback:** a small hardcoded per-year table in the planner used if the
   fetch/parse fails or returns something implausible (e.g. a start date in the past).
3. **Human confirmation:** write the parsed `instruction_begins` + break ranges into the
   `meta` item and surface them in the GUI. If parsing ever drifts, a human fixes the
   start date in one click and re-derives the plan — no code change needed.

The planner must be **idempotent** and **override-aware**: re-running it recomputes
NORMAL/FRIDAY/TUESDAY/SKIP for every non-overridden week but never clobbers a week a
human edited in the GUI.

---

## 5. API contract

API Gateway HTTP API → API Lambda. JSON in/out.

- `GET /plan?sem=Sp26`
  → `{ "sem": "Sp26", "meta": {...}, "weeks": [ {week item}, ... ] }`. Read-only; may be
  open or lightly gated.
- `POST /plan/week`
  body: `{ "sem": "Sp26", "week": 4, "fields": { "mode": "NORMAL" | ..., "post_at": ...,
  "remind_at": [...], "ping_at": ..., "due": ..., "skip": bool, "rosters": {...} },
  "passphrase": "..." }`
  → validates passphrase server-side; sets `overridden=true`; writes; returns the updated
  week. Changing `mode` server-side recomputes the standard times unless explicit times
  are also supplied.
- *(optional)* `POST /plan/reset` `{ "sem": "...", "passphrase": "..." }` → clear
  overrides and re-run the planner.

**Auth:** passphrase compared to a Lambda env var (or Secrets Manager). Never ship it in
the static bundle — the page prompts at edit time and sends it over HTTPS. **CORS:**
allow the Pages origin, methods GET/POST, headers `content-type` (+ a passphrase header
if you prefer header over body). Do **not** reuse the guessable `CRCSlackBot` string for
the public API.

---

## 6. GitHub Pages GUI

Single static `index.html` (vanilla HTML/CSS/JS, no framework needed), published from a
`/docs` folder or `gh-pages` branch.

- **Semester view:** list/calendar of all 16 weeks. Each row: week #, date range, mode
  badge (Normal / Friday / Tuesday / Skip), post time, reminder times, ping time, due,
  and a per-subteam @mention preview. Skipped weeks greyed; overridden weeks badged.
- **Meta banner:** shows parsed instruction-begins + break dates; "these look wrong?
  edit" link.
- **Edit-week modal:** pick a mode (auto-fills standard times) or override individual
  times; toggle skip; edit rosters or "refresh from sheet". Save → passphrase prompt →
  `POST /plan/week` → optimistic UI update.
- **Read-only by default:** if no passphrase entered, controls are disabled with a
  "viewing only" note.
- Config: API base URL baked into the page; passphrase never baked in.

---

## 7. File-by-file change plan (for Claude Code)

**Keep / refactor**
- `lambda/lambda_function.py` → **dispatcher**. Keep `run_reminder`,
  `_collect_repliers_with_files`, `_fetch_channel_members`, `get_timestamps_two_days_prior`
  (generalize the "two days prior" window to "the deadline day for this mode"). Replace
  the DynamoDB week-increment block with "load today's plan entry; branch on time-of-day
  and mode."
- `lambda/load_member_info.py` + `lambda/member_slackid.py` → consolidate into **roster
  refresher** (`refresher.py`). Reuse the Sheets reading, but wrap it in a proper
  `lambda_handler`. **Remove the module-level top-to-bottom script** in
  `load_member_info.py` (it executes on import — bad in Lambda).
- `lib/weekly_report_lambda_cdk-stack.ts` → rework: DynamoDB plan table; 5 Lambdas
  (planner, refresher, dispatcher, api, keep `InitItem` only if still needed); 2 annual
  planner crons; 1 weekly Thursday refresher cron; daily dispatcher crons (08:00, 18:00,
  19:00, 21:00, 23:00); API Gateway HTTP API; IAM (scheduler perms become unnecessary —
  dispatcher needs DynamoDB read + Slack token; refresher needs DynamoDB write; planner
  needs DynamoDB write + outbound fetch; api needs DynamoDB read/write). **Remove** the
  per-team/per-day recurring schedules and the governor toggling entirely.
- `README.md` → update deploy + Pages instructions.

**New**
- `lambda/planner.py` → calendar fetch + the §3 general algorithm + plan write
  (idempotent, override-aware, always 16 weeks).
- `lambda/api.py` → GET/POST plan, passphrase check.
- `docs/index.html` (+ optional `docs/app.js`, `docs/style.css`) → the dashboard.
- `lambda/test_planner.py` → assert the Fall 2025 / Spring 2026 16-week tables above,
  including week 16 and the spring SKIP at week 11.

**Delete (dead / duplicate / one-off)**
- root `lambda_function.py`, `run_lambda.py`, `custom_scheduler.py`, `create_lambda.py`,
  `create_dynamodb.py`, `lambda_role.py`, `lambda/creator.py`, `lambda/governor.py`
  (logic absorbed/replaced), and the stray `lambda/dynamodb_data.json` /
  `lambda/filtered_sorted_output.csv` build artifacts.

**Fix while in there**
- `sem` key must be **generated from the current year** (planner running Aug 1 2026 →
  `Fa26`), not hardcoded. This removes the existing `Fa23`/`Fa24` drift.
- The missing `update_slack_ids.py` handler reference disappears with the refactor.
- `creator.py`'s missing `import os` becomes moot (file deleted).
- *(optional, for full year-proofing)* derive the subteam list from the sheet's distinct
  subteam values instead of hardcoding `TL/Marketing/Autonomous/Kinetic/Sportsman/Infinity`
  in multiple places, so a roster reorg needs no code change.

---

## 8. Deploy / runbook

1. `cd lambda && pip install -r requirements.txt -t . && cd ..` (bundle deps; replace the
   `bs4` scrape dep with a PDF parser, e.g. `pdfplumber`/`pypdf`).
2. Set env / `.env`: `SLACK_TOKEN`, `SLACK_CHANNEL`, `SLACK_CHANNEL_ID`,
   `MEMBER_SHEET_ID` (+ Google service account), `EDIT_PASSPHRASE`, `PAGES_ORIGIN`.
3. `npm run build && cdk synth && cdk deploy`.
4. One-time: invoke the planner manually to seed the current semester
   (`aws lambda invoke ... planner`), then open the GUI and confirm the parsed dates.
5. Publish `docs/` to GitHub Pages; set the API base URL in the page.
6. Sanity check: open the dashboard, verify the week table matches §3, send yourself a
   test post by temporarily pointing `SLACK_CHANNEL` at a test channel.

---

## 9. Year-after-year operation

**It runs unattended across years by design:**

- The two annual EventBridge crons (Aug 1, Jan 1) re-fire every year with no
  intervention and regenerate that semester's plan.
- The planner reads the **actual** instruction-start and break dates each run, so it
  adapts to year-specific shifts automatically — e.g. MLK pushing the spring start to a
  Tuesday, or breaks landing on different calendar dates.
- The holiday classification (§3) is **derived from break ranges, not hardcoded dates or
  break names**, so a break that shifts — or even a renamed/added break — still resolves
  correctly (Saturday-start → Friday, Wednesday-start → Tuesday, fully-in-break → skip).
- "No reminders outside the window" and "stop after week 16" are structural: absent or
  out-of-range plan entries are no-ops. Per-week DynamoDB items for a new semester simply
  replace the prior ones.

**The one yearly human touch (~30 seconds):** confirm the parsed calendar dates in the
GUI at the start of each semester. The likeliest failure mode is Cornell changing its
webpage/PDF format, which is *outside* the bot. The fallback table, the one-click GUI
correction of `instruction_begins`/break dates, and per-week overrides all exist to
absorb that without a code change.

**Operational items unrelated to the yearly logic:** rotate the Slack token and Google
service-account credentials when they expire; if the team's subteams change, either edit
the hardcoded list or (better) adopt the sheet-derived-subteams option in §7.

---

## 10. Suggested first prompt to Claude Code

> Read REDESIGN.md. We're refactoring this repo from the recurring-cron + governor model
> to the date-driven plan + dispatcher model described there. Start by (1) reworking the
> CDK stack to the new resource set, (2) writing `lambda/planner.py` with the §3 general
> classification algorithm (always 16 weeks) and a `test_planner.py` asserting the Fall
> 2025 and Spring 2026 tables — including week 16 and the spring week-11 SKIP — then stop
> and let me review before you touch the dispatcher and API. Run `cdk synth` and the
> tests after each step.
