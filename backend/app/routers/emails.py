"""gso_admin only: recipient lists, SMTP status and test sends, and the sent-email log.

A recipient list has a running code (RL-001, RL-002 ..), a name and one or more email addresses, plus which
report it receives (APIs and template options). Retrieval schedules are paired with lists; after each
scheduled retrieval the worker emails every paired list (see mailer.py).
"""
import re
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import mailer
from ..auth import require_admin
from ..client_templates import FORECAST_INTERVALS, MMF_INTERVALS
from ..config import now_local
from ..db import get_session
from ..models import CATEGORIES, AppUser, PollSchedule, RecipientList, ReportEmail
from .admin import audit

router = APIRouter(prefix="/admin", tags=["email"])
MAX_EMAILS = 200
# Emails an admin sends from the page are delivered by the request itself; the worker takes one over only
# if the request died before sending it.
HANDOVER = timedelta(minutes=5)
# Deliberately plain: internal domains such as gso.local must be accepted, which strict validators refuse.
EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                      r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")


def clean_emails(raw: list[str]) -> list[str]:
    """Trimmed, lower-cased, de-duplicated addresses (order kept). Raises ValueError naming a bad one."""
    out, seen = [], set()
    for item in raw:
        for part in str(item).replace(";", ",").replace("\n", ",").split(","):
            a = part.strip()
            if not a:
                continue
            if len(a) > 254 or not EMAIL_RE.match(a):
                raise ValueError(f"Not a valid email address: {a}")
            a = a.lower()
            if a not in seen:
                seen.add(a)
                out.append(a)
    return out


class ListIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    emails: list[str]
    categories: list[str] | None = None  # None = every API in the retrieval
    forecast_interval: int = 15
    mmf_interval: int = 15
    mmf_source: str = Field("station", pattern="^(station|average)$")
    include_simulated: bool = False
    enabled: bool = True
    notes: str | None = Field(None, max_length=2000)

    @model_validator(mode="after")
    def check(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Give the list a name")
        self.emails = clean_emails(self.emails)
        if not self.emails:
            raise ValueError("Add at least one email address")
        if len(self.emails) > MAX_EMAILS:
            raise ValueError(f"A list can have at most {MAX_EMAILS} addresses")
        if self.categories is not None:
            bad = [c for c in self.categories if c not in CATEGORIES]
            if bad:
                raise ValueError(f"Unknown API {bad[0]}")
            self.categories = [c for c in CATEGORIES if c in self.categories] or None
            if self.categories and len(self.categories) == len(CATEGORIES):
                self.categories = None
        if self.forecast_interval not in FORECAST_INTERVALS:
            raise ValueError("Forecast interval must be 15 or 60 minutes")
        if self.mmf_interval not in MMF_INTERVALS:
            raise ValueError("MMF interval must be 5, 15 or 60 minutes")
        self.notes = (self.notes or "").strip() or None
        return self


def list_out(rl: RecipientList, used_by: list[dict] | None = None) -> dict:
    d = {c.name: getattr(rl, c.name) for c in RecipientList.__table__.columns}
    if used_by is not None:
        d["schedules"] = used_by
    return d


async def _usage(s: AsyncSession) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for sc in (await s.execute(select(PollSchedule).where(PollSchedule.recipient_list_ids.isnot(None)))).scalars():
        for lid in sc.recipient_list_ids or []:
            out.setdefault(lid, []).append({"id": sc.id, "name": sc.name, "enabled": sc.enabled})
    return out


@router.get("/recipient-lists")
async def recipient_lists(admin: AppUser = Depends(require_admin), s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(RecipientList).order_by(RecipientList.id))).scalars().all()
    usage = await _usage(s)
    last = {r.recipient_list_id: r for r in (await s.execute(text(
        "SELECT DISTINCT ON (recipient_list_id) recipient_list_id, status, created_at, sent_at FROM report_email "
        "WHERE recipient_list_id IS NOT NULL AND kind <> 'test' ORDER BY recipient_list_id, id DESC"))).all()}
    out = []
    for rl in rows:
        d = list_out(rl, usage.get(rl.id, []))
        lr = last.get(rl.id)
        d["last_email"] = {"status": lr.status, "at": lr.sent_at or lr.created_at} if lr else None
        out.append(d)
    return {"rows": out, "smtp": mailer.smtp_status()}


@router.post("/recipient-lists", status_code=201)
async def create_list(body: ListIn, admin: AppUser = Depends(require_admin), s: AsyncSession = Depends(get_session)):
    now = now_local()
    rl = RecipientList(**body.model_dump(), created_at=now, updated_at=now)
    s.add(rl)
    await s.flush()
    rl.code = f"RL-{rl.id:03d}"  # the running code; ids are never reused, so neither are codes
    await audit(s, admin, "recipients.create", "recipient_list", rl.code, None, body.model_dump())
    await s.commit()
    return list_out(rl, [])


@router.put("/recipient-lists/{lid}")
async def update_list(lid: int, body: ListIn, admin: AppUser = Depends(require_admin),
                      s: AsyncSession = Depends(get_session)):
    rl = await s.get(RecipientList, lid)
    if rl is None:
        raise HTTPException(404, "Recipient list not found")
    before = {k: v for k, v in list_out(rl).items() if k in body.model_fields}
    for k, v in body.model_dump().items():
        setattr(rl, k, v)
    rl.updated_at = now_local()
    await audit(s, admin, "recipients.update", "recipient_list", rl.code, before, body.model_dump())
    await s.commit()
    return list_out(rl, (await _usage(s)).get(rl.id, []))


@router.delete("/recipient-lists/{lid}")
async def delete_list(lid: int, admin: AppUser = Depends(require_admin), s: AsyncSession = Depends(get_session)):
    rl = await s.get(RecipientList, lid)
    if rl is None:
        raise HTTPException(404, "Recipient list not found")
    await audit(s, admin, "recipients.delete", "recipient_list", rl.code, list_out(rl), None)
    # Unpair it from every schedule; the sent-email log keeps the code it was sent under.
    await s.execute(text("UPDATE poll_schedule SET recipient_list_ids = array_remove(recipient_list_ids, :id) "
                         "WHERE :id = ANY(recipient_list_ids)"), {"id": lid})
    await s.delete(rl)
    await s.commit()
    return {"ok": True}


# ---------------------------------------------------------------- test sends
class TestIn(BaseModel):
    to: list[str] | None = None  # default: every address on the list


async def _send_test(s: AsyncSession, admin: AppUser, to: list[str], rl: RecipientList | None) -> dict:
    now = now_local()
    who = f"{rl.code} {rl.name}" if rl else "a single address"
    subject = f"[GSO] Test email ({who})"
    text_body = (f"This is a test email from the GSO Solar Data Platform, sent by {admin.name} at "
                 f"{now:%d/%m/%Y %H:%M} (Malaysia time).\n\n"
                 + (f"You are on recipient list {rl.code} ({rl.name}). After each scheduled retrieval of a schedule "
                    "paired with this list you will receive the report in the client template.\n" if rl else
                    "If you can read this, the email settings work.\n"))
    html_body = "<div style='font-family:Segoe UI,Arial,sans-serif;font-size:14px'>" + \
        "".join(f"<p>{line}</p>" for line in text_body.strip().split("\n\n")) + "</div>"
    e = ReportEmail(kind="test", recipient_list_id=rl.id if rl else None, list_code=rl.code if rl else None,
                    recipients=to, subject=subject, status="sending", attempts=1, created_at=now, created_by=admin.id)
    s.add(e)
    await s.commit()
    try:
        await mailer.send(mailer.build_message(to, subject, text_body, html_body))
        e.status, e.sent_at = "sent", now_local()
    except Exception as ex:  # noqa: BLE001
        e.status, e.last_error = "failed", str(ex)[:1000] or ex.__class__.__name__
    await s.commit()
    return {"id": e.id, "status": e.status, "error": e.last_error, "to": to}


@router.post("/recipient-lists/{lid}/test")
async def test_list(lid: int, body: TestIn | None = None, admin: AppUser = Depends(require_admin),
                    s: AsyncSession = Depends(get_session)):
    rl = await s.get(RecipientList, lid)
    if rl is None:
        raise HTTPException(404, "Recipient list not found")
    try:
        to = clean_emails(body.to) if body and body.to else list(rl.emails)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return await _send_test(s, admin, to, rl)


@router.post("/email/test")
async def test_address(body: TestIn, admin: AppUser = Depends(require_admin), s: AsyncSession = Depends(get_session)):
    try:
        to = clean_emails(body.to or [])
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    if not to:
        raise HTTPException(400, "Enter an email address")
    return await _send_test(s, admin, to, None)


@router.get("/email/settings")
async def email_settings(admin: AppUser = Depends(require_admin)):
    return mailer.smtp_status()


# ---------------------------------------------------------------- send the latest report now
class SendLatestIn(BaseModel):
    list_ids: list[int] | None = None  # default: every list paired with the schedule


@router.post("/schedules/{sid}/send-latest")
async def send_latest(sid: int, body: SendLatestIn | None = None, admin: AppUser = Depends(require_admin),
                      s: AsyncSession = Depends(get_session)):
    """Email the report of this schedule's latest finished retrieval now (marked as resent). For checking a
    pairing without waiting for the next scheduled retrieval."""
    sc = await s.get(PollSchedule, sid)
    if sc is None:
        raise HTTPException(404, "Schedule not found")
    ids = (body.list_ids if body and body.list_ids else None) or list(sc.recipient_list_ids or [])
    if not ids:
        raise HTTPException(400, "This schedule has no recipient lists")
    due = (await s.execute(text(
        "SELECT due_at FROM schedule_run WHERE schedule_id = :sid GROUP BY due_at "
        "HAVING count(*) FILTER (WHERE status = 'queued') = 0 ORDER BY due_at DESC LIMIT 1"), {"sid": sid})).scalar()
    if due is None:
        raise HTTPException(400, "This schedule has not finished a retrieval yet")
    lists = (await s.execute(select(RecipientList).where(RecipientList.id.in_(ids)))).scalars().all()
    now = now_local()
    rows = [ReportEmail(kind="resend", schedule_id=sc.id, schedule_name=sc.name, recipient_list_id=rl.id,
                        list_code=rl.code, due_at=due, recipients=list(rl.emails), status="pending",
                        attempts=0, created_at=now, next_attempt_at=now + HANDOVER, created_by=admin.id) for rl in lists]
    s.add_all(rows)
    await audit(s, admin, "email.send_latest", "poll_schedule", sid, None,
                {"due_at": due.isoformat(), "lists": [rl.code for rl in lists]})
    await s.commit()
    out = []
    for e in rows:
        status = await mailer.deliver(e.id)
        out.append({"id": e.id, "list": e.list_code, "status": status})
    return {"due_at": due, "emails": out}


# ---------------------------------------------------------------- sent-email log
def email_out(e: ReportEmail) -> dict:
    return {c.name: getattr(e, c.name) for c in ReportEmail.__table__.columns}


@router.get("/emails")
async def emails(status: str | None = None, kind: str | None = None, list_id: int | None = None,
                 schedule_id: int | None = None, email: int | None = None,
                 offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500),
                 admin: AppUser = Depends(require_admin), s: AsyncSession = Depends(get_session)):
    cond = []
    if email:
        cond.append(ReportEmail.id == email)
    if status == "problem":
        cond.append(ReportEmail.status.in_(("retry", "failed")))
    elif status:
        cond.append(ReportEmail.status == status)
    if kind:
        cond.append(ReportEmail.kind == kind)
    if list_id:
        cond.append(ReportEmail.recipient_list_id == list_id)
    if schedule_id:
        cond.append(ReportEmail.schedule_id == schedule_id)
    total = (await s.execute(select(func.count()).select_from(ReportEmail).where(*cond))).scalar()
    rows = (await s.execute(select(ReportEmail).where(*cond).order_by(ReportEmail.id.desc())
                            .offset(offset).limit(limit))).scalars().all()
    counts = dict((await s.execute(text(
        "SELECT status, count(*) FROM report_email WHERE created_at >= :since GROUP BY status"),
        {"since": now_local().replace(hour=0, minute=0, second=0)})).all())
    return {"rows": [email_out(e) for e in rows], "total": total, "today": counts}


@router.post("/emails/{eid}/resend")
async def resend(eid: int, admin: AppUser = Depends(require_admin), s: AsyncSession = Depends(get_session)):
    """Send a logged report email again, now, to the list's current addresses (a new row in the log)."""
    old = await s.get(ReportEmail, eid)
    if old is None:
        raise HTTPException(404, "Email not found")
    if old.kind == "test" or not old.schedule_id or not old.due_at:
        raise HTTPException(400, "Only report emails can be resent; use Send test for a test email")
    rl = await s.get(RecipientList, old.recipient_list_id) if old.recipient_list_id else None
    if rl is None:
        raise HTTPException(400, "That recipient list has been deleted")
    now = now_local()
    e = ReportEmail(kind="resend", schedule_id=old.schedule_id, schedule_name=old.schedule_name,
                    recipient_list_id=rl.id, list_code=rl.code, due_at=old.due_at, recipients=list(rl.emails),
                    status="pending", attempts=0, created_at=now, next_attempt_at=now + HANDOVER, created_by=admin.id)
    s.add(e)
    await audit(s, admin, "email.resend", "report_email", eid, None, {"list": rl.code})
    await s.commit()
    status = await mailer.deliver(e.id)
    await s.refresh(e)
    return email_out(e) | {"status": status}
