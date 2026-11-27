from datetime import datetime

from app.colfilter import py_match, sql_condition


def test_numbers():
    assert py_match(5.0, "num", ">4") and not py_match(5.0, "num", "<5")
    assert py_match(5.0, "num", "5") and py_match(5.0, "num", "1..5") and not py_match(None, "num", ">1")
    assert py_match("36.5", "text", "36") and py_match("36.5", "num", ">=36.5")


def test_dates_text_bool_blank():
    t = datetime(2026, 11, 28, 5, 30)
    assert py_match(t, "dt", "05:30") and py_match(t, "dt", ">2026-11-28 05:00") and not py_match(t, "dt", "<2026-11-28")
    assert py_match(t, "dt", "2026-11-28..2026-11-29")
    assert py_match("HorIrradiance", "text", "irr") and py_match("bdws", "text", "=BDWS") and py_match("bdws", "text", "!kmls")
    assert py_match(True, "bool", "yes") and py_match(False, "bool", "no") and not py_match(None, "bool", "no")
    assert py_match(None, "text", "blank") and py_match("x", "text", "!blank")


def test_sql():
    assert sql_condition("q.v", "num", ">=3", "c")[1] == {"c": 3.0}
    sql, p = sql_condition("q.parameter", "text", "50%_x", "c")
    assert "ILIKE" in sql and p["c"] == "%50\\%\\_x%"
