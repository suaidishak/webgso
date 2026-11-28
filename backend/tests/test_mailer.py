from datetime import datetime

import pytest

from app.mailer import CatSummary, attachment_name, build_message, compose, summarise, wanted_categories
from app.models import RecipientList
from app.routers.emails import ListIn, clean_emails


def run(m, cat, status="done", attempt=1, valid=True, raw=1, error=None, http=200, major=None):
    return {"plant_id": hash(m) % 1000, "mnemonic": m, "category": cat, "status": status, "attempt_id": attempt,
            "is_valid": valid, "http_status": http, "error": error, "major_flags": 1 if major else 0,
            "minor_flags": 0, "raw_response_id": raw, "first_major": major}


def test_clean_emails_splits_dedupes_and_validates():
    assert clean_emails(["A@Example.com; b@example.com", "a@example.com\nc@example.com"]) == [
        "a@example.com", "b@example.com", "c@example.com"]
    with pytest.raises(ValueError, match="not-an-email"):
        clean_emails(["ok@example.com, not-an-email"])


def test_list_in_rules():
    li = ListIn(name=" Ops ", emails=["x@example.com"], categories=["mmf", "dayahead"])
    assert li.name == "Ops" and li.categories == ["dayahead", "mmf"]  # API order, not click order
    assert ListIn(name="All", emails=["x@example.com"], categories=["rolling", "dayahead", "weekahead",
                                                                     "fourmonthsahead", "mmf"]).categories is None
    with pytest.raises(ValueError):
        ListIn(name="Empty", emails=[" "])
    with pytest.raises(ValueError):
        ListIn(name="Bad interval", emails=["x@example.com"], mmf_interval=30)


def test_summarise_sorts_out_valid_invalid_failed_skipped():
    runs = [run("kmls", "dayahead"), run("sids", "dayahead", valid=False, major="Expected 480 points, got 96"),
            run("bkhs", "dayahead", attempt=7, raw=None, http=401, error=None),
            run("mals", "dayahead", status="skipped", attempt=None), run("kmls", "mmf")]
    sm = summarise(runs, "dayahead")
    assert (sm.plants, sm.valid) == (4, 1)
    assert sm.invalid == [{"plant": "SIDS", "reason": "Expected 480 points, got 96"}]
    assert sm.failed == [{"plant": "BKHS", "reason": "HTTP 401"}]
    assert sm.skipped == ["MALS"]
    assert sm.issues == 3


def test_wanted_categories_intersects_list_and_retrieval():
    runs = [run("kmls", "dayahead"), run("kmls", "mmf")]
    assert wanted_categories(RecipientList(categories=None), runs) == ["dayahead", "mmf"]
    assert wanted_categories(RecipientList(categories=["mmf", "rolling"]), runs) == ["mmf"]
    assert wanted_categories(RecipientList(categories=["rolling"]), runs) == []


def test_compose_subject_and_body():
    rl = RecipientList(code="RL-001", name="GSO Ops")
    due = datetime(2026, 11, 28, 10, 5)
    ok = CatSummary("dayahead", plants=8, valid=8)
    subject, text_body, html_body = compose("Day-ahead (rev5 default)", due, rl, [ok], "http://x/#/")
    assert subject == "[GSO] Day Ahead report, 28/11/2026 10:05, 8 plants, all OK"
    assert "No issues." in text_body and "RL-001 GSO Ops" in text_body
    bad = CatSummary("mmf", plants=8, valid=7, invalid=[{"plant": "MALS", "reason": "<bad> shape"}], zero_filled=12)
    subject, text_body, html_body = compose("MMF", due, rl, [bad], "http://x/#/")
    assert subject.endswith("8 plants, 2 issues")
    assert "&lt;bad&gt; shape" in html_body and "12 MMF interval(s)" in text_body


def test_attachment_names_follow_the_download_names():
    due = datetime(2026, 11, 28, 10, 5)
    assert attachment_name("dayahead", due) == "Day Ahead-20261128-1005.xlsx"
    assert attachment_name("mmf", due) == "LSS-MMF-Data-20261128-1005.xlsx"


def test_message_has_html_and_xlsx_attachment():
    msg = build_message(["a@example.com", "b@example.com"], "S", "plain", "<p>html</p>", [("r.xlsx", b"PK..")])
    assert msg["To"] == "a@example.com, b@example.com"
    parts = [p.get_content_type() for p in msg.walk()]
    assert "text/html" in parts and "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in parts
