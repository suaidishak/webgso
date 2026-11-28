"""Report emails after scheduled retrievals.

After each SCHEDULED retrieval (a schedule's batch of pulls for one due time) has finished, every recipient
list paired with that schedule gets one email: the report for that submission in the client's template
(one .xlsx per API: the DDQ layout for forecasts, the MMF workbook for MMF) and a short summary of what
went wrong in the body. Retrieve now, Test and the start-up sweep have no schedule, so they never email.

It runs in the worker, next to the scheduler, and is driven by the database:
  queue_finished()  finds finished batches of schedules that have lists and adds one report_email row per
                    list (a unique index stops doubles, so a restart never sends twice);
  send_due()        builds and sends pending rows; a failure is retried after EMAIL_RETRY_MINUTES, then the
                    row is marked failed. Every email, sent or not, stays in report_email (the sent-email log).
"""
from __future__ import annotations

import asyncio
import html
import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import getaddresses, make_msgid

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert

from . import client_templates as ct
from .config import get_settings, now_local
from .db import SessionLocal
from .models import CATEGORIES, PollSchedule, RecipientList, ReportEmail

log = logging.getLogger("gso.mailer")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CAT_NAME = {"rolling": "Rolling", "dayahead": "Day Ahead", "weekahead": "Week Ahead",
            "fourmonthsahead": "Four Months Ahead", "mmf": "MMF"}
LOOKBACK = timedelta(hours=6)  # batches older than this are never emailed (e.g. after long downtime)
MAX_LISTED = 25  # problem plants named per API in the body


# ---------------------------------------------------------------- SMTP
def retry_delays() -> list[int]:
    out = []
    for x in get_settings().email_retry_minutes.split(","):
        try:
            out.append(max(0, int(x.strip())))
        except ValueError:
            pass
    return out


def smtp_status() -> dict:
    c = get_settings()
    return {"configured": bool(c.smtp_host), "host": c.smtp_host or None, "port": c.smtp_port,
            "security": c.smtp_security, "username": c.smtp_username or None, "from": c.mail_from,
            "reply_to": c.mail_reply_to or None, "retries": retry_delays(), "public_base_url": c.public_base_url,
            "catcher": c.smtp_host == "mailpit"}


def _send_sync(msg: EmailMessage):
    c = get_settings()
    if not c.smtp_host:
        raise RuntimeError("Email is not set up: SMTP_HOST is empty in .env")
    ctx = ssl.create_default_context()
    if c.smtp_security == "ssl":
        server = smtplib.SMTP_SSL(c.smtp_host, c.smtp_port, timeout=c.smtp_timeout_s, context=ctx)
    else:
        server = smtplib.SMTP(c.smtp_host, c.smtp_port, timeout=c.smtp_timeout_s)
    try:
        if c.smtp_security == "starttls":
            server.starttls(context=ctx)
        if c.smtp_username:
            server.login(c.smtp_username, c.smtp_password)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass


async def send(msg: EmailMessage):
    await asyncio.to_thread(_send_sync, msg)


def build_message(to: list[str], subject: str, text_body: str, html_body: str,
                  attachments: list[tuple[str, bytes]] = ()) -> EmailMessage:
    c = get_settings()
    msg = EmailMessage()
    msg["From"] = c.mail_from
    msg["To"] = ", ".join(to)
    if c.mail_reply_to:
        msg["Reply-To"] = c.mail_reply_to
    msg["Subject"] = subject
    domain = (getaddresses([c.mail_from])[0][1].split("@")[-1] or "gso.local")
    msg["Message-ID"] = make_msgid(domain=domain)
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    for name, data in attachments:
        msg.add_attachment(data, maintype="application", subtype=XLSX.split("/")[1], filename=name)
    return msg


# ---------------------------------------------------------------- what happened in one batch
@dataclass
class CatSummary:
    category: str
    plants: int = 0  # pulls in the batch
    valid: int = 0
    invalid: list[dict] = field(default_factory=list)  # {plant, reason}
    failed: list[dict] = field(default_factory=list)  # no usable response: {plant, reason}
    skipped: list[str] = field(default_factory=list)  # previous pull still running
    zero_filled: int = 0  # MMF intervals with no reading (written as 0 in the workbook)
    alerts: list[dict] = field(default_factory=list)  # open alerts raised by this batch: {plant, severity, message}

    @property
    def issues(self) -> int:
        return len(self.invalid) + len(self.failed) + len(self.skipped) + (1 if self.zero_filled else 0)

    def as_dict(self) -> dict:
        return {"category": self.category, "plants": self.plants, "valid": self.valid, "invalid": self.invalid,
                "failed": self.failed, "skipped": self.skipped, "zero_filled": self.zero_filled,
                "alerts": self.alerts}


async def batch_runs(s, schedule_id: int, due_at: datetime) -> list[dict]:
    """Every pull in one scheduled retrieval, with its result."""
    return [dict(r) for r in (await s.execute(text("""
        SELECT r.plant_id, p.mnemonic, p.is_simulated, r.category, r.status, r.poll_attempt_id AS attempt_id,
               a.is_valid, a.http_status, a.error, a.major_flags, a.minor_flags, a.raw_response_id, a.started_at,
               (SELECT f.message FROM compliance_flag f WHERE f.poll_attempt_id = a.id AND f.severity = 'major'
                ORDER BY f.id LIMIT 1) AS first_major
        FROM schedule_run r JOIN plant p ON p.id = r.plant_id
        LEFT JOIN poll_attempt a ON a.id = r.poll_attempt_id
        WHERE r.schedule_id = :sid AND r.due_at = :due ORDER BY p.mnemonic"""),
        {"sid": schedule_id, "due": due_at})).mappings().all()]


def summarise(runs: list[dict], category: str) -> CatSummary:
    sm = CatSummary(category)
    for r in runs:
        if r["category"] != category:
            continue
        sm.plants += 1
        m = r["mnemonic"].upper()
        if r["status"] == "skipped":
            sm.skipped.append(m)
        elif r["attempt_id"] is None or r["raw_response_id"] is None:
            reason = r["error"] or (f"HTTP {r['http_status']}" if r["http_status"] else
                                    "Still running when the email was sent" if r["status"] == "queued" else "No response")
            sm.failed.append({"plant": m, "reason": reason[:200]})
        elif r["is_valid"]:
            sm.valid += 1
        else:
            reason = r["first_major"] or r["error"] or f"{r['major_flags']} major finding(s)"
            sm.invalid.append({"plant": m, "reason": reason[:200]})
    return sm


async def batch_alerts(s, runs: list[dict], since: datetime) -> dict[str, list[dict]]:
    pairs = {(r["plant_id"], r["category"]) for r in runs}
    if not pairs:
        return {}
    rows = (await s.execute(text("""
        SELECT al.category, p.mnemonic, al.severity, al.message FROM alert_log al JOIN plant p ON p.id = al.plant_id
        WHERE al.cleared_at IS NULL AND al.last_seen_at >= :since
          AND (al.plant_id, al.category) IN (SELECT * FROM unnest(CAST(:pids AS integer[]), CAST(:cats AS text[])))
        ORDER BY al.severity, p.mnemonic"""),
        {"since": since, "pids": [p for p, _ in pairs], "cats": [c for _, c in pairs]})).all()
    out: dict[str, list[dict]] = {}
    for cat, mn, sev, msg in rows:
        out.setdefault(cat, []).append({"plant": mn.upper(), "severity": sev, "message": msg[:200]})
    return out


# ---------------------------------------------------------------- the report itself
def _cte(attempt_ids: list[int]) -> tuple[str, dict]:
    return ("WITH sel AS (SELECT a.id, a.plant_id, p.mnemonic AS plant, a.category, a.started_at, a.raw_response_id, "
            "a.is_valid, a.scheduled_for FROM poll_attempt a JOIN plant p ON p.id = a.plant_id "
            "WHERE a.id = ANY(:attempt_ids))", {"attempt_ids": attempt_ids})


def attachment_name(category: str, due_at: datetime) -> str:
    stamp = due_at.strftime("%Y%m%d-%H%M")
    return f"LSS-MMF-Data-{stamp}.xlsx" if category == "mmf" else f"{ct.DATA_TYPE[category]}-{stamp}.xlsx"


async def build_workbook(s, category: str, runs: list[dict], due_at: datetime, rl: RecipientList,
                         whole_fleet: bool) -> tuple[bytes, int]:
    """(xlsx bytes, zero-filled MMF cells) for one API of one submission, in the client's template."""
    mine = [r for r in runs if r["category"] == category]
    attempt_ids = [r["attempt_id"] for r in mine if r["attempt_id"]]
    cte, params = _cte(attempt_ids)
    params["ids"] = sorted({r["plant_id"] for r in mine})
    if category == "mmf":
        sheets = await ct.mmf_template(s, cte, params, None, None, whole_fleet, rl.mmf_interval or 15,
                                       rl.mmf_source or "station", only_selected=True)
        zero = sum(len(sh.replaced) for sh in sheets)
    else:
        sheets = [await ct.forecast_template(s, cte, params, category, False, rl.forecast_interval or 15,
                                             submission_at=due_at, only_selected=True)]
        zero = 0
    return ct.to_xlsx(sheets), zero


def wanted_categories(rl: RecipientList, runs: list[dict]) -> list[str]:
    ran = {r["category"] for r in runs}
    return [c for c in CATEGORIES if c in ran and (rl.categories is None or c in rl.categories)]


def compose(schedule_name: str, due_at: datetime, rl: RecipientList, sums: list[CatSummary],
            link: str) -> tuple[str, str, str]:
    """(subject, plain text, HTML) for one report email."""
    issues = sum(x.issues for x in sums)
    plants = max((x.plants for x in sums), default=0)
    apis = ", ".join(CAT_NAME[x.category] for x in sums)
    subject = (f"[GSO] {apis} report, {due_at:%d/%m/%Y %H:%M}, {plants} plant{'s' if plants != 1 else ''}"
               + (f", {issues} issue{'s' if issues != 1 else ''}" if issues else ", all OK"))
    t = [f"Scheduled retrieval: {schedule_name}", f"Submission: {due_at:%d/%m/%Y %H:%M} (Malaysia time)",
         f"Recipient list: {rl.code} {rl.name}", ""]
    h = [f"<p style='margin:0 0 12px'>Scheduled retrieval <b>{html.escape(schedule_name)}</b>, submission "
         f"<b>{due_at:%d/%m/%Y %H:%M}</b> (Malaysia time). The report in the client template is attached.</p>"]
    for x in sums:
        name = CAT_NAME[x.category]
        head = f"{name}: {x.valid} of {x.plants} plant(s) valid"
        t.append(head)
        colour = "#15803d" if not x.issues else "#b42318"
        h.append(f"<h3 style='margin:16px 0 6px;font-size:15px'>{name} "
                 f"<span style='color:{colour};font-weight:normal'>{x.valid} of {x.plants} valid</span></h3>")
        rows = ([("Failed", f["plant"], f["reason"]) for f in x.failed]
                + [("Invalid", f["plant"], f["reason"]) for f in x.invalid]
                + [("Skipped", m, "Previous retrieval still running") for m in x.skipped])
        if x.zero_filled:
            rows.append(("Missing", "-", f"{x.zero_filled} MMF interval(s) with no reading, written as 0 and "
                                         "highlighted (see the Anomaly Report sheet)"))
        if not rows:
            t.append("  No issues.")
            h.append("<p style='margin:0;color:#15803d'>No issues.</p>")
        else:
            h.append("<table style='border-collapse:collapse;font-size:13px'>"
                     "<tr style='background:#0875c9;color:#fff'><th style='padding:4px 8px;text-align:left'>Issue</th>"
                     "<th style='padding:4px 8px;text-align:left'>LSS</th><th style='padding:4px 8px;text-align:left'>Detail</th></tr>")
            for kind, plant, why in rows[:MAX_LISTED]:
                t.append(f"  {kind}: {why}" if plant == "-" else f"  {kind}: {plant}: {why}")
                h.append(f"<tr><td style='padding:3px 8px;border:1px solid #d0d7de'>{kind}</td>"
                         f"<td style='padding:3px 8px;border:1px solid #d0d7de'>{html.escape(plant)}</td>"
                         f"<td style='padding:3px 8px;border:1px solid #d0d7de'>{html.escape(why)}</td></tr>")
            h.append("</table>")
            if len(rows) > MAX_LISTED:
                t.append(f"  ... and {len(rows) - MAX_LISTED} more")
                h.append(f"<p style='margin:4px 0;color:#57606a'>... and {len(rows) - MAX_LISTED} more.</p>")
        if x.alerts:
            t.append(f"  Open alerts: {len(x.alerts)}")
            h.append(f"<p style='margin:6px 0 0'><b>Open alerts ({len(x.alerts)})</b></p><ul style='margin:2px 0'>")
            for a in x.alerts[:MAX_LISTED]:
                t.append(f"    [{a['severity']}] {a['plant']}: {a['message']}")
                h.append(f"<li>[{a['severity']}] {html.escape(a['plant'])}: {html.escape(a['message'])}</li>")
            h.append("</ul>")
        t.append("")
    t += [f"Open in the platform: {link}", "",
          "Sent automatically after a scheduled retrieval by the GSO Solar Data Platform. Contact the GSO "
          "administrator to be removed from this list."]
    h.append(f"<p style='margin:16px 0 0'><a href='{html.escape(link)}'>Open in the platform</a></p>"
             "<p style='color:#57606a;font-size:12px'>Sent automatically after a scheduled retrieval by the GSO Solar "
             "Data Platform. Contact the GSO administrator to be removed from this list.</p>")
    body = "<div style='font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1f2328'>" + "".join(h) + "</div>"
    return subject, "\n".join(t), body


async def prepare(s, e: ReportEmail) -> EmailMessage | None:
    """Build the message for a report email row (also stores subject, summary and attachment names)."""
    rl = await s.get(RecipientList, e.recipient_list_id) if e.recipient_list_id else None
    sch = await s.get(PollSchedule, e.schedule_id) if e.schedule_id else None
    if rl is None:
        raise RuntimeError("The recipient list was deleted")
    runs = await batch_runs(s, e.schedule_id, e.due_at) if e.schedule_id else []
    if not rl.include_simulated:  # fictitious simulator plants never reach real recipients by default
        runs = [r for r in runs if not r["is_simulated"]]
    cats = wanted_categories(rl, runs)
    if not cats:
        return None
    alerts = await batch_alerts(s, runs, e.due_at)
    files, sums = [], []
    whole = sch is not None and sch.plant_ids is None
    for cat in cats:
        sm = summarise(runs, cat)
        sm.alerts = alerts.get(cat, [])
        data, sm.zero_filled = await build_workbook(s, cat, runs, e.due_at, rl, whole)
        files.append((attachment_name(cat, e.due_at), data))
        sums.append(sm)
    link = f"{get_settings().public_base_url.rstrip('/')}/#/admin/recipients/log?email={e.id}"
    subject, text_body, html_body = compose(e.schedule_name or (sch.name if sch else "schedule"), e.due_at, rl, sums, link)
    if e.kind == "resend":
        subject = "[Resent] " + subject
    e.subject = subject
    e.summary = {"apis": [x.as_dict() for x in sums], "issues": sum(x.issues for x in sums)}
    e.attachments = [{"name": n, "bytes": len(d)} for n, d in files]
    return build_message(list(e.recipients), subject, text_body, html_body, files)


# ---------------------------------------------------------------- worker loop
async def queue_finished(now: datetime) -> int:
    """Add one email per paired list for every scheduled retrieval that has finished."""
    wait = timedelta(minutes=get_settings().email_wait_minutes)
    async with SessionLocal() as s:
        # One row per finished batch and paired list that has no scheduled email yet.
        groups = (await s.execute(text("""
            SELECT r.schedule_id, r.due_at, ps.name, l.lid,
                   count(*) FILTER (WHERE r.status = 'queued') AS pending
            FROM schedule_run r JOIN poll_schedule ps ON ps.id = r.schedule_id
            CROSS JOIN LATERAL unnest(ps.recipient_list_ids) AS l(lid)
            JOIN recipient_list rl ON rl.id = l.lid AND rl.enabled
            WHERE r.due_at >= :since AND NOT EXISTS (
                SELECT 1 FROM report_email e WHERE e.kind = 'scheduled' AND e.schedule_id = r.schedule_id
                  AND e.recipient_list_id = l.lid AND e.due_at = r.due_at)
            GROUP BY r.schedule_id, r.due_at, ps.name, ps.email_since, l.lid
            HAVING min(r.enqueued_at) >= COALESCE(ps.email_since, '-infinity'::timestamp)"""),
            {"since": now - LOOKBACK})).all()
        ready = [g for g in groups if g.pending == 0 or now - g.due_at > wait]
        if not ready:
            return 0
        lists = {rl.id: rl for rl in (await s.execute(select(RecipientList).where(
            RecipientList.id.in_({g.lid for g in ready})))).scalars().all()}
        rows = []
        for g in ready:
            rl = lists.get(g.lid)
            if rl is None or not rl.enabled or not rl.emails:
                continue
            rows.append(dict(kind="scheduled", schedule_id=g.schedule_id, schedule_name=g.name,
                             recipient_list_id=rl.id, list_code=rl.code, due_at=g.due_at,
                             recipients=list(rl.emails), status="pending", attempts=0, created_at=now,
                             next_attempt_at=now))
        if not rows:
            return 0
        res = await s.execute(insert(ReportEmail).values(rows).on_conflict_do_nothing(
            index_elements=["schedule_id", "recipient_list_id", "due_at"],
            index_where=text("kind = 'scheduled'")).returning(ReportEmail.id))
        n = len(res.all())
        await s.commit()
        if n:
            log.info("queued %d report email(s)", n)
        return n


async def deliver(email_id: int) -> str:
    """Build and send one email row; returns its new status."""
    delays = retry_delays()
    async with SessionLocal() as s:
        # Claim the row atomically, so the worker and an admin's Resend can never both send it.
        claimed = (await s.execute(update(ReportEmail).where(
            ReportEmail.id == email_id, ReportEmail.status.in_(("pending", "retry"))).values(
            status="sending", attempts=ReportEmail.attempts + 1).returning(ReportEmail.id))).scalar()
        await s.commit()
        if claimed is None:
            e = await s.get(ReportEmail, email_id)
            return e.status if e else "missing"
        e = await s.get(ReportEmail, email_id)
        await s.refresh(e)
        try:
            msg = await prepare(s, e)
            if msg is None:
                e.status, e.last_error, e.next_attempt_at = "skipped", "None of this list's APIs were in this retrieval", None
            else:
                await s.commit()  # keep the subject and summary even if sending fails
                await send(msg)
                e.status, e.sent_at, e.last_error, e.next_attempt_at = "sent", now_local(), None, None
        except Exception as ex:  # noqa: BLE001 - one bad email must never stop the others
            log.warning("email %s failed (try %s): %s", email_id, e.attempts, ex)
            e.last_error = str(ex)[:1000] or ex.__class__.__name__
            if e.attempts <= len(delays):
                e.status = "retry"
                e.next_attempt_at = now_local() + timedelta(minutes=delays[e.attempts - 1])
            else:
                e.status, e.next_attempt_at = "failed", None
        await s.commit()
        return e.status


async def send_due(now: datetime, limit: int = 20) -> int:
    async with SessionLocal() as s:
        ids = (await s.execute(select(ReportEmail.id).where(
            ReportEmail.status.in_(("pending", "retry")), ReportEmail.next_attempt_at <= now)
            .order_by(ReportEmail.next_attempt_at, ReportEmail.id).limit(limit))).scalars().all()
    for i in ids:
        await deliver(i)
    return len(ids)


async def run_forever():
    tick_s = get_settings().scheduler_tick_seconds
    # An email left 'sending' belonged to a worker that stopped mid-send: try it again.
    async with SessionLocal() as s:
        await s.execute(update(ReportEmail).where(ReportEmail.status == "sending").values(
            status="retry", next_attempt_at=now_local()))
        await s.commit()
    log.info("report mailer started (SMTP %s)", get_settings().smtp_host or "not set")
    while True:
        try:
            now = now_local()
            await queue_finished(now)
            await send_due(now)
        except Exception:  # noqa: BLE001
            log.exception("mailer tick failed")
        await asyncio.sleep(tick_s)
