# WebGSO: GSO Solar Data Platform (beta)

A central platform for GSO that **pulls** forecast and MMF (weather) data from every Large Scale
Solar (LSS) plant's rev5 web service, checks each response against the spec, stores the history
and shows which plants are compliant, late or broken.

This beta covers the first 8 pilot plants (KMLS, MALS, KALS, LMMS, BDWS, SIDS, SKNS, BKHS on
`https://ws3.gedine.work`) and runs locally with Docker Compose. AWS (ap-southeast-5) comes later.

## Run it

```bash
cp .env.example .env          # change JWT_SECRET and ADMIN_PASSWORD
docker compose up --build
```

Open http://localhost:8080 and sign in as `admin@gso.local` / `admin12345` (or whatever you set in
`.env`). API docs are at http://localhost:8000/api/docs. Report emails land in the local mail catcher at
http://localhost:8025 (Mailpit) until you set a real mail server in `.env` (see `.env.example`).

The worker polls every never-polled endpoint once at start-up, then follows the schedules.

**Offline demo** (no access to the plant host needed): run against the bundled mock plants, which
imitate what the pilot host served on 27 Nov 2026, quirks included. Start from an empty database,
because plant URLs are only seeded on first start.

```bash
docker compose down -v
SEED_BASE_URL=http://mockplants:9000 docker compose --profile mock up --build
```

The mock makes SIDS serve a short day-ahead forecast (`MOCK_FAULTS=sids:dayahead`) so there's an
alert to look at. It also makes BKHS require the API key `demo-key-1234` (`MOCK_REQUIRE_KEY`): until you
set that key under Plants, its tests fail with HTTP 401. The mock answers for any mnemonic, so you
can try adding new plants against it.

**Offline demo at fleet scale:** add `SEED_MOCK_FLEET=120` (again on an empty database) to seed the 8
pilots plus 112 simulated plants spread over four regions. The mock gives about one in five simulated plants a
fixed fault (unreachable, short day-ahead, slow, or broken MMF) so the problem-first views have something to show.

```bash
docker compose down -v
SEED_MOCK_FLEET=120 SEED_BASE_URL=http://mockplants:9000 docker compose --profile mock up --build
```

**Simulated plants (SOAP and REST).** With `--profile mock`, the simulator also serves 25 fictitious
plants, which the platform adds automatically, marked **SIM**: 5 SOAP plants (`SIMS01`..`SIMS05`,
e.g. Bukit Contoh Solar) and 20 REST plants (`SIMR01`..`SIMR20`). About a third have deliberate faults:

| Plant | Fault |
|---|---|
| SIMS02 | SOAP 1.2 with WS-Security username/password (works; shows the auth option) |
| SIMS03 | Day-ahead answers with a SOAP Fault |
| SIMS04 | Replies in an old namespace; Day-ahead values use decimal commas |
| SIMS05 | Rolling items miss Unit/EndTime; MMF reply is cut off (invalid XML) |
| SIMR03 / SIMR06 / SIMR09 / SIMR12 | Down (503) / slow (4 s) / Day-ahead 10 slots short / MMF HTTP 500 |
| SIMR15 / SIMR18 / SIMR20 | Wrong PlantName / needs an X-API-Key (stored) / negative Rolling values |

Simulated plants are hidden on the fleet board unless you pick "Simulated" or "Real and simulated",
and they are left out of report emails unless a recipient list ticks "Include simulated plants".
Plants page: **Remove all simulated plants** deletes them and their data (they then stay away after
restarts); **Add simulated plants** brings them back.

**SOAP plant APIs.** Each plant API is REST or SOAP (Plants > Edit > Endpoints > Protocol). SOAP
settings: service URL, operation, SOAPAction, SOAP 1.1/1.2 and namespace, or **Load from WSDL**.
Authentication adds HTTP Basic and WS-Security UsernameToken. There is no GSO SOAP specification
yet, so the platform uses a "GSO SOAP profile" (rev5 field names inside the envelope, see
`backend/app/soap.py`); replies are converted to the rev5 JSON shape, so checks, alerts, reports,
templates and emails work unchanged. The raw-response view shows the received XML with findings
highlighted, plus a Converted tab.

**Without Docker:** Postgres 16, Python 3.12+, Node 20+.

```bash
cd backend && pip install -r requirements.txt
export DATABASE_URL=postgresql+asyncpg://gso:gso@localhost:5432/gso
uvicorn app.main:app --port 8000      # API
python -m app.worker                  # scheduler + poller (second terminal)
pytest                                # validation and scheduler tests
cd ../frontend && npm install && npm run dev   # http://localhost:5173
```

## What's in the beta

| Part | Where | Notes |
|---|---|---|
| Poller | `backend/app/poller.py` | Async httpx, GET only, retries with backoff, at most 32 calls in flight and 4 per host (all 8 pilots share one host), one pooled client. Every call is logged in `poll_attempt`. |
| Raw landing | `raw_response` (JSONB) | Every distinct response kept exactly as received, with a SHA-256 hash, for disputes. Unchanged responses are not stored twice. |
| Tolerant validation | `backend/app/validation.py` | Nothing is rejected. Each rule break is stored as a **major** or **minor** flag in `compliance_flag`. Valid means no major flag. |
| Typed data | `forecast_snapshot`, `forecast_point`, `mmf_reading` | One snapshot per distinct forecast version. Extra MMF parameters are kept and marked non-standard. |
| Scheduler | `backend/app/scheduler.py` | Reads `poll_schedule` every 30 s. Each schedule targets all plants or chosen plants (`plant_ids`, NULL = all) and all APIs or chosen APIs (`categories`, NULL = all). Where several enabled, in-date schedules match a plant and API, the most specific governs (`precedence()`): chosen plants beat all plants and fewer plants beat more, then fewer APIs beat more, then a dated schedule beats an open-ended one, then the newest wins. The **Schedules** page lists schedules in that order, shows how many plant/API pairs each one governs, and has a paged **Which schedule governs each plant** grid (`GET /api/v1/admin/schedules/coverage`). Interval (with an optional time window) or cron, in Malaysia time. Edits apply on the next tick. A Postgres advisory lock keeps it to one scheduler, and `schedule_run` stops a pull from running twice. |
| Deadlines | `compliance_deadline`, `deadline_check` | Stored separately from pull times. Each one is marked met, late or missed after its grace period. |
| Alerts | `alert_log` | Raised from major flags, failed calls and missed deadlines. Cleared automatically when a later poll is clean. This is an audit log only, so nothing is sent out. |
| Auth | `backend/app/auth.py`, `routers/auth.py` | Self-registration, then admin approval. Passwords use Argon2, sessions use JWT, and an account locks for 15 minutes after 5 failed logins. Roles (multi-tenant): `super_admin` (SAE Malaysia, owns the app, the only role with the **Users** page: add users by email with a temporary password, approve sign-ups, set roles, pick plant acronyms, reset passwords), `gso` (sees every plant and runs the platform: plants, schedules, Retrieve now, email reports, audit; no user management) and `plant_user` (read-only, limited to the plant acronyms assigned to them, one or more; enforced on every API query, including the fleet board, reports and exports, alerts and raw submissions). Older role names migrate on start (`gso_admin` to `super_admin`, `gso_user` to `gso`, `lss_user` to `plant_user`). Everyone can change their own password from **Your account** (click your name). |
| Plant registry | `routers/admin.py`, `frontend/src/pages/PlantsAdmin.tsx` | Admins add, view, edit and delete plants under **Plants**. Each plant has its own URL per endpoint (with a "fill from base URL" helper) and an optional API key, sent as a header (default `X-API-Key`) or a query parameter (default `apiKey`). Keys are encrypted at rest with `PLANT_KEY_SECRET` (Fernet), never returned by the API, shown only as their last 4 characters, and kept out of logs. The poller reads the registry on every tick, and a newly live plant gets its first poll within 30 s. Deleting a plant asks you to type its mnemonic and also removes its history. Set the status to *suspended* to stop polling but keep the history. |
| Retrieval switches (Retrieval on/off) | `GET/PUT /api/v1/admin/polling[/fleet\|/types/{category}]`, `PUT /admin/plants/{mnem}/polling`, `PUT /admin/plants/{mnem}/endpoints/{category}/polling` | Enable or disable scheduled polling at four levels on the **Plants** page: the whole fleet (asks before pausing), each endpoint type for all plants, each plant, and each endpoint of each plant. An endpoint is polled only when all four are on and the plant is live. The Plants page and the fleet board show why an endpoint is paused, and every toggle goes to the audit log. Manual Test still works while polling is paused. |
| Endpoint tests | `POST /api/v1/admin/plants/{mnem}/test[?category=]` | The **Test** button on each endpoint and **Test all** on each plant (or for all plants) call the endpoints now with the stored key. Each result shows **Pass** (HTTP 200 and rev5-compliant), **Pass with warnings** (minor deviations only) or **Fail** (unreachable, error status, or a major rule broken), with the HTTP status, response time, size and the main reason. Test calls are stored like any other poll. |
| Retrieve now | `POST /api/v1/admin/poll-now`, `frontend/src/pages/PollNow.tsx` | Ad-hoc poll of the whole fleet, one plant, one API type or one API of one plant, from the **Retrieve now** page, **Retrieve whole fleet now** and each plant's **Retrieve now** on the fleet board, or **Retrieve this API now** in the inspector. Each result shows the verdict, the raw JSON with the findings highlighted, and the alerts it raised, kept open or cleared. It runs even while polling is paused, and each call is stored, validated and alerted on like a scheduled poll, marked as manual (`poll.manual` in the audit log). Every run is saved in `poll_run` with its own link (`#/admin/poll/runs/{id}`, API `GET /api/v1/admin/poll-runs[/{id}]`). The page lists recent runs (click a run's time to open its data in Reports, in the client template as one submission at the run's start minute; **Run details** opens the run itself) and reopens the latest one when you come back, so results, raw JSON and the alerts as they stood after the run are never lost. **Test** on the Plants page uses the same calls but is meant as a quick pass/fail check of a plant's setup. |
| Reports | `GET /api/v1/reports/rows`, `GET /api/v1/reports/export.csv`, `backend/app/routers/reports.py`, `backend/app/flatten.py`, `frontend/src/pages/Reports.tsx` | Polled data as a spreadsheet-style table and as CSV, from the **Reports** menu (all roles; LSS users see only their plants). Filters: plants (or whole fleet), API, poll time (presets or a custom from/to in MYT), latest poll only per plant and API, one poll (`attempt`) or one Poll now run (`run`). Views: **Forecast points** (one row per slot), **MMF readings** (one row per station and parameter), **As received** (the raw JSON flattened, field names and values exactly as sent, so non-spec responses export too) and **Submission log** (one row per submission). The table is a spreadsheet-style grid (`frontend/src/Sheet.tsx`): column letters and row numbers, gridlines, frozen header and first column, drag-to-resize columns (double-click to fit), a filter box under every column (`backend/app/colfilter.py`: contains, `=exact`, `>5`, `1..10`, dates, `blank`), cell selection by click/drag/Shift+arrows, Ctrl+C to paste into Excel, a name box and formula bar, an Excel-style Count/Sum/Average/Min/Max status bar, and full screen. A **Forecast by slot** view puts forecasts side by side like a worksheet (one row per slot, one column per plant poll, up to 100). Columns sort and filter on the server across all rows, pages of 100 to 1,000 rows, and **Download CSV** exports the whole selection with the same filters and sort (UTF-8 with BOM for Excel, streamed). The filters live in the URL so a report can be bookmarked. Shortcuts: **View as table** / **Download CSV** in the raw JSON inspector and on a Poll now run, **Reports** on each plant page. |
| Client report templates | `GET /api/v1/reports/template`, `GET /api/v1/reports/template.xlsx`, `backend/app/client_templates.py`, `frontend/src/pages/ReportTemplate.tsx` | The default **Client template** format on Reports (the old table stays under **Table view**). Forecast APIs use the client's DDQ layout (title band, Data Type / Submission Date & Time / Data Interval / Data Timestamp Range, then Submission Date & Time, Year, Month, Day, Period and one column per LSS, named by plant instead of API-001..). A submission is one scheduled pull (or the poll minute for a manual poll); **Latest submission only** keeps each plant's latest poll. MMF uses one sheet per parameter (LSS, **MMF** station, HR1..HRn = intervals; up to 2,976 interval columns per sheet) plus an **Anomaly Report**. MMF rows are either each station (**Individual MMF**) or one **Average** row per LSS (mean of the stations that reported). An interval with no reading is written as **0, highlighted** (amber online and in Excel) and listed in the Anomaly Report as a zero fallback; those zeros are never counted in any average. Intervals: MMF 5, 15 or 60 minutes (mean of readings in the interval); forecasts 15 or 60 minutes (60 = mean of the hour's four 15-minute points). Viewed online with Excel-like sheet tabs; **Download Excel (.xlsx)** writes the same workbook in the client's styling. |
| Email reports | `backend/app/mailer.py`, `routers/emails.py`, `frontend/src/pages/Recipients.tsx`, tables `recipient_list`, `report_email` | **Email reports** in the admin menu. A **recipient list** gets a running code when created (RL-001, RL-002 .., never reused), a name, one or more addresses and which report it receives (all APIs or chosen ones; forecast interval 15/60 min; MMF interval 5/15/60 min, Individual or Average). Each retrieval schedule can be paired with one or more lists (**Email the report to** in the schedule editor; `poll_schedule.recipient_list_ids`). After each **scheduled** retrieval finishes (every pull of that schedule for that due time is done, or 45 minutes have passed), the worker emails each paired list once: one .xlsx per API in the client template (the same workbook as the Reports download, for exactly the plants that schedule retrieved), and a body listing failed, invalid and skipped plants, zero-filled MMF intervals and the open alerts. Retrieve now, Test and the start-up sweep never email. A list paired later only gets retrievals from then on. Sending is retried after 1, 5, 15 and 60 minutes (`EMAIL_RETRY_MINUTES`), then marked failed. **Sent emails** is the log (`report_email`): every email with recipients, subject, attachments, status, tries and the last error, plus **Resend**; **Send test** checks a list or any address, and **Email latest** on a schedule sends its latest finished retrieval now. SMTP comes from `.env` (`SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `MAIL_FROM`); with docker compose it defaults to the **Mailpit** catcher at http://localhost:8025, so nothing reaches real inboxes until you set a real server. |
| Admin | `routers/admin.py` | Approve users and assign roles and plants, edit schedules and deadlines. Every change goes to `audit_event`. |
| Raw response inspector | `GET /api/v1/attempts/{id}/inspect`, `frontend/src/pages/Inspect.tsx` | Click any status on the fleet board (or Inspect on a plant's polls, or "see where" on a test result) to open that poll's raw JSON. Every place that caused a finding is highlighted: red for major, amber for minor. Hover a highlight to see the rule and reason. Click a finding to step through its places. Each validation flag records JSON Pointers to the offending fields. The inspector re-runs the validator on the stored body to place them, so no extra data is stored per poll. Raw responses are kept byte-for-byte as sent (`body_text`) as well as in JSONB. |
| Dashboard | `frontend/` | React + Vite. Pages: fleet board, plant drill-down (forecast chart, MMF table, flags, raw JSON, recent polls), alert log, compliance (24 h rule summary and deadlines), and admin pages for users, schedules and the audit log. |
| Guided tour | `frontend/src/Tour.tsx`, `PUT /api/v1/auth/me/tour` | A step-by-step bubble tour that opens each page and briefly explains it (fleet board, alerts, compliance, reports, and for admins Poll now, Plants, Users, Schedules and Audit). It opens at every login until the user ticks **Don't show this again**, which is saved on their account (`app_user.show_tour`) so it follows them across browsers. **Help** in the header reopens the tour anytime and turns the tour at login back on. Admin-only steps are skipped for other roles; works in both themes and with the keyboard (arrows, Esc). |

### Built for 100+ plants

The fleet will grow past 100 plants, so every page is built to stay usable at that size (tested with 120
simulated plants, 600 endpoints):

| Where | What |
|---|---|
| Fleet board | Server-side search (mnemonic, name, region), filter chips with counts (with problems, unreachable, non-compliant, open alerts, minor, not polled, paused, healthy), filter by one API, region or plant status, **problems-first** sort (or most alerts, stalest poll, name, region, capacity), group by region with per-region problem counts, compact density, pages of 25 to 250 (or all). Tiles are clickable filters. Admins tick plants (or "select all N matching") to Poll now, pause or resume polling, or open their reports and alerts. Filters live in the URL. |
| Plants | Search and filter by region, status, polling and API-key state, sort, paging. Bulk actions on ticked plants: Poll now, Test (4 at a time), pause or resume polling (whole plant or one API type), set status, set region. **Import plants** pastes rows from a spreadsheet or CSV (`POST /api/v1/admin/plants/import`, with a check step first). Each plant has an optional **region**. |
| Plant pickers | One searchable multi-select (`frontend/src/components.tsx`) with region shortcuts and "select these N", used by Poll now, Reports, Alerts, Compliance and user plant access. |
| Poll now | Any set of plants (or a region) and APIs. Runs in the background with a progress bar, so a 600-call fleet run doesn't time out; results open with problems first, with verdict filters, search and "show more". |
| Alerts, Compliance, Audit | Server-side paging, filters (plants, region, API, severity, rule, acknowledged, free text), "most alerts / most breaks" plant shortcuts, and bulk acknowledge (`POST /api/v1/alerts/ack`). `GET /alerts`, `/compliance/summary`, `/compliance/deadlines` and `/admin/audit` now return `{rows, total, ...}`. |
| Reports | Forecast by slot takes up to 300 plant polls as columns. |
| Poller and scheduler | One pooled HTTP client; at most `POLL_CONCURRENCY` (32) calls in flight and `POLL_PER_HOST` (4) per host; polls use at most half the DB pool (`DB_POOL_SIZE`, 20) so the API is never starved. Due pulls are queued in one multi-row insert per tick, a pull is skipped (status `skipped`) while the previous one for that endpoint is still running, and deadline checks are judged with two batched queries instead of several per plant. The board reads the latest poll per endpoint with an indexed lookup instead of scanning all poll history. |

### Validation rules (decided 27 Nov 2026)

| Endpoint | Expected | Tolerances |
|---|---|---|
| rolling | 96 points from the next 15-min slot after the pull | 97th or later points: minor, first 96 kept. Start one slot off: minor. |
| dayahead | 480 points (5 × 96) from 00:00 tomorrow | Before the 10:00 deadline, today's start (the previous publication) is accepted, and so is tomorrow's (published early). |
| weekahead | 864 points (9 × 96) from Saturday 00:00 | The Saturday after the latest Wednesday 12:30 deadline. Next week's set is accepted if published early. |
| fourmonthsahead | 1st of next month for 4 calendar months (e.g. 11,616 points Dec to Mar) | From the 25th onward expects next month, before that the current month. The count comes from the calendar. |
| mmf | rev5 `MMFResponse`, all 12 parameters, reading less than 10 min old | String values, `C`/`o` units, upper-case PlantName, NaN and sentinels (-3276.8), extra parameters: all minor. A non-rev5 shape (KMLS/KALS): major and not ingested. |

These apply to every forecast: mandatory keys, PlantName matches the plant, 15-min aligned slots,
EndTime = StartTime + 15 min, no gaps or duplicate slots, numeric values, no negative values, and
at most capacity + 5% when capacity is set. `ForecastValue` sent as a string is a minor flag.
`ForecastCategory` names are minor, because data is mapped by endpoint, not by that field.

All 5 endpoints of all 8 pilot plants are polled, including the MMF feeds of MALS and LMMS, which have known issues. Their findings show in the alert log and in the raw response inspector.

### Default schedules (editable under Schedules)

MMF every 5 min · rolling every 15 min · day-ahead at 10:05, then hourly to 23:05 · week-ahead on
Wednesday at 12:35, then hourly · four-months-ahead from the 25th to the 1st at 00:05.
Deadlines: day-ahead daily by 10:00 · week-ahead Wednesday by 12:30 · four-months-ahead by the 25th.

## Conventions

* All times are naive Malaysia local time (UTC+8), the same convention as rev5 payloads.
* Tables are created on start-up with `create_all`. Alembic migrations come before the first schema change on shared data.

## Not in the beta yet (next steps from the plan)

Email verification and password reset (needs SES) · S3 raw archive and monthly partitions ·
SQS between scheduler and workers · actual generation and accuracy KPIs (no plant exposes actuals
yet) · fleet-total forecasts and exports · duplicate-content check across plants (KMLS = KALS,
SIDS = SKNS) · plant API keys in AWS Secrets Manager instead of encrypted in the database · AWS deployment
(Terraform or CDK).
