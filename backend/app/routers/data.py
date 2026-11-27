"""Read API for the dashboard. Every query is filtered to the plants the user may see."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user, plant_for_user, visible_plant_ids
from ..config import now_local
from ..db import get_session
from ..models import (
    CATEGORIES, FORECAST_CATEGORIES, AlertLog, AppUser, ComplianceFlag, DeadlineCheck, ForecastPoint,
    ForecastSnapshot, MmfReading, Plant, PollAttempt, RawResponse,
)

router = APIRouter(tags=["data"])


def plant_out(p: Plant) -> dict:
    return {"id": p.id, "mnemonic": p.mnemonic, "name": p.name, "capacity_mw": p.capacity_mw,
            "mmf_station_count": p.mmf_station_count, "status": p.status, "notes": p.notes,
            "endpoints": [{"category": e.category, "url": e.url, "enabled": e.enabled, "note": e.note}
                          for e in sorted(p.endpoints, key=lambda e: CATEGORIES.index(e.category)
                                          if e.category in CATEGORIES else 99)]}


@router.get("/plants")
async def list_plants(user: AppUser = Depends(current_user), s: AsyncSession = Depends(get_session)):
    ids = await visible_plant_ids(user, s)
    plants = (await s.execute(select(Plant).where(Plant.id.in_(ids)).order_by(Plant.mnemonic))).scalars().all()
    return [plant_out(p) for p in plants]


@router.get("/plants/{mnem}")
async def get_plant(mnem: str, user: AppUser = Depends(current_user), s: AsyncSession = Depends(get_session)):
    return plant_out(await plant_for_user(mnem, user, s))


@router.get("/fleet/status")
async def fleet_status(user: AppUser = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Compliance board: the latest poll per plant and category, plus open alert counts."""
    ids = list(await visible_plant_ids(user, s))
    if not ids:
        return {"generated_at": now_local(), "plants": []}
    latest = (await s.execute(text("""
        SELECT DISTINCT ON (plant_id, category) plant_id, category, id, started_at, http_status, latency_ms,
               is_valid, major_flags, minor_flags, error
        FROM poll_attempt WHERE plant_id = ANY(:ids)
        ORDER BY plant_id, category, started_at DESC, id DESC"""), {"ids": ids})).mappings().all()
    last_ok = (await s.execute(text("""
        SELECT plant_id, category, max(started_at) AS t FROM poll_attempt
        WHERE plant_id = ANY(:ids) AND is_valid GROUP BY plant_id, category"""), {"ids": ids})).mappings().all()
    alerts = (await s.execute(select(AlertLog.plant_id, AlertLog.category, func.count()).where(
        AlertLog.plant_id.in_(ids), AlertLog.cleared_at.is_(None)).group_by(AlertLog.plant_id, AlertLog.category)
    )).all()
    plants = (await s.execute(select(Plant).where(Plant.id.in_(ids)).order_by(Plant.mnemonic))).scalars().all()

    by = {(r["plant_id"], r["category"]): dict(r) for r in latest}
    ok = {(r["plant_id"], r["category"]): r["t"] for r in last_ok}
    al = {(p, c): n for p, c, n in alerts}
    out = []
    for p in plants:
        cells = {}
        for e in p.endpoints:
            r = by.get((p.id, e.category))
            if not e.enabled:
                state = "disabled"
            elif r is None:
                state = "never"
            elif r["http_status"] != 200:
                state = "down"
            elif r["is_valid"]:
                state = "minor" if r["minor_flags"] else "ok"
            else:
                state = "invalid"
            cells[e.category] = {
                "state": state, "enabled": e.enabled, "last_poll": r["started_at"] if r else None,
                "http_status": r["http_status"] if r else None, "latency_ms": r["latency_ms"] if r else None,
                "major_flags": r["major_flags"] if r else 0, "minor_flags": r["minor_flags"] if r else 0,
                "error": r["error"] if r else None, "last_valid": ok.get((p.id, e.category)),
                "open_alerts": al.get((p.id, e.category), 0), "attempt_id": r["id"] if r else None,
            }
        out.append({"mnemonic": p.mnemonic, "name": p.name, "status": p.status, "cells": cells,
                    "open_alerts": sum(c["open_alerts"] for c in cells.values())})
    return {"generated_at": now_local(), "plants": out}


@router.get("/plants/{mnem}/forecast/{category}")
async def forecast(mnem: str, category: str, as_of: datetime | None = Query(None, alias="asOf"),
                   user: AppUser = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Latest forecast snapshot (or the one GSO held at `asOf`), its points and its flags."""
    if category not in FORECAST_CATEGORIES:
        raise HTTPException(404, "Unknown forecast category")
    plant = await plant_for_user(mnem, user, s)
    q = select(ForecastSnapshot).where(ForecastSnapshot.plant_id == plant.id, ForecastSnapshot.category == category)
    if as_of:
        q = q.where(ForecastSnapshot.retrieved_at <= as_of)
    snap = (await s.execute(q.order_by(ForecastSnapshot.retrieved_at.desc()).limit(1))).scalar_one_or_none()
    if snap is None:
        return {"snapshot": None, "points": [], "flags": []}
    points = (await s.execute(select(ForecastPoint).where(ForecastPoint.snapshot_id == snap.id)
                              .order_by(ForecastPoint.start_time))).scalars().all()
    flags = (await s.execute(select(ComplianceFlag).where(ComplianceFlag.poll_attempt_id == snap.poll_attempt_id)
                             )).scalars().all()
    return {
        "snapshot": {"id": snap.id, "retrieved_at": snap.retrieved_at, "horizon_start": snap.horizon_start,
                     "horizon_end": snap.horizon_end, "expected_start": snap.expected_start,
                     "point_count": snap.point_count, "expected_count": snap.expected_count,
                     "is_valid": snap.is_valid, "raw_response_id": snap.raw_response_id},
        "points": [{"t": p.start_time, "mw": p.value_mw} for p in points],
        "flags": [flag_out(f) for f in flags],
    }


@router.get("/plants/{mnem}/mmf/latest")
async def mmf_latest(mnem: str, user: AppUser = Depends(current_user), s: AsyncSession = Depends(get_session)):
    plant = await plant_for_user(mnem, user, s)
    attempt = (await s.execute(select(PollAttempt).where(
        PollAttempt.plant_id == plant.id, PollAttempt.category == "mmf").order_by(PollAttempt.started_at.desc())
        .limit(1))).scalar_one_or_none()
    if attempt is None:
        return {"attempt": None, "readings": [], "flags": []}
    rows = (await s.execute(select(MmfReading).where(MmfReading.poll_attempt_id == attempt.id)
                            .order_by(MmfReading.is_standard.desc(), MmfReading.parameter, MmfReading.station_no)
                            )).scalars().all()
    flags = (await s.execute(select(ComplianceFlag).where(ComplianceFlag.poll_attempt_id == attempt.id))).scalars().all()
    return {
        "attempt": attempt_out(attempt),
        "readings": [{"station": r.station_no, "parameter": r.parameter, "time": r.measured_at, "value": r.value,
                      "unit": r.unit, "standard": r.is_standard} for r in rows],
        "flags": [flag_out(f) for f in flags],
    }


@router.get("/plants/{mnem}/attempts")
async def attempts(mnem: str, category: str | None = None, limit: int = Query(50, le=500),
                   user: AppUser = Depends(current_user), s: AsyncSession = Depends(get_session)):
    plant = await plant_for_user(mnem, user, s)
    q = select(PollAttempt).where(PollAttempt.plant_id == plant.id)
    if category:
        q = q.where(PollAttempt.category == category)
    rows = (await s.execute(q.order_by(PollAttempt.started_at.desc(), PollAttempt.id.desc()).limit(limit))).scalars().all()
    return [attempt_out(a) for a in rows]


@router.get("/attempts/{attempt_id}/flags")
async def attempt_flags(attempt_id: int, user: AppUser = Depends(current_user), s: AsyncSession = Depends(get_session)):
    a = await s.get(PollAttempt, attempt_id)
    if a is None or a.plant_id not in await visible_plant_ids(user, s):
        raise HTTPException(404, "Not found")
    flags = (await s.execute(select(ComplianceFlag).where(ComplianceFlag.poll_attempt_id == attempt_id))).scalars().all()
    return {"attempt": attempt_out(a), "flags": [flag_out(f) for f in flags]}


@router.get("/compliance/flags")
async def compliance_flags(plant: str | None = None, category: str | None = None, severity: str | None = None,
                           limit: int = Query(200, le=1000), user: AppUser = Depends(current_user),
                           s: AsyncSession = Depends(get_session)):
    ids = await visible_plant_ids(user, s)
    q = select(ComplianceFlag, Plant.mnemonic).join(Plant, Plant.id == ComplianceFlag.plant_id).where(
        ComplianceFlag.plant_id.in_(ids))
    if plant:
        q = q.where(Plant.mnemonic == plant.lower())
    if category:
        q = q.where(ComplianceFlag.category == category)
    if severity:
        q = q.where(ComplianceFlag.severity == severity)
    rows = (await s.execute(q.order_by(ComplianceFlag.ts.desc(), ComplianceFlag.id.desc()).limit(limit))).all()
    return [{**flag_out(f), "plant": m} for f, m in rows]


@router.get("/compliance/summary")
async def compliance_summary(user: AppUser = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Rule counts per plant over the last 24 hours."""
    ids = list(await visible_plant_ids(user, s))
    rows = (await s.execute(text("""
        SELECT p.mnemonic, f.category, f.rule, f.severity, count(*) AS n, max(f.message) AS example
        FROM compliance_flag f JOIN plant p ON p.id = f.plant_id
        WHERE f.plant_id = ANY(:ids) AND f.ts > :since
        GROUP BY 1,2,3,4 ORDER BY 1,2,4,3"""), {"ids": ids, "since": now_local() - timedelta(hours=24)})).mappings().all()
    return [dict(r) for r in rows]


@router.get("/compliance/deadlines")
async def deadlines(limit: int = Query(200, le=1000), user: AppUser = Depends(current_user),
                    s: AsyncSession = Depends(get_session)):
    ids = await visible_plant_ids(user, s)
    rows = (await s.execute(select(DeadlineCheck, Plant.mnemonic).join(Plant, Plant.id == DeadlineCheck.plant_id)
                            .where(DeadlineCheck.plant_id.in_(ids))
                            .order_by(DeadlineCheck.due_at.desc(), Plant.mnemonic).limit(limit))).all()
    return [{"plant": m, "category": d.category, "due_at": d.due_at, "target_start": d.target_start,
             "first_valid_at": d.first_valid_at, "status": d.status} for d, m in rows]


@router.get("/alerts")
async def alerts(open_only: bool = Query(False, alias="open"), plant: str | None = None,
                 limit: int = Query(200, le=1000), user: AppUser = Depends(current_user),
                 s: AsyncSession = Depends(get_session)):
    ids = await visible_plant_ids(user, s)
    q = select(AlertLog, Plant.mnemonic).join(Plant, Plant.id == AlertLog.plant_id).where(AlertLog.plant_id.in_(ids))
    if open_only:
        q = q.where(AlertLog.cleared_at.is_(None))
    if plant:
        q = q.where(Plant.mnemonic == plant.lower())
    rows = (await s.execute(q.order_by(AlertLog.raised_at.desc(), AlertLog.id.desc()).limit(limit))).all()
    return [{"id": a.id, "plant": m, "category": a.category, "rule": a.rule, "severity": a.severity,
             "message": a.message, "raised_at": a.raised_at, "last_seen_at": a.last_seen_at,
             "occurrences": a.occurrences, "cleared_at": a.cleared_at, "acknowledged_by": a.acknowledged_by,
             "acknowledged_at": a.acknowledged_at, "detail": a.detail} for a, m in rows]


@router.post("/alerts/{alert_id}/ack")
async def ack_alert(alert_id: int, user: AppUser = Depends(current_user), s: AsyncSession = Depends(get_session)):
    if user.role not in ("gso_admin", "gso_user"):
        raise HTTPException(403, "Only GSO staff acknowledge alerts")
    a = await s.get(AlertLog, alert_id)
    if a is None:
        raise HTTPException(404, "Not found")
    a.acknowledged_by, a.acknowledged_at = user.id, now_local()
    await s.commit()
    return {"ok": True}


@router.get("/raw/{raw_id}")
async def raw(raw_id: int, user: AppUser = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """The exact response GSO received, for disputes."""
    r = await s.get(RawResponse, raw_id)
    if r is None or r.plant_id not in await visible_plant_ids(user, s):
        raise HTTPException(404, "Not found")
    return {"id": r.id, "category": r.category, "received_at": r.received_at, "sha256": r.content_hash,
            "body": r.body, "body_text": r.body_text}


def flag_out(f: ComplianceFlag) -> dict:
    return {"id": f.id, "attempt_id": f.poll_attempt_id, "category": f.category, "ts": f.ts, "rule": f.rule,
            "severity": f.severity, "message": f.message, "detail": f.detail}


def attempt_out(a: PollAttempt) -> dict:
    return {"id": a.id, "category": a.category, "url": a.url, "scheduled_for": a.scheduled_for,
            "started_at": a.started_at, "http_status": a.http_status, "latency_ms": a.latency_ms,
            "bytes": a.bytes, "tries": a.tries, "error": a.error, "is_valid": a.is_valid,
            "major_flags": a.major_flags, "minor_flags": a.minor_flags, "raw_response_id": a.raw_response_id}
