"""Database schema for the beta.

All timestamps are naive Malaysia local time (Asia/Kuala_Lumpur, UTC+8), the same convention
as the rev5 payloads, so stored forecast slots compare directly with what plants send.
"""
from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, Time,
    UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

CATEGORIES = ("rolling", "dayahead", "weekahead", "fourmonthsahead", "mmf")
FORECAST_CATEGORIES = ("rolling", "dayahead", "weekahead", "fourmonthsahead")
ROLES = ("gso_admin", "gso_user", "lss_user")


# ---------------------------------------------------------------- organisations and users
class Organisation(Base):
    __tablename__ = "organisation"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    type: Mapped[str] = mapped_column(String(10))  # GSO | LSS


class AppUser(Base):
    __tablename__ = "app_user"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    organisation_id: Mapped[int | None] = mapped_column(ForeignKey("organisation.id"))
    password_hash: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | active | disabled
    role: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    approved_by: Mapped[int | None] = mapped_column(Integer)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)

    organisation: Mapped[Organisation | None] = relationship(lazy="joined")


class UserPlantAccess(Base):
    __tablename__ = "user_plant_access"
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plant.id", ondelete="CASCADE"), primary_key=True)


class LoginEvent(Base):
    __tablename__ = "login_event"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(320))
    user_id: Mapped[int | None] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(DateTime)
    ip: Mapped[str | None] = mapped_column(String(64))
    success: Mapped[bool] = mapped_column(Boolean)


class AuditEvent(Base):
    """Who changed what: user approvals, schedule edits, plant config."""
    __tablename__ = "audit_event"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(60))
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)


# ---------------------------------------------------------------- plants
class Plant(Base):
    __tablename__ = "plant"
    id: Mapped[int] = mapped_column(primary_key=True)
    mnemonic: Mapped[str] = mapped_column(String(8), unique=True)  # lowercase
    name: Mapped[str] = mapped_column(String(200))
    organisation_id: Mapped[int | None] = mapped_column(ForeignKey("organisation.id"))
    capacity_mw: Mapped[float | None] = mapped_column(Float)
    mmf_station_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="live")  # onboarding | live | suspended
    notes: Mapped[str | None] = mapped_column(Text)
    # Outbound auth to the plant's web service. The key is encrypted at rest (see secrets.py)
    # and never returned by the API; only its last 4 characters are shown to admins.
    auth_method: Mapped[str] = mapped_column(String(10), default="none")  # none | header | query
    auth_param_name: Mapped[str | None] = mapped_column(String(100))  # e.g. X-API-Key or apiKey
    api_key_enc: Mapped[str | None] = mapped_column(Text)
    api_key_hint: Mapped[str | None] = mapped_column(String(8))
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)

    endpoints: Mapped[list["PlantEndpoint"]] = relationship(back_populates="plant", lazy="selectin",
                                                           cascade="all, delete-orphan")


class PlantEndpoint(Base):
    """One URL per plant and category; stored, not templated, because plants deviate from rev5 paths."""
    __tablename__ = "plant_endpoint"
    __table_args__ = (UniqueConstraint("plant_id", "category"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plant.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(30))
    url: Mapped[str] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Known issue acknowledged by GSO (e.g. MALS/LMMS MMF): polled = False when enabled is off.
    note: Mapped[str | None] = mapped_column(Text)

    plant: Mapped[Plant] = relationship(back_populates="endpoints")


# ---------------------------------------------------------------- scheduler
class PollSchedule(Base):
    """When each category is pulled. plant_id NULL = default for every plant; a plant row overrides it."""
    __tablename__ = "poll_schedule"
    __table_args__ = (UniqueConstraint("plant_id", "category", name="uq_schedule_scope"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    plant_id: Mapped[int | None] = mapped_column(ForeignKey("plant.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(30))
    kind: Mapped[str] = mapped_column(String(10))  # interval | cron
    interval_minutes: Mapped[int | None] = mapped_column(Integer)
    window_start: Mapped[time | None] = mapped_column(Time)  # optional window for interval schedules
    window_end: Mapped[time | None] = mapped_column(Time)
    cron: Mapped[str | None] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    timeout_s: Mapped[int] = mapped_column(Integer, default=60)
    retries: Mapped[int] = mapped_column(Integer, default=2)
    retry_backoff_s: Mapped[int] = mapped_column(Integer, default=20)
    jitter_s: Mapped[int] = mapped_column(Integer, default=20)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_by: Mapped[int | None] = mapped_column(Integer)


class ComplianceDeadline(Base):
    """Publish deadlines, kept separate from pull times so moving a pull never changes what counts as late."""
    __tablename__ = "compliance_deadline"
    __table_args__ = (UniqueConstraint("plant_id", "category", name="uq_deadline_scope"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    plant_id: Mapped[int | None] = mapped_column(ForeignKey("plant.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(30))
    period: Mapped[str] = mapped_column(String(10))  # daily | weekly | monthly
    weekday: Mapped[int | None] = mapped_column(Integer)  # 0 = Monday (weekly)
    day_of_month: Mapped[int | None] = mapped_column(Integer)  # monthly
    due_time: Mapped[time] = mapped_column(Time)
    grace_minutes: Mapped[int] = mapped_column(Integer, default=15)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ScheduleRun(Base):
    """One row per (schedule, plant, due time): the uniqueness stops a pull from ever being doubled."""
    __tablename__ = "schedule_run"
    __table_args__ = (UniqueConstraint("schedule_id", "plant_id", "due_at", name="uq_run"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    schedule_id: Mapped[int | None] = mapped_column(ForeignKey("poll_schedule.id", ondelete="SET NULL"))
    plant_id: Mapped[int] = mapped_column(ForeignKey("plant.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(30))
    due_at: Mapped[datetime] = mapped_column(DateTime)
    enqueued_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued | done | failed | manual
    poll_attempt_id: Mapped[int | None] = mapped_column(BigInteger)


# ---------------------------------------------------------------- polling and data
class PollAttempt(Base):
    __tablename__ = "poll_attempt"
    __table_args__ = (Index("ix_attempt_plant_cat_time", "plant_id", "category", "started_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plant.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(30))
    url: Mapped[str] = mapped_column(String(500))
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    http_status: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    bytes: Mapped[int | None] = mapped_column(Integer)
    tries: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str | None] = mapped_column(Text)
    is_valid: Mapped[bool | None] = mapped_column(Boolean)
    major_flags: Mapped[int] = mapped_column(Integer, default=0)
    minor_flags: Mapped[int] = mapped_column(Integer, default=0)
    raw_response_id: Mapped[int | None] = mapped_column(BigInteger)


class RawResponse(Base):
    """Landing table: every distinct response exactly as received (evidence for PPA disputes)."""
    __tablename__ = "raw_response"
    __table_args__ = (Index("ix_raw_plant_cat_hash", "plant_id", "category", "content_hash"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    poll_attempt_id: Mapped[int] = mapped_column(BigInteger)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plant.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(30))
    received_at: Mapped[datetime] = mapped_column(DateTime)
    content_hash: Mapped[str] = mapped_column(String(64))
    body: Mapped[dict | list | None] = mapped_column(JSONB)  # parsed JSON when valid
    body_text: Mapped[str | None] = mapped_column(Text)  # kept only when the body is not JSON


class ForecastSnapshot(Base):
    __tablename__ = "forecast_snapshot"
    __table_args__ = (Index("ix_snap_plant_cat_time", "plant_id", "category", "retrieved_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plant.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(30))
    raw_response_id: Mapped[int] = mapped_column(BigInteger)
    poll_attempt_id: Mapped[int] = mapped_column(BigInteger)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime)
    horizon_start: Mapped[datetime | None] = mapped_column(DateTime)
    horizon_end: Mapped[datetime | None] = mapped_column(DateTime)
    expected_start: Mapped[datetime | None] = mapped_column(DateTime)
    point_count: Mapped[int] = mapped_column(Integer)
    expected_count: Mapped[int | None] = mapped_column(Integer)
    is_valid: Mapped[bool] = mapped_column(Boolean)


class ForecastPoint(Base):
    __tablename__ = "forecast_point"
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("forecast_snapshot.id", ondelete="CASCADE"),
                                             primary_key=True)
    start_time: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    end_time: Mapped[datetime] = mapped_column(DateTime)
    value_mw: Mapped[float | None] = mapped_column(Float)


class MmfReading(Base):
    __tablename__ = "mmf_reading"
    __table_args__ = (Index("ix_mmf_plant_time", "plant_id", "retrieved_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plant.id", ondelete="CASCADE"))
    poll_attempt_id: Mapped[int] = mapped_column(BigInteger)
    station_no: Mapped[int] = mapped_column(Integer)
    parameter: Mapped[str] = mapped_column(String(60))
    measured_at: Mapped[datetime | None] = mapped_column(DateTime)
    value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(20))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime)
    is_standard: Mapped[bool] = mapped_column(Boolean, default=True)  # False = extra, non-rev5 parameter


# ---------------------------------------------------------------- compliance and alerts
class ComplianceFlag(Base):
    """Every rule break found in a response. Tolerant validation: flags are recorded, data is still kept."""
    __tablename__ = "compliance_flag"
    __table_args__ = (Index("ix_flag_plant_cat_time", "plant_id", "category", "ts"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    poll_attempt_id: Mapped[int] = mapped_column(BigInteger, index=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plant.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(30))
    ts: Mapped[datetime] = mapped_column(DateTime)
    rule: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))  # major | minor
    message: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSONB)


class DeadlineCheck(Base):
    __tablename__ = "deadline_check"
    __table_args__ = (UniqueConstraint("plant_id", "category", "due_at", name="uq_deadline_check"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plant.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(30))
    due_at: Mapped[datetime] = mapped_column(DateTime)
    target_start: Mapped[datetime] = mapped_column(DateTime)
    first_valid_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(10))  # met | late | missed
    checked_at: Mapped[datetime] = mapped_column(DateTime)


class AlertLog(Base):
    """The alert audit log. Raised and cleared automatically; never deleted."""
    __tablename__ = "alert_log"
    __table_args__ = (Index("ix_alert_open", "plant_id", "category", "rule", "cleared_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plant.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(30))
    rule: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(Text)
    raised_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime)
    acknowledged_by: Mapped[int | None] = mapped_column(Integer)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime)
    detail: Mapped[dict | None] = mapped_column(JSONB)
