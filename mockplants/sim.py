"""Simulated plants: 5 SOAP plants and 20 REST plants with made-up names, some with deliberate faults.

Every name is fictitious (Malay words for "example", "invented", "illusion"...): none is a real LSS plant.
The platform reads GET /sim/catalog and adds these plants marked as simulated.

SOAP plants follow the platform's "GSO SOAP profile": one service per plant at /soap/{mnem}
(WSDL at /soap/{mnem}?wsdl), one operation per plant API, rev5 field names inside the envelope.
"""
from xml.sax.saxutils import escape

NS = "urn:gso:lss:ws:v1"
NS_SOAP11 = "http://schemas.xmlsoap.org/soap/envelope/"
NS_SOAP12 = "http://www.w3.org/2003/05/soap-envelope"
OPERATIONS = {
    "rolling": "GetRollingForecast", "dayahead": "GetDayAheadForecast", "weekahead": "GetWeekAheadForecast",
    "fourmonthsahead": "GetFourMonthsAheadForecast", "mmf": "GetLiveMMF",
}
OP_TO_CAT = {v: k for k, v in OPERATIONS.items()}

# mnemonic: (name, region, MW, MMF stations, SOAP version, faults, note)
SOAP_PLANTS = {
    "sims01": ("Bukit Contoh Solar", "Northern", 48, 2, "1.1", set(), "Healthy SOAP 1.1 plant."),
    "sims02": ("Sungai Rekaan Solar", "Central", 35, 2, "1.2", {"wsse"},
               "SOAP 1.2 with WS-Security username/password (stored on the plant)."),
    "sims03": ("Padang Maya Solar", "Southern", 60, 3, "1.1", {"fault:dayahead"},
               "Fault: Day-ahead answers with a SOAP Fault (forecast not generated)."),
    "sims04": ("Tanjung Ilusi Solar", "East Coast", 42, 2, "1.1", {"oldns", "comma:dayahead"},
               "Faults: replies in an old namespace; Day-ahead values use decimal commas (12,5)."),
    "sims05": ("Kuala Fiksyen Solar", "Northern", 30, 2, "1.1", {"missing:rolling", "badxml:mmf"},
               "Faults: Rolling items miss Unit/EndTime; MMF reply is cut off (invalid XML)."),
}
WSSE_USER, WSSE_PASS = "gso-sim", "rekaan-2026"

# mnemonic: (name, region, MW, fault or None)
REST_PLANTS = {
    "simr01": ("Teluk Khayalan Solar", "Northern", 45, None),
    "simr02": ("Bandar Andaian Solar", "Central", 50, None),
    "simr03": ("Kampung Sampel Solar", "Southern", 38, "down"),
    "simr04": ("Ladang Uji Solar", "East Coast", 55, None),
    "simr05": ("Pulau Demo Solar", "Northern", 32, None),
    "simr06": ("Bukit Simulasi Solar", "Central", 47, "slow"),
    "simr07": ("Sri Contoh Solar", "Southern", 41, None),
    "simr08": ("Taman Rekaan Solar", "East Coast", 36, None),
    "simr09": ("Batu Maya Solar", "Northern", 52, "short"),
    "simr10": ("Air Fiksyen Solar", "Central", 44, None),
    "simr11": ("Gunung Andaian Solar", "Southern", 58, None),
    "simr12": ("Lembah Ujian Solar", "East Coast", 33, "mmf"),
    "simr13": ("Permatang Sampel Solar", "Northern", 49, None),
    "simr14": ("Kota Khayal Solar", "Central", 40, None),
    "simr15": ("Seri Ilusi Solar", "Southern", 46, "name"),
    "simr16": ("Bagan Rekaan Solar", "East Coast", 39, None),
    "simr17": ("Pantai Contoh Solar", "Northern", 54, None),
    "simr18": ("Paya Maya Solar", "Central", 37, "key"),
    "simr19": ("Changkat Demo Solar", "Southern", 43, None),
    "simr20": ("Simpang Uji Solar", "East Coast", 51, "negative"),
}
REST_KEY = "sim-key-5678"
REST_NOTES = {
    "down": "Fault: always answers 503 (down).", "slow": "Fault: every reply takes 4 seconds.",
    "short": "Fault: Day-ahead is 10 slots short.", "mmf": "Fault: MMF answers HTTP 500.",
    "name": "Fault: forecasts carry the wrong PlantName.",
    "key": "Needs an X-API-Key header (stored on the plant).", "negative": "Fault: negative Rolling values at night.",
}
CAPACITY = {m: p[2] for m, p in SOAP_PLANTS.items()} | {m: p[2] for m, p in REST_PLANTS.items()}
PATHS = {"rolling": "forecast/rolling", "dayahead": "forecast/dayahead", "weekahead": "forecast/weekahead",
         "fourmonthsahead": "forecast/fourmonthsahead", "mmf": "mmf/live"}


def catalog() -> dict:
    out = []
    for m, (name, region, mw, st, ver, faults, note) in SOAP_PLANTS.items():
        out.append({"mnemonic": m, "name": name, "region": region, "capacity_mw": mw, "mmf_station_count": st,
                    "notes": f"Simulated SOAP plant. {note}",
                    "auth": {"method": "wsse", "name": WSSE_USER, "secret": WSSE_PASS} if "wsse" in faults else None,
                    "endpoints": [{"category": c, "path": f"/soap/{m}", "protocol": "soap", "soap_operation": op,
                                   "soap_action": f"{NS}/{op}", "soap_version": ver, "soap_namespace": NS}
                                  for c, op in OPERATIONS.items()]})
    for m, (name, region, mw, fault) in REST_PLANTS.items():
        out.append({"mnemonic": m, "name": name, "region": region, "capacity_mw": mw, "mmf_station_count": 2,
                    "notes": "Simulated REST plant." + (f" {REST_NOTES[fault]}" if fault else " Healthy."),
                    "auth": {"method": "header", "name": "X-API-Key", "secret": REST_KEY} if fault == "key" else None,
                    "endpoints": [{"category": c, "path": f"/{m}/{p}", "protocol": "rest"} for c, p in PATHS.items()]})
    return {"plants": out}


def rest_fault(mnem: str) -> str | None:
    p = REST_PLANTS.get(mnem)
    return p[3] if p else None


# ---------------------------------------------------------------- SOAP
def wsdl(mnem: str, base: str) -> str:
    ver = SOAP_PLANTS[mnem][4]
    sns = "http://schemas.xmlsoap.org/wsdl/soap12/" if ver == "1.2" else "http://schemas.xmlsoap.org/wsdl/soap/"
    ops = OPERATIONS.values()
    msgs = "".join(f'<wsdl:message name="{o}Request"><wsdl:part name="parameters" element="tns:{o}"/></wsdl:message>'
                   f'<wsdl:message name="{o}Response"><wsdl:part name="parameters" element="tns:{o}Response"/></wsdl:message>'
                   for o in ops)
    port_ops = "".join(f'<wsdl:operation name="{o}"><wsdl:input message="tns:{o}Request"/>'
                       f'<wsdl:output message="tns:{o}Response"/></wsdl:operation>' for o in ops)
    bind_ops = "".join(f'<wsdl:operation name="{o}"><soap:operation soapAction="{NS}/{o}"/>'
                       f'<wsdl:input><soap:body use="literal"/></wsdl:input>'
                       f'<wsdl:output><soap:body use="literal"/></wsdl:output></wsdl:operation>' for o in ops)
    elems = "".join(f'<xs:element name="{o}"><xs:complexType><xs:sequence><xs:element name="PlantName" type="xs:string"/>'
                    f'</xs:sequence></xs:complexType></xs:element><xs:element name="{o}Response" type="xs:anyType"/>'
                    for o in ops)
    return f'''<?xml version="1.0" encoding="utf-8"?>
<wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/" xmlns:soap="{sns}"
    xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:tns="{NS}" targetNamespace="{NS}" name="LssPlantService">
  <wsdl:types><xs:schema targetNamespace="{NS}" elementFormDefault="qualified">{elems}</xs:schema></wsdl:types>
  {msgs}
  <wsdl:portType name="LssPlantPort">{port_ops}</wsdl:portType>
  <wsdl:binding name="LssPlantBinding" type="tns:LssPlantPort">
    <soap:binding style="document" transport="http://schemas.xmlsoap.org/soap/http"/>{bind_ops}
  </wsdl:binding>
  <wsdl:service name="LssPlantService">
    <wsdl:port name="LssPlantPort" binding="tns:LssPlantBinding"><soap:address location="{base}/soap/{mnem}"/></wsdl:port>
  </wsdl:service>
</wsdl:definitions>
'''


def _env(ver: str, inner: str) -> str:
    ens = NS_SOAP12 if ver == "1.2" else NS_SOAP11
    return (f'<?xml version="1.0" encoding="utf-8"?>\n<soap:Envelope xmlns:soap="{ens}">\n  <soap:Body>\n'
            f'{inner}\n  </soap:Body>\n</soap:Envelope>\n')


def fault(ver: str, code: str, reason: str, detail: str | None = None) -> str:
    if ver == "1.2":
        inner = (f'    <soap:Fault>\n      <soap:Code><soap:Value>soap:{code}</soap:Value></soap:Code>\n'
                 f'      <soap:Reason><soap:Text xml:lang="en">{escape(reason)}</soap:Text></soap:Reason>\n'
                 + (f'      <soap:Detail>{escape(detail)}</soap:Detail>\n' if detail else "") + '    </soap:Fault>')
    else:
        inner = (f'    <soap:Fault>\n      <faultcode>soap:{code}</faultcode>\n      <faultstring>{escape(reason)}</faultstring>\n'
                 + (f'      <detail>{escape(detail)}</detail>\n' if detail else "") + '    </soap:Fault>')
    return _env(ver, inner)


def _leaf(k, v, ind):
    return f"{ind}<{k}>{escape(str(v))}</{k}>"


def forecast_xml(mnem: str, ver: str, op: str, resp: dict, faults: set, cat: str) -> str:
    ns = "urn:gso:lss:ws:v0" if "oldns" in faults else NS
    fr = resp["ForecastResponse"]
    items = []
    for i, p in enumerate(fr["ForecastData"]):
        p = dict(p)
        if f"comma:{cat}" in faults:
            p["ForecastValue"] = str(p["ForecastValue"]).replace(".", ",")
        if f"missing:{cat}" in faults and i % 7 == 3:
            p.pop("Unit", None)
            if i % 14 == 3:
                p.pop("EndTime", None)
        items.append("          <Item>" + "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in p.items()) + "</Item>")
    inner = (f'    <{op}Response xmlns="{ns}">\n      <ForecastResponse>\n'
             f'{_leaf("PlantName", fr["PlantName"], "        ")}\n{_leaf("ForecastCategory", fr["ForecastCategory"], "        ")}\n'
             f'        <ForecastData>\n' + "\n".join(items) + '\n        </ForecastData>\n'
             f'      </ForecastResponse>\n    </{op}Response>')
    return _env(ver, inner)


def mmf_xml(mnem: str, ver: str, op: str, resp: dict, faults: set) -> str:
    ns = "urn:gso:lss:ws:v0" if "oldns" in faults else NS
    mr = resp["MMFResponse"]
    items = ["          <Item>" + "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in it.items()) + "</Item>"
             for it in mr["MMFData"]]
    inner = (f'    <{op}Response xmlns="{ns}">\n      <MMFResponse>\n{_leaf("PlantName", mr["PlantName"], "        ")}\n'
             f'        <MMFData>\n' + "\n".join(items) + '\n        </MMFData>\n      </MMFResponse>\n'
             f'    </{op}Response>')
    xml = _env(ver, inner)
    if "badxml:mmf" in faults:
        xml = xml[: int(len(xml) * 0.6)]  # connection cut mid-reply
    return xml


def wsse_ok(body: str) -> bool:
    return f"<wsse:Username>{WSSE_USER}</wsse:Username>" in body and f">{WSSE_PASS}</wsse:Password>" in body
