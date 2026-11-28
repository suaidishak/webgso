"""Per-column filter expressions for report tables, the same in SQL and in Python.

  text    contains (case-insensitive) · =abc exact · !abc does not contain
  number  5 (equals) · >5 · >=5 · <5 · <=5 · !=5 · 1..10 (between, inclusive)
  date    2026-11-28 05:30 contains (prefix or any part) · >2026-11-28 05:00 · <... · a..b
  yes/no  yes | no
  any     blank (empty cell) · !blank (not empty)
A number or date expression that isn't a number or date falls back to "contains" on the shown text.
"""
import re
from datetime import datetime

_OP = re.compile(r"^(>=|<=|!=|>|<|=)\s*(.+)$")
_RANGE = re.compile(r"^(.+?)\s*\.\.\s*(.+)$")
_SQL_OPS = {">": ">", ">=": ">=", "<": "<", "<=": "<=", "=": "=", "!=": "<>"}


def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _dt(s: str) -> datetime | None:
    s = s.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def _parse(typ: str, expr: str):
    """Returns ("blank"|"notblank"|"bool"|"cmp"|"range"|"eq"|"contains"|"not_contains", payload)."""
    e = expr.strip()
    low = e.lower()
    if low in ("blank", "(blank)", "empty"):
        return "blank", None
    if low in ("!blank", "not blank", "!empty"):
        return "notblank", None
    if typ == "bool":
        if low in ("yes", "y", "true", "1", "valid"):
            return "bool", True
        if low in ("no", "n", "false", "0", "invalid"):
            return "bool", False
        return "contains", e
    conv = _num if typ in ("num", "int") else _dt if typ == "dt" else None
    if conv:
        m = _RANGE.match(e)
        if m and conv(m.group(1)) is not None and conv(m.group(2)) is not None:
            return "range", (conv(m.group(1)), conv(m.group(2)))
        m = _OP.match(e)
        if m and conv(m.group(2)) is not None:
            return "cmp", (m.group(1), conv(m.group(2)))
        if typ != "dt" and conv(e) is not None:
            return "cmp", ("=", conv(e))
        return "contains", e
    if e.startswith("="):
        return "eq", e[1:].strip()
    if e.startswith("!") and len(e) > 1:
        return "not_contains", e[1:].strip()
    return "contains", e


def _text_sql(col: str, typ: str) -> str:
    if typ == "dt":
        return f"to_char({col}, 'YYYY-MM-DD HH24:MI:SS')"
    if typ == "bool":
        return f"(CASE WHEN {col} THEN 'yes' WHEN NOT {col} THEN 'no' END)"
    return f"CAST({col} AS TEXT)"


def sql_condition(col: str, typ: str, expr: str, name: str) -> tuple[str, dict]:
    kind, v = _parse(typ, expr)
    if kind == "blank":
        return (f"({col} IS NULL OR CAST({col} AS TEXT) = '')", {})
    if kind == "notblank":
        return (f"({col} IS NOT NULL AND CAST({col} AS TEXT) <> '')", {})
    if kind == "bool":
        return (f"{col} = :{name}", {name: v})
    if kind == "cmp":
        return (f"{col} {_SQL_OPS[v[0]]} :{name}", {name: v[1]})
    if kind == "range":
        return (f"{col} BETWEEN :{name}a AND :{name}b", {f"{name}a": v[0], f"{name}b": v[1]})
    if kind == "eq":
        return (f"lower({_text_sql(col, typ)}) = lower(:{name})", {name: v})
    like = "%" + v.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    if kind == "not_contains":
        return (f"({col} IS NULL OR {_text_sql(col, typ)} NOT ILIKE :{name})", {name: like})
    return (f"{_text_sql(col, typ)} ILIKE :{name}", {name: like})


def _as_text(v, typ: str) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return str(v)


def py_match(v, typ: str, expr: str) -> bool:
    kind, p = _parse(typ, expr)
    if kind == "blank":
        return v is None or v == ""
    if kind == "notblank":
        return not (v is None or v == "")
    if kind == "bool":
        return v is p
    if kind in ("cmp", "range"):
        x = v if isinstance(v, datetime) else _num(str(v)) if v is not None and not isinstance(v, bool) else None
        if x is None or (typ == "dt") != isinstance(x, datetime):
            return False
        if kind == "range":
            return p[0] <= x <= p[1]
        op, ref = p
        return {">": x > ref, ">=": x >= ref, "<": x < ref, "<=": x <= ref, "=": x == ref, "!=": x != ref}[op]
    t = _as_text(v, typ).lower()
    if kind == "eq":
        return t == p.lower()
    if kind == "not_contains":
        return p.lower() not in t
    return p.lower() in t
