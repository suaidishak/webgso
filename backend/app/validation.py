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


def validate_forecast(body, category: str, mnemonic: str, retrieved_at: datetime,
                      capacity_mw: float | None = None) -> ForecastResult:
    r = ForecastResult()
    flag = lambda *a, **k: r.flags.append(Flag(*a, **k))  # noqa: E731

    exp_start, exp_count, early_start = expected_horizon(category, retrieved_at)
    r.expected_start, r.expected_count = exp_start, exp_count

    if not isinstance(body, dict):
        flag("JSON_INVALID", MAJOR, "Response is not a JSON object")
        return r
    root = body.get("ForecastResponse")
    if root is None:
        alt = next((k for k in body if k.lower() == "forecastresponse"), None)
        if alt:
            flag("KEY_NAME", MINOR, f"Root key is '{alt}', expected 'ForecastResponse'")
            root = body[alt]
        else:
            flag("ROOT_KEY_MISSING", MAJOR, "Missing root key 'ForecastResponse'",
                 {"keys": list(body)[:20]})
            return r
    if not isinstance(root, dict):
        flag("STRUCTURE", MAJOR, "'ForecastResponse' is not an object")
        return r

    for key in ("PlantName", "ForecastCategory", "ForecastData"):
        if key not in root:
            flag("KEY_MISSING", MAJOR if key == "ForecastData" else MINOR, f"Missing key '{key}'")

    name = root.get("PlantName")
    if isinstance(name, str):
        if name.strip().lower() != mnemonic.lower():
            flag("PLANT_NAME", MAJOR, f"PlantName '{name}' does not match '{mnemonic}'")
        elif name != mnemonic.lower():
            flag("PLANT_NAME_CASE", MINOR, f"PlantName '{name}' should be lowercase '{mnemonic.lower()}'")

    cat = root.get("ForecastCategory")
    if isinstance(cat, str) and cat.replace(" ", "").lower() not in CATEGORY_NAMES[category]:
        flag("CATEGORY_NAME", MINOR, f"ForecastCategory '{cat}' on the {category} endpoint")

    data = root.get("ForecastData")
    if not isinstance(data, list):
        if "ForecastData" in root:
            flag("STRUCTURE", MAJOR, "'ForecastData' is not a list")
        return r
    if not data:
        flag("EMPTY", MAJOR, "ForecastData is empty")
        return r

    parsed: list[tuple[datetime, datetime | None, float | None]] = []
    string_values = bad_values = bad_times = bad_units = missing_keys = 0
    for item in data:
        if not isinstance(item, dict):
            bad_times += 1
            continue
        if not {"StartTime", "EndTime", "ForecastValue", "Unit"} <= item.keys():
            missing_keys += 1
        st, et = parse_ts(item.get("StartTime")), parse_ts(item.get("EndTime"))
        if st is None:
            bad_times += 1
            continue
        val, problem = parse_number(item.get("ForecastValue"))
        if problem == "string":
            string_values += 1
        elif problem:
            bad_values += 1
        unit = item.get("Unit")
        if not (isinstance(unit, str) and unit.strip().upper() == "MW"):
            bad_units += 1
        parsed.append((st, et, val))

    if missing_keys:
        flag("KEY_MISSING", MAJOR, f"{missing_keys} items lack StartTime/EndTime/ForecastValue/Unit")
    if bad_times:
        flag("TIMESTAMP", MAJOR, f"{bad_times} items have an unreadable StartTime")
    if string_values:
        flag("VALUE_AS_STRING", MINOR, f"ForecastValue sent as text in {string_values} items (rev5: number)")
    if bad_values:
        flag("VALUE_INVALID", MAJOR, f"{bad_values} items have a missing or non-numeric ForecastValue")
    if bad_units:
        flag("UNIT", MINOR, f"Unit is not 'MW' in {bad_units} items")
    if not parsed:
        return r

    parsed.sort(key=lambda p: p[0])
    r.served_count = len(parsed)
    r.horizon_start = parsed[0][0]
    r.horizon_end = parsed[-1][1] or parsed[-1][0] + SLOT

    # Slot shape: aligned to 15 min, EndTime = StartTime + 15 min, contiguous, no duplicates.
    misaligned = sum(1 for st, _, _ in parsed if st.minute % 15 or st.second)
    wrong_len = sum(1 for st, et, _ in parsed if et is not None and et - st != SLOT)
    dups = gaps = 0
    for (a, _, _), (b, _, _) in zip(parsed, parsed[1:]):
        if b == a:
            dups += 1
        elif b - a != SLOT:
            gaps += 1
    if misaligned:
        flag("SLOT_ALIGNMENT", MAJOR, f"{misaligned} slots do not start on a 15-minute boundary")
    if wrong_len:
        flag("SLOT_LENGTH", MAJOR, f"{wrong_len} slots are not exactly 15 minutes long")
    if dups:
        flag("DUPLICATE_SLOT", MAJOR, f"{dups} duplicate slots")
    if gaps:
        flag("GAP", MAJOR, f"{gaps} gaps between slots")

    # Horizon start
    start = r.horizon_start
    if category == "fourmonthsahead" and start in (exp_start, early_start):
        exp_count = fourmonth_count(start)
        r.expected_start, r.expected_count = start, exp_count
    elif start == early_start:
        r.expected_start = start
    elif start != exp_start:
        if category == "rolling" and abs(start - exp_start) <= SLOT:
            flag("START_OFFSET", MINOR,
                 f"Rolling starts {start:%H:%M}, expected {exp_start:%H:%M} (next slot after the pull)",
                 {"served": start.isoformat(), "expected": exp_start.isoformat()})
        else:
            flag("START_MISMATCH", MAJOR,
                 f"Horizon starts {start:%Y-%m-%d %H:%M}, expected {exp_start:%Y-%m-%d %H:%M}",
                 {"served": start.isoformat(), "expected": exp_start.isoformat()})

    # Point count. Extra trailing points are minor (e.g. the 97th rolling point); short is major.
    n = len(parsed)
    if n < exp_count:
        flag("POINT_COUNT", MAJOR, f"{n} points served, {exp_count} required",
             {"served": n, "expected": exp_count})
        keep = parsed
    else:
        if n > exp_count:
            flag("EXTRA_POINTS", MINOR, f"{n} points served, {exp_count} expected; extra points ignored",
                 {"served": n, "expected": exp_count})
        keep = parsed[:exp_count]

    # Values
    neg = sum(1 for *_, v in keep if v is not None and v < 0)
    if neg:
        flag("NEGATIVE_VALUE", MAJOR, f"{neg} negative forecast values")
    if capacity_mw:
        over = [v for *_, v in keep if v is not None and v > capacity_mw * 1.05]
        if over:
            flag("OVER_CAPACITY", MAJOR, f"{len(over)} values above capacity {capacity_mw} MW (max {max(over)})")
    if all(v in (None, 0) for *_, v in keep) and category != "rolling":
        flag("ALL_ZERO", MINOR, "Every forecast value is zero or empty")

    r.points = [(st, et or st + SLOT, v) for st, et, v in keep]
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
    flag = lambda *a, **k: r.flags.append(Flag(*a, **k))  # noqa: E731

    if not isinstance(body, dict):
        flag("JSON_INVALID", MAJOR, "Response is not a JSON object")
        return r
    root = body.get("MMFResponse")
    if not isinstance(root, dict):
        flag("MMF_SHAPE", MAJOR, "Response does not follow the rev5 MMFResponse structure; not ingested",
             {"keys": list(body)[:20]})
        return r

    name = root.get("PlantName")
    if not isinstance(name, str):
        flag("KEY_MISSING", MINOR, "Missing key 'PlantName'")
    elif name.strip().lower() != mnemonic.lower():
        flag("PLANT_NAME", MAJOR, f"PlantName '{name}' does not match '{mnemonic}'")
    elif name != mnemonic.lower():
        flag("PLANT_NAME_CASE", MINOR, f"PlantName '{name}' should be lowercase '{mnemonic.lower()}'")

    data = root.get("MMFData")
    if not isinstance(data, list) or not data:
        flag("STRUCTURE", MAJOR, "'MMFData' missing or empty")
        return r

    seen_params: set[str] = set()
    stations: set[int] = set()
    string_vals = missing_vals = unit_fixes = out_of_range = 0
    bad_units: set[str] = set()
    newest: datetime | None = None
    bad_time = 0
    malformed: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        param = item.get("Parameter")
        if not isinstance(param, str):
            continue
        seen_params.add(param)
        standard = param in MMF_PARAMETERS
        t = parse_ts(item.get("Time"))
        if t is None:
            bad_time += 1
        elif newest is None or t > newest:
            newest = t
        raw_unit = item.get("Unit")
        unit = normalise_unit(raw_unit)
        if unit != raw_unit:
            unit_fixes += 1
        if standard and unit and unit != MMF_PARAMETERS[param]:
            bad_units.add(f"{param}: {raw_unit}")
        for key, raw in item.items():
            if not (key.upper().startswith("MMF") and key[3:].isdigit()):
                continue
            st_no = int(key[3:])
            stations.add(st_no)
            val, problem = parse_number(raw)
            if problem == "string":
                string_vals += 1
            elif problem in ("nan", "sentinel"):
                missing_vals += 1
            elif problem == "bad":
                missing_vals += 1
                if len(malformed) < 5:
                    malformed.append(f"{param}/{key}={raw!r}")
            if val is not None and standard:
                lo, hi = MMF_RANGES[param]
                if not lo <= val <= hi:
                    out_of_range += 1
            r.values.append(MmfValue(st_no, param, t, val, unit, standard))

    r.station_count = len(stations)
    missing_params = sorted(set(MMF_PARAMETERS) - seen_params)
    extra_params = sorted(seen_params - set(MMF_PARAMETERS))
    if missing_params:
        flag("MISSING_PARAMETER", MINOR, f"{len(missing_params)} of 12 rev5 parameters missing",
             {"missing": missing_params})
    if extra_params:
        flag("EXTRA_PARAMETER", MINOR, f"Non-rev5 parameters stored as extras: {', '.join(extra_params)}",
             {"extra": extra_params})
    if string_vals:
        flag("VALUE_AS_STRING", MINOR, f"{string_vals} values sent as text (rev5: number)")
    if missing_vals:
        flag("VALUE_MISSING", MINOR, f"{missing_vals} values are NaN, sentinel or unreadable",
             {"examples": malformed} if malformed else None)
    if unit_fixes:
        flag("UNIT_FORMAT", MINOR, f"{unit_fixes} units normalised (e.g. C -> °C, o -> °)")
    if bad_units:
        flag("UNIT", MINOR, "Unexpected units: " + "; ".join(sorted(bad_units)))
    if out_of_range:
        flag("OUT_OF_RANGE", MINOR, f"{out_of_range} values outside the plausible range")
    if expected_stations and r.station_count < expected_stations:
        flag("STATION_COUNT", MAJOR, f"{r.station_count} stations reported, {expected_stations} expected")
    elif r.station_count < 1:
        flag("STATION_COUNT", MAJOR, "No MMF1..n station values")
    if bad_time:
        flag("TIMESTAMP", MINOR, f"{bad_time} parameters have an unreadable Time")
    if newest is None:
        flag("STALE_MMF", MAJOR, "No readable measurement time")
    elif retrieved_at - newest > MMF_MAX_AGE:
        flag("STALE_MMF", MAJOR, f"Latest reading {newest:%Y-%m-%d %H:%M} is older than "
             f"{int(MMF_MAX_AGE.total_seconds() // 60)} min", {"latest": newest.isoformat()})
    elif newest - retrieved_at > timedelta(minutes=5):
        flag("FUTURE_TIME", MINOR, f"Reading time {newest:%H:%M} is in the future")
    return r


def days_in_month(d: datetime) -> int:
    return calendar.monthrange(d.year, d.month)[1]
