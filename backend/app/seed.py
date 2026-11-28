"""Creates tables and seeds defaults on first start (idempotent)."""
from datetime import time

from sqlalchemy import select, text

from .auth import hash_password
from .config import get_settings, now_local
from .db import Base, SessionLocal, engine
from .models import (
    AppUser, ComplianceDeadline, Organisation, Plant, PlantEndpoint, PollSchedule,
)

PILOT_PLANTS = ["kmls", "mals", "kals", "lmms", "bdws", "sids", "skns", "bkhs"]
# Offline demo at fleet scale (SEED_MOCK_FLEET=120): simulated plants spread over these regions.
MOCK_REGIONS = [("nrt", "Northern"), ("cen", "Central"), ("sth", "Southern"), ("ecs", "East Coast")]
# 27 Nov 2026: GSO first asked to skip MALS and LMMS MMF (known issues), then asked to poll them
# so the alerts and the raw-response inspector show what is wrong with their JSON.
OLD_MMF_SKIP_NOTE = "Known MMF issues; ignored for now (GSO, 27 Nov 2026)"
FORECAST_PATHS = {
    "rolling": "forecast/rolling", "dayahead": "forecast/dayahead", "weekahead": "forecast/weekahead",
    "fourmonthsahead": "forecast/fourmonthsahead",
}
CAT_NAMES = {"rolling": "Rolling", "dayahead": "Day-ahead", "weekahead": "Week-ahead",
             "fourmonthsahead": "4-months", "mmf": "MMF"}
MMF_PATH = "mmf/live"  # as served by the pilot host; rev5 says live/mmf

# rev5 defaults. Admins edit these in the dashboard; they are only seeded once.
DEFAULT_SCHEDULES = [
    dict(category="mmf", kind="interval", interval_minutes=5, timeout_s=30, retries=1, retry_backoff_s=10, jitter_s=20),
    dict(category="rolling", kind="interval", interval_minutes=15, timeout_s=60, retries=2, retry_backoff_s=20, jitter_s=30),
    dict(category="dayahead", kind="cron", cron="5 10-23 * * *", timeout_s=60, retries=2, retry_backoff_s=60, jitter_s=60),
    dict(category="weekahead", kind="cron", cron="35 12-23 * * 3", timeout_s=90, retries=2, retry_backoff_s=60, jitter_s=60),
    dict(category="fourmonthsahead", kind="cron", cron="5 0 25-31,1 * *", timeout_s=180, retries=3, retry_backoff_s=120, jitter_s=120),
]
DEFAULT_DEADLINES = [
    dict(category="dayahead", period="daily", due_time=time(10, 0)),
    dict(category="weekahead", period="weekly", weekday=2, due_time=time(12, 30)),
    dict(category="fourmonthsahead", period="monthly", day_of_month=25, due_time=time(23, 59)),
]


# Beta-only schema upgrades for databases created before a column existed (Alembic later).
UPGRADES = [
    "ALTER TABLE plant ADD COLUMN IF NOT EXISTS auth_method VARCHAR(10) NOT NULL DEFAULT 'none'",
    "ALTER TABLE plant ADD COLUMN IF NOT EXISTS auth_param_name VARCHAR(100)",
    "ALTER TABLE plant ADD COLUMN IF NOT EXISTS api_key_enc TEXT",
    "ALTER TABLE plant ADD COLUMN IF NOT EXISTS api_key_hint VARCHAR(8)",
    "ALTER TABLE plant ADD COLUMN IF NOT EXISTS created_at TIMESTAMP",
    "ALTER TABLE plant ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP",
    "ALTER TABLE plant ADD COLUMN IF NOT EXISTS polling_enabled BOOLEAN NOT NULL DEFAULT true",
    "ALTER TABLE plant ADD COLUMN IF NOT EXISTS region VARCHAR(60)",
    "CREATE INDEX IF NOT EXISTS ix_plant_region ON plant (region)",
    "ALTER TABLE app_user ADD COLUMN IF NOT EXISTS show_tour BOOLEAN NOT NULL DEFAULT true",
    "ALTER TABLE poll_run ADD COLUMN IF NOT EXISTS status VARCHAR(10) NOT NULL DEFAULT 'done'",
    "ALTER TABLE poll_run ADD COLUMN IF NOT EXISTS total INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE poll_run ADD COLUMN IF NOT EXISTS done INTEGER NOT NULL DEFAULT 0",
    # Busy lists at 100+ plants: open alerts, deadline checks and flags by time.
    "CREATE INDEX IF NOT EXISTS ix_alert_cleared_raised ON alert_log (cleared_at, raised_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_deadline_due ON deadline_check (due_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_flag_ts ON compliance_flag (ts)",
    # The fleet board's "last valid poll" per endpoint, without scanning a long-broken endpoint's history.
    "CREATE INDEX IF NOT EXISTS ix_attempt_valid ON poll_attempt (plant_id, category, started_at) WHERE is_valid",
    # Reports join each poll to its points and readings.
    "CREATE INDEX IF NOT EXISTS ix_snap_raw ON forecast_snapshot (raw_response_id)",
    "CREATE INDEX IF NOT EXISTS ix_mmf_attempt ON mmf_reading (poll_attempt_id)",
    # 28 Nov 2026: a schedule targets all/one/many plants and all/one/many APIs instead of one plant
    # (or all) and one API. Old rows are carried over as one-plant / one-API targets.
    "ALTER TABLE poll_schedule ADD COLUMN IF NOT EXISTS name VARCHAR(120)",
    "ALTER TABLE poll_schedule ADD COLUMN IF NOT EXISTS plant_ids INTEGER[]",
    "ALTER TABLE poll_schedule ADD COLUMN IF NOT EXISTS categories VARCHAR(30)[]",
    """DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name = 'poll_schedule' AND column_name = 'category') THEN
        UPDATE poll_schedule SET categories = ARRAY[category]::VARCHAR(30)[] WHERE categories IS NULL;
        UPDATE poll_schedule SET plant_ids = ARRAY[plant_id] WHERE plant_id IS NOT NULL AND plant_ids IS NULL;
        ALTER TABLE poll_schedule DROP CONSTRAINT IF EXISTS uq_schedule_scope;
        ALTER TABLE poll_schedule DROP COLUMN plant_id;
        ALTER TABLE poll_schedule DROP COLUMN category;
      END IF;
    END $$""",
    # One schedule can now cover several APIs, so a run is unique per API too.
    """DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid
                     AND a.attnum = ANY (c.conkey) WHERE c.conname = 'uq_run' AND a.attname = 'category') THEN
        ALTER TABLE schedule_run DROP CONSTRAINT IF EXISTS uq_run;
        ALTER TABLE schedule_run ADD CONSTRAINT uq_run UNIQUE (schedule_id, plant_id, category, due_at);
      END IF;
    END $$""",
    # 28 Nov 2026: report emails after scheduled retrievals.
    "ALTER TABLE poll_schedule ADD COLUMN IF NOT EXISTS recipient_list_ids INTEGER[]",
    "ALTER TABLE poll_schedule ADD COLUMN IF NOT EXISTS email_since TIMESTAMP",
    # A scheduled report goes once per schedule, list and submission (resends and tests are extra rows).
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_report_email_scheduled ON report_email (schedule_id, recipient_list_id, due_at) "
    "WHERE kind = 'scheduled'",
    # Finding finished scheduled retrievals to email.
    "CREATE INDEX IF NOT EXISTS ix_run_sched_due ON schedule_run (schedule_id, due_at)",
    # 28 Nov 2026: SOAP plant APIs and simulated (fictitious) plants.
    "ALTER TABLE plant_endpoint ADD COLUMN IF NOT EXISTS protocol VARCHAR(4) NOT NULL DEFAULT 'rest'",
    "ALTER TABLE plant_endpoint ADD COLUMN IF NOT EXISTS soap_operation VARCHAR(120)",
    "ALTER TABLE plant_endpoint ADD COLUMN IF NOT EXISTS soap_action VARCHAR(300)",
    "ALTER TABLE plant_endpoint ADD COLUMN IF NOT EXISTS soap_version VARCHAR(3)",
    "ALTER TABLE plant_endpoint ADD COLUMN IF NOT EXISTS soap_namespace VARCHAR(300)",
    "ALTER TABLE raw_response ADD COLUMN IF NOT EXISTS body_format VARCHAR(4) NOT NULL DEFAULT 'json'",
    "ALTER TABLE plant ADD COLUMN IF NOT EXISTS is_simulated BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE recipient_list ADD COLUMN IF NOT EXISTS include_simulated BOOLEAN NOT NULL DEFAULT false",
    # The SEED_MOCK_FLEET demo plants (nrt001, cen002 ..) are simulated too.
    "UPDATE plant SET is_simulated = true WHERE NOT is_simulated AND mnemonic ~ '^(nrt|cen|sth|ecs)[0-9]{3}$'",
]


async def init_db():
    # API and worker start together; an advisory lock makes schema creation and seeding run once.
    async with engine.connect() as lock:
        await lock.execute(text("SELECT pg_advisory_lock(4242)"))
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                for stmt in UPGRADES:
                    await conn.execute(text(stmt))
            await seed()
        finally:
            await lock.execute(text("SELECT pg_advisory_unlock(4242)"))


async def seed():
    cfg = get_settings()
    async with SessionLocal() as s:
        gso = (await s.execute(select(Organisation).where(Organisation.name == "GSO"))).scalar_one_or_none()
        if gso is None:
            gso = Organisation(name="GSO", type="GSO")
            s.add(gso)
            await s.flush()

        if not (await s.execute(select(AppUser).where(AppUser.role == "gso_admin"))).first():
            s.add(AppUser(email=cfg.admin_email.lower(), name="GSO Admin", organisation_id=gso.id,
                          password_hash=hash_password(cfg.admin_password), status="active", role="gso_admin",
                          created_at=now_local(), approved_at=now_local()))

        if not (await s.execute(select(Plant))).first():
            base = cfg.seed_base_url.rstrip("/")
            plants = [(m, m.upper(), None, None) for m in PILOT_PLANTS]
            for i in range(max(0, cfg.seed_mock_fleet - len(PILOT_PLANTS))):
                code, region = MOCK_REGIONS[i % len(MOCK_REGIONS)]
                n = i // len(MOCK_REGIONS) + 1
                plants.append((f"{code}{n:03d}", f"{region} Solar {n}", region, float(40 + (i * 7) % 60)))
            for m, name, region, cap in plants:
                p = Plant(mnemonic=m, name=name, region=region, capacity_mw=cap, status="live", auth_method="none",
                          polling_enabled=True, created_at=now_local(), is_simulated=region is not None)
                for cat, path in FORECAST_PATHS.items():
                    p.endpoints.append(PlantEndpoint(category=cat, url=f"{base}/{m}/{path}"))
                p.endpoints.append(PlantEndpoint(category="mmf", url=f"{base}/{m}/{MMF_PATH}", enabled=True))
                s.add(p)

        # Databases seeded before that change: turn on the two skipped MMF endpoints, unless an admin
        # has since edited them (their note would differ).
        await s.execute(text("UPDATE plant_endpoint SET enabled = true, note = NULL WHERE note = :n"),
                        {"n": OLD_MMF_SKIP_NOTE})

        if not (await s.execute(select(PollSchedule))).first():
            s.add_all(PollSchedule(name=f"{CAT_NAMES[d['category']]} (rev5 default)", plant_ids=None,
                                   categories=[d["category"]], enabled=True, updated_at=now_local(),
                                   **{k: v for k, v in d.items() if k != "category"}) for d in DEFAULT_SCHEDULES)
        # Schedules carried over from before names existed.
        for sc in (await s.execute(select(PollSchedule).where(PollSchedule.name.is_(None)))).scalars().all():
            cats = ", ".join(CAT_NAMES.get(c, c) for c in (sc.categories or [])) or "All APIs"
            if sc.plant_ids:
                mn = (await s.execute(select(Plant.mnemonic).where(Plant.id.in_(sc.plant_ids)))).scalars().all()
                sc.name = f"{cats} for {', '.join(m.upper() for m in mn)}"[:120]
            else:
                sc.name = f"{cats} (rev5 default)"
        if not (await s.execute(select(ComplianceDeadline))).first():
            s.add_all(ComplianceDeadline(plant_id=None, **d) for d in DEFAULT_DEADLINES)
        await s.commit()
