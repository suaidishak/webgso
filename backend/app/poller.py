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

from . import soap
from . import validation as v
from .plant_keys import auth_parts, decrypt, scrub
from .config import get_settings, now_local
from .db import SessionLocal
from .models import (
    AlertLog, ComplianceFlag, ForecastPoint, ForecastSnapshot, MmfReading, Plant, PlantEndpoint,
    PollAttempt, RawResponse,
)

log = logging.getLogger("gso.poller")

_limits: dict = {}  # per event loop: the global and per-host semaphores and the shared HTTP client


def _state() -> dict:
    """Concurrency limits and one pooled HTTP client, created lazily for the running event loop.

    At fleet scale (100+ plants, 500+ endpoints) a tick can queue hundreds of polls at once. The global
    semaphore caps how many plant calls are in flight (poll_concurrency), the per-host one protects hosts
    that serve several plants (poll_per_host), and the shared client reuses connections and TLS sessions
    instead of opening a new client for every call."""
    loop = asyncio.get_running_loop()
    st = _limits.get("state")
    if st is None or st["loop"] is not loop:
        cfg = get_settings()
        st = {"loop": loop, "all": asyncio.Semaphore(cfg.poll_concurrency),
              # Polls may hold at most half the connection pool, so API requests are never starved.
              "db": asyncio.Semaphore(max(2, cfg.db_pool_size // 2)),
              "hosts": defaultdict(lambda: asyncio.Semaphore(cfg.poll_per_host)),
              "client": httpx.AsyncClient(
                  verify=cfg.verify_tls, follow_redirects=False,
                  limits=httpx.Limits(max_connections=cfg.poll_concurrency * 2,
                                      max_keepalive_connections=cfg.poll_concurrency),
                  headers={"Accept": "application/json", "Accept-Encoding": "gzip",
                           "User-Agent": "GSO-Solar-Platform/0.1"})}
        _limits["state"] = st
    return st


def in_flight() -> int:
    """Plant calls running right now (for logs and the health check)."""
    st = _limits.get("state")
    return 0 if st is None else get_settings().poll_concurrency - st["all"]._value  # noqa: SLF001


# Rules raised as alerts. Minor flags are recorded but never alert.
POLL_ALERT_RULES_PREFIX = ("HTTP_", "FETCH_")


async def fetch(url: str, timeout_s: int, retries: int, backoff_s: int,
                auth_headers: dict | None = None, auth_params: dict | None = None,
                soap_body: bytes | None = None, soap_headers: dict | None = None) -> dict:
    """GET (REST) or POST of a SOAP envelope, with retries. Returns a dict describing the final try.
    Auth values never reach the result. A SOAP Fault (HTTP 500 with a Fault body) is an answer, not
    an outage, so it is not retried."""
    secret = next(iter({**(auth_headers or {}), **(auth_params or {})}.values()), None)
    host = urlparse(url).netloc
    last: dict = {}
    for attempt in range(1, retries + 2):
        t0 = _time.monotonic()
        st = _state()
        try:
            async with st["all"], st["hosts"][host]:
                t0 = _time.monotonic()  # time the call itself, not the wait for a free slot
                if soap_body is not None:
                    resp = await st["client"].post(url, params=auth_params or None, content=soap_body,
                                                   headers={**(soap_headers or {}), **(auth_headers or {})},
                                                   timeout=timeout_s)
                else:
                    resp = await st["client"].get(url, params=auth_params or None, headers=auth_headers or None,
                                                  timeout=timeout_s)
            last = {"status": resp.status_code, "content": resp.content,
                    "latency_ms": int((_time.monotonic() - t0) * 1000), "error": None, "tries": attempt}
            if resp.status_code < 500 or (soap_body is not None and soap.looks_like_fault(resp.content)):
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
    db = _state()["db"]
    async with db, SessionLocal() as s:
        plant = await s.get(Plant, plant_id)
        ep = (await s.execute(select(PlantEndpoint).where(
            PlantEndpoint.plant_id == plant_id, PlantEndpoint.category == category))).scalar_one_or_none()
        if plant is None or ep is None:
            return None
        url, mnemonic, capacity, stations = ep.url, plant.mnemonic, plant.capacity_mw, plant.mmf_station_count
        key = decrypt(plant.api_key_enc)
        headers, params = auth_parts(plant.auth_method, plant.auth_param_name, key)
        sp = soap.profile(ep)
        body = sh = None
        if sp:
            wsse = (plant.auth_param_name or "", key or "") if plant.auth_method == "wsse" and key else None
            body = soap.envelope(sp["operation"], sp["namespace"], sp["version"], mnemonic, wsse)
            sh = soap.request_headers(sp["version"], sp["action"])

    started = now_local()
    res = await fetch(url, timeout_s, retries, backoff_s, headers, params, body, sh)

    async with db, SessionLocal() as s:
        attempt = PollAttempt(plant_id=plant_id, category=category, url=url, scheduled_for=scheduled_for,
                              started_at=started, http_status=res["status"], latency_ms=res["latency_ms"],
                              bytes=len(res["content"]) if res["content"] is not None else None,
                              tries=res["tries"], error=res["error"])
        s.add(attempt)
        await s.flush()

        flags: list[v.Flag] = []
        # A SOAP Fault comes as HTTP 500 with a Fault body: land it and report the Fault itself.
        fault = sp is not None and res["status"] == 500 and soap.looks_like_fault(res["content"])
        if res["status"] is None:
            flags.append(v.Flag("HTTP_DOWN", v.MAJOR, f"No response: {res['error']}"))
        elif res["status"] != 200 and not fault:
            hint = " (check the plant's API key)" if res["status"] in (401, 403) else ""
            flags.append(v.Flag("HTTP_STATUS", v.MAJOR, f"HTTP {res['status']}{hint}"))

        if res["content"] is not None and (res["status"] == 200 or fault):
            flags += await _ingest(s, attempt, plant_id, category, mnemonic, capacity, stations,
                                   res["content"], started, sp)

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
                  capacity: float | None, stations: int | None, content: bytes, now: datetime,
                  sp: dict | None = None) -> list[v.Flag]:
    digest = hashlib.sha256(content).hexdigest()
    # body_text keeps the response exactly as sent (JSONB re-orders keys); body is the queryable copy.
    # For SOAP, body_text is the XML and body the payload converted to the rev5 JSON shape.
    body_text = content.decode("utf-8", "replace")
    pre: list[v.Flag] = []
    if sp:
        conv = soap.convert(content, sp["operation"], sp["namespace"], sp["version"])
        body, pre = conv.body, conv.flags
    else:
        try:
            body = json.loads(content)
        except (ValueError, UnicodeDecodeError):
            body = None

    # Landing table, de-duplicated: an unchanged response re-uses the previous raw row.
    prev = (await s.execute(select(RawResponse.id).where(
        RawResponse.plant_id == plant_id, RawResponse.category == category,
        RawResponse.content_hash == digest).order_by(RawResponse.id.desc()).limit(1))).scalar_one_or_none()
    if prev is None:
        raw = RawResponse(poll_attempt_id=attempt.id, plant_id=plant_id, category=category, received_at=now,
                          content_hash=digest, body=body, body_text=body_text,
                          body_format="xml" if sp else "json")
        s.add(raw)
        await s.flush()
        raw_id, changed = raw.id, True
    else:
        raw_id, changed = prev, False
    attempt.raw_response_id = raw_id

    if sp and (body is None or any(f.severity == v.MAJOR for f in pre)):
        return pre  # unreadable XML, not a SOAP envelope, or a SOAP Fault: nothing to validate
    if body is None:
        return [v.Flag("JSON_INVALID", v.MAJOR, "Response body is not valid JSON")]

    if category == "mmf":
        r = v.validate_mmf(body, mnemonic, now, stations)
        if r.values:
            s.add_all(MmfReading(plant_id=plant_id, poll_attempt_id=attempt.id, station_no=x.station_no,
                                 parameter=x.parameter, measured_at=x.measured_at, value=x.value, unit=x.unit,
                                 retrieved_at=now, is_standard=x.is_standard) for x in r.values)
        return pre + r.flags

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
        return pre + r.flags
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
    return pre + r.flags


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


async def mark_run(run_id: int, attempt_id: int | None, status: str | None = None):
    async with SessionLocal() as s:
        from .models import ScheduleRun
        await s.execute(update(ScheduleRun).where(ScheduleRun.id == run_id).values(
            status=status or ("done" if attempt_id else "failed"), poll_attempt_id=attempt_id))
        await s.commit()
