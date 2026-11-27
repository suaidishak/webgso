"""DB-driven scheduler and deadline checker (the worker process).

Every tick it reads poll_schedule, works out which plant/endpoint pairs are due and runs them.
Edits made in the dashboard take effect on the next tick, with no redeploy. A PostgreSQL
advisory lock keeps a single active scheduler, and the unique (schedule, plant, due_at) row in
schedule_run means a pull is never doubled.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta

from croniter import croniter
from sqlalchemy import and_, select, text
from sqlalchemy.dialects.postgresql import insert

from .config import get_settings, now_local
from .db import SessionLocal, engine
from .models import (
    AlertLog, ComplianceDeadline, DeadlineCheck, ForecastSnapshot, Plant, PlantEndpoint, PollAttempt, PollSchedule,
    ScheduleRun,
)
from .poller import mark_run, poll_endpoint
from .switches import load_switches, off_reason
from . import validation as v

log = logging.getLogger("gso.scheduler")
LOCK_KEY = 815_2026


# ---------------------------------------------------------------- schedule maths
def last_due(sched: PollSchedule, now: datetime) -> datetime | None:
    """Most recent due time at or before now, or None if the schedule is inactive now."""
    if sched.valid_from and now.date() < sched.valid_from:
        return None
    if sched.valid_to and now.date() > sched.valid_to:
        return None
    if sched.kind == "interval" and sched.interval_minutes:
        step = sched.interval_minutes
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        mins = int((now - midnight).total_seconds() // 60)
        due = midnight + timedelta(minutes=mins - mins % step)
        if sched.window_start and due.time() < sched.window_start:
            return None
        if sched.window_end and due.time() > sched.window_end:
            return None
        return due
    if sched.kind == "cron" and sched.cron:
        it = croniter(sched.cron, now + timedelta(seconds=1))
        return it.get_prev(datetime)
    return None


def next_runs(sched: PollSchedule, now: datetime, n: int = 5) -> list[datetime]:
    out: list[datetime] = []
    if sched.kind == "cron" and sched.cron:
        it = croniter(sched.cron, now)
        while len(out) < n:
            t = it.get_next(datetime)
            if sched.valid_to and t.date() > sched.valid_to:
                break
            out.append(t)
        return out
    if sched.kind == "interval" and sched.interval_minutes:
        t = (last_due(sched, now) or now.replace(second=0, microsecond=0))
        guard = 0
        while len(out) < n and guard < 2000:
            guard += 1
            t += timedelta(minutes=sched.interval_minutes)
            if sched.window_start and t.time() < sched.window_start:
                continue
            if sched.window_end and t.time() > sched.window_end:
                continue
            out.append(t)
    return out


def effective_schedules(schedules: list[PollSchedule], plant_ids: list[int]) -> dict[tuple[int, str], PollSchedule]:
    """Plant override wins over the default (plant_id NULL)."""
    defaults = {s.category: s for s in schedules if s.plant_id is None}
    overrides = {(s.plant_id, s.category): s for s in schedules if s.plant_id is not None}
    out = {}
    for pid in plant_ids:
        for cat in set(defaults) | {c for (p, c) in overrides if p == pid}:
            sch = overrides.get((pid, cat), defaults.get(cat))
            if sch and sch.enabled:
                out[(pid, cat)] = sch
    return out


# ---------------------------------------------------------------- deadlines
def last_deadline(d: ComplianceDeadline, now: datetime) -> tuple[datetime, datetime] | None:
    """(due_at, target horizon start) of the most recent deadline at or before now."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if d.period == "daily":
        due = datetime.combine(day.date(), d.due_time)
        if due > now:
            due -= timedelta(days=1)
        return due, due.replace(hour=0, minute=0) + timedelta(days=1)
    if d.period == "weekly":
        back = (day.weekday() - (d.weekday or 0)) % 7
        due = datetime.combine((day - timedelta(days=back)).date(), d.due_time)
        if due > now:
            due -= timedelta(days=7)
        # Week-ahead published Wednesday covers from the following Saturday.
        return due, due.replace(hour=0, minute=0) + timedelta(days=(5 - due.weekday()) % 7)
    if d.period == "monthly":
        dom = d.day_of_month or 25
        due = datetime.combine(day.replace(day=dom).date(), d.due_time)
        if due > now:
            due = datetime.combine(v.add_months(day.replace(day=1), -1).replace(day=dom).date(), d.due_time)
        return due, v.add_months(due.replace(day=1, hour=0, minute=0), 1)
    return None


async def check_deadlines(now: datetime):
    async with SessionLocal() as s:
        deadlines = (await s.execute(select(ComplianceDeadline).where(ComplianceDeadline.enabled))).scalars().all()
        sw = await load_switches(s)
        plants = (await s.execute(select(Plant).where(Plant.status == "live"))).scalars().all()
        defaults = {d.category: d for d in deadlines if d.plant_id is None}
        overrides = {(d.plant_id, d.category): d for d in deadlines if d.plant_id is not None}
        for p in plants:
            enabled_cats = {e.category for e in p.endpoints if off_reason(sw, p, e) is None}
            for cat in set(defaults) | {c for (pid, c) in overrides if pid == p.id}:
                if cat not in enabled_cats:
                    continue
                d = overrides.get((p.id, cat), defaults.get(cat))
                if not d:
                    continue
                due, target = last_deadline(d, now)
                evaluate_at = due + timedelta(minutes=d.grace_minutes)
                first_valid = (await s.execute(select(ForecastSnapshot.retrieved_at).where(
                    ForecastSnapshot.plant_id == p.id, ForecastSnapshot.category == cat,
                    ForecastSnapshot.horizon_start == target, ForecastSnapshot.is_valid,
                ).order_by(ForecastSnapshot.retrieved_at).limit(1))).scalar_one_or_none()
                existing = (await s.execute(select(DeadlineCheck).where(
                    DeadlineCheck.plant_id == p.id, DeadlineCheck.category == cat,
                    DeadlineCheck.due_at == due))).scalar_one_or_none()
                if existing is None:
                    if now < evaluate_at:
                        continue
                    # Only judge deadlines the platform was already polling for (not ones before go-live).
                    polled_before = (await s.execute(select(PollAttempt.id).where(
                        PollAttempt.plant_id == p.id, PollAttempt.category == cat,
                        PollAttempt.started_at <= due).limit(1))).scalar_one_or_none()
                    if polled_before is None and first_valid is None:
                        continue
                    status = "met" if first_valid and first_valid <= evaluate_at else (
                        "late" if first_valid else "missed")
                    s.add(DeadlineCheck(plant_id=p.id, category=cat, due_at=due, target_start=target,
                                        first_valid_at=first_valid, status=status, checked_at=now))
                    if status == "missed":
                        s.add(AlertLog(plant_id=p.id, category=cat, rule="DEADLINE_MISSED", severity="major",
                                       message=f"No valid {cat} forecast for {target:%Y-%m-%d} by "
                                               f"{due:%Y-%m-%d %H:%M}", raised_at=now, last_seen_at=now,
                                       detail={"due_at": due.isoformat(), "target": target.isoformat()}))
                elif existing.status == "missed" and first_valid:
                    existing.status, existing.first_valid_at, existing.checked_at = "late", first_valid, now
                    alert = (await s.execute(select(AlertLog).where(
                        AlertLog.plant_id == p.id, AlertLog.category == cat, AlertLog.rule == "DEADLINE_MISSED",
                        AlertLog.cleared_at.is_(None)))).scalars().all()
                    for a in alert:
                        a.cleared_at = now
                        a.message += f" (arrived late at {first_valid:%Y-%m-%d %H:%M})"
        await s.commit()


# ---------------------------------------------------------------- the loop
async def tick(tasks: set[asyncio.Task]):
    now = now_local()
    catchup = timedelta(minutes=get_settings().scheduler_catchup_minutes)
    async with SessionLocal() as s:
        schedules = (await s.execute(select(PollSchedule))).scalars().all()
        sw = await load_switches(s)
        if not sw.fleet:
            return  # fleet polling paused: nothing is queued and no deadlines are judged
        rows = (await s.execute(select(PlantEndpoint.plant_id, PlantEndpoint.category).join(Plant).where(
            and_(PlantEndpoint.enabled, Plant.status == "live", Plant.polling_enabled)))).all()
        enabled = {(r.plant_id, r.category) for r in rows if sw.types.get(r.category, True)}
        plant_ids = sorted({p for p, _ in enabled})
        jobs = []
        for (pid, cat), sch in effective_schedules(schedules, plant_ids).items():
            if (pid, cat) not in enabled:
                continue
            due = last_due(sch, now)
            if due is None or now - due > catchup:
                continue
            res = await s.execute(insert(ScheduleRun).values(
                schedule_id=sch.id, plant_id=pid, category=cat, due_at=due, enqueued_at=now, status="queued",
            ).on_conflict_do_nothing(constraint="uq_run").returning(ScheduleRun.id))
            run_id = res.scalar_one_or_none()
            if run_id:
                jobs.append((run_id, pid, cat, due, sch.timeout_s, sch.retries, sch.retry_backoff_s, sch.jitter_s))
        await s.commit()

    for run_id, pid, cat, due, timeout_s, retries, backoff, jitter in jobs:
        t = asyncio.create_task(_run(run_id, pid, cat, due, timeout_s, retries, backoff, jitter))
        tasks.add(t)
        t.add_done_callback(tasks.discard)
    if jobs:
        log.info("tick %s: queued %d polls", now, len(jobs))
    await check_deadlines(now)


async def _run(run_id, pid, cat, due, timeout_s, retries, backoff, jitter):
    if jitter:
        await asyncio.sleep(random.uniform(0, jitter))
    try:
        attempt_id = await poll_endpoint(pid, cat, scheduled_for=due, timeout_s=timeout_s, retries=retries,
                                         backoff_s=backoff)
    except Exception:  # noqa: BLE001 - one bad plant must never stop the scheduler
        log.exception("poll failed %s/%s", pid, cat)
        attempt_id = None
    await mark_run(run_id, attempt_id)


_sweeping: set[tuple[int, str]] = set()


async def initial_sweep(tasks: set[asyncio.Task]):
    """Poll every enabled endpoint of a live plant that has never been polled. Runs at start-up and on
    every tick, so a fresh install or a newly added plant fills the board without waiting for the
    weekly or monthly windows."""
    async with SessionLocal() as s:
        rows = (await s.execute(text("""
            SELECT e.plant_id, e.category FROM plant_endpoint e JOIN plant p ON p.id = e.plant_id
            WHERE e.enabled AND p.status = 'live' AND p.polling_enabled AND NOT EXISTS (
                SELECT 1 FROM poll_attempt a WHERE a.plant_id = e.plant_id AND a.category = e.category)
        """))).all()
        sw = await load_switches(s)
    rows = [(pid, cat) for pid, cat in rows
            if (pid, cat) not in _sweeping and sw.fleet and sw.types.get(cat, True)]
    if rows:
        log.info("first poll for %d never-polled endpoints", len(rows))
    for pid, cat in rows:
        _sweeping.add((pid, cat))
        t = asyncio.create_task(_first_poll(pid, cat))
        tasks.add(t)
        t.add_done_callback(tasks.discard)


async def _first_poll(pid: int, cat: str):
    try:
        await poll_endpoint(pid, cat, timeout_s=180, retries=1, backoff_s=30)
    except Exception:  # noqa: BLE001
        log.exception("first poll failed %s/%s", pid, cat)
    finally:
        _sweeping.discard((pid, cat))


async def run_forever():
    tick_s = get_settings().scheduler_tick_seconds
    tasks: set[asyncio.Task] = set()
    async with engine.connect() as lock_conn:
        while True:
            got = (await lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY})).scalar()
            if got:
                break
            log.info("another scheduler holds the lock; waiting")
            await asyncio.sleep(tick_s)
        log.info("scheduler started (tick %ss)", tick_s)
        while True:
            try:
                await tick(tasks)
                await initial_sweep(tasks)
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")
            await asyncio.sleep(tick_s)
