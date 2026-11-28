"""SOAP plant APIs: build the request envelope, read the reply into the same JSON shape as rev5 REST.

There is no GSO SOAP specification yet, so the platform uses a "GSO SOAP profile": the rev5 field
names (PlantName, ForecastData, StartTime, ForecastValue, MMFData, MMF1...) inside a SOAP 1.1 or 1.2
envelope. Each plant API is one operation, e.g. GetDayAheadForecast, answered by
GetDayAheadForecastResponse, whose children convert to JSON exactly like a REST reply:

    <GetDayAheadForecastResponse xmlns="urn:gso:lss:ws:v1">
      <ForecastResponse>
        <PlantName>bdws</PlantName> ... <ForecastData><Item><StartTime>...</StartTime>...</Item></ForecastData>
      </ForecastResponse>
    </GetDayAheadForecastResponse>
  ->
    {"ForecastResponse": {"PlantName": "bdws", ..., "ForecastData": [{"StartTime": ...}, ...]}}

So compliance checks, reports, templates and emails work on SOAP plants unchanged. The XML itself is
kept exactly as received; `convert` also returns display lines for the raw-response inspector, each
tagged with the JSON Pointer it became, so findings highlight the XML element that caused them.
"""
from __future__ import annotations

import base64
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from xml.parsers import expat
from xml.sax.saxutils import escape

from . import validation as v

NS_SOAP11 = "http://schemas.xmlsoap.org/soap/envelope/"
NS_SOAP12 = "http://www.w3.org/2003/05/soap-envelope"
NS_WSSE = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
NS_WSU = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
PW_TEXT = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordText"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"
DEFAULT_NAMESPACE = "urn:gso:lss:ws:v1"

# Default operation per plant API under the GSO SOAP profile (admins can override each one).
DEFAULT_OPERATIONS = {
    "rolling": "GetRollingForecast", "dayahead": "GetDayAheadForecast", "weekahead": "GetWeekAheadForecast",
    "fourmonthsahead": "GetFourMonthsAheadForecast", "mmf": "GetLiveMMF",
}
# Elements whose children are always a list, even with one child.
LIST_CONTAINERS = {"ForecastData", "MMFData"}
# Fields that rev5 sends as numbers. XML has no types, so plain decimals become numbers; anything else
# (e.g. "12,5", "NaN") stays text and is flagged by the normal checks.
NUMERIC = re.compile(r"^-?\d+(\.\d+)?([eE][-+]?\d+)?$")
MAX_LINES = 60000

# Pointers for the parts of the XML that are not in the converted JSON.
P_ENVELOPE, P_OP, P_FAULT = "#envelope", "#op", "#fault"


def is_numeric_key(k: str) -> bool:
    return k == "ForecastValue" or (k.upper().startswith("MMF") and k[3:].isdigit())


# ---------------------------------------------------------------- request
def profile(ep) -> dict | None:
    """An endpoint's SOAP settings with GSO SOAP profile defaults filled in, or None for REST."""
    if ep is None or ep.protocol != "soap":
        return None
    op = ep.soap_operation or DEFAULT_OPERATIONS.get(ep.category, "")
    return {"operation": op, "action": ep.soap_action if ep.soap_action is not None else f"{DEFAULT_NAMESPACE}/{op}",
            "version": ep.soap_version or "1.1", "namespace": ep.soap_namespace or DEFAULT_NAMESPACE}


def envelope(operation: str, namespace: str | None, version: str, mnemonic: str,
             wsse: tuple[str, str] | None = None) -> bytes:
    env_ns = NS_SOAP12 if version == "1.2" else NS_SOAP11
    ns = namespace or DEFAULT_NAMESPACE
    header = ""
    if wsse:
        user, pw = wsse
        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        header = (f'<soap:Header><wsse:Security xmlns:wsse="{NS_WSSE}" xmlns:wsu="{NS_WSU}" soap:mustUnderstand="1">'
                  f'<wsse:UsernameToken><wsse:Username>{escape(user)}</wsse:Username>'
                  f'<wsse:Password Type="{PW_TEXT}">{escape(pw)}</wsse:Password>'
                  f'<wsu:Created>{created}</wsu:Created></wsse:UsernameToken></wsse:Security></soap:Header>')
    return (f'<?xml version="1.0" encoding="utf-8"?>'
            f'<soap:Envelope xmlns:soap="{env_ns}" xmlns:gso="{escape(ns)}">{header}'
            f'<soap:Body><gso:{operation}><gso:PlantName>{escape(mnemonic)}</gso:PlantName></gso:{operation}>'
            f'</soap:Body></soap:Envelope>').encode()


def request_headers(version: str, action: str | None) -> dict:
    if version == "1.2":
        ct = "application/soap+xml; charset=utf-8" + (f'; action="{action}"' if action else "")
        return {"Content-Type": ct, "Accept": "application/soap+xml, text/xml"}
    return {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": f'"{action or ""}"',
            "Accept": "text/xml, application/soap+xml"}


def basic_header(user: str, pw: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def looks_like_fault(content: bytes | None) -> bool:
    return bool(content) and b"Fault" in content[:20000] and b"Envelope" in content[:2000]


# ---------------------------------------------------------------- parse
@dataclass
class Node:
    qname: str
    local: str
    ns: str | None
    attrs: dict
    start: int
    end: int = 0
    text: str = ""
    children: list["Node"] = field(default_factory=list)
    nil: bool = False


class XmlError(ValueError):
    pass


def parse(content: bytes) -> Node:
    """Parse with expat, keeping each element's byte range. DTDs are refused (no entity expansion)."""
    p = expat.ParserCreate()
    stack: list[Node] = []
    nsstack: list[dict] = [{"xml": "http://www.w3.org/XML/1998/namespace"}]
    root: list[Node] = []

    def refuse(*_a):
        raise XmlError("DOCTYPE and entity declarations are not allowed")

    def start(name, attrs):
        nsmap = dict(nsstack[-1])
        for k, val in attrs.items():
            if k == "xmlns":
                nsmap[""] = val
            elif k.startswith("xmlns:"):
                nsmap[k[6:]] = val
        nsstack.append(nsmap)
        prefix, _, local = name.rpartition(":")
        n = Node(name, local, nsmap.get(prefix) if prefix else nsmap.get(""), dict(attrs), p.CurrentByteIndex)
        n.nil = any(k.endswith(":nil") and nsmap.get(k.split(":")[0]) == NS_XSI and val in ("true", "1")
                    for k, val in attrs.items())
        if stack:
            stack[-1].children.append(n)
        else:
            root.append(n)
        stack.append(n)

    def end(_name):
        n = stack.pop()
        nsstack.pop()
        n.end = p.CurrentByteIndex

    def chars(data):
        if stack:
            stack[-1].text += data

    p.StartElementHandler, p.EndElementHandler, p.CharacterDataHandler = start, end, chars
    p.StartDoctypeDeclHandler = refuse
    p.EntityDeclHandler = refuse
    try:
        p.Parse(content, True)
    except expat.ExpatError as e:
        raise XmlError(f"Not well-formed XML: {expat.ErrorString(e.code)} at line {e.lineno}, column {e.offset}") from e
    if not root:
        raise XmlError("No XML element found")
    return root[0]


def to_json(n: Node):
    """Element -> JSON value: leaf -> text (numbers for numeric rev5 fields), list containers and
    repeated children -> lists, other elements -> objects keyed by local name."""
    if n.nil:
        return None
    if not n.children:
        t = n.text.strip()
        if is_numeric_key(n.local):
            if not t:
                return None
            return float(t) if NUMERIC.match(t) else t
        return t
    if n.local in LIST_CONTAINERS:
        return [to_json(c) for c in n.children]
    out: dict = {}
    rep = _repeated(n)
    for c in n.children:
        if c.local in rep:
            out.setdefault(c.local, []).append(to_json(c))
        else:
            out[c.local] = to_json(c)
    return out


def _repeated(n: Node) -> set[str]:
    seen, rep = set(), set()
    for c in n.children:
        (rep if c.local in seen else seen).add(c.local)
    return rep


def _pointers(n: Node, ptr: str, out: dict[int, str]):
    """JSON Pointer for every element under the payload root, matching to_json's shape."""
    out[id(n)] = ptr
    if not n.children:
        return
    if n.local in LIST_CONTAINERS:
        for i, c in enumerate(n.children):
            _pointers(c, f"{ptr}/{i}", out)
        return
    rep, idx = _repeated(n), {}
    for c in n.children:
        key = c.local.replace("~", "~0").replace("/", "~1")
        if c.local in rep:
            i = idx.get(c.local, 0)
            idx[c.local] = i + 1
            _pointers(c, f"{ptr}/{key}/{i}", out)
        else:
            _pointers(c, f"{ptr}/{key}", out)


# ---------------------------------------------------------------- display lines
def _open(n: Node) -> str:
    attrs = "".join(f' {k}="{escape(str(val), {chr(34): "&quot;"})}"' for k, val in n.attrs.items())
    return f"<{n.qname}{attrs}>"


def _lines(n: Node, ptr: dict[int, str], default: str, indent: int, out: list[dict]):
    if len(out) >= MAX_LINES:
        return
    path = ptr.get(id(n), default)
    if not n.children:
        t = n.text.strip()
        parts = [{"text": _open(n), "cls": "j-key", "path": path}]
        if t:
            parts.append({"text": escape(t), "cls": "j-num" if NUMERIC.match(t) else "j-str", "path": path})
        parts.append({"text": f"</{n.qname}>", "cls": "j-key", "path": path})
        out.append({"indent": indent, "path": path, "parts": parts})
        return
    if (len(n.children) <= 10 and all(not c.children for c in n.children) and n.local not in LIST_CONTAINERS
            and n.local != "Fault"):
        parts = [{"text": _open(n), "cls": "j-key"}]
        for c in n.children:
            cp = ptr.get(id(c), path)
            t = c.text.strip()
            parts.append({"text": _open(c), "cls": "j-key", "path": cp})
            if t:
                parts.append({"text": escape(t), "cls": "j-num" if NUMERIC.match(t) else "j-str", "path": cp})
            parts.append({"text": f"</{c.qname}>", "cls": "j-key", "path": cp})
        parts.append({"text": f"</{n.qname}>", "cls": "j-key"})
        out.append({"indent": indent, "path": path, "parts": parts})
        return
    head = [{"text": _open(n), "cls": "j-key"}]
    if n.local in LIST_CONTAINERS:
        head.append({"text": f"  {len(n.children)} items", "cls": "j-meta"})
    out.append({"indent": indent, "path": path, "parts": head})
    for c in n.children:
        _lines(c, ptr, path, indent + 1, out)
    out.append({"indent": indent, "path": path, "close": True, "parts": [{"text": f"</{n.qname}>", "cls": "j-key"}]})


# ---------------------------------------------------------------- convert
@dataclass
class SoapResult:
    body: dict | None = None       # the payload as rev5-shaped JSON (None when unreadable or a Fault)
    flags: list = field(default_factory=list)
    lines: list[dict] | None = None  # inspector display lines (None when the XML does not parse)
    fault: dict | None = None


def _child(n: Node, local: str) -> Node | None:
    return next((c for c in n.children if c.local == local), None)


def _fault_info(f: Node) -> dict:
    def txt(*names):
        cur: Node | None = f
        for nm in names:
            cur = _child(cur, nm) if cur else None
        return (cur.text.strip() if cur and not cur.children else
                " ".join(x.text.strip() for x in cur.children) if cur else None) or None
    # SOAP 1.1: faultcode/faultstring; SOAP 1.2: Code/Value and Reason/Text.
    code = txt("faultcode") or txt("Code", "Value")
    reason = txt("faultstring") or txt("Reason", "Text") or txt("Reason")
    detail = txt("detail") or txt("Detail")
    return {"code": code, "reason": reason, "detail": detail}


def convert(content: bytes, operation: str | None, namespace: str | None, version: str | None) -> SoapResult:
    r = SoapResult()

    def flag(rule, sev, msg, detail=None, paths=None):
        r.flags.append(v.Flag(rule, sev, msg, detail, list(paths or [])))

    try:
        root = parse(content)
    except XmlError as e:
        flag("XML_INVALID", v.MAJOR, f"Response is not readable XML: {e}", paths=[""])
        return r

    ptr: dict[int, str] = {}
    lines: list[dict] = []
    if root.local != "Envelope" or root.ns not in (NS_SOAP11, NS_SOAP12):
        flag("SOAP_ENVELOPE", v.MAJOR, f"Root element is <{root.qname}>, not a SOAP Envelope", paths=[P_ENVELOPE])
        ptr[id(root)] = P_ENVELOPE
        _lines(root, ptr, P_ENVELOPE, 0, lines)
        r.lines = lines
        return r
    want_ns = NS_SOAP12 if version == "1.2" else NS_SOAP11
    if root.ns != want_ns:
        flag("SOAP_VERSION", v.MINOR, f"Reply is SOAP {'1.2' if root.ns == NS_SOAP12 else '1.1'}, "
             f"this API is set to SOAP {version or '1.1'}", paths=[P_ENVELOPE])
    body = _child(root, "Body")
    payload = next((c for c in body.children), None) if body is not None else None
    ptr[id(root)] = P_ENVELOPE
    if body is None or payload is None:
        flag("SOAP_ENVELOPE", v.MAJOR, "The SOAP Body is missing or empty", paths=[P_ENVELOPE])
    elif payload.local == "Fault":
        r.fault = _fault_info(payload)
        _pointers(payload, P_FAULT, ptr)
        f = r.fault
        flag("SOAP_FAULT", v.MAJOR, f"SOAP Fault {f['code'] or ''}: {f['reason'] or 'no reason given'}".replace("  ", " "),
             {k: val for k, val in f.items() if val}, paths=[P_FAULT])
    else:
        _pointers(payload, "", ptr)
        ptr[id(payload)] = P_OP
        if operation and payload.local != f"{operation}Response":
            flag("SOAP_OPERATION", v.MINOR, f"Reply element is <{payload.local}>, expected <{operation}Response>",
                 paths=[P_OP])
        ns = namespace or DEFAULT_NAMESPACE
        if payload.ns != ns:
            flag("SOAP_NAMESPACE", v.MINOR, f"Reply namespace is '{payload.ns or 'none'}', expected '{ns}'",
                 {"served": payload.ns, "expected": ns}, paths=[P_OP])
        val = to_json(payload)
        r.body = val if isinstance(val, dict) else {payload.local: val}
    _lines(root, ptr, P_ENVELOPE, 0, lines)
    r.lines = lines
    return r


# ---------------------------------------------------------------- WSDL
NS_WSDL = "http://schemas.xmlsoap.org/wsdl/"
NS_WSDL_SOAP11 = "http://schemas.xmlsoap.org/wsdl/soap/"
NS_WSDL_SOAP12 = "http://schemas.xmlsoap.org/wsdl/soap12/"


def guess_category(op: str) -> str | None:
    o = op.lower()
    for cat, key in (("fourmonthsahead", "fourmonth"), ("weekahead", "week"), ("dayahead", "day"),
                     ("rolling", "rolling"), ("mmf", "mmf")):
        if key in o:
            return cat
    return None


def parse_wsdl(content: bytes) -> dict:
    """Operations, SOAPActions, SOAP version and service address from a WSDL 1.1 document."""
    if b"<!DOCTYPE" in content[:4000] or b"<!ENTITY" in content:
        raise XmlError("DOCTYPE and entity declarations are not allowed")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise XmlError(f"Not well-formed XML: {e}") from e
    if root.tag != f"{{{NS_WSDL}}}definitions":
        raise XmlError("This is not a WSDL 1.1 document (no wsdl:definitions)")
    tns = root.get("targetNamespace")
    ops: dict[tuple[str, str], dict] = {}
    for b in root.findall(f"{{{NS_WSDL}}}binding"):
        for ver, sns in (("1.1", NS_WSDL_SOAP11), ("1.2", NS_WSDL_SOAP12)):
            if b.find(f"{{{sns}}}binding") is None:
                continue
            for op in b.findall(f"{{{NS_WSDL}}}operation"):
                so = op.find(f"{{{sns}}}operation")
                name = op.get("name")
                ops[(name, ver)] = {"operation": name, "soap_action": so.get("soapAction") if so is not None else None,
                                    "soap_version": ver, "category": guess_category(name or "")}
    addresses = []
    for svc in root.findall(f"{{{NS_WSDL}}}service"):
        for port in svc.findall(f"{{{NS_WSDL}}}port"):
            for ver, sns in (("1.1", NS_WSDL_SOAP11), ("1.2", NS_WSDL_SOAP12)):
                a = port.find(f"{{{sns}}}address")
                if a is not None and a.get("location"):
                    addresses.append({"soap_version": ver, "url": a.get("location")})
    if not ops:
        raise XmlError("The WSDL has no SOAP bindings")
    return {"namespace": tns, "addresses": addresses,
            "operations": sorted(ops.values(), key=lambda o: (o["soap_version"], o["operation"] or ""))}
