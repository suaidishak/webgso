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
# 27 Nov 2026: GSO first asked to skip MALS and LMMS MMF (known issues), then asked to poll them
# so the alerts and the raw-response inspector show what is wrong with their JSON.
OLD_MMF_SKIP_NOTE = "Known MMF issues; ignored for now (GSO, 27 Nov 2026)"
FORECAST_PATHS = {
    "rolling": "forecast/rolling", "dayahead": "forecast/dayahead", "weekahead": "forecast/weekahead",
    "fourmonthsahead": "forecast/fourmonthsahead",
}
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
    # Reports join each poll to its points and readings.
    "CREATE INDEX IF NOT EXISTS ix_snap_raw ON forecast_snapshot (raw_response_id)",
    "CREATE INDEX IF NOT EXISTS ix_mmf_attempt ON mmf_reading (poll_attempt_id)",
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
            for m in PILOT_PLANTS:
                p = Plant(mnemonic=m, name=m.upper(), status="live", auth_method="none",
                          polling_enabled=True, created_at=now_local())
                for cat, path in FORECAST_PATHS.items():
                    p.endpoints.append(PlantEndpoint(category=cat, url=f"{base}/{m}/{path}"))
                p.endpoints.append(PlantEndpoint(category="mmf", url=f"{base}/{m}/{MMF_PATH}", enabled=True))
                s.add(p)

        # Databases seeded before that change: turn on the two skipped MMF endpoints, unless an admin
        # has since edited them (their note would differ).
        await s.execute(text("UPDATE plant_endpoint SET enabled = true, note = NULL WHERE note = :n"),
                        {"n": OLD_MMF_SKIP_NOTE})

        if not (await s.execute(select(PollSchedule))).first():
            s.add_all(PollSchedule(plant_id=None, enabled=True, updated_at=now_local(), **d) for d in DEFAULT_SCHEDULES)
        if not (await s.execute(select(ComplianceDeadline))).first():
            s.add_all(ComplianceDeadline(plant_id=None, **d) for d in DEFAULT_DEADLINES)
        await s.commit()
