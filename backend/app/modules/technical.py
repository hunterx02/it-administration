"""M04 — Technical services & renewals (FINAL v1.0).

Locked rules:
- No default renewal period anywhere: next due = last confirmed payment + per-record period (days or
  calendar months). Missing input -> no date (never invented).
- Alert lead days per record (no hardcoded 10).
- Internet usage thresholds per line: remaining < warn -> "prepare"; remaining <= critical -> "urgent"
  (urgent wins). No reading -> "incomplete"; no thresholds -> "thresholds_missing".
- Internet bundle renewal and landline renewal are different events with different dates.
- A stale automated reading never overwrites a newer (manual) one; every reading is kept with source/time.
- Saudi SIM "with agent" is a possession place, independent of allocation and network health.
- WhatsApp backup date changes only through an explicit confirmation by a person.
- Payment in M05 alone never renews: only a renewal event here moves the due date.
- No new SIM handover to a frozen (offboarding) employee.
"""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .. import audit
from ..common import (add_period, apply_changes, bad, check_version, conflict, days_until, ensure_company,
                      get_company, iso, next_code, not_found, row, scope_filter, today)
from ..db import Base
from ..deps import Principal, client_ip, get_db, require
from ..models import Lookup, new_id, utcnow
from .people import Employee, assert_can_receive_custody

ALLOCATION = {"available": "خالي", "assigned": "مستخدم", "in_stock": "مخزن", "suspended": "موقوف", "cancelled": "ملغي"}
NETWORK = {"working": "تعمل", "not_working": "لا تعمل", "unknown": "غير معروف"}
DEFAULT_LOOKUPS = {
    "sim_operator_sa": ["Mobily", "Zain", "STC", "Friendi"],
    "sim_operator_eg": [],
    "sim_possession": ["مع الشركة في مصر", "مع الوكيل في السعودية"],
    "service_type": ["AI", "أداة", "إيميل مدفوع", "Domain", "SSL", "استضافة", "أخرى"],
    "usage_unit": ["Tokens", "Credits", "Requests", "GB", "Seats"],
    "internet_provider": [],
}
PERIOD_UNITS = ("days", "months")


def seed_lookups(db: Session) -> None:
    for key, values in DEFAULT_LOOKUPS.items():
        if db.scalar(select(Lookup).where(Lookup.list_key == key)) is None:
            for i, v in enumerate(values):
                db.add(Lookup(list_key=key, value=v, sort=i))


class Sim(Base):
    __tablename__ = "sims"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    country: Mapped[str] = mapped_column(String(2))  # EG | SA — separate registers
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    department_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    number: Mapped[str] = mapped_column(String(40), default="")   # text, never numeric
    iccid: Mapped[str] = mapped_column(String(40), default="")    # text
    operator: Mapped[str] = mapped_column(String(60), default="")
    holder_employee_id: Mapped[str | None] = mapped_column(ForeignKey("employees.id"), nullable=True, index=True)
    allocation_status: Mapped[str] = mapped_column(String(20), default="available")
    network_status: Mapped[str] = mapped_column(String(20), default="unknown")
    calls_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    possession: Mapped[str] = mapped_column(String(80), default="")
    charge_owner: Mapped[str] = mapped_column(String(160), default="")
    agent_details: Mapped[str] = mapped_column(Text, default="")
    phone_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # M02 MOB asset running WhatsApp
    backup_email: Mapped[str] = mapped_column(String(254), default="")
    last_backup_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    backup_interval_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_payment: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    period_unit: Mapped[str] = mapped_column(String(10), default="")
    alert_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_handover: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    legacy_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(20), default="ui")
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SimEvent(Base):
    __tablename__ = "sim_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sim_id: Mapped[str] = mapped_column(ForeignKey("sims.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # created, handover, return, possession
    employee_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    result: Mapped[str] = mapped_column(String(20), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Line(Base):
    """Internet + landline record. Two independent renewal tracks on one line."""
    __tablename__ = "lines"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    branch_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    landline_number: Mapped[str] = mapped_column(String(40), default="")
    provider: Mapped[str] = mapped_column(String(80), default="")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)  # one of the "3 main lines" on the dashboard
    quota_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    used_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    remaining_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_reading_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reading_source: Mapped[str] = mapped_column(String(20), default="")
    warn_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    critical_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_last_payment: Mapped[date | None] = mapped_column(Date, nullable=True)
    net_period_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    net_period_unit: Mapped[str] = mapped_column(String(10), default="")
    net_alert_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    land_last_payment: Mapped[date | None] = mapped_column(Date, nullable=True)
    land_period_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    land_period_unit: Mapped[str] = mapped_column(String(10), default="")
    land_alert_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    legacy_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    service_type: Mapped[str] = mapped_column(String(60), default="")
    provider: Mapped[str] = mapped_column(String(120), default="")
    name: Mapped[str] = mapped_column(String(160), default="")
    plan: Mapped[str] = mapped_column(String(120), default="")
    account_owner: Mapped[str] = mapped_column(String(254), default="")
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | paused | cancelled
    last_payment: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    period_unit: Mapped[str] = mapped_column(String(10), default="")
    alert_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage_unit: Mapped[str] = mapped_column(String(30), default="")
    quota_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    remaining: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_reading_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reading_source: Mapped[str] = mapped_column(String(20), default="")
    warn_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    critical_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    legacy_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class UsageReading(Base):
    __tablename__ = "usage_readings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    target_type: Mapped[str] = mapped_column(String(20))  # line | subscription
    target_id: Mapped[str] = mapped_column(String(36), index=True)
    remaining: Mapped[float | None] = mapped_column(Float, nullable=True)
    used: Mapped[float | None] = mapped_column(Float, nullable=True)
    total: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(30), default="GB")
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(20))  # manual | api | script
    applied: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(String(200), default="")
    recorded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RenewalEvent(Base):
    __tablename__ = "renewal_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    target_type: Mapped[str] = mapped_column(String(20))  # sim | line_internet | line_landline | subscription
    target_id: Mapped[str] = mapped_column(String(36), index=True)
    company_id: Mapped[str] = mapped_column(String(36))
    renewed_on: Mapped[date] = mapped_column(Date)
    period_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    period_unit: Mapped[str] = mapped_column(String(10), default="")
    next_due: Mapped[date | None] = mapped_column(Date, nullable=True)
    receipt_ref: Mapped[str] = mapped_column(String(120), default="")
    financial_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # link to M05, not a copy
    notes: Mapped[str] = mapped_column(Text, default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    recorded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BackupConfirmation(Base):
    __tablename__ = "backup_confirmations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sim_id: Mapped[str] = mapped_column(ForeignKey("sims.id"), index=True)
    phone_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    backup_email: Mapped[str] = mapped_column(String(254), default="")
    performed_on: Mapped[date] = mapped_column(Date)
    method: Mapped[str] = mapped_column(String(120), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    confirmed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- computations
def due_info(last: date | None, value: int | None, unit: str | None, alert_days: int | None) -> dict:
    nxt = add_period(last, value, unit)
    left = days_until(nxt)
    if nxt is None:
        state = "incomplete"
    elif left < 0:
        state = "overdue"
    elif alert_days is not None and left <= alert_days:
        state = "due_soon"
    else:
        state = "ok"
    return {"next_due": iso(nxt), "days_left": left, "due_state": state}


def usage_state(remaining: float | None, warn: float | None, critical: float | None) -> str:
    if remaining is None:
        return "incomplete"
    if warn is None and critical is None:
        return "thresholds_missing"
    if critical is not None and remaining <= critical:
        return "urgent"
    if warn is not None and remaining < warn:
        return "prepare"
    return "normal"


USAGE_AR = {"incomplete": "لا توجد قراءة", "thresholds_missing": "حدد العتبات", "urgent": "تجديد عاجل",
            "prepare": "جهز رصيد", "normal": "طبيعي"}
DUE_AR = {"incomplete": "بيانات التجديد ناقصة", "overdue": "متأخر", "due_soon": "قرب التجديد", "ok": "منتظم"}

SIM_FIELDS = ["id", "code", "country", "company_id", "department_id", "name", "number", "iccid", "operator",
              "holder_employee_id", "allocation_status", "network_status", "calls_enabled", "possession",
              "charge_owner", "agent_details", "phone_asset_id", "backup_email", "last_backup_at",
              "backup_interval_days", "last_payment", "period_value", "period_unit", "alert_days", "last_handover",
              "notes", "source", "version"]
LINE_FIELDS = ["id", "code", "company_id", "branch_id", "name", "landline_number", "provider", "is_primary",
               "quota_gb", "used_gb", "remaining_gb", "last_reading_at", "last_reading_source", "warn_gb",
               "critical_gb", "net_last_payment", "net_period_value", "net_period_unit", "net_alert_days",
               "land_last_payment", "land_period_value", "land_period_unit", "land_alert_days", "notes", "version"]
SUB_FIELDS = ["id", "code", "company_id", "service_type", "provider", "name", "plan", "account_owner", "status",
              "last_payment", "period_value", "period_unit", "alert_days", "usage_unit", "quota_total", "remaining",
              "last_reading_at", "last_reading_source", "warn_level", "critical_level", "notes", "version"]


def backup_info(s: Sim) -> dict:
    due = add_period(s.last_backup_at, s.backup_interval_days, "days")
    left = days_until(due)
    state = "incomplete" if due is None else ("overdue" if left < 0 else "ok")
    return {"backup_due": iso(due), "backup_days_left": left, "backup_state": state}


def sim_out(s: Sim, db: Session | None = None) -> dict:
    d = row(s, SIM_FIELDS) | due_info(s.last_payment, s.period_value, s.period_unit, s.alert_days) | backup_info(s)
    d["due_ar"] = DUE_AR[d["due_state"]]
    if db is not None and s.holder_employee_id:
        e = db.get(Employee, s.holder_employee_id)
        d["holder_name"] = e.full_name if e else ""
    return d


def line_out(ln: Line) -> dict:
    d = row(ln, LINE_FIELDS)
    d["internet"] = due_info(ln.net_last_payment, ln.net_period_value, ln.net_period_unit, ln.net_alert_days)
    d["landline"] = due_info(ln.land_last_payment, ln.land_period_value, ln.land_period_unit, ln.land_alert_days)
    d["usage_state"] = usage_state(ln.remaining_gb, ln.warn_gb, ln.critical_gb)
    d["usage_ar"] = USAGE_AR[d["usage_state"]]
    return d


def sub_out(s: Subscription) -> dict:
    d = row(s, SUB_FIELDS) | due_info(s.last_payment, s.period_value, s.period_unit, s.alert_days)
    d["usage_state"] = usage_state(s.remaining, s.warn_level, s.critical_level) if s.usage_unit else "not_tracked"
    d["usage_ar"] = USAGE_AR.get(d["usage_state"], "غير متابع")
    d["due_ar"] = DUE_AR[d["due_state"]]
    return d


# ---------------------------------------------------------------- schemas
def _period_ok(unit: str | None) -> None:
    if unit not in (None, "", *PERIOD_UNITS):
        raise bad("وحدة المدة لازم تكون أيام أو شهور")


def _thresholds_ok(warn: float | None, critical: float | None) -> None:
    if warn is not None and critical is not None and critical > warn:
        raise bad("عتبة التجديد العاجل لازم تكون أقل من أو تساوي عتبة تجهيز الرصيد")


class SimIn(BaseModel):
    country: str = Field(pattern="^(EG|SA)$")
    company_id: str
    code: str | None = Field(default=None, max_length=40)
    department_id: str | None = None
    name: str = ""
    number: str = ""
    iccid: str = ""
    operator: str = ""
    network_status: str = Field(default="unknown", pattern="^(working|not_working|unknown)$")
    calls_enabled: bool = False
    possession: str = ""
    charge_owner: str = ""
    agent_details: str = ""
    phone_asset_id: str | None = None
    backup_email: str = ""
    backup_interval_days: int | None = Field(default=None, ge=1)
    last_payment: date | None = None
    period_value: int | None = Field(default=None, ge=1)
    period_unit: str = ""
    alert_days: int | None = Field(default=None, ge=0)
    allocation_status: str = Field(default="available", pattern="^(available|in_stock|suspended|cancelled)$")
    notes: str = ""


class SimPatch(BaseModel):
    version: int
    department_id: str | None = None
    name: str | None = None
    number: str | None = None
    iccid: str | None = None
    operator: str | None = None
    network_status: str | None = Field(default=None, pattern="^(working|not_working|unknown)$")
    calls_enabled: bool | None = None
    charge_owner: str | None = None
    agent_details: str | None = None
    phone_asset_id: str | None = None
    backup_email: str | None = None
    backup_interval_days: int | None = Field(default=None, ge=1)
    period_value: int | None = Field(default=None, ge=1)
    period_unit: str | None = None
    alert_days: int | None = Field(default=None, ge=0)
    allocation_status: str | None = Field(default=None, pattern="^(available|in_stock|suspended|cancelled)$")
    notes: str | None = None


SIM_EDITABLE = set(SimPatch.model_fields) - {"version"}


class LineIn(BaseModel):
    company_id: str
    code: str | None = Field(default=None, max_length=40)
    branch_id: str | None = None
    name: str = ""
    landline_number: str = ""
    provider: str = ""
    is_primary: bool = False
    quota_gb: float | None = Field(default=None, ge=0)
    warn_gb: float | None = Field(default=None, ge=0)
    critical_gb: float | None = Field(default=None, ge=0)
    net_last_payment: date | None = None
    net_period_value: int | None = Field(default=None, ge=1)
    net_period_unit: str = ""
    net_alert_days: int | None = Field(default=None, ge=0)
    land_last_payment: date | None = None
    land_period_value: int | None = Field(default=None, ge=1)
    land_period_unit: str = ""
    land_alert_days: int | None = Field(default=None, ge=0)
    notes: str = ""


class LinePatch(BaseModel):
    version: int
    branch_id: str | None = None
    name: str | None = None
    landline_number: str | None = None
    provider: str | None = None
    is_primary: bool | None = None
    quota_gb: float | None = Field(default=None, ge=0)
    warn_gb: float | None = Field(default=None, ge=0)
    critical_gb: float | None = Field(default=None, ge=0)
    net_period_value: int | None = Field(default=None, ge=1)
    net_period_unit: str | None = None
    net_alert_days: int | None = Field(default=None, ge=0)
    land_period_value: int | None = Field(default=None, ge=1)
    land_period_unit: str | None = None
    land_alert_days: int | None = Field(default=None, ge=0)
    notes: str | None = None


LINE_EDITABLE = set(LinePatch.model_fields) - {"version"}


class SubIn(BaseModel):
    company_id: str
    code: str | None = Field(default=None, max_length=40)
    service_type: str = ""
    provider: str = ""
    name: str = Field(min_length=1)
    plan: str = ""
    account_owner: str = ""
    last_payment: date | None = None
    period_value: int | None = Field(default=None, ge=1)
    period_unit: str = ""
    alert_days: int | None = Field(default=None, ge=0)
    usage_unit: str = ""
    quota_total: float | None = Field(default=None, ge=0)
    warn_level: float | None = Field(default=None, ge=0)
    critical_level: float | None = Field(default=None, ge=0)
    notes: str = ""


class SubPatch(BaseModel):
    version: int
    service_type: str | None = None
    provider: str | None = None
    name: str | None = None
    plan: str | None = None
    account_owner: str | None = None
    status: str | None = Field(default=None, pattern="^(active|paused|cancelled)$")
    period_value: int | None = Field(default=None, ge=1)
    period_unit: str | None = None
    alert_days: int | None = Field(default=None, ge=0)
    usage_unit: str | None = None
    quota_total: float | None = Field(default=None, ge=0)
    warn_level: float | None = Field(default=None, ge=0)
    critical_level: float | None = Field(default=None, ge=0)
    notes: str | None = None


SUB_EDITABLE = set(SubPatch.model_fields) - {"version"}


class ReadingIn(BaseModel):
    target_type: str = Field(pattern="^(line|subscription)$")
    target_id: str
    remaining: float | None = Field(default=None, ge=0)
    used: float | None = Field(default=None, ge=0)
    total: float | None = Field(default=None, ge=0)
    read_at: datetime | None = None
    source: str = Field(default="manual", pattern="^(manual|api|script)$")
    note: str = Field(default="", max_length=200)  # e.g. "my.te.eg عبر الإضافة · تاريخ تجديد WE 2026-12-01" (info only)


class RenewalIn(BaseModel):
    target_type: str = Field(pattern="^(sim|line_internet|line_landline|subscription)$")
    target_id: str
    renewed_on: date
    period_value: int | None = Field(default=None, ge=1)
    period_unit: str | None = None
    receipt_ref: str = ""
    financial_event_id: str | None = None
    notes: str = ""
    idempotency_key: str | None = Field(default=None, max_length=80)


class SimHandoverIn(BaseModel):
    employee_id: str
    notes: str = ""


class SimReturnIn(BaseModel):
    result: str = Field(pattern="^(returned|damaged|missing)$")
    notes: str = ""


class PossessionIn(BaseModel):
    possession: str = Field(min_length=1, max_length=80)
    charge_owner: str | None = None
    agent_details: str | None = None
    notes: str = ""


class BackupIn(BaseModel):
    performed_on: date
    method: str = Field(min_length=2, max_length=120)
    notes: str = ""


class LookupIn(BaseModel):
    value: str = Field(min_length=1, max_length=120)


# ---------------------------------------------------------------- shared operations
def sim_handover(db: Session, s: Sim, employee_id: str, actor_id: str | None, notes: str = "",
                 source: str = "ui") -> None:
    emp = assert_can_receive_custody(db, employee_id)
    if emp.company_id != s.company_id:
        raise bad("الشريحة تابعة لشركة غير شركة الموظف")
    if s.allocation_status != "available":
        raise conflict(f"الشريحة حالتها «{ALLOCATION.get(s.allocation_status)}» ومينفعش تتسلم")
    s.allocation_status, s.holder_employee_id, s.last_handover = "assigned", employee_id, today()
    s.version += 1
    db.add(SimEvent(sim_id=s.id, kind="handover", employee_id=employee_id, detail=f"{notes} [source={source}]".strip(),
                    actor_id=actor_id))


def sim_return(db: Session, s: Sim, result: str, actor_id: str | None, notes: str = "") -> None:
    if s.allocation_status != "assigned" or not s.holder_employee_id:
        raise conflict("الشريحة مش مسلمة لحد")
    emp, s.holder_employee_id = s.holder_employee_id, None
    s.allocation_status = "suspended" if result == "missing" else "available"
    if result == "damaged":
        s.network_status = "not_working"
    s.version += 1
    db.add(SimEvent(sim_id=s.id, kind="return", employee_id=emp, result=result, detail=notes, actor_id=actor_id))


def employee_sims(db: Session, employee_id: str) -> list[Sim]:
    return list(db.scalars(select(Sim).where(Sim.holder_employee_id == employee_id)))


def apply_reading(db: Session, target, target_type: str, body: ReadingIn, actor_id: str | None) -> UsageReading:  # noqa: ANN001
    """Store every reading; apply it only if it is newer than the current one and not an automated reading
    older than a manual reading (M04 §0.6)."""
    from ..common import now as _now
    read_at = body.read_at or _now()
    if read_at.tzinfo is None:
        from datetime import timezone
        read_at = read_at.replace(tzinfo=timezone.utc)
    remaining, used, total = body.remaining, body.used, body.total
    if target_type == "line":
        total = total if total is not None else target.quota_gb
    else:
        total = total if total is not None else target.quota_total
    if remaining is None and used is not None and total is not None:
        remaining = max(total - used, 0)
    if remaining is None:
        raise bad("لازم تدخل المتبقي أو (المستهلك + السعة)")
    cur_at = target.last_reading_at
    if cur_at is not None and cur_at.tzinfo is None:
        from datetime import timezone
        cur_at = cur_at.replace(tzinfo=timezone.utc)
    applied, note = True, ""
    if cur_at is not None and read_at <= cur_at:
        applied, note = False, "قراءة أقدم من الحالية — اتسجلت بدون ما تغيّر الرصيد"
        if body.source != "manual" and target.last_reading_source == "manual":
            note = "قراءة آلية أقدم من قراءة يدوية مؤكدة — مرفوضة من التطبيق (تعارض)"
    note = " · ".join(x for x in (note, body.note.strip()) if x)[:200]
    reading = UsageReading(target_type=target_type, target_id=target.id, remaining=remaining, used=used, total=total,
                           unit="GB" if target_type == "line" else (target.usage_unit or ""), read_at=read_at,
                           source=body.source, applied=applied, note=note, recorded_by=actor_id)
    db.add(reading)
    if applied:
        if target_type == "line":
            target.remaining_gb, target.used_gb = remaining, used if used is not None else (
                total - remaining if total is not None else target.used_gb)
            if body.total is not None:
                target.quota_gb = body.total
        else:
            target.remaining = remaining
            if body.total is not None:
                target.quota_total = body.total
        target.last_reading_at, target.last_reading_source = read_at, body.source
        target.version += 1
    return reading


# ---------------------------------------------------------------- API
router = APIRouter(prefix="/api/technical", tags=["M04 technical"])


def _load(db: Session, p: Principal, model, obj_id: str):  # noqa: ANN001, ANN202
    obj = db.get(model, obj_id)
    if obj is None:
        raise not_found()
    ensure_company(p, obj.company_id)
    return obj


@router.get("/meta")
def meta(_: Principal = Depends(require("technical:view")), db: Session = Depends(get_db)) -> dict:
    lists: dict[str, list[str]] = {k: [] for k in DEFAULT_LOOKUPS}
    for lk in db.scalars(select(Lookup).where(Lookup.list_key.in_(list(DEFAULT_LOOKUPS)), Lookup.active.is_(True))
                         .order_by(Lookup.sort, Lookup.value)):
        lists[lk.list_key].append(lk.value)
    return {"lists": lists, "allocation": ALLOCATION, "network": NETWORK, "period_units": {"days": "أيام",
            "months": "شهور"}, "usage": USAGE_AR, "due": DUE_AR}


@router.post("/meta/{list_key}", status_code=201)
def add_lookup(list_key: str, body: LookupIn, request: Request, p: Principal = Depends(require("technical:edit")),
               db: Session = Depends(get_db)) -> dict:
    if list_key not in DEFAULT_LOOKUPS:
        raise not_found("قائمة غير معروفة")
    if db.scalar(select(Lookup).where(Lookup.list_key == list_key, Lookup.value == body.value)):
        raise conflict("القيمة موجودة")
    db.add(Lookup(list_key=list_key, value=body.value, sort=999))
    audit.record(db, "lookup.added", actor_id=p.user.id, entity="lookup", entity_id=list_key,
                 details={"value": body.value}, ip=client_ip(request))
    db.commit()
    return {"ok": True}


# --- SIMs
@router.get("/sims")
def list_sims(country: str | None = None, company_id: str | None = None, holder: str | None = None,
              p: Principal = Depends(require("technical:view")), db: Session = Depends(get_db)) -> list[dict]:
    s = scope_filter(select(Sim), p, Sim.company_id).order_by(Sim.code)
    for col, val in ((Sim.country, country), (Sim.company_id, company_id), (Sim.holder_employee_id, holder)):
        if val:
            s = s.where(col == val)
    return [sim_out(x, db) for x in db.scalars(s)]


@router.post("/sims", status_code=201)
def create_sim(body: SimIn, request: Request, p: Principal = Depends(require("technical:create")),
               db: Session = Depends(get_db)) -> dict:
    ensure_company(p, body.company_id)
    get_company(db, body.company_id)
    _period_ok(body.period_unit)
    data = body.model_dump()
    data["code"] = body.code or next_code(db, Sim, Sim.code, f"SIM-{body.country}-", 3)
    if db.scalar(select(Sim).where(Sim.code == data["code"])):
        raise conflict("كود الشريحة مستخدم")
    s = Sim(**data)
    db.add(s)
    db.flush()
    db.add(SimEvent(sim_id=s.id, kind="created", actor_id=p.user.id, detail=f"possession={s.possession}"))
    audit.record(db, "sim.created", actor_id=p.user.id, entity="sim", entity_id=s.id, company_id=s.company_id,
                 details={"code": s.code}, ip=client_ip(request))
    db.commit()
    return sim_out(s, db)


@router.get("/sims/{sim_id}")
def sim_detail(sim_id: str, p: Principal = Depends(require("technical:view")), db: Session = Depends(get_db)) -> dict:
    s = _load(db, p, Sim, sim_id)
    d = sim_out(s, db)
    d["events"] = [row(e, ["id", "kind", "employee_id", "result", "detail", "at"])
                   for e in db.scalars(select(SimEvent).where(SimEvent.sim_id == s.id).order_by(SimEvent.at.desc()))]
    d["renewals"] = [row(r, ["id", "renewed_on", "period_value", "period_unit", "next_due", "receipt_ref",
                             "financial_event_id", "notes"])
                     for r in db.scalars(select(RenewalEvent).where(RenewalEvent.target_type == "sim",
                                                                    RenewalEvent.target_id == s.id)
                                         .order_by(RenewalEvent.renewed_on.desc()))]
    d["backups"] = [row(b, ["id", "performed_on", "method", "backup_email", "notes", "confirmed_by", "created_at"])
                    for b in db.scalars(select(BackupConfirmation).where(BackupConfirmation.sim_id == s.id)
                                        .order_by(BackupConfirmation.performed_on.desc()))]
    return d


@router.patch("/sims/{sim_id}")
def update_sim(sim_id: str, body: SimPatch, request: Request, p: Principal = Depends(require("technical:edit")),
               db: Session = Depends(get_db)) -> dict:
    s = _load(db, p, Sim, sim_id)
    check_version(s, body.version)
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    _period_ok(changes.get("period_unit"))
    if changes.get("allocation_status") and s.allocation_status == "assigned":
        raise conflict("الشريحة مسلمة — استخدم زر الاسترجاع")
    applied = apply_changes(s, changes, SIM_EDITABLE)
    if applied:
        audit.record(db, "sim.updated", actor_id=p.user.id, entity="sim", entity_id=s.id, company_id=s.company_id,
                     details=applied, ip=client_ip(request))
    db.commit()
    return sim_out(s, db)


@router.post("/sims/{sim_id}/handover")
def handover_sim(sim_id: str, body: SimHandoverIn, request: Request,
                 p: Principal = Depends(require("technical:edit")), db: Session = Depends(get_db)) -> dict:
    s = _load(db, p, Sim, sim_id)
    sim_handover(db, s, body.employee_id, p.user.id, body.notes)
    audit.record(db, "sim.handover", actor_id=p.user.id, entity="sim", entity_id=s.id, company_id=s.company_id,
                 details={"employee_id": body.employee_id}, ip=client_ip(request))
    db.commit()
    return sim_out(s, db)


@router.post("/sims/{sim_id}/return")
def return_sim(sim_id: str, body: SimReturnIn, request: Request, p: Principal = Depends(require("technical:edit")),
               db: Session = Depends(get_db)) -> dict:
    s = _load(db, p, Sim, sim_id)
    sim_return(db, s, body.result, p.user.id, body.notes)
    audit.record(db, "sim.returned", actor_id=p.user.id, entity="sim", entity_id=s.id, company_id=s.company_id,
                 details={"result": body.result}, ip=client_ip(request))
    db.commit()
    return sim_out(s, db)


@router.post("/sims/{sim_id}/possession")
def change_possession(sim_id: str, body: PossessionIn, request: Request,
                      p: Principal = Depends(require("technical:edit")), db: Session = Depends(get_db)) -> dict:
    s = _load(db, p, Sim, sim_id)
    prev = s.possession
    s.possession = body.possession
    if body.charge_owner is not None:
        s.charge_owner = body.charge_owner
    if body.agent_details is not None:
        s.agent_details = body.agent_details
    s.version += 1
    db.add(SimEvent(sim_id=s.id, kind="possession", detail=f"{prev} → {body.possession}. {body.notes}".strip(),
                    actor_id=p.user.id))
    audit.record(db, "sim.possession", actor_id=p.user.id, entity="sim", entity_id=s.id, company_id=s.company_id,
                 details={"from": prev, "to": body.possession}, ip=client_ip(request))
    db.commit()
    return sim_out(s, db)


@router.post("/sims/{sim_id}/backup-confirm", status_code=201)
def confirm_backup(sim_id: str, body: BackupIn, request: Request, p: Principal = Depends(require("technical:edit")),
                   db: Session = Depends(get_db)) -> dict:
    s = _load(db, p, Sim, sim_id)
    if body.performed_on > today():
        raise bad("تاريخ النسخة مينفعش يكون في المستقبل")
    db.add(BackupConfirmation(sim_id=s.id, phone_asset_id=s.phone_asset_id, backup_email=s.backup_email,
                              performed_on=body.performed_on, method=body.method, notes=body.notes,
                              confirmed_by=p.user.id))
    if s.last_backup_at is None or body.performed_on > s.last_backup_at:
        s.last_backup_at = body.performed_on
        s.version += 1
    audit.record(db, "sim.backup_confirmed", actor_id=p.user.id, entity="sim", entity_id=s.id,
                 company_id=s.company_id, details={"performed_on": body.performed_on.isoformat()},
                 ip=client_ip(request))
    db.commit()
    return sim_out(s, db)


# --- Internet & landlines
@router.get("/lines")
def list_lines(company_id: str | None = None, p: Principal = Depends(require("technical:view")),
               db: Session = Depends(get_db)) -> list[dict]:
    s = scope_filter(select(Line), p, Line.company_id).order_by(Line.is_primary.desc(), Line.code)
    if company_id:
        s = s.where(Line.company_id == company_id)
    return [line_out(x) for x in db.scalars(s)]


@router.post("/lines", status_code=201)
def create_line(body: LineIn, request: Request, p: Principal = Depends(require("technical:create")),
                db: Session = Depends(get_db)) -> dict:
    ensure_company(p, body.company_id)
    get_company(db, body.company_id)
    _period_ok(body.net_period_unit)
    _period_ok(body.land_period_unit)
    _thresholds_ok(body.warn_gb, body.critical_gb)
    data = body.model_dump()
    data["code"] = body.code or next_code(db, Line, Line.code, "NET-", 3)
    if db.scalar(select(Line).where(Line.code == data["code"])):
        raise conflict("كود الخط مستخدم")
    ln = Line(**data)
    db.add(ln)
    db.flush()
    audit.record(db, "line.created", actor_id=p.user.id, entity="line", entity_id=ln.id, company_id=ln.company_id,
                 details={"code": ln.code}, ip=client_ip(request))
    db.commit()
    return line_out(ln)


@router.get("/lines/{line_id}")
def line_detail(line_id: str, p: Principal = Depends(require("technical:view")), db: Session = Depends(get_db)) -> dict:
    ln = _load(db, p, Line, line_id)
    d = line_out(ln)
    d["readings"] = [row(r, ["id", "remaining", "used", "total", "read_at", "source", "applied", "note"])
                     for r in db.scalars(select(UsageReading).where(UsageReading.target_type == "line",
                                                                    UsageReading.target_id == ln.id)
                                         .order_by(UsageReading.read_at.desc()).limit(200))]
    d["renewals"] = [row(r, ["id", "target_type", "renewed_on", "period_value", "period_unit", "next_due",
                             "receipt_ref", "financial_event_id", "notes"])
                     for r in db.scalars(select(RenewalEvent).where(RenewalEvent.target_id == ln.id)
                                         .order_by(RenewalEvent.renewed_on.desc()))]
    return d


@router.patch("/lines/{line_id}")
def update_line(line_id: str, body: LinePatch, request: Request, p: Principal = Depends(require("technical:edit")),
                db: Session = Depends(get_db)) -> dict:
    ln = _load(db, p, Line, line_id)
    check_version(ln, body.version)
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    _period_ok(changes.get("net_period_unit"))
    _period_ok(changes.get("land_period_unit"))
    _thresholds_ok(changes.get("warn_gb", ln.warn_gb), changes.get("critical_gb", ln.critical_gb))
    applied = apply_changes(ln, changes, LINE_EDITABLE)
    if applied:
        audit.record(db, "line.updated", actor_id=p.user.id, entity="line", entity_id=ln.id, company_id=ln.company_id,
                     details=applied, ip=client_ip(request))
    db.commit()
    return line_out(ln)


# --- Subscriptions
@router.get("/subscriptions")
def list_subs(company_id: str | None = None, service_type: str | None = None,
              p: Principal = Depends(require("technical:view")), db: Session = Depends(get_db)) -> list[dict]:
    s = scope_filter(select(Subscription), p, Subscription.company_id).order_by(Subscription.code)
    if company_id:
        s = s.where(Subscription.company_id == company_id)
    if service_type:
        s = s.where(Subscription.service_type == service_type)
    return [sub_out(x) for x in db.scalars(s)]


@router.post("/subscriptions", status_code=201)
def create_sub(body: SubIn, request: Request, p: Principal = Depends(require("technical:create")),
               db: Session = Depends(get_db)) -> dict:
    ensure_company(p, body.company_id)
    get_company(db, body.company_id)
    _period_ok(body.period_unit)
    _thresholds_ok(body.warn_level, body.critical_level)
    data = body.model_dump()
    data["code"] = body.code or next_code(db, Subscription, Subscription.code, "LIC-", 3)
    if db.scalar(select(Subscription).where(Subscription.code == data["code"])):
        raise conflict("كود الاشتراك مستخدم")
    s = Subscription(**data)
    db.add(s)
    db.flush()
    audit.record(db, "subscription.created", actor_id=p.user.id, entity="subscription", entity_id=s.id,
                 company_id=s.company_id, details={"code": s.code}, ip=client_ip(request))
    db.commit()
    return sub_out(s)


@router.get("/subscriptions/{sub_id}")
def sub_detail(sub_id: str, p: Principal = Depends(require("technical:view")), db: Session = Depends(get_db)) -> dict:
    s = _load(db, p, Subscription, sub_id)
    d = sub_out(s)
    d["readings"] = [row(r, ["id", "remaining", "total", "unit", "read_at", "source", "applied", "note"])
                     for r in db.scalars(select(UsageReading).where(UsageReading.target_type == "subscription",
                                                                    UsageReading.target_id == s.id)
                                         .order_by(UsageReading.read_at.desc()).limit(200))]
    d["renewals"] = [row(r, ["id", "renewed_on", "period_value", "period_unit", "next_due", "receipt_ref",
                             "financial_event_id", "notes"])
                     for r in db.scalars(select(RenewalEvent).where(RenewalEvent.target_id == s.id)
                                         .order_by(RenewalEvent.renewed_on.desc()))]
    return d


@router.patch("/subscriptions/{sub_id}")
def update_sub(sub_id: str, body: SubPatch, request: Request, p: Principal = Depends(require("technical:edit")),
               db: Session = Depends(get_db)) -> dict:
    s = _load(db, p, Subscription, sub_id)
    check_version(s, body.version)
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    _period_ok(changes.get("period_unit"))
    _thresholds_ok(changes.get("warn_level", s.warn_level), changes.get("critical_level", s.critical_level))
    applied = apply_changes(s, changes, SUB_EDITABLE)
    if applied:
        audit.record(db, "subscription.updated", actor_id=p.user.id, entity="subscription", entity_id=s.id,
                     company_id=s.company_id, details=applied, ip=client_ip(request))
    db.commit()
    return sub_out(s)


# --- Readings & renewals
@router.post("/readings", status_code=201)
def add_reading(body: ReadingIn, request: Request, p: Principal = Depends(require("technical:edit")),
                db: Session = Depends(get_db)) -> dict:
    target = _load(db, p, Line if body.target_type == "line" else Subscription, body.target_id)
    r = apply_reading(db, target, body.target_type, body, p.user.id)
    audit.record(db, "usage.reading", actor_id=p.user.id, entity=body.target_type, entity_id=target.id,
                 company_id=target.company_id, details={"remaining": r.remaining, "source": r.source,
                                                        "applied": r.applied}, ip=client_ip(request))
    db.commit()
    return {"reading": row(r, ["id", "remaining", "used", "total", "read_at", "source", "applied", "note"]),
            "target": line_out(target) if body.target_type == "line" else sub_out(target)}


def record_renewal(db: Session, body: RenewalIn, actor_id: str | None, p: Principal | None = None) -> RenewalEvent:
    tt = body.target_type
    model = Sim if tt == "sim" else Line if tt.startswith("line") else Subscription
    target = db.get(model, body.target_id)
    if target is None:
        raise not_found("العنصر المطلوب تجديده غير موجود")
    if p is not None:
        ensure_company(p, target.company_id)
    if body.idempotency_key:
        ex = db.scalar(select(RenewalEvent).where(RenewalEvent.idempotency_key == body.idempotency_key))
        if ex:
            return ex
    _period_ok(body.period_unit)
    prefix = {"line_internet": "net_", "line_landline": "land_"}.get(tt, "")
    last_attr = f"{prefix}last_payment"
    if getattr(target, last_attr) and body.renewed_on < getattr(target, last_attr):
        raise bad("تاريخ التجديد أقدم من آخر تجديد مسجل")
    if body.period_value:
        setattr(target, f"{prefix}period_value", body.period_value)
    if body.period_unit:
        setattr(target, f"{prefix}period_unit", body.period_unit)
    setattr(target, last_attr, body.renewed_on)  # next due computed from the NEW date (M04 test 6)
    target.version += 1
    pv, pu = getattr(target, f"{prefix}period_value"), getattr(target, f"{prefix}period_unit")
    ev = RenewalEvent(target_type=tt, target_id=target.id, company_id=target.company_id, renewed_on=body.renewed_on,
                      period_value=pv, period_unit=pu or "", next_due=add_period(body.renewed_on, pv, pu),
                      receipt_ref=body.receipt_ref, financial_event_id=body.financial_event_id, notes=body.notes,
                      idempotency_key=body.idempotency_key, recorded_by=actor_id)
    db.add(ev)
    db.flush()
    return ev


@router.post("/renewals", status_code=201)
def renew(body: RenewalIn, request: Request, p: Principal = Depends(require("technical:edit")),
          db: Session = Depends(get_db)) -> dict:
    ev = record_renewal(db, body, p.user.id, p)
    audit.record(db, "renewal.recorded", actor_id=p.user.id, entity=body.target_type, entity_id=body.target_id,
                 company_id=ev.company_id, details={"renewed_on": body.renewed_on.isoformat(),
                                                    "next_due": iso(ev.next_due)}, ip=client_ip(request))
    db.commit()
    return row(ev, ["id", "target_type", "target_id", "renewed_on", "period_value", "period_unit", "next_due",
                    "financial_event_id"])


@router.get("/renewals/upcoming")
def upcoming(days: int = Query(30, ge=0, le=366), p: Principal = Depends(require("technical:view")),
             db: Session = Depends(get_db)) -> dict:
    """Renewal center: one list across SIMs, internet, landline, subscriptions (+ incomplete records)."""
    items, incomplete = [], []

    def push(kind: str, kind_ar: str, obj, info: dict, link: str, title: str) -> None:  # noqa: ANN001
        entry = {"kind": kind, "kind_ar": kind_ar, "id": obj.id, "code": obj.code, "title": title,
                 "company_id": obj.company_id, "link": link, **info}
        if info["due_state"] == "incomplete":
            incomplete.append(entry)
        elif info["days_left"] is not None and info["days_left"] <= days:
            items.append(entry)

    for s in db.scalars(scope_filter(select(Sim), p, Sim.company_id).where(Sim.allocation_status != "cancelled")):
        push(f"sim_{s.country.lower()}", "شريحة سعودية" if s.country == "SA" else "شريحة مصرية", s,
             due_info(s.last_payment, s.period_value, s.period_unit, s.alert_days), f"sim/{s.id}", s.name or s.number)
    for ln in db.scalars(scope_filter(select(Line), p, Line.company_id)):
        push("internet", "باقة إنترنت", ln, due_info(ln.net_last_payment, ln.net_period_value, ln.net_period_unit,
                                                     ln.net_alert_days), f"line/{ln.id}", ln.name or ln.landline_number)
        if ln.landline_number:
            push("landline", "اشتراك أرضي", ln, due_info(ln.land_last_payment, ln.land_period_value,
                                                        ln.land_period_unit, ln.land_alert_days), f"line/{ln.id}",
                 ln.name or ln.landline_number)
    for sub in db.scalars(scope_filter(select(Subscription), p, Subscription.company_id)
                          .where(Subscription.status == "active")):
        push("subscription", sub.service_type or "اشتراك", sub,
             due_info(sub.last_payment, sub.period_value, sub.period_unit, sub.alert_days), f"subscription/{sub.id}",
             sub.name)
    items.sort(key=lambda x: x["days_left"])
    return {"days": days, "items": items, "incomplete": incomplete}
