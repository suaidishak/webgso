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
`.env`). API docs are at http://localhost:8000/api/docs.

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
| Poller | `backend/app/poller.py` | Async httpx, GET only, retries with backoff, at most 4 concurrent calls per host (all 8 pilots share one host). Every call is logged in `poll_attempt`. |
| Raw landing | `raw_response` (JSONB) | Every distinct response kept exactly as received, with a SHA-256 hash, for disputes. Unchanged responses are not stored twice. |
| Tolerant validation | `backend/app/validation.py` | Nothing is rejected. Each rule break is stored as a **major** or **minor** flag in `compliance_flag`. Valid means no major flag. |
| Typed data | `forecast_snapshot`, `forecast_point`, `mmf_reading` | One snapshot per distinct forecast version. Extra MMF parameters are kept and marked non-standard. |
| Scheduler | `backend/app/scheduler.py` | Reads `poll_schedule` every 30 s. A per-plant row overrides the default. Interval (with an optional time window) or cron, in Malaysia time. Edits apply on the next tick. A Postgres advisory lock keeps it to one scheduler, and `schedule_run` stops a pull from running twice. |
| Deadlines | `compliance_deadline`, `deadline_check` | Stored separately from pull times. Each one is marked met, late or missed after its grace period. |
| Alerts | `alert_log` | Raised from major flags, failed calls and missed deadlines. Cleared automatically when a later poll is clean. This is an audit log only, so nothing is sent out. |
| Auth | `backend/app/auth.py`, `routers/auth.py` | Self-registration, then admin approval. Passwords use Argon2, sessions use JWT, and an account locks for 15 minutes after 5 failed logins. Roles: `gso_admin`, `gso_user`, `lss_user` (limited to assigned plants; enforced on every API query). |
| Plant registry | `routers/admin.py`, `frontend/src/pages/PlantsAdmin.tsx` | Admins add, view, edit and delete plants under **Plants**. Each plant has its own URL per endpoint (with a "fill from base URL" helper) and an optional API key, sent as a header (default `X-API-Key`) or a query parameter (default `apiKey`). Keys are encrypted at rest with `PLANT_KEY_SECRET` (Fernet), never returned by the API, shown only as their last 4 characters, and kept out of logs. The poller reads the registry on every tick, and a newly live plant gets its first poll within 30 s. Deleting a plant asks you to type its mnemonic and also removes its history. Set the status to *suspended* to stop polling but keep the history. |
| Endpoint tests | `POST /api/v1/admin/plants/{mnem}/test[?category=]` | The **Test** button on each endpoint and **Test all** on each plant (or for all plants) call the endpoints now with the stored key. Each result shows **Pass** (HTTP 200 and rev5-compliant), **Pass with warnings** (minor deviations only) or **Fail** (unreachable, error status, or a major rule broken), with the HTTP status, response time, size and the main reason. Test calls are stored like any other poll. |
| Admin | `routers/admin.py` | Approve users and assign roles and plants, edit schedules and deadlines. Every change goes to `audit_event`. |
| Dashboard | `frontend/` | React + Vite. Pages: fleet board, plant drill-down (forecast chart, MMF table, flags, raw JSON, recent polls), alert log, compliance (24 h rule summary and deadlines), and admin pages for users, schedules and the audit log. |

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

MALS and LMMS MMF are seeded as **not polled**: GSO already knows about their MMF issues.

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
