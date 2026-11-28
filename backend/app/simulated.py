"""Simulated (fictitious) plants served by the mockplants simulator.

The simulator publishes its plant list at {SIM_BASE_URL}/sim/catalog: 5 SOAP plants and 20 REST
plants with made-up names, some with deliberate faults. The platform adds them as ordinary plants
marked is_simulated, so they exercise every feature (checks, alerts, reports, emails) while staying
apart from real plants: SIM badge, hidden from the fleet board by default, left out of report
emails unless a recipient list opts in, and removable in one go.

On API start, when SIM_BASE_URL is set (docker compose sets it to the mockplants service), missing
catalog plants are added in the background. If mockplants is not running (no `--profile mock`), the
catalog cannot be reached and nothing is added. After an admin removes the simulated plants they are
not re-added on restart; the Plants page's "Add simulated plants" brings them back.
"""
from __future__ import annotations

import asyncio
import logging

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings, now_local
from .db import SessionLocal
from .models import AuditEvent, Plant, PlantEndpoint
from .plant_keys import encrypt, hint

log = logging.getLogger("gso.simulated")
REMOVED_ACTION = "plant.simulated.remove"


async def fetch_catalog(base: str, timeout: float = 10) -> list[dict]:
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.get(f"{base.rstrip('/')}/sim/catalog")
        r.raise_for_status()
        return r.json()["plants"]


async def add_from_catalog(s: AsyncSession, base: str, catalog: list[dict]) -> dict:
    """Add catalog plants that do not exist yet. Existing plants (same mnemonic) are left untouched."""
    base = base.rstrip("/")
    have = set((await s.execute(select(Plant.mnemonic))).scalars().all())
    added, skipped = [], []
    for c in catalog:
        m = c["mnemonic"].lower()
        if m in have:
            skipped.append(m)
            continue
        auth = c.get("auth") or {}
        p = Plant(mnemonic=m, name=c["name"], region=c.get("region"), capacity_mw=c.get("capacity_mw"),
                  mmf_station_count=c.get("mmf_station_count"), status="live", notes=c.get("notes"),
                  polling_enabled=True, is_simulated=True, created_at=now_local(),
                  auth_method=auth.get("method") or "none", auth_param_name=auth.get("name"))
        if auth.get("secret"):
            p.api_key_enc, p.api_key_hint = encrypt(auth["secret"]), hint(auth["secret"])
        for e in c["endpoints"]:
            p.endpoints.append(PlantEndpoint(
                category=e["category"], url=f"{base}{e['path']}", enabled=True,
                protocol=e.get("protocol", "rest"), soap_operation=e.get("soap_operation"),
                soap_action=e.get("soap_action"), soap_version=e.get("soap_version"),
                soap_namespace=e.get("soap_namespace")))
        s.add(p)
        added.append(m)
    return {"added": added, "skipped": skipped}


async def remove_all(s: AsyncSession) -> list[str]:
    """Delete every simulated plant and all its stored data."""
    plants = (await s.execute(select(Plant).where(Plant.is_simulated))).scalars().all()
    for p in plants:
        await s.execute(text("UPDATE poll_schedule SET plant_ids = array_remove(plant_ids, :id) WHERE :id = ANY(plant_ids)"),
                        {"id": p.id})
        await s.delete(p)
    return sorted(p.mnemonic for p in plants)


async def autoload():
    """Background task on API start: add the simulator's plants once it answers (up to ~3 minutes)."""
    base = get_settings().sim_base_url
    if not base:
        return
    async with SessionLocal() as s:
        if (await s.execute(select(AuditEvent.id).where(AuditEvent.action == REMOVED_ACTION).limit(1))).first():
            log.info("Simulated plants were removed by an admin; not adding them again")
            return
    for i in range(36):
        try:
            catalog = await fetch_catalog(base, timeout=5)
            break
        except (httpx.HTTPError, KeyError, ValueError):
            await asyncio.sleep(5)
    else:
        log.info("Simulator not reachable at %s; no simulated plants added (start compose with --profile mock)", base)
        return
    async with SessionLocal() as s:
        await s.execute(text("SELECT pg_advisory_xact_lock(4243)"))
        res = await add_from_catalog(s, base, catalog)
        await s.commit()
    if res["added"]:
        log.info("Added %d simulated plants from %s", len(res["added"]), base)
