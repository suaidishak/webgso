"""SOAP profile: envelope, XML -> rev5 JSON conversion, pointers for the inspector, faults, WSDL."""
import sys
from datetime import datetime
from pathlib import Path

import pytest

from app import soap
from app import validation as v

# The simulator's XML builders double as fixtures (skipped where mockplants/ is not alongside, e.g. in the image).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mockplants"))
sim = pytest.importorskip("sim")

NOW = datetime(2026, 11, 28, 9, 7)


def _forecast(n=96, value=12.5):
    return {"ForecastResponse": {"PlantName": "sims01", "ForecastCategory": "DayAhead", "ForecastData": [
        {"StartTime": f"2026-11-29T{h:02d}:{m:02d}:00", "EndTime": "x", "ForecastValue": value, "Unit": "MW"}
        for h in range(24) for m in (0, 15, 30, 45)][:n]}}


def test_forecast_converts_to_rev5_shape_with_numbers():
    xml = sim.forecast_xml("sims01", "1.1", "GetDayAheadForecast", _forecast(), set(), "dayahead").encode()
    r = soap.convert(xml, "GetDayAheadForecast", sim.NS, "1.1")
    assert not r.flags
    fr = r.body["ForecastResponse"]
    assert fr["PlantName"] == "sims01" and len(fr["ForecastData"]) == 96
    assert fr["ForecastData"][0]["ForecastValue"] == 12.5
    paths = {p["path"] for line in r.lines for p in line["parts"] if "path" in p}
    assert "/ForecastResponse/ForecastData/3/ForecastValue" in paths


def test_single_item_list_stays_a_list():
    xml = sim.forecast_xml("sims01", "1.1", "GetDayAheadForecast", _forecast(1), set(), "dayahead").encode()
    assert isinstance(soap.convert(xml, None, None, "1.1").body["ForecastResponse"]["ForecastData"], list)


def test_decimal_comma_and_namespace_are_flagged():
    xml = sim.forecast_xml("sims04", "1.1", "GetDayAheadForecast", _forecast(), {"oldns", "comma:dayahead"},
                           "dayahead").encode()
    r = soap.convert(xml, "GetDayAheadForecast", sim.NS, "1.1")
    assert [f.rule for f in r.flags] == ["SOAP_NAMESPACE"]
    assert r.body["ForecastResponse"]["ForecastData"][0]["ForecastValue"] == "12,5"
    res = v.validate_forecast(r.body, "dayahead", "sims01", NOW, 50)
    assert "VALUE_INVALID" in {f.rule for f in res.flags}


def test_fault_11_and_12():
    for ver in ("1.1", "1.2"):
        r = soap.convert(sim.fault(ver, "Server", "Not generated", "pending").encode(), "X", None, ver)
        assert r.body is None and r.fault["reason"] == "Not generated"
        assert r.flags[0].rule == "SOAP_FAULT" and r.flags[0].paths == [soap.P_FAULT]


def test_truncated_xml_and_doctype():
    xml = sim.forecast_xml("sims01", "1.1", "GetDayAheadForecast", _forecast(), set(), "dayahead")
    assert soap.convert(xml[:500].encode(), None, None, "1.1").flags[0].rule == "XML_INVALID"
    bomb = b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>'
    assert soap.convert(bomb, None, None, "1.1").flags[0].rule == "XML_INVALID"


def test_version_mismatch_and_wrong_operation():
    xml = sim.forecast_xml("sims01", "1.2", "GetRollingForecast", _forecast(), set(), "rolling").encode()
    rules = {f.rule for f in soap.convert(xml, "GetDayAheadForecast", sim.NS, "1.1").flags}
    assert rules == {"SOAP_VERSION", "SOAP_OPERATION"}


def test_envelope_with_wsse():
    env = soap.envelope("GetLiveMMF", None, "1.2", "sims02", ("gso-sim", "rekaan-2026")).decode()
    assert sim.wsse_ok(env) and soap.NS_SOAP12 in env and "<gso:PlantName>sims02</gso:PlantName>" in env


def test_wsdl():
    w = soap.parse_wsdl(sim.wsdl("sims02", "http://mock:9000").encode())
    assert w["namespace"] == sim.NS and w["addresses"] == [{"soap_version": "1.2", "url": "http://mock:9000/soap/sims02"}]
    ops = {o["operation"]: o for o in w["operations"]}
    assert ops["GetLiveMMF"]["category"] == "mmf" and ops["GetWeekAheadForecast"]["category"] == "weekahead"
    assert ops["GetDayAheadForecast"]["soap_action"] == f"{sim.NS}/GetDayAheadForecast"
