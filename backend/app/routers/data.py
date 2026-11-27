"""Read API for the dashboard. Every query is filtered to the plants the user may see."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import validation as v
from ..auth import current_user, plant_for_user, visible_plant_ids
from ..config import now_local
from ..db import get_session
from ..switches import Switches, load_switches, off_reason
from ..models import (
    CATEGORIES, FORECAST_CATEGORIES, AlertLog, AppUser, ComplianceFlag, DeadlineCheck, ForecastPoint,
    ForecastSnapshot, MmfReading, Plant, PollAttempt, RawResponse,
)

router = APIRouter(tags=["data"])


def plant_out(p: Plant, admin: bool = False, sw: Switches | None = None) -> dict:
    """The API key itself is never returned; admins see whether one is set and its last 4 characters.
    With switches given, each endpoint also says whether it is polled on schedule and, if not, why."""
    sw = sw or Switches()
    auth = {"auth_method": p.auth_method, "auth_param_name": p.auth_param_name,
            "has_api_key": bool(p.api_key_enc), "api_key_hint": p.api_key_hint,
            "created_at": p.created_at, "updated_at": p.updated_at} if admin else {}
    return {"id": p.id, "mnemonic": p.mnemonic, "name": p.name, "region": p.region, "capacity_mw": p.capacity_mw,
            "mmf_station_count": p.mmf_station_count, "status": p.status, "notes": p.notes, **auth,
            "polling_enabled": p.polling_enabled,
            "endpoints": [{"category": e.category, "url": e.url, "enabled": e.enabled, "note": e.note,
                           "polled": off_reason(sw, p, e) is None, "off_reason": off_reason(sw, p, e)}
                          for e in sorted(p.endpoints, key=lambda e: CATEGORIES.index(e.category)
                                          if e.category in CATEGORIES else 99)]}


@router.get("/plants")
async def list_plants(brief: bool = False, user: AppUser = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Every plant the user may see. brief=true returns only what plant pickers need (mnemonic, name,
    region, status), which stays small at hundreds of plants."""
    ids = await visible_plant_ids(user, s)
    if brief:
        rows = (await s.execute(select(Plant.mnemonic, Plant.name, Plant.region, Plant.status)
                                .where(Plant.id.in_(ids)).order_by(Plant.mnemonic))).all()
        return [{"mnemonic": m, "name": n, "region": r, "status": st} for m, n, r, st in rows]
    plants = (await s.execute(select(Plant).where(Plant.id.in_(ids)).order_by(Plant.mnemonic))).scalars().all()
    sw = await load_switches(s)
    return [plant_out(p, user.role == "gso_admin", sw) for p in plants]


@router.get("/plants/{mnem}")
async def get_plant(mnem: str, user: AppUser = Depends(current_user), s: AsyncSession = Depends(get_session)):
    return plant_out(await plant_for_user(mnem, user, s), user.role == "gso_admin", await load_switches(s))


# Fleet board: how bad each endpoint state is, for problem-first sorting.
STATE_RANK = {"down": 5, "invalid": 4, "never": 2, "minor": 1, "ok": 0, "disabled": 0}
BOARD_STATES = ("problems", "healthy", "alerts", "down", "invalid", "minor", "ok", "never", "disabled")
BOARD_SORTS = ("problems", "mnemonic", "name", "region", "alerts", "last_poll", "capacity")
NO_REGION = "No region"


def _board_match(row: dict, state: str | None, category: str | None) -> bool:
    cells = [row["cells"][category]] if category and category in row["cells"] else (
        [] if category else list(row["cells"].values()))
    states = {c["state"] for c in cells}
    alerts = sum(c["open_alerts"] for c in cells)
    if state is None:
        return True
    if state == "problems":
        return bool(states & {"down", "invalid"}) or alerts > 0
    if state == "healthy":
        return not (states & {"down", "invalid"}) and alerts == 0
    if state == "alerts":
        return alerts > 0
    return state in states


def _board_key(sort: str):
    if sort == "mnemonic":
        return lambda r: (r["mnemonic"],)
    if sort == "name":
        return lambda r: ((r["name"] or "").lower(), r["mnemonic"])
    if sort == "region":
        return lambda r: (r["region"] is None, (r["region"] or "").lower(), r["mnemonic"])
    if sort == "alerts":
        return lambda r: (-r["open_alerts"], -r["worst_rank"], r["mnemonic"])
    if sort == "last_poll":  # stalest first
        return lambda r: (r["last_poll"] is not None, r["last_poll"] or datetime.min, r["mnemonic"])
    if sort == "capacity":
        return lambda r: (-(r["capacity_mw"] or 0), r["mnemonic"])
    # problems first: worst endpoint state, then how many endpoints have problems, then open alerts
    return lambda r: (-r["worst_rank"], -r["problem_count"], -r["open_alerts"], r["mnemonic"])


@router.get("/fleet/status")
async def fleet_status(q: str | None = None, state: str | None = None, category: str | None = None,
                       region: str | None = None, plant_status: str | None = Query(None, alias="status"),
                       sort: str = "problems", desc: bool = False, group: str | None = None,
                       offset: int = Query(0, ge=0), limit: int = Query(0, ge=0, le=5000),
                       user: AppUser = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Compliance board: the latest poll per plant and category, plus open alert counts.

    Built for a fleet of hundreds of plants: filter (q matches mnemonic, name or region; state; one API;
    region; plant status), sort (problems first by default), optionally group by region, and page
    (limit 0 = every matching plant). Tile counts cover every visible plant; facet counts (plants per
    state) apply every filter except state, so the filter chips can show what each choice would give."""
    if state and state not in BOARD_STATES:
        raise HTTPException(400, f"state must be one of {', '.join(BOARD_STATES)}")
    if category and category not in CATEGORIES:
        raise HTTPException(400, "Unknown API")
    if sort not in BOARD_SORTS:
        raise HTTPException(400, f"sort must be one of {', '.join(BOARD_SORTS)}")
    ids = list(await visible_plant_ids(user, s))
    sw = await load_switches(s)
    polling = {"fleet": sw.fleet, "types": sw.types}
    empty = {"generated_at": now_local(), "plants": [], "polling": polling, "total": 0, "matched": 0,
             "counts": {}, "facets": {}, "regions": [], "groups": [], "mnemonics": [], "offset": offset,
             "limit": limit}
    if not ids:
        return empty
    # One indexed lookup per endpoint (LATERAL ... LIMIT 1) instead of DISTINCT ON over all poll history,
    # so the board stays fast as poll_attempt grows by tens of thousands of rows a day.
    latest = (await s.execute(text("""
        SELECT e.plant_id, e.category, a.id, a.started_at, a.http_status, a.latency_ms, a.is_valid,
               a.major_flags, a.minor_flags, a.error, ok.started_at AS last_valid
        FROM plant_endpoint e
        LEFT JOIN LATERAL (
            SELECT id, started_at, http_status, latency_ms, is_valid, major_flags, minor_flags, error
            FROM poll_attempt x WHERE x.plant_id = e.plant_id AND x.category = e.category
            ORDER BY x.started_at DESC, x.id DESC LIMIT 1) a ON true
        LEFT JOIN LATERAL (
            SELECT started_at FROM poll_attempt y
            WHERE y.plant_id = e.plant_id AND y.category = e.category AND y.is_valid
            ORDER BY y.started_at DESC LIMIT 1) ok ON true
        WHERE e.plant_id = ANY(:ids)"""), {"ids": ids})).mappings().all()
    alerts = (await s.execute(select(AlertLog.plant_id, AlertLog.category, func.count()).where(
        AlertLog.plant_id.in_(ids), AlertLog.cleared_at.is_(None)).group_by(AlertLog.plant_id, AlertLog.category)
    )).all()
    plants = (await s.execute(select(Plant).where(Plant.id.in_(ids)).order_by(Plant.mnemonic))).scalars().all()

    by = {(r["plant_id"], r["category"]): r for r in latest}
    al = {(p, c): n for p, c, n in alerts}
    rows = []
    counts: dict[str, int] = {}
    for p in plants:
        cells = {}
        for e in p.endpoints:
            row = by.get((p.id, e.category))
            r = row if row is not None and row["id"] is not None else None
            last_valid = row["last_valid"] if row is not None else None
            reason = off_reason(sw, p, e)
            if reason:
                state_ = "disabled"
            elif r is None:
                state_ = "never"
            elif r["http_status"] != 200:
                state_ = "down"
            elif r["is_valid"]:
                state_ = "minor" if r["minor_flags"] else "ok"
            else:
                state_ = "invalid"
            counts[state_] = counts.get(state_, 0) + 1
            cells[e.category] = {
                "state": state_, "enabled": reason is None, "off_reason": reason,
                "last_poll": r["started_at"] if r else None,
                "http_status": r["http_status"] if r else None, "latency_ms": r["latency_ms"] if r else None,
                "major_flags": r["major_flags"] if r else 0, "minor_flags": r["minor_flags"] if r else 0,
                "error": r["error"] if r else None, "last_valid": last_valid,
                "open_alerts": al.get((p.id, e.category), 0), "attempt_id": r["id"] if r else None,
            }
        last_polls = [c["last_poll"] for c in cells.values() if c["last_poll"]]
        rows.append({"mnemonic": p.mnemonic, "name": p.name, "region": p.region, "status": p.status,
                     "capacity_mw": p.capacity_mw, "polling_enabled": p.polling_enabled, "cells": cells,
                     "open_alerts": sum(c["open_alerts"] for c in cells.values()),
                     "worst_rank": max((STATE_RANK.get(c["state"], 0) for c in cells.values()), default=0),
                     "problem_count": sum(c["state"] in ("down", "invalid") for c in cells.values()),
                     "last_poll": max(last_polls) if last_polls else None})

    regions: dict[str, dict] = {}
    for r in rows:
        g = regions.setdefault(r["region"] or NO_REGION, {"region": r["region"], "plants": 0, "problems": 0})
        g["plants"] += 1
        g["problems"] += _board_match(r, "problems", None)

    base = rows
    if q:
        needle = q.strip().lower()
        base = [r for r in base if needle in r["mnemonic"] or needle in (r["name"] or "").lower()
                or needle in (r["region"] or "").lower()]
    if region:
        want = {x for x in region.split(",") if x}
        base = [r for r in base if (r["region"] or NO_REGION) in want]
    if plant_status:
        want = set(plant_status.split(","))
        base = [r for r in base if r["status"] in want]
    facets = {st: sum(_board_match(r, st, category) for r in base) for st in BOARD_STATES}
    facets["all"] = len(base)
    matched = [r for r in base if _board_match(r, state, category)]

    key = _board_key(sort)
    matched.sort(key=key, reverse=desc)
    groups = []
    if group == "region":
        # Stable sort keeps the chosen order within each region; regions A-Z, plants without one last.
        matched.sort(key=lambda r: (r["region"] is None, (r["region"] or "").lower()))
        seen: dict[str, dict] = {}
        for r in matched:
            g = seen.get(r["region"] or NO_REGION)
            if g is None:
                g = seen[r["region"] or NO_REGION] = {"region": r["region"], "plants": 0, "problems": 0,
                                                      "open_alerts": 0}
                groups.append(g)
            g["plants"] += 1
            g["problems"] += _board_match(r, "problems", None)
            g["open_alerts"] += r["open_alerts"]
    page = matched[offset:offset + limit] if limit else matched[offset:]
    return {**empty, "plants": page, "total": len(rows), "matched": len(matched), "counts": counts,
            "open_alerts": sum(r["open_alerts"] for r in rows),
            "facets": facets, "groups": groups, "mnemonics": [r["mnemonic"] for r in matched],
            "regions": sorted(regions.values(), key=lambda g: (g["region"] is None, (g["region"] or "").lower()))}


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
                     "is_valid": snap.is_valid, "raw_response_id": snap.raw_response_id,
                     "poll_attempt_id": snap.poll_attempt_id},
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


@router.get("/attempts/{attempt_id}/inspect")
async def inspect_attempt(attempt_id: int, user: AppUser = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """A poll's raw response plus each finding with the JSON Pointers of the places that caused it.

    Locations are found by re-running the (pure, deterministic) validator on the stored body with the
    poll's own time, so nothing extra is stored per poll. The stored flags stay the record of truth;
    a flag the re-run cannot place (e.g. HTTP errors or unchanged-content checks) applies to the
    whole response."""
    a = await s.get(PollAttempt, attempt_id)
    if a is None or a.plant_id not in await visible_plant_ids(user, s):
        raise HTTPException(404, "Not found")
    plant = await s.get(Plant, a.plant_id)
    raw = await s.get(RawResponse, a.raw_response_id) if a.raw_response_id else None
    stored = (await s.execute(select(ComplianceFlag).where(ComplianceFlag.poll_attempt_id == attempt_id)
                              .order_by(ComplianceFlag.id))).scalars().all()
    located: dict[tuple[str, str], list[str]] = {}
    if raw is not None and raw.body is not None:
        if a.category == "mmf":
            res = v.validate_mmf(raw.body, plant.mnemonic, a.started_at, plant.mmf_station_count)
        else:
            res = v.validate_forecast(raw.body, a.category, plant.mnemonic, a.started_at, plant.capacity_mw)
        for f in res.flags:
            located.setdefault((f.rule, f.severity), []).extend(f.paths)
    flags = []
    for f in stored:
        paths = located.get((f.rule, f.severity))
        flags.append({**flag_out(f), "paths": paths if paths is not None else [""],
                      "whole_response": paths is None or paths == [""]})
    return {
        "attempt": attempt_out(a),
        "plant": {"mnemonic": plant.mnemonic, "name": plant.name},
        "raw": None if raw is None else {"id": raw.id, "received_at": raw.received_at, "sha256": raw.content_hash,
                                         "body": raw.body, "body_text": raw.body_text,
                                         "first_seen_attempt": raw.poll_attempt_id},
        "flags": flags,
    }


async def scope_ids(user: AppUser, s: AsyncSession, plants: str | None = None, region: str | None = None) -> list[int]:
    """The visible plant ids, narrowed to a comma list of mnemonics and/or regions ("No region" = none set)."""
    ids = await visible_plant_ids(user, s)
    q = select(Plant.id).where(Plant.id.in_(ids))
    want = [m.strip().lower() for m in (plants or "").split(",") if m.strip() and m.strip() != "all"]
    if want:
        q = q.where(Plant.mnemonic.in_(want))
    regions = [r for r in (region or "").split(",") if r]
    if regions:
        named = [r for r in regions if r != NO_REGION]
        cond = Plant.region.in_(named) if named else None
        if NO_REGION in regions:
            cond = Plant.region.is_(None) if cond is None else (cond | Plant.region.is_(None))
        q = q.where(cond)
    return list((await s.execute(q)).scalars().all())


def _cats(category: str | None) -> list[str]:
    cats = [c for c in (category or "").split(",") if c and c != "all"]
    if any(c not in CATEGORIES for c in cats):
        raise HTTPException(400, "Unknown API")
    return cats


def _page(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(limit, 1000)), max(0, offset)


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
async def compliance_summary(plants: str | None = None, region: str | None = None, category: str | None = None,
                             severity: str | None = None, hours: int = Query(24, ge=1, le=24 * 31),
                             sort: str = "count", offset: int = 0, limit: int = 100,
                             user: AppUser = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Rule counts per plant, endpoint and rule over the last `hours` (24 by default), one page at a time.
    sort=count puts the most frequent breaks first; sort=plant orders by plant. by_plant gives the plants
    with the most breaks (top 10), and plants_affected how many plants had any."""
    limit, offset = _page(limit, offset)
    ids = await scope_ids(user, s, plants, region)
    where, params = ["f.plant_id = ANY(:ids)", "f.ts > :since"], {"ids": ids, "since": now_local() - timedelta(hours=hours)}
    cats = _cats(category)
    if cats:
        where.append("f.category = ANY(:cats)")
        params["cats"] = cats
    if severity in ("major", "minor"):
        where.append("f.severity = :sev")
        params["sev"] = severity
    order = "1,2,4,3" if sort == "plant" else "(f.severity = 'major') DESC, count(*) DESC, 1, 2, 3"
    base = f"""FROM compliance_flag f JOIN plant p ON p.id = f.plant_id WHERE {' AND '.join(where)}"""
    rows = (await s.execute(text(f"""
        SELECT p.mnemonic, f.category, f.rule, f.severity, count(*) AS n, max(f.message) AS example,
               max(f.ts) AS last_seen, count(*) OVER () AS total
        {base} GROUP BY p.mnemonic, f.category, f.rule, f.severity ORDER BY {order}
        LIMIT :limit OFFSET :offset"""), {**params, "limit": limit, "offset": offset})).mappings().all()
    top = (await s.execute(text(f"""
        SELECT p.mnemonic, count(*) FILTER (WHERE f.severity = 'major') AS major,
               count(*) FILTER (WHERE f.severity = 'minor') AS minor
        {base} GROUP BY p.mnemonic ORDER BY 2 DESC, 3 DESC, 1"""), params)).mappings().all()
    total = rows[0]["total"] if rows else (await s.execute(text(
        f"SELECT count(*) FROM (SELECT 1 {base} GROUP BY p.mnemonic, f.category, f.rule, f.severity) x"), params)
    ).scalar_one()
    return {"rows": [{k: v for k, v in r.items() if k != "total"} for r in rows], "total": total,
            "offset": offset, "limit": limit, "plants_affected": len(top), "by_plant": [dict(r) for r in top[:10]]}


@router.get("/compliance/deadlines")
async def deadlines(plants: str | None = None, region: str | None = None, category: str | None = None,
                    status: str | None = None, offset: int = 0, limit: int = Query(100, le=1000),
                    user: AppUser = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Deadline checks, newest first, one page at a time; counts give met/late/missed for the same filters
    without the status filter."""
    limit, offset = _page(limit, offset)
    ids = await scope_ids(user, s, plants, region)
    cond = [DeadlineCheck.plant_id.in_(ids)]
    cats = _cats(category)
    if cats:
        cond.append(DeadlineCheck.category.in_(cats))
    counts = dict((await s.execute(select(DeadlineCheck.status, func.count()).where(*cond)
                                   .group_by(DeadlineCheck.status))).all())
    statuses = [x for x in (status or "").split(",") if x in ("met", "late", "missed")]
    if statuses:
        cond.append(DeadlineCheck.status.in_(statuses))
    total = (await s.execute(select(func.count()).select_from(DeadlineCheck).where(*cond))).scalar_one()
    rows = (await s.execute(select(DeadlineCheck, Plant.mnemonic).join(Plant, Plant.id == DeadlineCheck.plant_id)
                            .where(*cond).order_by(DeadlineCheck.due_at.desc(), Plant.mnemonic)
                            .limit(limit).offset(offset))).all()
    return {"rows": [{"plant": m, "category": d.category, "due_at": d.due_at, "target_start": d.target_start,
                      "first_valid_at": d.first_valid_at, "status": d.status} for d, m in rows],
            "total": total, "offset": offset, "limit": limit, "counts": counts}


ALERT_SORTS = {
    "newest": (AlertLog.raised_at.desc(), AlertLog.id.desc()),
    "oldest": (AlertLog.raised_at.asc(), AlertLog.id.asc()),
    "last_seen": (AlertLog.last_seen_at.desc(), AlertLog.id.desc()),
    "occurrences": (AlertLog.occurrences.desc(), AlertLog.id.desc()),
    "plant": (Plant.mnemonic.asc(), AlertLog.raised_at.desc()),
}


@router.get("/alerts")
async def alerts(open_only: bool = Query(False, alias="open"), plant: str | None = None, plants: str | None = None,
                 region: str | None = None, category: str | None = None, severity: str | None = None,
                 rule: str | None = None, q: str | None = None, acked: str | None = None, sort: str = "newest",
                 offset: int = 0, limit: int = Query(100, le=1000), user: AppUser = Depends(current_user),
                 s: AsyncSession = Depends(get_session)):
    """The alert log, one page at a time, filtered by plants, region, API, severity, rule, acknowledged
    (yes/no) and free text. rules lists each rule with its count under the same filters (rule aside),
    and by_plant the plants with the most matching alerts."""
    limit, offset = _page(limit, offset)
    ids = await scope_ids(user, s, plants or plant, region)
    cond = [AlertLog.plant_id.in_(ids)]
    if open_only:
        cond.append(AlertLog.cleared_at.is_(None))
    cats = _cats(category)
    if cats:
        cond.append(AlertLog.category.in_(cats))
    if severity:
        cond.append(AlertLog.severity == severity)
    if acked == "yes":
        cond.append(AlertLog.acknowledged_at.is_not(None))
    elif acked == "no":
        cond.append(AlertLog.acknowledged_at.is_(None))
    if q and q.strip():
        like = f"%{q.strip()}%"
        cond.append(AlertLog.message.ilike(like) | AlertLog.rule.ilike(like) | Plant.mnemonic.ilike(like))
    joined = lambda x: x.select_from(AlertLog).join(Plant, Plant.id == AlertLog.plant_id).where(*cond)  # noqa: E731
    rules = (await s.execute(joined(select(AlertLog.rule, func.count())).group_by(AlertLog.rule)
                             .order_by(func.count().desc()))).all()
    if rule:
        cond.append(AlertLog.rule.in_(rule.split(",")))
    total = (await s.execute(joined(select(func.count())))).scalar_one()
    by_plant = (await s.execute(joined(select(Plant.mnemonic, func.count())).group_by(Plant.mnemonic)
                                .order_by(func.count().desc(), Plant.mnemonic).limit(10))).all()
    rows = (await s.execute(joined(select(AlertLog, Plant.mnemonic)).order_by(*ALERT_SORTS.get(sort, ALERT_SORTS["newest"]))
                            .limit(limit).offset(offset))).all()
    return {"rows": [{"id": a.id, "plant": m, "category": a.category, "rule": a.rule, "severity": a.severity,
                      "message": a.message, "raised_at": a.raised_at, "last_seen_at": a.last_seen_at,
                      "occurrences": a.occurrences, "cleared_at": a.cleared_at, "acknowledged_by": a.acknowledged_by,
                      "acknowledged_at": a.acknowledged_at, "detail": a.detail} for a, m in rows],
            "total": total, "offset": offset, "limit": limit,
            "rules": [{"rule": r, "n": n} for r, n in rules], "by_plant": [{"plant": m, "n": n} for m, n in by_plant]}


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


class AckIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=1000)


@router.post("/alerts/ack")
async def ack_alerts(body: AckIn, user: AppUser = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Acknowledge several alerts at once (those already acknowledged are left as they were)."""
    if user.role not in ("gso_admin", "gso_user"):
        raise HTTPException(403, "Only GSO staff acknowledge alerts")
    res = await s.execute(update(AlertLog).where(AlertLog.id.in_(body.ids), AlertLog.acknowledged_at.is_(None))
                          .values(acknowledged_by=user.id, acknowledged_at=now_local()))
    await s.commit()
    return {"ok": True, "acknowledged": res.rowcount}


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
