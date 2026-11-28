"""gso_admin only: user approval, plant config, schedules, deadlines, audit log."""
import asyncio
import logging
import time as _time
from datetime import date, time
from typing import Literal

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Text
from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..config import get_settings, now_local
from ..db import SessionLocal, get_session
from ..models import (
    CATEGORIES, ROLES, AlertLog, AppUser, AuditEvent, ComplianceDeadline, ComplianceFlag, Plant, PlantEndpoint, PollAttempt,
    PollRun, PollSchedule, PollSwitch, RecipientList,
    ScheduleRun, UserPlantAccess,
)
from ..plant_keys import encrypt, hint as key_hint
from ..poller import poll_endpoint
from ..scheduler import effective_schedules, in_dates, in_force, matches, next_runs, precedence
from .auth import user_out
from .data import plant_out
from ..seed import FORECAST_PATHS, MMF_PATH
from ..switches import load_switches, off_reason

router = APIRouter(prefix="/admin", tags=["admin"])
log = logging.getLogger("gso.admin")


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
    region: str | None = Field(None, max_length=60)
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
    p.region = (body.region or "").strip() or None
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
    # Take the plant out of schedules that list it. A schedule left with no plants governs nothing (it does
    # not fall back to all plants); the Schedules page shows it as "No plants".
    await s.execute(text("UPDATE poll_schedule SET plant_ids = array_remove(plant_ids, :id) WHERE :id = ANY(plant_ids)"),
                    {"id": p.id})
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


class BulkIn(BaseModel):
    plants: list[str] = Field(min_length=1, max_length=5000)
    action: Literal["pause", "resume", "pause_endpoint", "resume_endpoint", "set_status", "set_region"]
    category: str | None = None  # for pause_endpoint / resume_endpoint
    value: str | None = Field(None, max_length=60)  # for set_status / set_region (empty clears the region)

    @model_validator(mode="after")
    def check(self):
        if self.action in ("pause_endpoint", "resume_endpoint") and self.category not in CATEGORIES:
            raise ValueError("Choose an endpoint type")
        if self.action == "set_status" and self.value not in ("onboarding", "live", "suspended"):
            raise ValueError("Status must be onboarding, live or suspended")
        return self


@router.post("/plants/bulk")
async def bulk_plants(body: BulkIn, admin: AppUser = Depends(require_admin), s: AsyncSession = Depends(get_session)):
    """Change many plants at once: pause or resume polling (whole plant or one endpoint type), set the
    status or the region. One audit entry lists every plant changed."""
    wanted = {m.lower() for m in body.plants}
    plants = (await s.execute(select(Plant).where(Plant.mnemonic.in_(wanted)))).scalars().all()
    missing = sorted(wanted - {p.mnemonic for p in plants})
    if missing:
        raise HTTPException(404, f"Unknown plant: {', '.join(m.upper() for m in missing[:10])}")
    now, changed, skipped = now_local(), [], []
    for p in plants:
        if body.action in ("pause", "resume"):
            new = body.action == "resume"
            if p.polling_enabled != new:
                p.polling_enabled, p.updated_at = new, now
                changed.append(p.mnemonic)
        elif body.action in ("pause_endpoint", "resume_endpoint"):
            ep = next((e for e in p.endpoints if e.category == body.category), None)
            if ep is None:
                skipped.append(p.mnemonic)
                continue
            new = body.action == "resume_endpoint"
            if ep.enabled != new:
                ep.enabled, p.updated_at = new, now
                changed.append(p.mnemonic)
        elif body.action == "set_status":
            if p.status != body.value:
                p.status, p.updated_at = body.value, now
                changed.append(p.mnemonic)
        else:
            region = (body.value or "").strip() or None
            if p.region != region:
                p.region, p.updated_at = region, now
                changed.append(p.mnemonic)
    if changed:
        await audit(s, admin, f"plant.bulk.{body.action}", "plant", f"{len(changed)} plants", None,
                    {"plants": sorted(changed), "category": body.category, "value": body.value})
    await s.commit()
    return {"ok": True, "changed": len(changed), "unchanged": len(plants) - len(changed) - len(skipped),
            "skipped": sorted(skipped)}


class ImportRow(BaseModel):
    mnemonic: str = Field(min_length=2, max_length=8, pattern="^[A-Za-z0-9]+$")
    name: str = Field(min_length=1, max_length=200)
    base_url: str | None = Field(None, pattern=r"^https?://\S+$", max_length=400)
    region: str | None = Field(None, max_length=60)
    capacity_mw: float | None = Field(None, gt=0)
    mmf_station_count: int | None = Field(None, ge=0, le=50)
    status: str = Field("onboarding", pattern="^(onboarding|live|suspended)$")


class ImportIn(BaseModel):
    rows: list[dict] = Field(min_length=1, max_length=2000)
    dry_run: bool = True
    update_existing: bool = False


def _blank_to_none(d: dict) -> dict:
    return {str(k).strip().lower(): (v.strip() if isinstance(v, str) else v) or None for k, v in d.items()
            if str(k).strip()}


@router.post("/plants/import")
async def import_plants(body: ImportIn, admin: AppUser = Depends(require_admin), s: AsyncSession = Depends(get_session)):
    """Add many plants at once (e.g. pasted from a spreadsheet). Each new plant gets the five rev5
    endpoints under its base URL ({base_url}/{mnemonic}/forecast/... and /mmf/live), with no API key,
    and starts as onboarding unless the row says otherwise. Existing plants are skipped, or with
    update_existing their name, region, capacity, MMF stations and status are updated (URLs and keys never).
    dry_run (the default) only reports what would happen."""
    existing = {p.mnemonic: p for p in (await s.execute(select(Plant))).scalars().all()}
    out, seen = [], set()
    created = updated = 0
    for i, raw in enumerate(body.rows, start=1):
        d = _blank_to_none(raw)
        mnem = (d.get("mnemonic") or "").lower()
        try:
            row = ImportRow.model_validate(d)
        except ValueError as e:
            msg = "; ".join(f"{'.'.join(str(x) for x in er['loc'])}: {er['msg']}" for er in e.errors()) \
                if hasattr(e, "errors") else str(e)
            out.append({"line": i, "mnemonic": mnem, "action": "error", "message": msg})
            continue
        mnem = row.mnemonic.lower()
        if mnem in seen:
            out.append({"line": i, "mnemonic": mnem, "action": "error", "message": "Repeated in this import"})
            continue
        seen.add(mnem)
        if mnem in existing:
            if not body.update_existing:
                out.append({"line": i, "mnemonic": mnem, "action": "skip", "message": "Already registered"})
                continue
            p = existing[mnem]
            p.name, p.region = row.name, row.region
            if row.capacity_mw is not None:
                p.capacity_mw = row.capacity_mw
            if row.mmf_station_count is not None:
                p.mmf_station_count = row.mmf_station_count
            p.status, p.updated_at = row.status, now_local()
            updated += 1
            out.append({"line": i, "mnemonic": mnem, "action": "update", "message": "Details updated"})
            continue
        if not row.base_url:
            out.append({"line": i, "mnemonic": mnem, "action": "error", "message": "base_url is required for a new plant"})
            continue
        base = row.base_url.rstrip("/")
        p = Plant(mnemonic=mnem, name=row.name, region=row.region, capacity_mw=row.capacity_mw,
                  mmf_station_count=row.mmf_station_count, status=row.status, auth_method="none",
                  polling_enabled=True, created_at=now_local(), updated_at=now_local())
        for cat, path in FORECAST_PATHS.items():
            p.endpoints.append(PlantEndpoint(category=cat, url=f"{base}/{mnem}/{path}"))
        p.endpoints.append(PlantEndpoint(category="mmf", url=f"{base}/{mnem}/{MMF_PATH}"))
        s.add(p)
        created += 1
        out.append({"line": i, "mnemonic": mnem, "action": "create", "message": f"{base}/{mnem}/…"})
    errors = sum(r["action"] == "error" for r in out)
    summary = {"create": created, "update": updated, "skip": sum(r["action"] == "skip" for r in out), "error": errors}
    if body.dry_run:
        await s.rollback()
    else:
        if created or updated:
            await audit(s, admin, "plant.import", "plant", f"{created + updated} plants", None,
                        {**summary, "plants": sorted(r["mnemonic"] for r in out if r["action"] in ("create", "update"))})
        await s.commit()
    return {"dry_run": body.dry_run, "summary": summary, "rows": out}


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


async def _open_alerts(s: AsyncSession, pairs: set[tuple[int, str]]) -> dict[int, AlertLog]:
    if not pairs:
        return {}
    rows = (await s.execute(select(AlertLog).where(
        AlertLog.cleared_at.is_(None), ~AlertLog.rule.like("DEADLINE_%"),
        AlertLog.plant_id.in_({p for p, _ in pairs})).execution_options(populate_existing=True))).scalars().all()
    return {a.id: a for a in rows if (a.plant_id, a.category) in pairs}


def _alert_out(a: AlertLog) -> dict:
    return {"id": a.id, "rule": a.rule, "severity": a.severity, "message": a.message,
            "raised_at": a.raised_at, "occurrences": a.occurrences}


def _verdict(a: PollAttempt | None, majors: list, minors: list) -> tuple[str, str]:
    if a is None:
        return "fail", "The call could not run"
    if a.http_status is None:
        return "fail", a.error or "No response"
    if a.http_status != 200:
        return "fail", (majors[0].message if majors else f"HTTP {a.http_status}")
    if majors:
        return "fail", majors[0].message
    if minors:
        return "warn", minors[0].message
    return "pass", "Reachable and compliant with rev5"


async def _run_now(s: AsyncSession, targets: list[tuple[Plant, PlantEndpoint]], on_done=None) -> list[dict]:
    """Poll each (plant, endpoint) now, ignoring the polling switches. Each call is an ordinary poll:
    stored, validated and able to raise or clear alerts. Returns one result per call, with the alerts
    it raised, the ones still open and the ones it cleared. on_done() is called after each call
    (progress for large runs). Concurrency is bounded by the poller's limits."""
    sw = await load_switches(s)
    pairs = {(p.id, e.category) for p, e in targets}
    before = await _open_alerts(s, pairs)
    now = now_local()

    async def one(pid: int, cat: str):
        try:
            return await poll_endpoint(pid, cat, scheduled_for=None, timeout_s=90, retries=0)
        except Exception:  # noqa: BLE001 - one plant's failure must not sink the run
            log.exception("poll now failed %s/%s", pid, cat)
            return None
        finally:
            if on_done:
                on_done()

    ids = await asyncio.gather(*(one(p.id, e.category) for p, e in targets))
    after = await _open_alerts(s, pairs)
    cleared = {}
    if before:
        gone = [i for i in before if i not in after]
        if gone:
            cleared = {a.id: a for a in (await s.execute(select(AlertLog).where(AlertLog.id.in_(gone))
                                                          .execution_options(populate_existing=True))).scalars()}
    # Attempts and flags for every call in two queries, not two per call (a fleet run has 500+ calls).
    got = [i for i in ids if i]
    attempts = {a.id: a for a in (await s.execute(select(PollAttempt).where(PollAttempt.id.in_(got)))).scalars()} \
        if got else {}
    flags_by: dict[int, list] = {}
    if got:
        for f in (await s.execute(select(ComplianceFlag).where(ComplianceFlag.poll_attempt_id.in_(got))
                                  .order_by(ComplianceFlag.id))).scalars():
            flags_by.setdefault(f.poll_attempt_id, []).append(f)
    alerts_by: dict[tuple[int, str], dict[str, list]] = {}
    for kind, d in (("after", after), ("cleared", cleared)):
        for x in d.values():
            alerts_by.setdefault((x.plant_id, x.category), {"after": [], "cleared": []})[kind].append(x)
    results = []
    for (p, e), aid in zip(targets, ids):
        s.add(ScheduleRun(schedule_id=None, plant_id=p.id, category=e.category, due_at=now, enqueued_at=now,
                          status="manual", poll_attempt_id=aid))
        a = attempts.get(aid) if aid else None
        flags = flags_by.get(aid, []) if aid else []
        majors = [f for f in flags if f.severity == "major"]
        minors = [f for f in flags if f.severity == "minor"]
        verdict, reason = _verdict(a, majors, minors)
        mine = alerts_by.get((p.id, e.category), {"after": [], "cleared": []})
        results.append({
            "plant": p.mnemonic, "category": e.category, "url": e.url, "polled": off_reason(sw, p, e) is None,
            "off_reason": off_reason(sw, p, e), "verdict": verdict, "reason": reason,
            "http_status": a.http_status if a else None, "latency_ms": a.latency_ms if a else None,
            "bytes": a.bytes if a else None, "attempt_id": aid, "tested_at": a.started_at if a else now,
            "major": [f.message for f in majors], "minor": [f.message for f in minors],
            "alerts": {
                "raised": [_alert_out(x) for x in mine["after"] if x.id not in before],
                "ongoing": [_alert_out(x) for x in mine["after"] if x.id in before],
                "cleared": [_alert_out(x) for x in mine["cleared"]],
            },
        })
    order = {c: i for i, c in enumerate(CATEGORIES)}
    results.sort(key=lambda r: (r["plant"], order.get(r["category"], 99)))
    return results


def _summary(results: list[dict]) -> dict:
    out = {v: sum(r["verdict"] == v for r in results) for v in ("pass", "warn", "fail")}
    for k in ("raised", "ongoing", "cleared"):
        out[f"alerts_{k}"] = sum(len(r["alerts"][k]) for r in results)
    return out


@router.post("/plants/{mnem}/test")
async def test_plant(mnem: str, category: str | None = None, admin: AppUser = Depends(require_admin),
                     s: AsyncSession = Depends(get_session)):
    """GET one endpoint (or every endpoint with a URL, polled or not) with the stored key and report
    pass/fail. Each call is a normal poll, so the result is kept in the history as evidence."""
    p = await _plant(s, mnem)
    eps = [e for e in p.endpoints if category is None or e.category == category]
    if not eps:
        raise HTTPException(400, "No matching endpoint")
    results = await _run_now(s, [(p, e) for e in eps])
    await audit(s, admin, "plant.test", "plant", p.mnemonic, None,
                {r["category"]: r["verdict"] for r in results})
    await s.commit()
    return {"plant": p.mnemonic, "tested_at": now_local(), "results": results, "summary": _summary(results)}


class PollNowIn(BaseModel):
    plants: list[str] | None = None  # None = every plant
    category: str | None = None      # None = every endpoint
    region: str | None = None        # limit the plants to one region

    @model_validator(mode="after")
    def check(self):
        if self.category is not None and self.category not in CATEGORIES:
            raise ValueError(f"Unknown category {self.category}")
        return self


_background: set[asyncio.Task] = set()


async def _execute_run(run_id: int, pairs: list[tuple[int, str]], user_id: int | None):
    """Carry out a Poll now run in the background, saving progress as calls finish and the results at the end."""
    done = 0

    def tick():
        nonlocal done
        done += 1

    async def progress():
        last = -1
        while True:
            await asyncio.sleep(1.5)
            if done != last:
                last = done
                async with SessionLocal() as s2:
                    await s2.execute(update(PollRun).where(PollRun.id == run_id).values(done=done))
                    await s2.commit()

    reporter = asyncio.create_task(progress())
    try:
        async with SessionLocal() as s:
            plants = {p.id: p for p in (await s.execute(select(Plant).where(
                Plant.id.in_({pid for pid, _ in pairs})))).scalars().all()}
            targets = []
            for pid, cat in pairs:
                p = plants.get(pid)
                e = next((x for x in p.endpoints if x.category == cat), None) if p else None
                if e is not None:
                    targets.append((p, e))
            results = await _run_now(s, targets, on_done=tick)
            summary = _summary(results)
            run = await s.get(PollRun, run_id)
            run.results, run.summary, run.status = _jsonable(results), summary, "done"
            run.done, run.total, run.finished_at = len(results), len(results), now_local()
            s.add(AuditEvent(ts=now_local(), user_id=user_id, action="poll.manual", entity="poll_run",
                             entity_id=str(run_id), after={"plants": run.plants or "all",
                                                           "category": run.category or "all", **summary}))
            await s.commit()
    except Exception as e:  # noqa: BLE001
        log.exception("poll now run %s failed", run_id)
        async with SessionLocal() as s:
            await s.execute(update(PollRun).where(PollRun.id == run_id).values(
                status="failed", finished_at=now_local(), summary={"error": str(e)[:300]}))
            await s.commit()
    finally:
        reporter.cancel()


@router.post("/poll-now")
async def poll_now_scope(body: PollNowIn, admin: AppUser = Depends(require_admin),
                         s: AsyncSession = Depends(get_session)):
    """Ad-hoc poll of the whole fleet, a region, some plants, one endpoint type, or one endpoint of one plant.
    Runs even while polling is paused, stores and validates like a scheduled poll, and reports the
    alerts each call raised, kept open or cleared.

    The run goes on in the background, so a fleet of hundreds of plants works too: the request waits up
    to poll_now_wait_s (20 s) and returns the finished run, or the run so far (status running, done of
    total) for the client to follow at GET /admin/poll-runs/{id}."""
    cfg = get_settings()
    q = select(Plant).order_by(Plant.mnemonic)
    if body.plants:
        q = q.where(Plant.mnemonic.in_([m.lower() for m in body.plants]))
    if body.region:
        q = q.where(Plant.region.is_(None) if body.region == "No region" else Plant.region == body.region)
    plants = (await s.execute(q)).scalars().all()
    if body.plants and not body.region and len(plants) != len(set(m.lower() for m in body.plants)):
        raise HTTPException(404, "Plant not found")
    targets = [(p, e) for p in plants for e in p.endpoints
               if e.url and (body.category is None or e.category == body.category)]
    if not targets:
        raise HTTPException(400, "No matching endpoint")
    if len(targets) > cfg.poll_now_max_endpoints:
        raise HTTPException(400, f"Too many endpoints for one Retrieve now run ({len(targets)}; the limit is "
                                 f"{cfg.poll_now_max_endpoints}). Choose fewer plants or one API.")
    scope = sorted(p.mnemonic for p in plants) if body.plants or body.region else None
    run = PollRun(started_at=now_local(), user_id=admin.id, plants=scope, category=body.category,
                  status="running", total=len(targets), done=0, summary={}, results=[])
    s.add(run)
    await s.commit()
    task = asyncio.create_task(_execute_run(run.id, [(p.id, e.category) for p, e in targets], admin.id))
    _background.add(task)
    task.add_done_callback(_background.discard)
    t0 = _time.monotonic()
    while not task.done() and _time.monotonic() - t0 < cfg.poll_now_wait_s:
        await asyncio.sleep(0.25)
    await s.refresh(run)
    return await _run_out(s, run, full=True)


async def _run_out(s: AsyncSession, r: PollRun, full: bool) -> dict:
    email = (await s.execute(select(AppUser.email).where(AppUser.id == r.user_id))).scalar_one_or_none() \
        if r.user_id else None
    out = {"id": r.id, "polled_at": r.started_at, "finished_at": r.finished_at, "by": email,
           "plants": r.plants, "category": r.category, "summary": r.summary, "status": r.status,
           "done": r.done, "total": r.total, "count": len(r.results or []) or r.total}
    if full:
        out["results"] = r.results
    return out


@router.get("/poll-runs")
async def list_poll_runs(limit: int = 20, offset: int = 0, admin: AppUser = Depends(require_admin),
                         s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(PollRun).order_by(PollRun.id.desc()).limit(min(max(limit, 1), 200))
                            .offset(max(offset, 0)))).scalars().all()
    return [await _run_out(s, r, full=False) for r in rows]


@router.get("/poll-runs/{run_id}")
async def get_poll_run(run_id: int, admin: AppUser = Depends(require_admin), s: AsyncSession = Depends(get_session)):
    r = await s.get(PollRun, run_id)
    if r is None:
        raise HTTPException(404, "Retrieve now run not found")
    return await _run_out(s, r, full=True)


# ---------------------------------------------------------------- schedules
class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    plants: list[str] | None = None  # None = all plants
    categories: list[str] | None = None  # None = all APIs
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
    # Recipient lists emailed the report after each retrieval of this schedule.
    recipient_list_ids: list[int] = []

    @model_validator(mode="after")
    def check(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Give the schedule a name")
        self.recipient_list_ids = sorted(set(self.recipient_list_ids or []))
        if self.plants is not None:
            self.plants = sorted({p.strip().lower() for p in self.plants if p.strip()})
            if not self.plants:
                raise ValueError("Choose at least one plant, or all plants")
        if self.categories is not None:
            bad = [c for c in self.categories if c not in CATEGORIES]
            if bad:
                raise ValueError(f"Unknown API {bad[0]}")
            self.categories = [c for c in CATEGORIES if c in self.categories]
            if not self.categories:
                raise ValueError("Choose at least one API, or all APIs")
            if len(self.categories) == len(CATEGORIES):
                self.categories = None  # every API ticked = all APIs
        if self.kind == "interval" and not self.interval_minutes:
            raise ValueError("interval_minutes is required for an interval schedule")
        if self.kind == "cron" and (not self.cron or not croniter.is_valid(self.cron)):
            raise ValueError("A valid 5-field cron expression is required")
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("The end date is before the start date")
        return self


async def _plant_maps(s: AsyncSession) -> tuple[dict[int, str], dict[str, int]]:
    rows = (await s.execute(select(Plant.id, Plant.mnemonic))).all()
    return {r.id: r.mnemonic for r in rows}, {r.mnemonic: r.id for r in rows}


async def _governed_pairs(s: AsyncSession) -> list[tuple[int, str]]:
    """Every (plant, API) a schedule can govern: each enabled endpoint of a live plant."""
    return [(r.plant_id, r.category) for r in (await s.execute(
        select(PlantEndpoint.plant_id, PlantEndpoint.category).join(Plant).where(
            PlantEndpoint.enabled, Plant.status == "live"))).all()]


def schedule_out(sc: PollSchedule, names: dict[int, str], governs: dict[int, int] | None = None,
                 matched: dict[int, int] | None = None, priority: int | None = None) -> dict:
    d = {c.name: getattr(sc, c.name) for c in PollSchedule.__table__.columns}
    d["plants"] = None if sc.plant_ids is None else sorted(names[i] for i in sc.plant_ids if i in names)
    d["next_runs"] = next_runs(sc, now_local()) if sc.enabled else []
    d["in_force"] = bool(sc.enabled and in_dates(sc, now_local().date()))
    d["priority"] = priority
    if governs is not None:
        d["governs"] = governs.get(sc.id, 0)
        d["matches"] = (matched or {}).get(sc.id, 0)
    return d


@router.get("/schedules")
async def schedules(admin: AppUser = Depends(require_admin), s: AsyncSession = Depends(get_session)):
    """Every schedule, highest priority first, with how many plant/API pairs it matches and how many it
    actually governs (the rest are taken by a more specific schedule)."""
    rows = (await s.execute(select(PollSchedule))).scalars().all()
    names, _ = await _plant_maps(s)
    pairs = await _governed_pairs(s)
    today = now_local().date()
    eff = effective_schedules(rows, pairs, today)
    governs: dict[int, int] = {}
    for sc in eff.values():
        governs[sc.id] = governs.get(sc.id, 0) + 1
    matched = {sc.id: sum(1 for p, c in pairs if matches(sc, p, c)) for sc in rows}
    ordered = sorted(rows, key=precedence)
    pr = {sc.id: i + 1 for i, sc in enumerate(ordered)}
    return {"rows": [schedule_out(sc, names, governs, matched, pr[sc.id]) for sc in ordered],
            "pairs": len(pairs), "unscheduled": len(pairs) - len(eff)}


@router.get("/schedules/coverage")
async def schedule_coverage(q: str | None = None, region: str | None = None, gaps: bool = False,
                            schedule: int | None = None, offset: int = 0,
                            limit: int = 50, admin: AppUser = Depends(require_admin),
                            s: AsyncSession = Depends(get_session)):
    """Which schedule governs each plant and API, one page of plants at a time. gaps=true lists only plants
    with an enabled API that no schedule covers; schedule=<id> lists only plants that schedule governs."""
    cond = []
    if q and q.strip():
        like = f"%{q.strip()}%"
        cond.append(or_(Plant.mnemonic.ilike(like), Plant.name.ilike(like), Plant.region.ilike(like)))
    if region:
        cond.append(Plant.region.is_(None) if region == "No region" else Plant.region == region)
    plants = (await s.execute(select(Plant).where(*cond).order_by(Plant.mnemonic))).scalars().all()
    scheds = (await s.execute(select(PollSchedule))).scalars().all()
    today = now_local().date()
    ranked = in_force(scheds, today)
    sw = await load_switches(s)
    out = []
    for p in plants:
        cells = {}
        for cat in CATEGORIES:
            ep = next((e for e in p.endpoints if e.category == cat), None)
            hits = [sc for sc in ranked if matches(sc, p.id, cat)]
            state = ("no_endpoint" if ep is None else "endpoint_off" if not ep.enabled
                     else "not_live" if p.status != "live" else "scheduled" if hits else "none")
            cells[cat] = {"state": state, "schedule_id": hits[0].id if hits else None,
                          "also": [h.id for h in hits[1:]],
                          "off_reason": off_reason(sw, p, ep) if ep is not None and ep.enabled else None}
        if gaps and not any(c["state"] == "none" for c in cells.values()):
            continue
        if schedule and not any(c["state"] == "scheduled" and c["schedule_id"] == schedule for c in cells.values()):
            continue
        out.append({"plant": p.mnemonic, "name": p.name, "region": p.region, "status": p.status, "cells": cells})
    lim = max(1, min(limit, 500))
    return {"rows": out[max(0, offset):max(0, offset) + lim], "total": len(out), "offset": offset, "limit": lim}


async def _schedule_fields(s: AsyncSession, body: ScheduleIn) -> dict:
    data = body.model_dump(exclude={"plants"})
    if body.plants is None:
        data["plant_ids"] = None
    else:
        _, ids = await _plant_maps(s)
        unknown = [m for m in body.plants if m not in ids]
        if unknown:
            raise HTTPException(400, f"Unknown plant {unknown[0].upper()}")
        data["plant_ids"] = sorted(ids[m] for m in body.plants)
    if body.recipient_list_ids:
        found = set((await s.execute(select(RecipientList.id).where(
            RecipientList.id.in_(body.recipient_list_ids)))).scalars().all())
        missing = [i for i in body.recipient_list_ids if i not in found]
        if missing:
            raise HTTPException(400, "A chosen recipient list no longer exists; reload the page")
    data["recipient_list_ids"] = body.recipient_list_ids or None
    return data


def _mark_new_lists(sc: PollSchedule, old_ids: list[int] | None):
    """A newly paired list gets only retrievals queued from now on, never ones that ran before."""
    if set(sc.recipient_list_ids or []) - set(old_ids or []):
        sc.email_since = now_local()


def _audit_view(sc: PollSchedule, names: dict[int, str]) -> dict:
    return {k: v for k, v in schedule_out(sc, names).items()
            if k not in ("next_runs", "in_force", "priority", "email_since")}


@router.post("/schedules", status_code=201)
async def create_schedule(body: ScheduleIn, admin: AppUser = Depends(require_admin),
                          s: AsyncSession = Depends(get_session)):
    sc = PollSchedule(updated_at=now_local(), updated_by=admin.id, **await _schedule_fields(s, body))
    _mark_new_lists(sc, None)
    s.add(sc)
    await s.flush()
    await audit(s, admin, "schedule.create", "poll_schedule", sc.id, None, body.model_dump())
    await s.commit()
    names, _ = await _plant_maps(s)
    return schedule_out(sc, names)


@router.put("/schedules/{sid}")
async def update_schedule(sid: int, body: ScheduleIn, admin: AppUser = Depends(require_admin),
                          s: AsyncSession = Depends(get_session)):
    sc = await s.get(PollSchedule, sid)
    if sc is None:
        raise HTTPException(404, "Schedule not found")
    names, _ = await _plant_maps(s)
    before = _audit_view(sc, names)
    old_lists = list(sc.recipient_list_ids or [])
    for k, v in (await _schedule_fields(s, body)).items():
        setattr(sc, k, v)
    _mark_new_lists(sc, old_lists)
    sc.updated_at, sc.updated_by = now_local(), admin.id
    await audit(s, admin, "schedule.update", "poll_schedule", sid, before, body.model_dump())
    await s.commit()
    return schedule_out(sc, names)


@router.delete("/schedules/{sid}")
async def delete_schedule(sid: int, admin: AppUser = Depends(require_admin), s: AsyncSession = Depends(get_session)):
    sc = await s.get(PollSchedule, sid)
    if sc is None:
        raise HTTPException(404, "Schedule not found")
    names, _ = await _plant_maps(s)
    await audit(s, admin, "schedule.delete", "poll_schedule", sid, _audit_view(sc, names), None)
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
async def audit_log(q: str | None = None, action: str | None = None, offset: int = 0, limit: int = 100,
                    admin: AppUser = Depends(require_admin), s: AsyncSession = Depends(get_session)):
    """The audit log, newest first, one page at a time. q matches the user, action, target or change."""
    cond = []
    if q and q.strip():
        like = f"%{q.strip()}%"
        cond.append(or_(AppUser.email.ilike(like), AuditEvent.action.ilike(like), AuditEvent.entity.ilike(like),
                        AuditEvent.entity_id.ilike(like), func.cast(AuditEvent.after, Text).ilike(like)))
    if action:
        cond.append(AuditEvent.action.like(f"{action}%"))
    base = select(AuditEvent, AppUser.email).outerjoin(AppUser, AppUser.id == AuditEvent.user_id).where(*cond)
    total = (await s.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await s.execute(base.order_by(AuditEvent.ts.desc(), AuditEvent.id.desc())
                            .limit(max(1, min(limit, 1000))).offset(max(0, offset)))).all()
    return {"rows": [{"id": a.id, "ts": a.ts, "user": e, "action": a.action, "entity": a.entity,
                      "entity_id": a.entity_id, "before": a.before, "after": a.after} for a, e in rows],
            "total": total, "offset": offset, "limit": limit}
