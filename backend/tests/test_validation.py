from datetime import datetime, timedelta

from app import validation as v
from app.scheduler import last_deadline, last_due
from app.models import ComplianceDeadline, PollSchedule
from datetime import time

S = timedelta(minutes=15)


def fc(mnem, cat, start, n, as_string=True, category_name=None):
    data = []
    for i in range(n):
        t = start + i * S
        val = "1.5" if as_string else 1.5
        data.append({"StartTime": t.isoformat(), "EndTime": (t + S).isoformat(), "ForecastValue": val, "Unit": "MW"})
    return {"ForecastResponse": {"PlantName": mnem, "ForecastCategory": category_name or cat, "ForecastData": data}}


def rules(r):
    return {f.rule: f.severity for f in r.flags}


def test_next_slot():
    assert v.next_slot(datetime(2026, 11, 27, 12, 52)) == datetime(2026, 11, 27, 13, 0)
    assert v.next_slot(datetime(2026, 11, 27, 13, 0)) == datetime(2026, 11, 27, 13, 15)


def test_rolling_97_points_is_minor_and_first_96_kept():
    now = datetime(2026, 11, 27, 17, 31)
    body = fc("kmls", "rolling", datetime(2026, 11, 27, 17, 45), 97, category_name="ActivePowerProduction")
    r = v.validate_forecast(body, "rolling", "kmls", now, capacity_mw=50)
    assert r.is_valid
    assert rules(r) == {"EXTRA_POINTS": "minor", "VALUE_AS_STRING": "minor", "CATEGORY_NAME": "minor"}
    assert len(r.points) == 96 and r.points[0][0] == datetime(2026, 11, 27, 17, 45)


def test_rolling_short_and_wrong_start_is_major():
    now = datetime(2026, 11, 27, 17, 31)
    r = v.validate_forecast(fc("kmls", "rolling", datetime(2026, 11, 27, 15, 0), 90), "rolling", "kmls", now)
    assert not r.is_valid
    assert rules(r)["POINT_COUNT"] == "major" and rules(r)["START_MISMATCH"] == "major"


def test_rolling_one_slot_off_is_minor():
    now = datetime(2026, 11, 27, 17, 31)
    r = v.validate_forecast(fc("kmls", "rolling", datetime(2026, 11, 27, 17, 30), 96, False), "rolling", "kmls", now)
    assert r.is_valid and rules(r) == {"START_OFFSET": "minor"}


def test_dayahead_after_deadline_expects_tomorrow():
    now = datetime(2026, 11, 27, 10, 5)
    ok = v.validate_forecast(fc("sids", "dayahead", datetime(2026, 11, 28), 480, False), "dayahead", "sids", now)
    assert ok.is_valid and ok.flags == []
    stale = v.validate_forecast(fc("sids", "dayahead", datetime(2026, 11, 27), 480, False), "dayahead", "sids", now)
    assert rules(stale)["START_MISMATCH"] == "major"


def test_dayahead_before_deadline_accepts_today_or_early_tomorrow():
    now = datetime(2026, 11, 27, 9, 0)
    for start in (datetime(2026, 11, 27), datetime(2026, 11, 28)):
        r = v.validate_forecast(fc("sids", "dayahead", start, 480, False), "dayahead", "sids", now)
        assert r.is_valid, r.flags


def test_weekahead_from_saturday():
    now = datetime(2026, 11, 27, 17, 0)  # Friday; last Wednesday 25 Nov -> Saturday 28 Nov
    r = v.validate_forecast(fc("bdws", "weekahead", datetime(2026, 11, 28), 864, False), "weekahead", "bdws", now)
    assert r.is_valid and r.flags == []
    assert v.expected_horizon("weekahead", datetime(2026, 12, 2, 12, 0))[0] == datetime(2026, 11, 28)
    assert v.expected_horizon("weekahead", datetime(2026, 12, 2, 12, 35))[0] == datetime(2026, 12, 5)


def test_fourmonths_calendar_months():
    now = datetime(2026, 11, 27, 17, 0)
    r = v.validate_forecast(fc("bkhs", "fourmonthsahead", datetime(2026, 12, 1), 11616, False),
                            "fourmonthsahead", "bkhs", now)
    assert r.is_valid and r.expected_count == 11616
    short = v.validate_forecast(fc("bkhs", "fourmonthsahead", datetime(2026, 12, 1), 11520, False),
                                "fourmonthsahead", "bkhs", now)
    assert rules(short)["POINT_COUNT"] == "major"


def test_forecast_structure_problems():
    now = datetime(2026, 11, 27, 17, 0)
    assert rules(v.validate_forecast({"foo": 1}, "rolling", "kmls", now)) == {"ROOT_KEY_MISSING": "major"}
    body = fc("KMLS", "dayahead", datetime(2026, 11, 28), 480, False)
    body["ForecastResponse"]["ForecastData"][5]["ForecastValue"] = "NaN"
    body["ForecastResponse"]["ForecastData"][6]["ForecastValue"] = -2
    body["ForecastResponse"]["ForecastData"][7]["ForecastValue"] = 999
    r = v.validate_forecast(body, "dayahead", "kmls", now, capacity_mw=50)
    assert rules(r) == {"PLANT_NAME_CASE": "minor", "VALUE_INVALID": "major", "NEGATIVE_VALUE": "major",
                        "OVER_CAPACITY": "major"}


def test_gaps_and_duplicates():
    now = datetime(2026, 11, 27, 10, 30)
    body = fc("sids", "dayahead", datetime(2026, 11, 28), 480, False)
    d = body["ForecastResponse"]["ForecastData"]
    d[10] = dict(d[9])
    del d[20]
    r = v.validate_forecast(body, "dayahead", "sids", now)
    assert {"DUPLICATE_SLOT", "GAP", "POINT_COUNT"} <= set(rules(r))


def mmf_body(mnem="bdws", t="2026-11-27T17:30:00", stations=2, nan=False):
    items = []
    for p, unit in v.MMF_PARAMETERS.items():
        unit = {"°C": "C", "°": "o"}.get(unit, unit)
        it = {"Parameter": p, "Time": t, "Unit": unit}
        for s in range(1, stations + 1):
            it[f"MMF{s}"] = "NaN" if nan and p == "Humidity" else "10"
        items.append(it)
    items.append({"Parameter": "MWPerArea", "Time": t, "Unit": "MW/m2", "MMF1": "0.0001"})
    return {"MMFResponse": {"PlantName": mnem.upper(), "MMFData": items}}


def test_mmf_tolerant():
    r = v.validate_mmf(mmf_body(), "bdws", datetime(2026, 11, 27, 17, 32), expected_stations=2)
    assert r.is_valid and r.station_count == 2
    assert set(rules(r)) == {"PLANT_NAME_CASE", "VALUE_AS_STRING", "UNIT_FORMAT", "EXTRA_PARAMETER", "OUT_OF_RANGE"}
    temps = [x for x in r.values if x.parameter == "AmbientTemp"]
    assert temps[0].unit == "°C" and temps[0].value == 10.0


def test_mmf_stale_and_nan_and_raw_shape():
    r = v.validate_mmf(mmf_body(nan=True), "lmms", datetime(2026, 11, 27, 18, 30))
    assert rules(r)["STALE_MMF"] == "major" and rules(r)["PLANT_NAME"] == "major"
    r = v.validate_mmf(mmf_body("lmms", nan=True), "lmms", datetime(2026, 11, 27, 17, 31))
    assert rules(r)["VALUE_MISSING"] == "minor"
    raw = {"data": [{"tgi": 1.0, "poa": -3276.8, "ws": "0.15820.0661"}]}
    assert rules(v.validate_mmf(raw, "kmls", datetime(2026, 11, 27, 17, 31))) == {"MMF_SHAPE": "major"}


def test_schedule_due_times():
    now = datetime(2026, 11, 27, 17, 7, 30)
    assert last_due(PollSchedule(kind="interval", interval_minutes=5), now) == datetime(2026, 11, 27, 17, 5)
    assert last_due(PollSchedule(kind="cron", cron="5 10-23 * * *"), now) == datetime(2026, 11, 27, 17, 5)
    assert last_due(PollSchedule(kind="interval", interval_minutes=15, window_start=time(6), window_end=time(16, 45)),
                    now) is None


def test_deadlines():
    now = datetime(2026, 11, 27, 17, 0)
    d = ComplianceDeadline(period="daily", due_time=time(10, 0))
    assert last_deadline(d, now) == (datetime(2026, 11, 27, 10, 0), datetime(2026, 11, 28))
    w = ComplianceDeadline(period="weekly", weekday=2, due_time=time(12, 30))
    assert last_deadline(w, now) == (datetime(2026, 11, 25, 12, 30), datetime(2026, 11, 28))
    m = ComplianceDeadline(period="monthly", day_of_month=25, due_time=time(23, 59))
    assert last_deadline(m, now) == (datetime(2026, 11, 25, 23, 59), datetime(2026, 12, 1))
    assert last_deadline(m, datetime(2026, 11, 20)) == (datetime(2026, 10, 25, 23, 59), datetime(2026, 11, 1))


def test_plant_key_encryption_and_auth_parts():
    from app import plant_keys as k
    tok = k.encrypt("demo-key-1234")
    assert "demo-key" not in tok and k.decrypt(tok) == "demo-key-1234"
    assert k.decrypt("garbage") is None
    assert k.hint("demo-key-1234") == "1234" and k.hint("short") == ""
    assert k.auth_parts("header", None, "abc") == ({"X-API-Key": "abc"}, {})
    assert k.auth_parts("query", "key", "abc") == ({}, {"key": "abc"})
    assert k.auth_parts("none", None, "abc") == ({}, {})
    assert k.auth_parts("header", "X-API-Key", None) == ({}, {})
    assert k.scrub("GET https://x/y?apiKey=abc failed", "abc") == "GET https://x/y?apiKey=*** failed"
