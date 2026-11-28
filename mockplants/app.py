"""Offline stand-in for the pilot host (ws3.gedine.work), for local demos and tests.

It imitates what the 8 pilot plants served on 27 Nov 2026, quirks included: ForecastValue as a
string, rolling category 'ActivePowerProduction' with 97 points, KMLS/KALS MMF in a raw non-rev5
shape with sentinel values, LMMS NaN MMF values, 'C'/'o' units and upper-case PlantName.
MOCK_FAULTS (e.g. "sids:dayahead,bdws:rolling") makes a plant serve a short forecast to show alerts.
"""
import asyncio
import math
import os
import random
import zlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

import sim

app = FastAPI(title="Mock LSS plants")
TZ = ZoneInfo("Asia/Kuala_Lumpur")
PLANTS = {"kmls": 50, "mals": 30, "kals": 50, "lmms": 49, "bdws": 45, "sids": 50, "skns": 50, "bkhs": 30}
FAULTS = {tuple(x.split(":")) for x in os.getenv("MOCK_FAULTS", "").split(",") if ":" in x}
SLOT = timedelta(minutes=15)
# MOCK_REQUIRE_KEY="bkhs:secret" makes a plant answer 401 unless the key comes as an X-API-Key header
# or an apiKey query parameter, to try the platform's per-plant API key settings.
REQUIRED_KEYS = dict(x.split(":", 1) for x in os.getenv("MOCK_REQUIRE_KEY", "").split(",") if ":" in x)
# MOCK_FLEET_FAULTS=1: simulated (non-pilot) plants get a fixed, mnemonic-derived fault, so a 100+ plant
# demo fleet has a realistic mix of problems: ~5% down (503), ~5% short day-ahead, ~5% slow (3 s),
# ~5% broken MMF (500). The other ~80% are healthy.
FLEET_FAULTS = os.getenv("MOCK_FLEET_FAULTS", "") in ("1", "true", "yes")


def fleet_fault(mnem: str) -> str | None:
    if not FLEET_FAULTS or mnem in PLANTS or mnem in sim.CAPACITY:
        return None
    return {0: "down", 1: "short", 2: "slow", 3: "mmf"}.get(zlib.crc32(mnem.encode()) % 20)


async def apply_fleet_fault(mnem: str, cat: str):
    f = fleet_fault(mnem) or sim.rest_fault(mnem)
    if f == "down":
        raise HTTPException(503, "Service unavailable")
    if f == "slow":
        await asyncio.sleep(4 if mnem in sim.CAPACITY else 3)
    if f == "mmf" and cat == "mmf":
        raise HTTPException(500, "MMF logger offline")


def check_key(mnem: str, request: Request):
    want = REQUIRED_KEYS.get(mnem) or (sim.REST_KEY if sim.rest_fault(mnem) == "key" else None)
    if want and want not in (request.headers.get("x-api-key"), request.query_params.get("apiKey")):
        raise HTTPException(401, "Invalid or missing API key")


def now():
    return datetime.now(TZ).replace(tzinfo=None, microsecond=0)


def solar(t: datetime, cap: float) -> float:
    h = t.hour + t.minute / 60
    if not 7 <= h <= 19:
        return 0.0
    return round(cap * 0.85 * math.sin(math.pi * (h - 7) / 12) * (0.9 + 0.1 * math.cos(t.day)), 2)


def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    return d.replace(year=d.year + y, month=m + 1, day=1)


def series(mnem, cat, start, end):
    # any other mnemonic answers too, so new plants can be tried
    cap = PLANTS.get(mnem) or sim.CAPACITY.get(mnem, 40)
    neg = cat == "rolling" and sim.rest_fault(mnem) == "negative"
    pts, t = [], start
    while t < end:
        val = solar(t, cap)
        if neg and val == 0 and t.minute == 0:
            val = -0.35
        pts.append({"StartTime": t.isoformat(), "EndTime": (t + SLOT).isoformat(),
                    "ForecastValue": val if mnem in sim.CAPACITY else str(val), "Unit": "MW"})
        t += SLOT
    if (mnem, cat) in FAULTS or (cat == "dayahead" and (fleet_fault(mnem) or sim.rest_fault(mnem)) == "short"):
        pts = pts[:-10]
    return pts


CATEGORY = {"rolling": "ActivePowerProduction", "dayahead": "DayAhead", "weekahead": "WeekAhead",
            "fourmonthsahead": "FourMonthsAhead"}


@app.get("/{mnem}/forecast/{cat}")
async def forecast(mnem: str, cat: str, request: Request):
    if not mnem.isalnum() or cat not in CATEGORY:
        raise HTTPException(404)
    check_key(mnem, request)
    await apply_fleet_fault(mnem, cat)
    return forecast_body(mnem, cat)


def forecast_body(mnem: str, cat: str) -> dict:
    n = now()
    day = n.replace(hour=0, minute=0, second=0)
    if cat == "rolling":
        start = n.replace(minute=n.minute - n.minute % 15, second=0) + SLOT
        # 97 points, as the pilot host serves; simulated plants follow rev5 (96)
        end = start + timedelta(hours=24, minutes=0 if mnem in sim.CAPACITY else 15)
    elif cat == "dayahead":
        start = day + timedelta(days=1) if n.hour >= 10 else day
        end = start + timedelta(days=5)
    elif cat == "weekahead":
        back = (day.weekday() - 2) % 7
        wed = day - timedelta(days=back)
        if back == 0 and (n.hour, n.minute) < (12, 30):
            wed -= timedelta(days=7)
        start = wed + timedelta(days=3)
        end = start + timedelta(days=9)
    else:
        first = day.replace(day=1)
        start = add_months(first, 1) if day.day >= 25 else first
        end = add_months(start, 4)
    src = "kmls" if mnem == "kals" else "sids" if mnem == "skns" else mnem  # duplicated data, as observed
    name = mnem[:-1] + "x" if sim.rest_fault(mnem) == "name" else mnem
    category = "Rolling" if cat == "rolling" and mnem in sim.CAPACITY else CATEGORY[cat]
    return {"ForecastResponse": {"PlantName": name, "ForecastCategory": category,
                                 "ForecastData": series(src, cat, start, end)}}


PARAMS = [("HorIrradiance", "W/m2", 0, 1000), ("IncIrradiance", "W/m2", 0, 1100), ("TotRadiation", "Wh/m2", 0, 5000),
          ("AmbientTemp", "C", 24, 34), ("WindSpeed", "m/s", 0, 6), ("WindDir", "o", 0, 359),
          ("WindDirSD", "o", 0, 40), ("Humidity", "%", 55, 95), ("Precipitation", "mm/h", 0, 2),
          ("AirPressure", "hPa", 1005, 1012), ("AtmPressure", "atm", 0.99, 1.0), ("PVTemperature", "C", 25, 60),
          ("MWPerArea", "MW/m2", 0, 0.0002), ("HorIrradiance2", "W/m2", 0, 1000)]


@app.get("/{mnem}/mmf/live")
async def mmf(mnem: str, request: Request):
    if not mnem.isalnum():
        raise HTTPException(404)
    check_key(mnem, request)
    await apply_fleet_fault(mnem, "mmf")
    return mmf_body(mnem)


def mmf_body(mnem: str) -> dict:
    n = now().replace(second=0)
    n = n.replace(minute=n.minute - n.minute % 5)
    if mnem in ("kmls", "kals"):  # raw sensor dump, not rev5
        return {"data": [{"ts": n.timestamp(), "tgi": round(random.uniform(0, 900), 1), "poa": -3276.8,
                          "amb_temp": -40, "ws": "0.15820.0661"}]}
    stations = 3 if mnem == "mals" else 2
    clean = mnem in sim.CAPACITY  # simulated plants send rev5-clean MMF (numbers, proper units, 12 parameters)
    if clean:
        stations = sim.SOAP_PLANTS[mnem][3] if mnem in sim.SOAP_PLANTS else 2
    items = []
    for name, unit, lo, hi in PARAMS:
        if clean and name == "HorIrradiance2" or clean and name == "MWPerArea":
            continue
        if clean:
            unit = {"C": "°C", "o": "°"}.get(unit, unit)
        item = {"Parameter": name, "Time": n.isoformat(), "Unit": unit}
        for st in range(1, stations + 1):
            v = round(random.uniform(lo, hi), 3)
            item[f"MMF{st}"] = v if clean else "NaN" if mnem == "lmms" and name == "Humidity" else str(v)
        items.append(item)
    return {"MMFResponse": {"PlantName": mnem if clean else mnem.upper(), "MMFData": items}}


# ---------------------------------------------------------------- simulated plants (see sim.py)
@app.get("/sim/catalog")
async def sim_catalog():
    """The fictitious plants the platform adds as simulated plants."""
    return sim.catalog()


def _xml(body: str, ver: str, status: int = 200) -> Response:
    ct = "application/soap+xml; charset=utf-8" if ver == "1.2" else "text/xml; charset=utf-8"
    return Response(body, status_code=status, media_type=ct.split(";")[0], headers={"Content-Type": ct})


@app.get("/soap/{mnem}")
async def soap_wsdl(mnem: str, request: Request):
    if mnem not in sim.SOAP_PLANTS or "wsdl" not in {k.lower() for k in request.query_params}:
        raise HTTPException(404)
    base = str(request.base_url).rstrip("/")
    return Response(sim.wsdl(mnem, base), media_type="text/xml")


@app.post("/soap/{mnem}")
async def soap_call(mnem: str, request: Request):
    if mnem not in sim.SOAP_PLANTS:
        raise HTTPException(404)
    _, _, _, stations, ver, faults, _ = sim.SOAP_PLANTS[mnem]
    body = (await request.body()).decode("utf-8", "replace")
    # Operation: SOAPAction header (1.1) or the action parameter of the Content-Type (1.2); else the body element.
    action = request.headers.get("soapaction", "").strip('"')
    ct = request.headers.get("content-type", "")
    if not action and 'action="' in ct:
        action = ct.split('action="', 1)[1].split('"', 1)[0]
    op = action.rsplit("/", 1)[-1] if action else ""
    if op not in sim.OP_TO_CAT:
        op = next((o for o in sim.OP_TO_CAT if f":{o}>" in body or f"<{o}>" in body), "")
    if not op:
        return _xml(sim.fault(ver, "Client" if ver == "1.1" else "Sender", "Unknown operation or SOAPAction"), ver, 500)
    if ("Envelope" not in body) or (sim.NS_SOAP12 if ver == "1.2" else sim.NS_SOAP11) not in body:
        return _xml(sim.fault(ver, "VersionMismatch", f"This service speaks SOAP {ver}"), ver, 500)
    if "wsse" in faults and not sim.wsse_ok(body):
        return _xml(sim.fault(ver, "Client" if ver == "1.1" else "Sender", "WS-Security authentication failed",
                              "Missing or wrong UsernameToken"), ver, 500)
    if f"<{'gso:' if 'gso:PlantName' in body else ''}PlantName>{mnem}<" not in body:
        return _xml(sim.fault(ver, "Client" if ver == "1.1" else "Sender", "Unknown PlantName"), ver, 500)
    cat = sim.OP_TO_CAT[op]
    if f"fault:{cat}" in faults:
        return _xml(sim.fault(ver, "Server" if ver == "1.1" else "Receiver",
                              "Forecast for the requested period has not been generated yet",
                              "Forecast engine job DA-RUN is still pending"), ver, 500)
    if cat == "mmf":
        return _xml(sim.mmf_xml(mnem, ver, op, mmf_body(mnem), faults), ver)
    return _xml(sim.forecast_xml(mnem, ver, op, forecast_body(mnem, cat), faults, cat), ver)
