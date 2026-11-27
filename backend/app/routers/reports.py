"""Reports: polled data as spreadsheet rows, viewable in pages and exportable as CSV.

Every view starts from the same selection of polls (poll_attempt rows), filtered by plant, API, poll
datetime range, a single poll, or a saved Poll now run, optionally keeping only the latest poll per plant
and API. The views then expand those polls into rows:

  points  one row per forecast point (from forecast_point, typed)
  mmf     one row per MMF station reading (from mmf_reading, typed)
  json    the response exactly as received, flattened: one row per record (see flatten.py)
  polls   one row per poll: status, verdict, flag counts

Unchanged forecast responses re-use the earlier raw row and snapshot (see poller._ingest), so points are
reached through the poll's raw_response_id, which makes every poll show its points, changed or not.
"""
import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_user, visible_plant_ids
from ..db import SessionLocal, get_session
from ..flatten import ROW_KEY, flatten_text
from ..models import CATEGORIES, AppUser, Plant, PollRun

router = APIRouter(prefix="/reports", tags=["reports"])

VIEWS = ("points", "mmf", "json", "polls")
JSON_POLL_CAP = 2000  # the json view flattens in Python; beyond this many polls, narrow the filters


@dataclass
class Col:
    key: str
    label: str
    type: str  # text | num | dt | bool | int
    sql: str | None = None


COLS: dict[str, list[Col]] = {
    "points": [
        Col("plant", "Plant", "text", "sel.plant"),
        Col("api", "API", "text", "sel.category"),
        Col("polled_at", "Polled at", "dt", "sel.started_at"),
        Col("poll_id", "Poll ID", "int", "sel.id"),
        Col("poll_valid", "Poll valid", "bool", "sel.is_valid"),
        Col("slot_start", "Slot start", "dt", "fp.start_time"),
        Col("slot_end", "Slot end", "dt", "fp.end_time"),
        Col("value_mw", "Value (MW)", "num", "fp.value_mw"),
    ],
    "mmf": [
        Col("plant", "Plant", "text", "sel.plant"),
        Col("polled_at", "Polled at", "dt", "sel.started_at"),
        Col("poll_id", "Poll ID", "int", "sel.id"),
        Col("poll_valid", "Poll valid", "bool", "sel.is_valid"),
        Col("station", "Station", "int", "m.station_no"),
        Col("parameter", "Parameter", "text", "m.parameter"),
        Col("measured_at", "Measured at", "dt", "m.measured_at"),
        Col("value", "Value", "num", "m.value"),
        Col("unit", "Unit", "text", "m.unit"),
        Col("standard", "rev5 parameter", "bool", "m.is_standard"),
    ],
    "polls": [
        Col("plant", "Plant", "text", "sel.plant"),
        Col("api", "API", "text", "sel.category"),
        Col("polled_at", "Polled at", "dt", "sel.started_at"),
        Col("poll_id", "Poll ID", "int", "sel.id"),
        Col("http_status", "HTTP", "int", "sel.http_status"),
        Col("poll_valid", "Valid", "bool", "sel.is_valid"),
        Col("major_flags", "Major flags", "int", "sel.major_flags"),
        Col("minor_flags", "Minor flags", "int", "sel.minor_flags"),
        Col("latency_ms", "Latency (ms)", "int", "sel.latency_ms"),
        Col("bytes", "Bytes", "int", "sel.bytes"),
        Col("error", "Error", "text", "sel.error"),
        Col("url", "URL", "text", "sel.url"),
    ],
}
JSON_BASE = [Col("plant", "Plant", "text"), Col("api", "API", "text"), Col("polled_at", "Polled at", "dt"),
             Col("poll_id", "Poll ID", "int"), Col("poll_valid", "Poll valid", "bool"),
             Col(ROW_KEY, "Row", "int")]

FROM = {
    "points": """sel JOIN LATERAL (SELECT fs.id FROM forecast_snapshot fs WHERE fs.raw_response_id = sel.raw_response_id
                 ORDER BY fs.id LIMIT 1) snap ON true JOIN forecast_point fp ON fp.snapshot_id = snap.id""",
    "mmf": "sel JOIN mmf_reading m ON m.poll_attempt_id = sel.id",
    "polls": "sel",
}
DEFAULT_ORDER = {
    "points": "sel.plant, sel.category, sel.started_at, sel.id, fp.start_time",
    "mmf": "sel.plant, sel.started_at, sel.id, m.is_standard DESC, m.parameter, m.station_no",
    "polls": "sel.plant, sel.category, sel.started_at, sel.id",
}


@dataclass
class Filters:
    view: str
    plants: list[str]
    categories: list[str]
    start: datetime | None
    end: datetime | None
    attempt: int | None
    run: int | None
    latest: bool
    sort: str | None
    desc: bool


def filters(view: str = "points", plants: str | None = None, category: str | None = None,
            start: datetime | None = Query(None, alias="from"), end: datetime | None = Query(None, alias="to"),
            attempt: int | None = None, run: int | None = None, latest: bool = False,
            sort: str | None = None, dir: str = "asc") -> Filters:
    if view not in VIEWS:
        raise HTTPException(400, f"view must be one of {', '.join(VIEWS)}")
    cats = [c for c in (category or "").split(",") if c and c != "all"]
    bad = [c for c in cats if c not in CATEGORIES]
    if bad:
        raise HTTPException(400, f"Unknown API: {', '.join(bad)}")
    pl = [p.strip().lower() for p in (plants or "").split(",") if p.strip() and p.strip() != "all"]
    return Filters(view, pl, cats, start, end, attempt, run, latest, sort, dir == "desc")


async def _selection(f: Filters, user: AppUser, s: AsyncSession) -> tuple[str, dict]:
    """SQL for the selected polls (a CTE named sel) and its parameters, limited to the user's plants."""
    ids = list(await visible_plant_ids(user, s))
    if f.plants:
        wanted = (await s.execute(select(Plant.id).where(Plant.mnemonic.in_(f.plants)))).scalars().all()
        ids = [i for i in ids if i in set(wanted)]
    where, params = ["a.plant_id = ANY(:ids)"], {"ids": ids}
    cats = f.categories
    if f.view == "points":
        cats = [c for c in (cats or CATEGORIES) if c != "mmf"] or ["none"]  # MMF has no forecast points
    elif f.view == "mmf":
        cats = ["mmf"] if not cats or "mmf" in cats else ["none"]
    if cats:
        where.append("a.category = ANY(:cats)")
        params["cats"] = cats
    if f.start:
        where.append("a.started_at >= :start")
        params["start"] = f.start
    if f.end:
        where.append("a.started_at <= :end")
        params["end"] = f.end
    if f.attempt:
        where.append("a.id = :attempt")
        params["attempt"] = f.attempt
    if f.run:
        run = await s.get(PollRun, f.run)
        if run is None:
            raise HTTPException(404, "Poll now run not found")
        where.append("a.id = ANY(:run_ids)")
        params["run_ids"] = [r["attempt_id"] for r in (run.results or []) if r.get("attempt_id")]
    cols = """a.id, a.plant_id, p.mnemonic AS plant, a.category, a.started_at, a.raw_response_id, a.is_valid,
              a.http_status, a.latency_ms, a.bytes, a.major_flags, a.minor_flags, a.error, a.url"""
    base = f"FROM poll_attempt a JOIN plant p ON p.id = a.plant_id WHERE {' AND '.join(where)}"
    if f.latest:
        sql = f"SELECT DISTINCT ON (a.plant_id, a.category) {cols} {base} " \
              "ORDER BY a.plant_id, a.category, a.started_at DESC, a.id DESC"
    else:
        sql = f"SELECT {cols} {base}"
    return f"WITH sel AS ({sql})", params


def _order(f: Filters, cols: list[Col]) -> str:
    by = {c.key: c.sql for c in cols}
    default = DEFAULT_ORDER[f.view]
    if f.sort in by:
        return f"{by[f.sort]} {'DESC' if f.desc else 'ASC'} NULLS LAST, {default}"
    return default


def _sql_query(f: Filters, cte: str) -> str:
    cols = COLS[f.view]
    select_list = ", ".join(f"{c.sql} AS {c.key}" for c in cols)
    return f"{cte} SELECT {select_list} FROM {FROM[f.view]} ORDER BY {_order(f, cols)}"


async def _json_rows(f: Filters, cte: str, params: dict, s: AsyncSession) -> tuple[list[Col], list[dict], bool]:
    polls = (await s.execute(text(f"{cte} SELECT sel.*, r.body_text FROM sel "
                                  "LEFT JOIN raw_response r ON r.id = sel.raw_response_id "
                                  f"ORDER BY {DEFAULT_ORDER['polls']} LIMIT {JSON_POLL_CAP + 1}"), params)).mappings().all()
    truncated = len(polls) > JSON_POLL_CAP
    cache: dict[int, list[dict]] = {}
    keys: dict[str, None] = {}
    rows = []
    for p in polls[:JSON_POLL_CAP]:
        rid = p["raw_response_id"]
        if rid not in cache:
            cache[rid] = flatten_text(p["body_text"])[1] if rid else []
        flat = cache[rid]
        base = {"plant": p["plant"], "api": p["category"], "polled_at": p["started_at"], "poll_id": p["id"],
                "poll_valid": p["is_valid"]}
        if not flat:
            rows.append({**base, "error": p["error"] or "No response body stored"})
            keys["error"] = None
        for r in flat:
            keys.update(dict.fromkeys(k for k in r if k != ROW_KEY))
            rows.append({**base, **r})
    cols = JSON_BASE + [Col(k, k, "text") for k in keys]
    if f.sort in {c.key for c in cols}:
        k = f.sort
        present = [r for r in rows if r.get(k) is not None]
        missing = [r for r in rows if r.get(k) is None]
        present.sort(key=lambda r: _sortable(r[k]), reverse=f.desc)
        rows = present + missing
    return cols, rows, truncated


def _sortable(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return (0, v, "")
    try:
        return (0, float(v), "")
    except (TypeError, ValueError):
        return (1, 0, str(v))


def _cols_out(cols: list[Col]) -> list[dict]:
    return [{"key": c.key, "label": c.label, "type": c.type} for c in cols]


@router.get("/rows")
async def rows(f: Filters = Depends(filters), limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
               user: AppUser = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """One page of the report as JSON, with the total row count."""
    cte, params = await _selection(f, user, s)
    polls = (await s.execute(text(f"{cte} SELECT count(*) FROM sel"), params)).scalar_one()
    if f.view == "json":
        cols, all_rows, truncated = await _json_rows(f, cte, params, s)
        return {"columns": _cols_out(cols), "rows": all_rows[offset:offset + limit], "total": len(all_rows),
                "polls": polls, "truncated": truncated}
    total = (await s.execute(text(f"{cte} SELECT count(*) FROM {FROM[f.view]}"), params)).scalar_one()
    data = (await s.execute(text(f"{_sql_query(f, cte)} LIMIT :limit OFFSET :offset"),
                            {**params, "limit": limit, "offset": offset})).mappings().all()
    return {"columns": _cols_out(COLS[f.view]), "rows": [dict(r) for r in data], "total": total,
            "polls": polls, "truncated": False}


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _filename(f: Filters) -> str:
    parts = ["gso", f.view]
    if f.attempt:
        parts.append(f"poll{f.attempt}")
    if f.run:
        parts.append(f"run{f.run}")
    parts.append("-".join(f.plants) if f.plants else "fleet")
    parts.append("-".join(f.categories) if f.categories else "allapis")
    if f.latest:
        parts.append("latest")
    if f.start:
        parts.append(f.start.strftime("%Y%m%d%H%M"))
    if f.end:
        parts.append("to" + f.end.strftime("%Y%m%d%H%M"))
    return re.sub(r"[^A-Za-z0-9_.-]", "", "_".join(parts))[:150] + ".csv"


@router.get("/export.csv")
async def export_csv(f: Filters = Depends(filters), user: AppUser = Depends(current_user),
                     s: AsyncSession = Depends(get_session)):
    """The whole report as CSV (UTF-8 with BOM so Excel opens it cleanly), streamed from the database."""
    cte, params = await _selection(f, user, s)
    if f.view == "json":
        cols, all_rows, _ = await _json_rows(f, cte, params, s)
        source = None
    else:
        cols, all_rows, source = COLS[f.view], None, _sql_query(f, cte)

    def line(values) -> str:
        buf = io.StringIO()
        csv.writer(buf).writerow(values)
        return buf.getvalue()

    async def gen():
        yield "\ufeff" + line(c.label for c in cols)
        if all_rows is not None:
            for r in all_rows:
                yield line(_cell(r.get(c.key)) for c in cols)
            return
        # Own session: the request's session closes before a streamed body is sent.
        async with SessionLocal() as s2:
            result = await s2.stream(text(source), params)
            batch = []
            async for r in result:
                batch.append(line(_cell(v) for v in r))
                if len(batch) >= 2000:
                    yield "".join(batch)
                    batch = []
            if batch:
                yield "".join(batch)

    return StreamingResponse(gen(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{_filename(f)}"'})
