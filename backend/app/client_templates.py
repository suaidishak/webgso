"""The client's report templates, built from polled data, for the online view and the Excel download.

DDQ forecast template (one per forecast API; the client's sample is "Day Ahead"):
  row 1   SOLAR DATA SUBMISSION / DATA MONITORING (merged title band)
  row 2-3 Data Type | Submission Date & Time | Data Interval | Data Timestamp Range
  row 4   Generated <time> | <n> record(s)
  row 6   Submission Date & Time | Year | Month | Day | Period | <one column per LSS>
  row 7+  one row per submission and 15-minute period. The client's API-001.. labels are replaced by the
          LSS (plant) names, as GSO asked.
A submission is one scheduled pull (poll_attempt.scheduled_for), or the poll minute for a manual poll, so
every plant pulled for the same slot lands on the same rows.

MMF template: one sheet per parameter plus an Anomaly Report.
  row 2-3 Parameter | MMF Value Source | Data Interval | Submission Date & Time | Result Scope | Date Range
  row 6   LSS | MMF | HR1 .. HRn   (HRk = k-th 15-minute interval of the date range; the MMF column, added
          at GSO's request, says which weather station the row is, since a plant has more than one)
  rows    one per plant and station; a cell is the average of that station's readings in the interval.
  A row is one station (MMF1, MMF2..) or, with source=average, one "Average" row per LSS that averages the
  stations' real readings. An interval with no reading is written as 0, highlighted, and listed in the
  Anomaly Report ("Zero fallback"); those zeros are never counted in any average.

Intervals: forecasts 15 or 60 minutes (60 = mean of the hour's 15-minute points); MMF 5, 15 or 60 minutes
(mean of the readings in each interval).
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import now_local
from .validation import MMF_PARAMETERS

DATA_TYPE = {"rolling": "Rolling", "dayahead": "Day Ahead", "weekahead": "Week Ahead",
             "fourmonthsahead": "Four Months Ahead"}
FORECAST_TITLE = "SOLAR DATA SUBMISSION / DATA MONITORING"
INTERVAL = timedelta(minutes=15)
MAX_HR = 31 * 96  # interval columns per MMF sheet (31 days at 15 minutes, about 10 days at 5)
MAX_ANOMALIES = 250_000


@dataclass
class TCol:
    label: str
    type: str = "num"  # text | num | int
    width: float = 12
    hint: str | None = None  # shown on hover online (e.g. the time an HR column covers)


@dataclass
class TSheet:
    name: str
    title: str
    info_headers: list[str]
    info_values: list[str]
    generated: str
    columns: list[TCol]
    rows: list[list] = field(default_factory=list)
    freeze_cols: int = 1  # columns left of the data that stay in view
    note: str | None = None
    replaced: set[tuple[int, int]] = field(default_factory=set)  # (row, col) cells zero-filled for a missing value

    @property
    def records(self) -> int:
        return len(self.rows)


def dmy(t: datetime, seconds: bool = True) -> str:
    return t.strftime("%d/%m/%Y %H:%M:%S" if seconds else "%d/%m/%Y %H:%M")


def span(a: datetime | None, b: datetime | None, seconds: bool = True) -> str:
    if a is None:
        return "-"
    return dmy(a, seconds) if b is None or a == b else f"{dmy(a, seconds)} - {dmy(b, seconds)}"


def generated() -> str:
    return f"Generated {now_local():%d %b %Y %H:%M}"


FORECAST_INTERVALS = (15, 60)
MMF_INTERVALS = (5, 15, 60)


def period_of(t: datetime, minutes: int = 15) -> int:
    return (t.hour * 60 + t.minute) // minutes + 1


def floor_to(t: datetime, minutes: int) -> datetime:
    m = t.hour * 60 + t.minute
    m -= m % minutes
    return t.replace(hour=m // 60, minute=m % 60, second=0, microsecond=0)


def floor15(t: datetime) -> datetime:
    return floor_to(t, 15)


def interval_label(minutes: int) -> str:
    return "1 hour" if minutes == 60 else f"{minutes} minutes"


def mean(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 6)


async def _plants(s: AsyncSession, cte: str, params: dict, category: str) -> list[dict]:
    """Selected plants that have this API, in name order: they are the template's LSS columns/rows."""
    return [dict(r) for r in (await s.execute(text(
        "SELECT p.id, p.mnemonic, p.mmf_station_count FROM plant p JOIN plant_endpoint e ON e.plant_id = p.id "
        "AND e.category = :cat WHERE p.id = ANY(:ids) ORDER BY p.mnemonic"),
        {"cat": category, "ids": params["ids"]})).mappings().all()]


async def forecast_template(s: AsyncSession, cte: str, params: dict, category: str, latest: bool,
                            interval: int = 15, submission_at: datetime | None = None) -> TSheet:
    """submission_at puts every selected call under one submission time (a Retrieve now run, shown like a
    scheduled submission)."""
    plants = await _plants(s, cte, params, category)
    polls = (await s.execute(text(
        f"{cte} SELECT sel.id, sel.plant_id, sel.started_at, "
        "COALESCE(sel.scheduled_for, date_trunc('minute', sel.started_at)) AS sub FROM sel "
        "WHERE sel.raw_response_id IS NOT NULL ORDER BY sub, sel.id"), params)).mappings().all()
    if submission_at is not None:
        polls = [{**p, "sub": submission_at} for p in polls]
    elif latest and polls:  # one submission: each plant's latest poll, shown under the newest time
        newest = max(p["sub"] for p in polls)
        polls = [{**p, "sub": newest} for p in polls]
    chosen: dict[tuple[datetime, int], int] = {}  # (submission, plant) -> poll id; a re-poll replaces
    for p in polls:
        chosen[(p["sub"], p["plant_id"])] = p["id"]
    ids = list(chosen.values())
    pts = (await s.execute(text(
        "SELECT a.id, fp.start_time, fp.value_mw FROM poll_attempt a "
        "JOIN LATERAL (SELECT fs.id FROM forecast_snapshot fs WHERE fs.raw_response_id = a.raw_response_id "
        "ORDER BY fs.id LIMIT 1) snap ON true JOIN forecast_point fp ON fp.snapshot_id = snap.id "
        "WHERE a.id = ANY(:ids)"), {"ids": ids})).all() if ids else []
    by_poll: dict[int, dict[datetime, float | None]] = {}
    for pid, st, v in pts:
        by_poll.setdefault(pid, {})[st] = v
    col_of = {p["id"]: i for i, p in enumerate(plants)}
    grid: dict[datetime, dict[datetime, list]] = {}  # submission -> slot -> values per plant
    for (sub, plant_id), pid in chosen.items():
        if plant_id not in col_of:
            continue
        slots = grid.setdefault(sub, {})
        for st, v in by_poll.get(pid, {}).items():
            slots.setdefault(st, [None] * len(plants))[col_of[plant_id]] = v
    if interval != 15:  # hourly: the mean of each plant's 15-minute points in the hour
        for sub, slots in grid.items():
            hours: dict[datetime, list[list[float]]] = {}
            for st, vals in slots.items():
                acc = hours.setdefault(floor_to(st, interval), [[] for _ in plants])
                for i, v in enumerate(vals):
                    if v is not None:
                        acc[i].append(v)
            grid[sub] = {h: [mean(a) if a else None for a in acc] for h, acc in hours.items()}
    rows = []
    for sub in sorted(grid):
        for st in sorted(grid[sub]):
            rows.append([dmy(sub), st.year, st.month, st.day, period_of(st, interval), *grid[sub][st]])
    subs = sorted(grid)
    slots = sorted({st for g in grid.values() for st in g})
    last_slot = slots[-1] + timedelta(minutes=interval) - INTERVAL if slots else None
    label = DATA_TYPE[category]
    return TSheet(
        name=label, title=FORECAST_TITLE,
        info_headers=["Data Type", "Submission Date & Time", "Data Interval", "Data Timestamp Range"],
        info_values=[label, span(subs[0], subs[-1]) if subs else "-", interval_label(interval),
                     span(slots[0], last_slot) if slots else "-"],
        generated=generated(),
        columns=[TCol("Submission Date & Time", "text", 21), TCol("Year", "int", 8), TCol("Month", "int", 8),
                 TCol("Day", "int", 8), TCol("Period", "int", 8)]
                + [TCol(p["mnemonic"].upper(), "num", 11) for p in plants],
        rows=rows, freeze_cols=5)


async def mmf_template(s: AsyncSession, cte: str, params: dict, start: datetime | None, end: datetime | None,
                       whole_fleet: bool, interval: int = 15, source: str = "station") -> list[TSheet]:
    plants = await _plants(s, cte, params, "mmf")
    data = (await s.execute(text(
        f"{cte} SELECT sel.plant_id, sel.started_at, m.station_no, m.parameter, "
        "COALESCE(m.measured_at, m.retrieved_at) AS t, m.value FROM sel "
        "JOIN mmf_reading m ON m.poll_attempt_id = sel.id"), params)).all()
    polled = (await s.execute(text(f"{cte} SELECT min(started_at), max(started_at) FROM sel"), params)).one()
    step = timedelta(minutes=interval)

    # Real readings per parameter, plant, station and interval (a repeated reading counts once).
    seen_reading: set[tuple] = set()
    acc: dict[tuple, list[float]] = {}
    stations: dict[int, set[int]] = {p["id"]: set(range(1, (p["mmf_station_count"] or 0) + 1)) for p in plants}
    params_seen: set[str] = set()
    for plant_id, _, st_no, param, t, v in data:
        if plant_id not in stations or t is None:
            continue
        stations[plant_id].add(st_no)
        params_seen.add(param)
        if v is None or (param, plant_id, st_no, t) in seen_reading:
            continue
        seen_reading.add((param, plant_id, st_no, t))
        acc.setdefault((param, plant_id, st_no, floor_to(t, interval)), []).append(v)
    buckets = [k[3] for k in acc]
    first = floor_to(start, interval) if start else (min(buckets) if buckets else None)
    last = floor_to(end, interval) if end else (max(buckets) if buckets else None)
    if first is None or last is None or last < first:
        hrs: list[datetime] = []
    else:
        n = int((last - first) / step) + 1
        if n > MAX_HR:
            raise ValueError(f"The MMF template allows at most {MAX_HR:,} interval columns; narrow the retrieval time "
                             "or use a longer interval.")
        hrs = [first + i * step for i in range(n)]

    scope = "All LSS plants (fleet)" if whole_fleet else f"{len(plants)} selected LSS plant(s)"
    sub_span = span(polled[0], polled[1], seconds=False)
    date_span = span(hrs[0], hrs[-1], seconds=False) if hrs else "-"
    head = ["Parameter", "MMF Value Source", "Data Interval", "Submission Date & Time", "Result Scope", "Date Range"]
    value_source = "Average of MMF stations" if source == "average" else "Individual MMF"
    gen = generated()
    sheets, anomalies = [], []
    for param in sorted(set(MMF_PARAMETERS) | params_seen):
        rows, replaced = [], set()
        for p in plants:
            sts = sorted(stations[p["id"]]) or [1]
            groups = [("Average", sts)] if source == "average" else [(f"MMF{x}", [x]) for x in sts]
            for label, members in groups:
                vals = []
                for i, h in enumerate(hrs):
                    # Each station's interval mean, then (for Average) the mean across stations that reported.
                    per = [mean(acc[(param, p["id"], x, h)]) for x in members if acc.get((param, p["id"], x, h))]
                    if per:
                        vals.append(mean(per))
                        continue
                    vals.append(0)
                    replaced.add((len(rows), i + 2))
                    if len(anomalies) < MAX_ANOMALIES:
                        anomalies.append([param, dmy(h, False), p["mnemonic"].upper(), label, None, 0,
                                          "SYSTEM-MISSING", "Missing reading",
                                          "No MMF reading was received for this interval", "Zero fallback",
                                          "Replaced with 0 and highlighted; excluded from averages"])
                rows.append([p["mnemonic"].upper(), label, *vals])
        sheets.append(TSheet(
            name=param[:31], title=param.upper(), info_headers=head,
            info_values=[param, value_source, interval_label(interval), sub_span, scope, date_span], generated=gen,
            columns=[TCol("LSS", "text", 9), TCol("MMF", "text", 9)]
                    + [TCol(f"HR{i + 1}", "num", 10, f"{dmy(h, False)} - {(h + step):%H:%M}") for i, h in enumerate(hrs)],
            rows=rows, freeze_cols=2, replaced=replaced))
    anomaly = TSheet(
        name="Anomaly Report", title="ANOMALY REPORT", info_headers=head,
        info_values=["All selected parameters", value_source, interval_label(interval), sub_span, scope, date_span],
        generated=gen,
        columns=[TCol("Parameter", "text", 16), TCol("Data Timestamp", "text", 17), TCol("LSS", "text", 9),
                 TCol("MMF", "text", 9), TCol("Original Value", "num", 13), TCol("Report Value", "num", 13),
                 TCol("Rule Code", "text", 17), TCol("Rule Name", "text", 17), TCol("Anomaly Detail", "text", 40),
                 TCol("Substitution Source", "text", 19), TCol("Source Reference", "text", 44)],
        rows=anomalies, freeze_cols=4,
        note=f"Only the first {MAX_ANOMALIES:,} anomalies are listed." if len(anomalies) >= MAX_ANOMALIES else None)
    return sheets + [anomaly]


def to_xlsx(sheets: list[TSheet]) -> bytes:
    """The workbook in the client's layout: title band, info block, generated line, blue header row."""
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    thin = Side(style="thin", color="FFD0D7DE")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    title_font = Font(name="Aptos Display", size=15, bold=True, color="FFFFFFFF")
    title_fill = PatternFill("solid", fgColor="FF1F6F43")
    info_fill = PatternFill("solid", fgColor="FFF4F8F2")
    info_head_font = Font(name="Aptos", size=10, bold=True, color="FF5D6D60")
    body_font = Font(name="Aptos", size=11)
    head_font = Font(name="Aptos", size=11, bold=True, color="FFFFFFFF")
    head_fill = PatternFill("solid", fgColor="FF0875C9")
    head_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    # Zero-filled (replaced) values: amber fill and dark red text, so they stand out from real zeros.
    repl_fill = PatternFill("solid", fgColor="FFFFE08A")
    repl_font = Font(name="Aptos", size=11, bold=True, color="FF9C2A00")

    wb = Workbook()
    wb.remove(wb.active)
    for sh in sheets:
        ws = wb.create_sheet(sh.name[:31])
        ncols = max(len(sh.columns), len(sh.info_headers), 2)
        ws.cell(1, 1, sh.title).font = title_font
        for c in range(1, ncols + 1):
            ws.cell(1, c).fill = title_fill
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
        ws.row_dimensions[1].height = 24
        for i, (h, v) in enumerate(zip(sh.info_headers, sh.info_values), 1):
            a = ws.cell(2, i, h); a.font, a.fill, a.border = info_head_font, info_fill, border
            b = ws.cell(3, i, v); b.font, b.fill, b.border = body_font, info_fill, border
        for i, v in enumerate([sh.generated, f"{sh.records:,} record(s)"], 1):
            c = ws.cell(4, i, v); c.font, c.border = body_font, border
        for i, col in enumerate(sh.columns, 1):
            c = ws.cell(6, i, col.label)
            c.font, c.fill, c.alignment, c.border = head_font, head_fill, head_align, border
            ws.column_dimensions[get_column_letter(i)].width = col.width
        for r, row in enumerate(sh.rows, 7):
            for i, v in enumerate(row, 1):
                c = ws.cell(r, i, v)
                c.font, c.border = body_font, border
                if (r - 7, i - 1) in sh.replaced:
                    c.fill, c.font = repl_fill, repl_font
        last_row = 6 + max(len(sh.rows), 1)
        ws.auto_filter.ref = f"A6:{get_column_letter(len(sh.columns))}{last_row}"
        ws.freeze_panes = ws.cell(7, sh.freeze_cols + 1)
        if sh.replaced:
            k = ws.cell(5, 1, "Highlighted cells: no reading was received, value replaced with 0 (see Anomaly Report).")
            k.font, k.fill = Font(name="Aptos", size=10, italic=True, color="FF9C2A00"), repl_fill
        if sh.note:
            ws.cell(5, 1, sh.note).font = Font(name="Aptos", size=10, italic=True, color="FFB42318")
    if not wb.worksheets:
        wb.create_sheet("Report")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
