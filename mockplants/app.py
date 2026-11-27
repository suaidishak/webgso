"""Offline stand-in for the pilot host (ws3.gedine.work), for local demos and tests.

It imitates what the 8 pilot plants served on 27 Nov 2026, quirks included: ForecastValue as a
string, rolling category 'ActivePowerProduction' with 97 points, KMLS/KALS MMF in a raw non-rev5
shape with sentinel values, LMMS NaN MMF values, 'C'/'o' units and upper-case PlantName.
MOCK_FAULTS (e.g. "sids:dayahead,bdws:rolling") makes a plant serve a short forecast to show alerts.
"""
import math
import os
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException

app = FastAPI(title="Mock LSS plants")
TZ = ZoneInfo("Asia/Kuala_Lumpur")
PLANTS = {"kmls": 50, "mals": 30, "kals": 50, "lmms": 49, "bdws": 45, "sids": 50, "skns": 50, "bkhs": 30}
FAULTS = {tuple(x.split(":")) for x in os.getenv("MOCK_FAULTS", "").split(",") if ":" in x}
SLOT = timedelta(minutes=15)


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
    cap = PLANTS[mnem]
    pts, t = [], start
    while t < end:
        pts.append({"StartTime": t.isoformat(), "EndTime": (t + SLOT).isoformat(),
                    "ForecastValue": str(solar(t, cap)), "Unit": "MW"})
        t += SLOT
    if (mnem, cat) in FAULTS:
        pts = pts[:-10]
    return pts


CATEGORY = {"rolling": "ActivePowerProduction", "dayahead": "DayAhead", "weekahead": "WeekAhead",
            "fourmonthsahead": "FourMonthsAhead"}


@app.get("/{mnem}/forecast/{cat}")
def forecast(mnem: str, cat: str):
    if mnem not in PLANTS or cat not in CATEGORY:
        raise HTTPException(404)
    n = now()
    day = n.replace(hour=0, minute=0, second=0)
    if cat == "rolling":
        start = n.replace(minute=n.minute - n.minute % 15, second=0) + SLOT
        end = start + timedelta(hours=24, minutes=15)  # 97 points, as the pilot host serves
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
    return {"ForecastResponse": {"PlantName": mnem, "ForecastCategory": CATEGORY[cat],
                                 "ForecastData": series(src, cat, start, end)}}


PARAMS = [("HorIrradiance", "W/m2", 0, 1000), ("IncIrradiance", "W/m2", 0, 1100), ("TotRadiation", "Wh/m2", 0, 5000),
          ("AmbientTemp", "C", 24, 34), ("WindSpeed", "m/s", 0, 6), ("WindDir", "o", 0, 359),
          ("WindDirSD", "o", 0, 40), ("Humidity", "%", 55, 95), ("Precipitation", "mm/h", 0, 2),
          ("AirPressure", "hPa", 1005, 1012), ("AtmPressure", "atm", 0.99, 1.0), ("PVTemperature", "C", 25, 60),
          ("MWPerArea", "MW/m2", 0, 0.0002), ("HorIrradiance2", "W/m2", 0, 1000)]


@app.get("/{mnem}/mmf/live")
def mmf(mnem: str):
    if mnem not in PLANTS:
        raise HTTPException(404)
    n = now().replace(second=0)
    n = n.replace(minute=n.minute - n.minute % 5)
    if mnem in ("kmls", "kals"):  # raw sensor dump, not rev5
        return {"data": [{"ts": n.timestamp(), "tgi": round(random.uniform(0, 900), 1), "poa": -3276.8,
                          "amb_temp": -40, "ws": "0.15820.0661"}]}
    stations = 3 if mnem == "mals" else 2
    items = []
    for name, unit, lo, hi in PARAMS:
        item = {"Parameter": name, "Time": n.isoformat(), "Unit": unit}
        for st in range(1, stations + 1):
            item[f"MMF{st}"] = "NaN" if mnem == "lmms" and name == "Humidity" else str(round(random.uniform(lo, hi), 3))
        items.append(item)
    return {"MMFResponse": {"PlantName": mnem.upper(), "MMFData": items}}
