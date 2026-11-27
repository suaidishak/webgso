"""Tolerant validation of rev5 forecast and MMF responses.

Nothing here rejects data outright. Each rule break becomes a Flag (major or minor) and the
parsed data is still returned, so dashboards show what plants actually serve and the
compliance record says precisely what to fix. A response is "valid" when it has no major flag.

Decided horizon rules (27 Nov 2026):
  rolling          96 points from the next 15-min slot after the pull; a 97th point is a minor flag
  dayahead         5 x 96 = 480 points from 00:00 tomorrow
  weekahead        9 x 96 = 864 points from Saturday 00:00
  fourmonthsahead  from 00:00 on the 1st of next month for 4 calendar months
"""
from __future__ import annotations

import calendar
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, time

SLOT = timedelta(minutes=15)
MAJOR, MINOR = "major", "minor"

# Publish deadlines from rev5; used to decide which cycle a response should belong to.
DAYAHEAD_DEADLINE = time(10, 0)
WEEKAHEAD_DEADLINE = time(12, 30)  # Wednesday
WEEKAHEAD_DEADLINE_WEEKDAY = 2
FOURMONTHS_DEADLINE_DAY = 25

MMF_PARAMETERS = {
    "HorIrradiance": "W/m2", "IncIrradiance": "W/m2", "TotRadiation": "Wh/m2", "AmbientTemp": "°C",
    "WindSpeed": "m/s", "WindDir": "°", "WindDirSD": "°", "Humidity": "%", "Precipitation": "mm/h",
    "AirPressure": "hPa", "AtmPressure": "atm", "PVTemperature": "°C",
}
MMF_RANGES = {
    "HorIrradiance": (0, 1600), "IncIrradiance": (0, 1800), "TotRadiation": (0, 15000),
    "AmbientTemp": (5, 50), "WindSpeed": (0, 60), "WindDir": (0, 359.999), "WindDirSD": (0, 360),
    "Humidity": (0, 100), "Precipitation": (0, 300), "AirPressure": (800, 1100),
    "AtmPressure": (0.7, 1.1), "PVTemperature": (0, 90),
}
UNIT_ALIASES = {"c": "°C", "degc": "°C", "°c": "°C", "o": "°", "deg": "°", "w/m^2": "W/m2", "w/m²": "W/m2",
                "wh/m^2": "Wh/m2", "wh/m²": "Wh/m2", "mbar": "hPa"}
SENTINELS = {-3276.8, -9999.0, -999.0, 3276.7}
MMF_MAX_AGE = timedelta(minutes=10)


@dataclass
class Flag:
    rule: str
    severity: str
    message: str
    detail: dict | None = None
    paths: list[str] = field(default_factory=list)  # JSON Pointers into the response body


@dataclass
class ForecastResult:
    flags: list[Flag] = field(default_factory=list)
    points: list[tuple[datetime, datetime, float | None]] = field(default_factory=list)  # stored points
    horizon_start: datetime | None = None
    horizon_end: datetime | None = None
    expected_start: datetime | None = None
    expected_count: int | None = None
    served_count: int = 0

    @property
    def is_valid(self) -> bool:
        return not any(f.severity == MAJOR for f in self.flags)


@dataclass
class MmfValue:
    station_no: int
    parameter: str
    measured_at: datetime | None
    value: float | None
    unit: str | None
    is_standard: bool


@dataclass
class MmfResult:
    flags: list[Flag] = field(default_factory=list)
    values: list[MmfValue] = field(default_factory=list)
    station_count: int = 0

    @property
    def is_valid(self) -> bool:
        return not any(f.severity == MAJOR for f in self.flags)


# ---------------------------------------------------------------- time helpers
def floor_slot(t: datetime) -> datetime:
    return t.replace(minute=t.minute - t.minute % 15, second=0, microsecond=0)


def next_slot(t: datetime) -> datetime:
    """The next 15-min boundary strictly after t (12:52 -> 13:00, 13:00:00 -> 13:15)."""
    return floor_slot(t) + SLOT


def add_months(d: datetime, n: int) -> datetime:
    y, m = divmod(d.month - 1 + n, 12)
    return d.replace(year=d.year + y, month=m + 1, day=1)


def slots_between(a: datetime, b: datetime) -> int:
    return int((b - a) / SLOT)


def parse_ts(v) -> datetime | None:
    if not isinstance(v, str):
        return None
    s = v.strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1]
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is not None:  # convert any offset to Malaysia local naive
        dt = (dt - dt.utcoffset()).replace(tzinfo=None) + timedelta(hours=8)
    return dt


def parse_number(v) -> tuple[float | None, str | None]:
    """Returns (value, problem). problem is 'string' for numeric strings, 'nan', 'sentinel' or 'bad'."""
    if isinstance(v, bool) or v is None:
        return None, "bad"
    if isinstance(v, (int, float)):
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None, "nan"
        return (None, "sentinel") if f in SENTINELS else (f, None)
    if isinstance(v, str):
        s = v.strip()
        if s.lower() in ("nan", "null", "", "-", "n/a", "inf", "-inf"):
            return None, "nan"
        try:
            f = float(s)
        except ValueError:
            return None, "bad"
        if math.isnan(f) or math.isinf(f):
            return None, "nan"
        if f in SENTINELS:
            return None, "sentinel"
        return f, "string"
    return None, "bad"


# ---------------------------------------------------------------- expected horizons
def expected_horizon(category: str, retrieved_at: datetime) -> tuple[datetime, int, datetime | None]:
    """(expected start, expected point count, early-publish start that is also accepted).

    Before a publish deadline the plant legitimately still serves the previous cycle; after it,
    the new one. Publishing the next cycle early is fine, so it is returned as an accepted alternative.
    """
    day = retrieved_at.replace(hour=0, minute=0, second=0, microsecond=0)
    if category == "rolling":
        return next_slot(retrieved_at), 96, None
    if category == "dayahead":
        target = day + timedelta(days=1)
        if retrieved_at.time() < DAYAHEAD_DEADLINE:
            return day, 480, target  # yesterday's publication still current; today's may be early
        return target, 480, None
    if category == "weekahead":
        # Most recent Wednesday deadline at or before now -> the Saturday after it.
        days_since_wed = (day.weekday() - WEEKAHEAD_DEADLINE_WEEKDAY) % 7
        wed = day - timedelta(days=days_since_wed)
        if days_since_wed == 0 and retrieved_at.time() < WEEKAHEAD_DEADLINE:
            wed -= timedelta(days=7)
        start = wed + timedelta(days=3)
        return start, 9 * 96, start + timedelta(days=7)
    if category == "fourmonthsahead":
        first_this = day.replace(day=1)
        start = add_months(first_this, 1) if day.day >= FOURMONTHS_DEADLINE_DAY else first_this
        end = add_months(start, 4)
        early = add_months(start, 1) if start == first_this else None
        return start, slots_between(start, end), early
    raise ValueError(f"unknown forecast category {category}")


def fourmonth_count(start: datetime) -> int:
    return slots_between(start, add_months(start, 4))


# ---------------------------------------------------------------- forecast validation
CATEGORY_NAMES = {
    "rolling": {"rolling"},
    "dayahead": {"dayahead"},
    "weekahead": {"weekahead"},
    "fourmonthsahead": {"fourmonthsahead"},
}


def ptr(*parts) -> str:
    """JSON Pointer (RFC 6901) to a place in the response body, e.g. /ForecastResponse/ForecastData/5/ForecastValue."""
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def validate_forecast(body, category: str, mnemonic: str, retrieved_at: datetime,
                      capacity_mw: float | None = None) -> ForecastResult:
    """Every flag carries `paths`: JSON Pointers to the exact places in the body that caused it."""
    r = ForecastResult()

    def flag(rule, sev, msg, detail=None, paths=None):
        r.flags.append(Flag(rule, sev, msg, detail, list(paths or [])))

    exp_start, exp_count, early_start = expected_horizon(category, retrieved_at)
    r.expected_start, r.expected_count = exp_start, exp_count

    if not isinstance(body, dict):
        flag("JSON_INVALID", MAJOR, "Response is not a JSON object", paths=[""])
        return r
    rk = "ForecastResponse"
    root = body.get(rk)
    if root is None:
        alt = next((k for k in body if k.lower() == "forecastresponse"), None)
        if alt:
            flag("KEY_NAME", MINOR, f"Root key is '{alt}', expected 'ForecastResponse'", paths=[ptr(alt)])
            rk, root = alt, body[alt]
        else:
            flag("ROOT_KEY_MISSING", MAJOR, "Missing root key 'ForecastResponse'",
                 {"keys": list(body)[:20]}, paths=[""])
            return r
    if not isinstance(root, dict):
        flag("STRUCTURE", MAJOR, "'ForecastResponse' is not an object", paths=[ptr(rk)])
        return r

    for key in ("PlantName", "ForecastCategory", "ForecastData"):
        if key not in root:
            flag("KEY_MISSING", MAJOR if key == "ForecastData" else MINOR, f"Missing key '{key}'", paths=[ptr(rk)])

    name = root.get("PlantName")
    if isinstance(name, str):
        if name.strip().lower() != mnemonic.lower():
            flag("PLANT_NAME", MAJOR, f"PlantName '{name}' does not match '{mnemonic}'", paths=[ptr(rk, "PlantName")])
        elif name != mnemonic.lower():
            flag("PLANT_NAME_CASE", MINOR, f"PlantName '{name}' should be lowercase '{mnemonic.lower()}'",
                 paths=[ptr(rk, "PlantName")])

    cat = root.get("ForecastCategory")
    if isinstance(cat, str) and cat.replace(" ", "").lower() not in CATEGORY_NAMES[category]:
        flag("CATEGORY_NAME", MINOR, f"ForecastCategory '{cat}' on the {category} endpoint",
             paths=[ptr(rk, "ForecastCategory")])

    data = root.get("ForecastData")
    dp = ptr(rk, "ForecastData")
    if not isinstance(data, list):
        if "ForecastData" in root:
            flag("STRUCTURE", MAJOR, "'ForecastData' is not a list", paths=[dp])
        return r
    if not data:
        flag("EMPTY", MAJOR, "ForecastData is empty", paths=[dp])
        return r

    # (start, end, value, original index)
    parsed: list[tuple[datetime, datetime | None, float | None, int]] = []
    p_string, p_bad_val, p_bad_time, p_unit, p_missing = [], [], [], [], []
    for i, item in enumerate(data):
        ip = f"{dp}/{i}"
        if not isinstance(item, dict):
            p_bad_time.append(ip)
            continue
        if not {"StartTime", "EndTime", "ForecastValue", "Unit"} <= item.keys():
            p_missing.append(ip)
        st, et = parse_ts(item.get("StartTime")), parse_ts(item.get("EndTime"))
        if st is None:
            p_bad_time.append(f"{ip}/StartTime" if "StartTime" in item else ip)
            continue
        val, problem = parse_number(item.get("ForecastValue"))
        if problem == "string":
            p_string.append(f"{ip}/ForecastValue")
        elif problem:
            p_bad_val.append(f"{ip}/ForecastValue" if "ForecastValue" in item else ip)
        unit = item.get("Unit")
        if not (isinstance(unit, str) and unit.strip().upper() == "MW"):
            p_unit.append(f"{ip}/Unit" if "Unit" in item else ip)
        parsed.append((st, et, val, i))

    if p_missing:
        flag("KEY_MISSING", MAJOR, f"{len(p_missing)} items lack StartTime/EndTime/ForecastValue/Unit", paths=p_missing)
    if p_bad_time:
        flag("TIMESTAMP", MAJOR, f"{len(p_bad_time)} items have an unreadable StartTime", paths=p_bad_time)
    if p_string:
        flag("VALUE_AS_STRING", MINOR, f"ForecastValue sent as text in {len(p_string)} items (rev5: number)",
             paths=p_string)
    if p_bad_val:
        flag("VALUE_INVALID", MAJOR, f"{len(p_bad_val)} items have a missing or non-numeric ForecastValue",
             paths=p_bad_val)
    if p_unit:
        flag("UNIT", MINOR, f"Unit is not 'MW' in {len(p_unit)} items", paths=p_unit)
    if not parsed:
        return r

    parsed.sort(key=lambda p: p[0])
    r.served_count = len(parsed)
    r.horizon_start = parsed[0][0]
    r.horizon_end = parsed[-1][1] or parsed[-1][0] + SLOT
    at = lambda i, k: f"{dp}/{i}/{k}"  # noqa: E731

    # Slot shape: aligned to 15 min, EndTime = StartTime + 15 min, contiguous, no duplicates.
    p_align = [at(i, "StartTime") for st, _, _, i in parsed if st.minute % 15 or st.second]
    p_len = [at(i, "EndTime") for st, et, _, i in parsed if et is not None and et - st != SLOT]
    p_dup, p_gap = [], []
    for (a, _, _, _), (b, _, _, j) in zip(parsed, parsed[1:]):
        if b == a:
            p_dup.append(at(j, "StartTime"))
        elif b - a != SLOT:
            p_gap.append(at(j, "StartTime"))
    if p_align:
        flag("SLOT_ALIGNMENT", MAJOR, f"{len(p_align)} slots do not start on a 15-minute boundary", paths=p_align)
    if p_len:
        flag("SLOT_LENGTH", MAJOR, f"{len(p_len)} slots are not exactly 15 minutes long", paths=p_len)
    if p_dup:
        flag("DUPLICATE_SLOT", MAJOR, f"{len(p_dup)} duplicate slots", paths=p_dup)
    if p_gap:
        flag("GAP", MAJOR, f"{len(p_gap)} gaps between slots (marked at the slot after each gap)", paths=p_gap)

    # Horizon start
    start, first_i = parsed[0][0], parsed[0][3]
    if category == "fourmonthsahead" and start in (exp_start, early_start):
        exp_count = fourmonth_count(start)
        r.expected_start, r.expected_count = start, exp_count
    elif start == early_start:
        r.expected_start = start
    elif start != exp_start:
        detail = {"served": start.isoformat(), "expected": exp_start.isoformat()}
        if category == "rolling" and abs(start - exp_start) <= SLOT:
            flag("START_OFFSET", MINOR,
                 f"Rolling starts {start:%H:%M}, expected {exp_start:%H:%M} (next slot after the pull)",
                 detail, paths=[at(first_i, "StartTime")])
        else:
            flag("START_MISMATCH", MAJOR,
                 f"Horizon starts {start:%Y-%m-%d %H:%M}, expected {exp_start:%Y-%m-%d %H:%M}",
                 detail, paths=[at(first_i, "StartTime")])

    # Point count. Extra trailing points are minor (e.g. the 97th rolling point); short is major.
    n = len(parsed)
    if n < exp_count:
        flag("POINT_COUNT", MAJOR, f"{n} points served, {exp_count} required",
             {"served": n, "expected": exp_count}, paths=[dp])
        keep = parsed
    else:
        if n > exp_count:
            flag("EXTRA_POINTS", MINOR, f"{n} points served, {exp_count} expected; extra points ignored",
                 {"served": n, "expected": exp_count}, paths=[f"{dp}/{i}" for *_, i in parsed[exp_count:]])
        keep = parsed[:exp_count]

    # Values
    p_neg = [at(i, "ForecastValue") for _, _, v_, i in keep if v_ is not None and v_ < 0]
    if p_neg:
        flag("NEGATIVE_VALUE", MAJOR, f"{len(p_neg)} negative forecast values", paths=p_neg)
    if capacity_mw:
        over = [(v_, i) for _, _, v_, i in keep if v_ is not None and v_ > capacity_mw * 1.05]
        if over:
            flag("OVER_CAPACITY", MAJOR,
                 f"{len(over)} values above capacity {capacity_mw} MW (max {max(v_ for v_, _ in over)})",
                 paths=[at(i, "ForecastValue") for _, i in over])
    if all(v_ in (None, 0) for _, _, v_, _ in keep) and category != "rolling":
        flag("ALL_ZERO", MINOR, "Every forecast value is zero or empty", paths=[dp])

    r.points = [(st, et or st + SLOT, v_) for st, et, v_, _ in keep]
    return r


# ---------------------------------------------------------------- MMF validation
def normalise_unit(u) -> str | None:
    if not isinstance(u, str):
        return None
    s = u.strip()
    return UNIT_ALIASES.get(s.lower(), s)


def validate_mmf(body, mnemonic: str, retrieved_at: datetime,
                 expected_stations: int | None = None) -> MmfResult:
    r = MmfResult()

    def flag(rule, sev, msg, detail=None, paths=None):
        r.flags.append(Flag(rule, sev, msg, detail, list(paths or [])))

    if not isinstance(body, dict):
        flag("JSON_INVALID", MAJOR, "Response is not a JSON object", paths=[""])
        return r
    root = body.get("MMFResponse")
    if not isinstance(root, dict):
        flag("MMF_SHAPE", MAJOR, "Response does not follow the rev5 MMFResponse structure; not ingested",
             {"keys": list(body)[:20]}, paths=[""])
        return r

    name = root.get("PlantName")
    if not isinstance(name, str):
        flag("KEY_MISSING", MINOR, "Missing key 'PlantName'", paths=["/MMFResponse"])
    elif name.strip().lower() != mnemonic.lower():
        flag("PLANT_NAME", MAJOR, f"PlantName '{name}' does not match '{mnemonic}'", paths=["/MMFResponse/PlantName"])
    elif name != mnemonic.lower():
        flag("PLANT_NAME_CASE", MINOR, f"PlantName '{name}' should be lowercase '{mnemonic.lower()}'",
             paths=["/MMFResponse/PlantName"])

    data = root.get("MMFData")
    if not isinstance(data, list) or not data:
        flag("STRUCTURE", MAJOR, "'MMFData' missing or empty", paths=["/MMFResponse"])
        return r

    seen_params: set[str] = set()
    param_path: dict[str, str] = {}
    stations: set[int] = set()
    p_string, p_missing, p_unitfix, p_range, p_badunit, p_badtime = [], [], [], [], [], []
    bad_units: set[str] = set()
    newest: datetime | None = None
    time_paths: list[tuple[datetime, str]] = []
    malformed: list[str] = []
    for i, item in enumerate(data):
        ip = f"/MMFResponse/MMFData/{i}"
        if not isinstance(item, dict):
            continue
        param = item.get("Parameter")
        if not isinstance(param, str):
            continue
        seen_params.add(param)
        param_path.setdefault(param, f"{ip}/Parameter")
        standard = param in MMF_PARAMETERS
        t = parse_ts(item.get("Time"))
        if t is None:
            p_badtime.append(f"{ip}/Time" if "Time" in item else ip)
        else:
            time_paths.append((t, f"{ip}/Time"))
            if newest is None or t > newest:
                newest = t
        raw_unit = item.get("Unit")
        unit = normalise_unit(raw_unit)
        if unit != raw_unit:
            p_unitfix.append(f"{ip}/Unit" if "Unit" in item else ip)
        if standard and unit and unit != MMF_PARAMETERS[param]:
            bad_units.add(f"{param}: {raw_unit}")
            p_badunit.append(f"{ip}/Unit")
        for key, raw in item.items():
            if not (key.upper().startswith("MMF") and key[3:].isdigit()):
                continue
            vp = f"{ip}/{key}"
            st_no = int(key[3:])
            stations.add(st_no)
            val, problem = parse_number(raw)
            if problem == "string":
                p_string.append(vp)
            elif problem in ("nan", "sentinel", "bad"):
                p_missing.append(vp)
                if problem == "bad" and len(malformed) < 5:
                    malformed.append(f"{param}/{key}={raw!r}")
            if val is not None and standard:
                lo, hi = MMF_RANGES[param]
                if not lo <= val <= hi:
                    p_range.append(vp)
            r.values.append(MmfValue(st_no, param, t, val, unit, standard))

    r.station_count = len(stations)
    missing_params = sorted(set(MMF_PARAMETERS) - seen_params)
    extra_params = sorted(seen_params - set(MMF_PARAMETERS))
    if missing_params:
        flag("MISSING_PARAMETER", MINOR, f"{len(missing_params)} of 12 rev5 parameters missing: "
             + ", ".join(missing_params), {"missing": missing_params}, paths=["/MMFResponse/MMFData"])
    if extra_params:
        flag("EXTRA_PARAMETER", MINOR, f"Non-rev5 parameters stored as extras: {', '.join(extra_params)}",
             {"extra": extra_params}, paths=[param_path[p] for p in extra_params])
    if p_string:
        flag("VALUE_AS_STRING", MINOR, f"{len(p_string)} values sent as text (rev5: number)", paths=p_string)
    if p_missing:
        flag("VALUE_MISSING", MINOR, f"{len(p_missing)} values are NaN, sentinel or unreadable",
             {"examples": malformed} if malformed else None, paths=p_missing)
    if p_unitfix:
        flag("UNIT_FORMAT", MINOR, f"{len(p_unitfix)} units normalised (e.g. C -> °C, o -> °)", paths=p_unitfix)
    if bad_units:
        flag("UNIT", MINOR, "Unexpected units: " + "; ".join(sorted(bad_units)), paths=p_badunit)
    if p_range:
        flag("OUT_OF_RANGE", MINOR, f"{len(p_range)} values outside the plausible range", paths=p_range)
    if expected_stations and r.station_count < expected_stations:
        flag("STATION_COUNT", MAJOR, f"{r.station_count} stations reported, {expected_stations} expected",
             paths=["/MMFResponse/MMFData"])
    elif r.station_count < 1:
        flag("STATION_COUNT", MAJOR, "No MMF1..n station values", paths=["/MMFResponse/MMFData"])
    if p_badtime:
        flag("TIMESTAMP", MINOR, f"{len(p_badtime)} parameters have an unreadable Time", paths=p_badtime)
    if newest is None:
        flag("STALE_MMF", MAJOR, "No readable measurement time", paths=["/MMFResponse/MMFData"])
    elif retrieved_at - newest > MMF_MAX_AGE:
        flag("STALE_MMF", MAJOR, f"Latest reading {newest:%Y-%m-%d %H:%M} is older than "
             f"{int(MMF_MAX_AGE.total_seconds() // 60)} min", {"latest": newest.isoformat()},
             paths=[p for _, p in time_paths])
    elif newest - retrieved_at > timedelta(minutes=5):
        flag("FUTURE_TIME", MINOR, f"Reading time {newest:%H:%M} is in the future",
             paths=[p for t, p in time_paths if t - retrieved_at > timedelta(minutes=5)])
    return r


def days_in_month(d: datetime) -> int:
    return calendar.monthrange(d.year, d.month)[1]
