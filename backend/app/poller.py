"""Calls one plant endpoint, lands the raw response, validates it and stores typed data.

GET only. Every call writes a poll_attempt row, whether it succeeded or not.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time as _time
from collections import defaultdict
from datetime import datetime
from urllib.parse import urlparse

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import validation as v
from .plant_keys import auth_parts, decrypt, scrub
from .config import get_settings, now_local
from .db import SessionLocal
from .models import (
    AlertLog, ComplianceFlag, ForecastPoint, ForecastSnapshot, MmfReading, Plant, PlantEndpoint,
    PollAttempt, RawResponse,
)

log = logging.getLogger("gso.poller")

MAX_PER_HOST = 4  # several plants share one host (ws3.gedine.work serves all 8 pilot plants)
_host_locks: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(MAX_PER_HOST))

# Rules raised as alerts. Minor flags are recorded but never alert.
POLL_ALERT_RULES_PREFIX = ("HTTP_", "FETCH_")


async def fetch(url: str, timeout_s: int, retries: int, backoff_s: int,
                auth_headers: dict | None = None, auth_params: dict | None = None) -> dict:
    """GET with retries. Returns a dict describing the final try. Auth values never reach the result."""
    secret = next(iter({**(auth_headers or {}), **(auth_params or {})}.values()), None)
    host = urlparse(url).netloc
    last: dict = {}
    for attempt in range(1, retries + 2):
        t0 = _time.monotonic()
        try:
            async with _host_locks[host]:
                async with httpx.AsyncClient(timeout=timeout_s, verify=get_settings().verify_tls,
                                             headers={"Accept": "application/json",
                                                      "Accept-Encoding": "gzip",
                                                      "User-Agent": "GSO-Solar-Platform/0.1",
                                                      **(auth_headers or {})}) as client:
                    resp = await client.get(url, params=auth_params or None)
            last = {"status": resp.status_code, "content": resp.content,
                    "latency_ms": int((_time.monotonic() - t0) * 1000), "error": None, "tries": attempt}
            if resp.status_code < 500:
                return last
            last["error"] = f"HTTP {resp.status_code}"
        except httpx.HTTPError as e:
            last = {"status": None, "content": None, "latency_ms": int((_time.monotonic() - t0) * 1000),
                    "error": scrub(f"{type(e).__name__}: {e}", secret)[:500], "tries": attempt}
        if attempt <= retries:
            await asyncio.sleep(backoff_s * attempt)
    return last


async def poll_endpoint(plant_id: int, category: str, *, scheduled_for: datetime | None = None,
                        timeout_s: int = 60, retries: int = 2, backoff_s: int = 20) -> int | None:
    """Poll one endpoint end to end. Returns the poll_attempt id."""
    async with SessionLocal() as s:
        plant = await s.get(Plant, plant_id)
        ep = (await s.execute(select(PlantEndpoint).where(
            PlantEndpoint.plant_id == plant_id, PlantEndpoint.category == category))).scalar_one_or_none()
        if plant is None or ep is None:
            return None
        url, mnemonic, capacity, stations = ep.url, plant.mnemonic, plant.capacity_mw, plant.mmf_station_count
        headers, params = auth_parts(plant.auth_method, plant.auth_param_name, decrypt(plant.api_key_enc))

    started = now_local()
    res = await fetch(url, timeout_s, retries, backoff_s, headers, params)

    async with SessionLocal() as s:
        attempt = PollAttempt(plant_id=plant_id, category=category, url=url, scheduled_for=scheduled_for,
                              started_at=started, http_status=res["status"], latency_ms=res["latency_ms"],
                              bytes=len(res["content"]) if res["content"] is not None else None,
                              tries=res["tries"], error=res["error"])
        s.add(attempt)
        await s.flush()

        flags: list[v.Flag] = []
        if res["status"] is None:
            flags.append(v.Flag("HTTP_DOWN", v.MAJOR, f"No response: {res['error']}"))
        elif res["status"] != 200:
            hint = " (check the plant's API key)" if res["status"] in (401, 403) else ""
            flags.append(v.Flag("HTTP_STATUS", v.MAJOR, f"HTTP {res['status']}{hint}"))

        if res["content"] is not None and res["status"] == 200:
            flags += await _ingest(s, attempt, plant_id, category, mnemonic, capacity, stations,
                                   res["content"], started)

        attempt.major_flags = sum(f.severity == v.MAJOR for f in flags)
        attempt.minor_flags = sum(f.severity == v.MINOR for f in flags)
        attempt.is_valid = attempt.major_flags == 0 and res["status"] == 200
        for f in flags:
            s.add(ComplianceFlag(poll_attempt_id=attempt.id, plant_id=plant_id, category=category, ts=started,
                                 rule=f.rule, severity=f.severity, message=f.message, detail=f.detail))
        await sync_alerts(s, plant_id, category, flags, started)
        await s.commit()
        log.info("polled %s/%s status=%s valid=%s major=%d minor=%d", mnemonic, category, res["status"],
                 attempt.is_valid, attempt.major_flags, attempt.minor_flags)
        return attempt.id


async def _ingest(s: AsyncSession, attempt: PollAttempt, plant_id: int, category: str, mnemonic: str,
                  capacity: float | None, stations: int | None, content: bytes, now: datetime) -> list[v.Flag]:
    digest = hashlib.sha256(content).hexdigest()
    try:
        body = json.loads(content)
        body_text = None
    except (ValueError, UnicodeDecodeError):
        body, body_text = None, content.decode("utf-8", "replace")[:200_000]

    # Landing table, de-duplicated: an unchanged response re-uses the previous raw row.
    prev = (await s.execute(select(RawResponse.id).where(
        RawResponse.plant_id == plant_id, RawResponse.category == category,
        RawResponse.content_hash == digest).order_by(RawResponse.id.desc()).limit(1))).scalar_one_or_none()
    if prev is None:
        raw = RawResponse(poll_attempt_id=attempt.id, plant_id=plant_id, category=category, received_at=now,
                          content_hash=digest, body=body, body_text=body_text)
        s.add(raw)
        await s.flush()
        raw_id, changed = raw.id, True
    else:
        raw_id, changed = prev, False
    attempt.raw_response_id = raw_id

    if body is None:
        return [v.Flag("JSON_INVALID", v.MAJOR, "Response body is not valid JSON")]

    if category == "mmf":
        r = v.validate_mmf(body, mnemonic, now, stations)
        if r.values:
            s.add_all(MmfReading(plant_id=plant_id, poll_attempt_id=attempt.id, station_no=x.station_no,
                                 parameter=x.parameter, measured_at=x.measured_at, value=x.value, unit=x.unit,
                                 retrieved_at=now, is_standard=x.is_standard) for x in r.values)
        return r.flags

    r = v.validate_forecast(body, category, mnemonic, now, capacity)
    if not changed:
        # Same content as before: no new snapshot, but stale rolling data is itself a finding.
        if category == "rolling":
            last = (await s.execute(select(RawResponse.received_at).where(RawResponse.id == raw_id))
                    ).scalar_one()
            age = (now - last).total_seconds() / 60
            if age > 35:
                r.flags.append(v.Flag("STALE_ROLLING", v.MAJOR,
                                      f"Rolling forecast unchanged for {int(age)} min (must update every 30)"))
        return r.flags
    snap = ForecastSnapshot(plant_id=plant_id, category=category, raw_response_id=raw_id,
                            poll_attempt_id=attempt.id, retrieved_at=now, horizon_start=r.horizon_start,
                            horizon_end=r.horizon_end, expected_start=r.expected_start,
                            point_count=r.served_count, expected_count=r.expected_count, is_valid=r.is_valid)
    s.add(snap)
    await s.flush()
    if r.points:
        await s.execute(ForecastPoint.__table__.insert(), [
            {"snapshot_id": snap.id, "start_time": st, "end_time": et, "value_mw": val}
            for st, et, val in _dedupe(r.points)])
    return r.flags


def _dedupe(points):
    seen = set()
    for p in points:
        if p[0] not in seen:
            seen.add(p[0])
            yield p


async def sync_alerts(s: AsyncSession, plant_id: int, category: str, flags: list[v.Flag], now: datetime):
    """Open an alert for each major rule, bump it while it persists, clear it once a poll is clean of it."""
    major = {f.rule: f for f in flags if f.severity == v.MAJOR}
    open_alerts = (await s.execute(select(AlertLog).where(
        AlertLog.plant_id == plant_id, AlertLog.category == category, AlertLog.cleared_at.is_(None),
        ~AlertLog.rule.like("DEADLINE_%")))).scalars().all()
    by_rule = {a.rule: a for a in open_alerts}
    for rule, f in major.items():
        if rule in by_rule:
            a = by_rule[rule]
            a.last_seen_at, a.occurrences, a.message = now, a.occurrences + 1, f.message
        else:
            s.add(AlertLog(plant_id=plant_id, category=category, rule=rule, severity=f.severity,
                           message=f.message, raised_at=now, last_seen_at=now, detail=f.detail))
    for rule, a in by_rule.items():
        if rule not in major:
            a.cleared_at = now


async def mark_run(run_id: int, attempt_id: int | None):
    async with SessionLocal() as s:
        from .models import ScheduleRun
        await s.execute(update(ScheduleRun).where(ScheduleRun.id == run_id).values(
            status="done" if attempt_id else "failed", poll_attempt_id=attempt_id))
        await s.commit()
