import io
from datetime import datetime

from openpyxl import load_workbook

from app.client_templates import TCol, TSheet, floor15, period_of, span, to_xlsx


def test_period_and_floor():
    assert period_of(datetime(2026, 11, 26, 0, 0)) == 1
    assert period_of(datetime(2026, 11, 26, 23, 45)) == 96
    assert floor15(datetime(2026, 11, 26, 10, 44, 59)) == datetime(2026, 11, 26, 10, 30)
    assert span(datetime(2026, 11, 26), datetime(2026, 11, 30, 23, 45)) == "26/11/2026 00:00:00 - 30/11/2026 23:45:00"


def test_xlsx_layout_matches_client_template():
    sh = TSheet(name="Day Ahead", title="SOLAR DATA SUBMISSION / DATA MONITORING",
                info_headers=["Data Type", "Submission Date & Time", "Data Interval", "Data Timestamp Range"],
                info_values=["Day Ahead", "25/11/2026 00:00:00", "15 minutes", "26/11/2026 00:00:00 - 26/11/2026 00:15:00"],
                generated="Generated 25 Nov 2026 11:11",
                columns=[TCol("Submission Date & Time", "text"), TCol("Year", "int"), TCol("Month", "int"),
                         TCol("Day", "int"), TCol("Period", "int"), TCol("BDWS"), TCol("SIDS")],
                rows=[["25/11/2026 00:00:00", 2026, 11, 26, 1, 0.0, None]], freeze_cols=5)
    ws = load_workbook(io.BytesIO(to_xlsx([sh])))["Day Ahead"]
    assert ws["A1"].value == "SOLAR DATA SUBMISSION / DATA MONITORING" and ws["A1"].fill.fgColor.rgb == "FF1F6F43"
    assert [c.value for c in ws[2]][:4] == sh.info_headers and ws["B4"].value == "1 record(s)"
    assert [c.value for c in ws[6]] == ["Submission Date & Time", "Year", "Month", "Day", "Period", "BDWS", "SIDS"]
    assert ws["F6"].fill.fgColor.rgb == "FF0875C9" and ws.freeze_panes == "F7"
    assert [c.value for c in ws[7]] == ["25/11/2026 00:00:00", 2026, 11, 26, 1, 0, None]


def test_intervals():
    from app.client_templates import floor_to
    t = datetime(2026, 11, 26, 13, 47, 12)
    assert floor_to(t, 5) == datetime(2026, 11, 26, 13, 45) and floor_to(t, 60) == datetime(2026, 11, 26, 13, 0)
    assert period_of(datetime(2026, 11, 26, 23, 0), 60) == 24 and period_of(datetime(2026, 11, 26, 0, 5), 5) == 2


def test_replaced_cells_are_highlighted():
    sh = TSheet(name="AirPressure", title="AIRPRESSURE", info_headers=["Parameter"], info_values=["AirPressure"],
                generated="Generated", columns=[TCol("LSS", "text"), TCol("MMF", "text"), TCol("HR1"), TCol("HR2")],
                rows=[["BDWS", "Average", 1008.5, 0]], freeze_cols=2, replaced={(0, 3)})
    ws = load_workbook(io.BytesIO(to_xlsx([sh])))["AirPressure"]
    assert ws["D7"].value == 0 and ws["D7"].fill.fgColor.rgb == "FFFFE08A"
    assert ws["C7"].fill.fill_type is None and ws["B7"].value == "Average"
