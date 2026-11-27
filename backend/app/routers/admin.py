"""gso_admin only: user approval, plant config, schedules, deadlines, audit log."""
import asyncio
from datetime import date, time

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..config import now_local
from ..db import get_session
from ..models import (
    CATEGORIES, ROLES, AppUser, AuditEvent, ComplianceDeadline, ComplianceFlag, Plant, PlantEndpoint, PollAttempt,
    PollSchedule, PollSwitch,
    ScheduleRun, UserPlantAccess,
)
from ..plant_keys import encrypt, hint as key_hint
from ..poller import poll_endpoint
from ..scheduler import next_runs
from .auth import user_out
from .data import plant_out
from ..switches import load_switches, off_reason

router = APIRouter(prefix="/admin", tags=["admin"])


async def audit(s: AsyncSession, user: AppUser, action: str, entity: str, entity_id, before=None, after=None):
    s.add(AuditEvent(ts=now_local(), user_id=user.id, action=action, entity=entity,
                     entity_id=str(entity_id) if entity_id is not None else None,
                     before=_jsonable(before), after=_jsonable(after)))


def _jsonable(d):
    if d is None:
        return None
    if isinstance(d, dict):
        return {k: _jsonable(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_jsonable(v) for v in d]
    return d.isoformat() if isinstance(d, (date, time)) else d


# ---------------------------------------------------------------- users
@router.get("/users")
async def users(admin: AppUser = Depends(require_admin), s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(AppUser).order_by(AppUser.status != "pending", AppUser.created_at.desc()))
            ).scalars().all()
    access = (await s.execute(select(UserPlantAccess.user_id, Plant.mnemonic).join(
        Plant, Plant.id == UserPlantAccess.plant_id))).all()
    by_user: dict[int, list[str]] = {}
    for uid, m in access:
        by_user.setdefault(uid, []).append(m)
    return [user_out(u, sorted(by_user.get(u.id, []))) for u in rows]


class ApproveIn(BaseModel):
    role: str = Field(pattern="^(gso_admin|gso_user|lss_user)$")
    plants: list[str] = []
    status: str = Field("active", pattern="^(active|disabled|pending)$")


@router.put("/users/{user_id}")
async def set_user(user_id: int, body: ApproveIn, admin: AppUser = Depends(require_admin),
                   s: AsyncSession = Depends(get_session)):
    """Approve (status active), change role/plants, or disable an account."""
    u = await s.get(AppUser, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    if u.id == admin.id and (body.status != "active" or body.role != "gso_admin"):
        raise HTTPException(400, "You cannot demote or disable your own account")
    if body.role == "lss_user" and not body.plants and body.status == "active":
        raise HTTPException(400, "An LSS user needs at least one plant")
    before = {"role": u.role, "status": u.status}
    plants = (await s.execute(select(Plant).where(Plant.mnemonic.in_([p.lower() for p in body.plants])))
              ).scalars().all()
    if len(plants) != len(set(body.plants)):
        raise HTTPException(400, "Unknown plant in list")
    if u.status == "pending" and body.status == "active":
        u.approved_at, u.approved_by = now_local(), admin.id
    u.role, u.status = body.role, body.status
    await s.execute(delete(UserPlantAccess).where(UserPlantAccess.user_id == u.id))
    if body.role == "lss_user":
        s.add_all(UserPlantAccess(user_id=u.id, plant_id=p.id) for p in plants)
    await audit(s, admin, "user.update", "app_user", u.id, before,
                {"role": u.role, "status": u.status, "plants": sorted(p.mnemonic for p in plants)})
    await s.commit()
    return {"ok": True}


# ---------------------------------------------------------------- plants
class EndpointIn(BaseModel):
    category: str
    url: str = Field(pattern=r"^https?://\S+$", max_length=500)
    enabled: bool = True
    note: str | None = None


class PlantIn(BaseModel):
    mnemonic: str = Field(min_length=2, max_length=8, pattern="^[A-Za-z0-9]+$")
    name: str = Field(min_length=1, max_length=200)
    capacity_mw: float | None = Field(None, gt=0)
    mmf_station_count: int | None = Field(None, ge=0, le=50)
    status: str = Field("live", pattern="^(onboarding|live|suspended)$")
    notes: str | None = None
    endpoints: list[EndpointIn] = []
    auth_method: str = Field("none", pattern="^(none|header|query)$")
    auth_param_name: str | None = Field(None, max_length=100, pattern=r"^[A-Za-z0-9_\-]+$")
    # Write-only. Omitted or null keeps the stored key; "" removes it; any other value replaces it.
    api_key: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def check(self):
        cats = [e.category for e in self.endpoints]
        if len(cats) != len(set(cats)):
            raise ValueError("Each endpoint type can appear only once")
        for c in cats:
            if c not in CATEGORIES:
                raise ValueError(f"Unknown endpoint type {c}")
        return self

    def audit_view(self) -> dict:
        d = self.model_dump(exclude={"api_key"})
        d["api_key"] = "unchanged" if self.api_key is None else "removed" if self.api_key == "" else "replaced"
        return d


def _apply(p: Plant, body: PlantIn):
    p.name, p.capacity_mw, p.mmf_station_count = body.name.strip(), body.capacity_mw, body.mmf_station_count
    p.status, p.notes = body.status, body.notes
    p.auth_method = body.auth_method
    p.auth_param_name = (body.auth_param_name or ("X-API-Key" if body.auth_method == "header" else "apiKey")
                         ) if body.auth_method != "none" else None
    if body.api_key is not None:
        key = body.api_key.strip()
        p.api_key_enc, p.api_key_hint = (encrypt(key), key_hint(key)) if key else (None, None)
    if p.auth_method != "none" and not p.api_key_enc:
        raise HTTPException(400, "Enter the API key, or set authentication to None")
    p.updated_at = now_local()
    _set_endpoints(p, body.endpoints)


@router.post("/plants", status_code=201)
async def create_plant(body: PlantIn, admin: AppUser = Depends(require_admin), s: AsyncSession = Depends(get_session)):
    if (await s.execute(select(Plant).where(Plant.mnemonic == body.mnemonic.lower()))).first():
        raise HTTPException(409, f"A plant with mnemonic {body.mnemonic.upper()} already exists")
    p = Plant(mnemonic=body.mnemonic.lower(), created_at=now_local())
    _apply(p, body)
    s.add(p)
    await s.flush()
    await audit(s, admin, "plant.create", "plant", p.mnemonic, None, body.audit_view())
    await s.commit()
    return plant_out(p, admin=True)


@router.put("/plants/{mnem}")
async def update_plant(mnem: str, body: PlantIn, admin: AppUser = Depends(require_admin),
                       s: AsyncSession = Depends(get_session)):
    p = (await s.execute(select(Plant).where(Plant.mnemonic == mnem.lower()))).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, "Plant not found")
    if body.mnemonic.lower() != p.mnemonic:
        raise HTTPException(400, "The mnemonic cannot be changed; add a new plant instead")
    before = plant_out(p, admin=True)
    _apply(p, body)
    await audit(s, admin, "plant.update", "plant", p.mnemonic, _jsonable(before), body.audit_view())
    await s.commit()
    return plant_out(p, admin=True)


@router.delete("/plants/{mnem}")
async def delete_plant(mnem: str, confirm: str = "", admin: AppUser = Depends(require_admin),
                       s: AsyncSession = Depends(get_session)):
    """Deletes the plant and ALL its stored data (polls, raw responses, forecasts, flags, alerts).
    The caller must repeat the mnemonic in `confirm`. To stop polling but keep history, set status suspended."""
    p = (await s.execute(select(Plant).where(Plant.mnemonic == mnem.lower()))).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, "Plant not found")
    if confirm.lower() != p.mnemonic:
        raise HTTPException(400, "Type the plant's mnemonic to confirm deletion")
    counts = (await s.execute(text("""
        SELECT (SELECT count(*) FROM poll_attempt WHERE plant_id = :id) AS polls,
               (SELECT count(*) FROM forecast_snapshot WHERE plant_id = :id) AS snapshots,
               (SELECT count(*) FROM alert_log WHERE plant_id = :id) AS alerts"""), {"id": p.id})).mappings().one()
    await audit(s, admin, "plant.delete", "plant", p.mnemonic, _jsonable(plant_out(p, admin=True)), dict(counts))
    await s.delete(p)
    await s.commit()
    return {"ok": True, "deleted": dict(counts)}


@router.get("/plants/{mnem}/usage")
async def plant_usage(mnem: str, admin: AppUser = Depends(require_admin), s: AsyncSession = Depends(get_session)):
    """How much stored history a delete would remove."""
    p = (await s.execute(select(Plant).where(Plant.mnemonic == mnem.lower()))).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, "Plant not found")
    return dict((await s.execute(text("""
        SELECT (SELECT count(*) FROM poll_attempt WHERE plant_id = :id) AS polls,
               (SELECT count(*) FROM forecast_snapshot WHERE plant_id = :id) AS snapshots,
               (SELECT count(*) FROM alert_log WHERE plant_id = :id) AS alerts,
               (SELECT count(*) FROM user_plant_access WHERE plant_id = :id) AS users"""), {"id": p.id})).mappings().one())


def _set_endpoints(p: Plant, eps: list[EndpointIn]):
    """Replace the plant's endpoints with the given list (an endpoint left out is removed)."""
    existing = {e.category: e for e in p.endpoints}
    wanted = {e.category for e in eps}
    for cat, x in existing.items():
        if cat not in wanted:
            p.endpoints.remove(x)
    for e in eps:
        url = e.url.strip()
        if e.category in existing:
            x = existing[e.category]
            x.url, x.enabled, x.note = url, e.enabled, e.note
        else:
            p.endpoints.append(PlantEndpoint(category=e.category, url=url, enabled=e.enabled, note=e.note))


@router.post("/plants/{mnem}/poll")
async def poll_now(mnem: str, category: str | None = None, admin: AppUser = Depends(require_admin),
                   s: AsyncSession = Depends(get_session)):
    """Call the plant's endpoints now (all enabled ones, or one category) and return the attempts."""
    p = (await s.execute(select(Plant).where(Plant.mnemonic == mnem.lower()))).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, "Plant not found")
    cats = [e.category for e in p.endpoints if (e.enabled or category) and (category is None or e.category == category)]
    if not cats:
        raise HTTPException(400, "No matching endpoint")
    now = now_local()
    ids = await asyncio.gather(*(poll_endpoint(p.id, c, scheduled_for=None, timeout_s=90, retries=0) for c in cats))
    for c, aid in zip(cats, ids):
        s.add(ScheduleRun(schedule_id=None, plant_id=p.id, category=c, due_at=now, enqueued_at=now,
                          status="manual", poll_attempt_id=aid))
    await audit(s, admin, "plant.poll_now", "plant", p.mnemonic, None, {"categories": cats})
    await s.commit()
    return {"attempts": dict(zip(cats, ids))}


# ---------------------------------------------------------------- polling switches
class SwitchIn(BaseModel):
    enabled: bool


async def _set_switch(s: AsyncSession, admin: AppUser, key: str, enabled: bool):
    row = await s.get(PollSwitch, key)
    before = row.enabled if row else True
    if row is None:
        row = PollSwitch(key=key)
        s.add(row)
    row.enabled, row.updated_at, row.updated_by = enabled, now_local(), admin.id
    await audit(s, admin, "polling.fleet" if key == "fleet" else "polling.type", "poll_switch", key,
                {"enabled": before}, {"enabled": enabled})


@router.get("/polling")
async def get_polling(admin: AppUser = Depends(require_admin), s: AsyncSession = Depends(get_session)):
    sw = await load_switches(s)
    rows = {r.key: r for r in (await s.execute(select(PollSwitch))).scalars().all()}
    meta = {k: {"updated_at": r.updated_at, "updated_by": r.updated_by} for k, r in rows.items()}
    return {"fleet": sw.fleet, "types": sw.types, "changes": meta}


@router.put("/polling/fleet")
async def set_fleet_polling(body: SwitchIn, admin: AppUser = Depends(require_admin), s: AsyncSession = Depends(get_session)):
    """Level 1: pause or resume all scheduled polling."""
    await _set_switch(s, admin, "fleet", body.enabled)
    await s.commit()
    return await get_polling(admin, s)


@router.put("/polling/types/{category}")
async def set_type_polling(category: str, body: SwitchIn, admin: AppUser = Depends(require_admin),
                           s: AsyncSession = Depends(get_session)):
    """Level 2: pause or resume one endpoint type (e.g. MMF) for every plant."""
    if category not in CATEGORIES:
        raise HTTPException(404, "Unknown endpoint type")
    await _set_switch(s, admin, f"type:{category}", body.enabled)
    await s.commit()
    return await get_polling(admin, s)


async def _plant(s: AsyncSession, mnem: str) -> Plant:
    p = (await s.execute(select(Plant).where(Plant.mnemonic == mnem.lower()))).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, "Plant not found")
    return p


@router.put("/plants/{mnem}/polling")
async def set_plant_polling(mnem: str, body: SwitchIn, admin: AppUser = Depends(require_admin),
                            s: AsyncSession = Depends(get_session)):
    """Level 3: pause or resume every endpoint of one plant."""
    p = await _plant(s, mnem)
    before, p.polling_enabled, p.updated_at = p.polling_enabled, body.enabled, now_local()
    await audit(s, admin, "polling.plant", "plant", p.mnemonic, {"enabled": before}, {"enabled": body.enabled})
    await s.commit()
    return plant_out(p, admin=True, sw=await load_switches(s))


@router.put("/plants/{mnem}/endpoints/{category}/polling")
async def set_endpoint_polling(mnem: str, category: str, body: SwitchIn, admin: AppUser = Depends(require_admin),
                               s: AsyncSession = Depends(get_session)):
    """Level 4: pause or resume one endpoint of one plant."""
    p = await _plant(s, mnem)
    ep = next((e for e in p.endpoints if e.category == category), None)
    if ep is None:
        raise HTTPException(404, "This plant has no such endpoint")
    before, ep.enabled, p.updated_at = ep.enabled, body.enabled, now_local()
    await audit(s, admin, "polling.endpoint", "plant_endpoint", f"{p.mnemonic}/{category}",
                {"enabled": before}, {"enabled": body.enabled})
    await s.commit()
    return plant_out(p, admin=True, sw=await load_switches(s))


@router.post("/plants/{mnem}/test")
async def test_plant(mnem: str, category: str | None = None, admin: AppUser = Depends(require_admin),
                     s: AsyncSession = Depends(get_session)):
    """GET one endpoint (or every endpoint with a URL, polled or not) with the stored key and report
    pass/fail. Each call is a normal poll, so the result is kept in the history as evidence."""
    p = (await s.execute(select(Plant).where(Plant.mnemonic == mnem.lower()))).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, "Plant not found")
    eps = [e for e in p.endpoints if category is None or e.category == category]
    sw = await load_switches(s)
    if not eps:
        raise HTTPException(400, "No matching endpoint")
    now = now_local()
    ids = await asyncio.gather(*(poll_endpoint(p.id, e.category, scheduled_for=None, timeout_s=90, retries=0)
                                 for e in eps))
    results = []
    for e, aid in zip(eps, ids):
        s.add(ScheduleRun(schedule_id=None, plant_id=p.id, category=e.category, due_at=now, enqueued_at=now,
                          status="manual", poll_attempt_id=aid))
        a = await s.get(PollAttempt, aid) if aid else None
        flags = (await s.execute(select(ComplianceFlag).where(ComplianceFlag.poll_attempt_id == aid))
                 ).scalars().all() if aid else []
        majors = [f for f in flags if f.severity == "major"]
        minors = [f for f in flags if f.severity == "minor"]
        if a is None:
            verdict, reason = "fail", "Test could not run"
        elif a.http_status is None:
            verdict, reason = "fail", a.error or "No response"
        elif a.http_status != 200:
            verdict, reason = "fail", (majors[0].message if majors else f"HTTP {a.http_status}")
        elif majors:
            verdict, reason = "fail", majors[0].message
        elif minors:
            verdict, reason = "warn", minors[0].message
        else:
            verdict, reason = "pass", "Reachable and compliant with rev5"
        results.append({
            "category": e.category, "url": e.url, "polled": off_reason(sw, p, e) is None, "verdict": verdict, "reason": reason,
            "http_status": a.http_status if a else None, "latency_ms": a.latency_ms if a else None,
            "bytes": a.bytes if a else None, "attempt_id": aid, "tested_at": a.started_at if a else now,
            "major": [f.message for f in majors], "minor": [f.message for f in minors],
        })
    await audit(s, admin, "plant.test", "plant", p.mnemonic, None,
                {r["category"]: r["verdict"] for r in results})
    await s.commit()
    order = {c: i for i, c in enumerate(CATEGORIES)}
    results.sort(key=lambda r: order.get(r["category"], 99))
    return {"plant": p.mnemonic, "tested_at": now, "results": results,
            "summary": {v: sum(r["verdict"] == v for r in results) for v in ("pass", "warn", "fail")}}


# ---------------------------------------------------------------- schedules
class ScheduleIn(BaseModel):
    plant: str | None = None  # None = default for all plants
    category: str
    kind: str = Field(pattern="^(interval|cron)$")
    interval_minutes: int | None = Field(None, ge=1, le=1440)
    window_start: time | None = None
    window_end: time | None = None
    cron: str | None = None
    enabled: bool = True
    timeout_s: int = Field(60, ge=5, le=600)
    retries: int = Field(2, ge=0, le=10)
    retry_backoff_s: int = Field(20, ge=0, le=3600)
    jitter_s: int = Field(20, ge=0, le=600)
    valid_from: date | None = None
    valid_to: date | None = None

    @model_validator(mode="after")
    def check(self):
        if self.category not in CATEGORIES:
            raise ValueError(f"Unknown category {self.category}")
        if self.kind == "interval" and not self.interval_minutes:
            raise ValueError("interval_minutes is required for an interval schedule")
        if self.kind == "cron" and (not self.cron or not croniter.is_valid(self.cron)):
            raise ValueError("A valid 5-field cron expression is required")
        return self


def schedule_out(sc: PollSchedule, mnem: str | None) -> dict:
    d = {c.name: getattr(sc, c.name) for c in PollSchedule.__table__.columns}
    d["plant"] = mnem
    d["next_runs"] = next_runs(sc, now_local()) if sc.enabled else []
    return d


@router.get("/schedules")
async def schedules(admin: AppUser = Depends(require_admin), s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(PollSchedule, Plant.mnemonic).outerjoin(Plant, Plant.id == PollSchedule.plant_id)
                            .order_by(PollSchedule.plant_id.nulls_first(), PollSchedule.id))).all()
    return [schedule_out(sc, m) for sc, m in rows]


async def _plant_id(s: AsyncSession, mnem: str | None) -> int | None:
    if mnem is None:
        return None
    pid = (await s.execute(select(Plant.id).where(Plant.mnemonic == mnem.lower()))).scalar_one_or_none()
    if pid is None:
        raise HTTPException(400, "Unknown plant")
    return pid


@router.post("/schedules", status_code=201)
async def create_schedule(body: ScheduleIn, admin: AppUser = Depends(require_admin),
                          s: AsyncSession = Depends(get_session)):
    pid = await _plant_id(s, body.plant)
    if (await s.execute(select(PollSchedule).where(PollSchedule.plant_id.is_(None) if pid is None else
                                                   PollSchedule.plant_id == pid,
                                                   PollSchedule.category == body.category))).first():
        raise HTTPException(409, "A schedule for this scope and category already exists; edit it instead")
    data = body.model_dump(exclude={"plant"})
    sc = PollSchedule(plant_id=pid, updated_at=now_local(), updated_by=admin.id, **data)
    s.add(sc)
    await s.flush()
    await audit(s, admin, "schedule.create", "poll_schedule", sc.id, None, body.model_dump())
    await s.commit()
    return schedule_out(sc, body.plant)


@router.put("/schedules/{sid}")
async def update_schedule(sid: int, body: ScheduleIn, admin: AppUser = Depends(require_admin),
                          s: AsyncSession = Depends(get_session)):
    sc = await s.get(PollSchedule, sid)
    if sc is None:
        raise HTTPException(404, "Schedule not found")
    before = {k: v for k, v in schedule_out(sc, None).items() if k != "next_runs"}
    for k, v in body.model_dump(exclude={"plant", "category"}).items():
        setattr(sc, k, v)
    sc.updated_at, sc.updated_by = now_local(), admin.id
    await audit(s, admin, "schedule.update", "poll_schedule", sid, before, body.model_dump())
    await s.commit()
    return schedule_out(sc, body.plant)


@router.delete("/schedules/{sid}")
async def delete_schedule(sid: int, admin: AppUser = Depends(require_admin), s: AsyncSession = Depends(get_session)):
    sc = await s.get(PollSchedule, sid)
    if sc is None:
        raise HTTPException(404, "Schedule not found")
    if sc.plant_id is None:
        raise HTTPException(400, "Default schedules cannot be deleted; disable them instead")
    await audit(s, admin, "schedule.delete", "poll_schedule", sid,
                {k: v for k, v in schedule_out(sc, None).items() if k != "next_runs"}, None)
    await s.delete(sc)
    await s.commit()
    return {"ok": True}


# ---------------------------------------------------------------- deadlines
class DeadlineIn(BaseModel):
    period: str = Field(pattern="^(daily|weekly|monthly)$")
    weekday: int | None = Field(None, ge=0, le=6)
    day_of_month: int | None = Field(None, ge=1, le=28)
    due_time: time
    grace_minutes: int = Field(15, ge=0, le=1440)
    enabled: bool = True


@router.get("/deadlines")
async def list_deadlines(admin: AppUser = Depends(require_admin), s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(ComplianceDeadline, Plant.mnemonic).outerjoin(
        Plant, Plant.id == ComplianceDeadline.plant_id).order_by(ComplianceDeadline.category))).all()
    return [{**{c.name: getattr(d, c.name) for c in ComplianceDeadline.__table__.columns}, "plant": m} for d, m in rows]


@router.put("/deadlines/{did}")
async def update_deadline(did: int, body: DeadlineIn, admin: AppUser = Depends(require_admin),
                          s: AsyncSession = Depends(get_session)):
    d = await s.get(ComplianceDeadline, did)
    if d is None:
        raise HTTPException(404, "Deadline not found")
    before = {c.name: getattr(d, c.name) for c in ComplianceDeadline.__table__.columns}
    for k, v in body.model_dump().items():
        setattr(d, k, v)
    await audit(s, admin, "deadline.update", "compliance_deadline", did, before, body.model_dump())
    await s.commit()
    return {"ok": True}


# ---------------------------------------------------------------- audit
@router.get("/audit")
async def audit_log(limit: int = 200, admin: AppUser = Depends(require_admin), s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(AuditEvent, AppUser.email).outerjoin(AppUser, AppUser.id == AuditEvent.user_id)
                            .order_by(AuditEvent.ts.desc(), AuditEvent.id.desc()).limit(min(limit, 1000)))).all()
    return [{"id": a.id, "ts": a.ts, "user": e, "action": a.action, "entity": a.entity, "entity_id": a.entity_id,
             "before": a.before, "after": a.after} for a, e in rows]
