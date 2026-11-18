"""Employee e-mail accounts register (الإيميلات والحسابات).

Rules:
- Every account gets a permanent code: <CompanyPrefix>-MAIL-001 (MAIL-001 when the company has no prefix).
- The address is unique (stored lower-case) and never silently overwritten.
- An account can belong to one employee at a time; giving it to an employee in offboarding is blocked.
- Marking an account as "primary" copies its address to the employee's e-mail field.
- The password never sits in the DB: it is encrypted in the secret store and every reveal is audited.
- Offboarding: IT cannot approve while the employee still has active accounts; an IT-check of type
  "mailbox" with result "deprovisioned" suspends the account.
- Every change leaves an append-only event.
"""
from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .. import audit
from ..common import (apply_changes, bad, check_version, conflict, ensure_company, get_company, iso, next_code,
                      not_found, row, scope_filter)
from ..db import Base
from ..deps import Principal, client_ip, get_db, require
from ..models import Company, new_id, utcnow
from .people import Employee, assert_can_receive_custody

KINDS = {"personal": "شخصي", "shared": "مشترك", "service": "خدمة/نظام"}
STATUSES = {"active": "نشط", "suspended": "موقوف", "deleted": "محذوف"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PW_PREFIX = "mail-pw-"


class Mailbox(Base):
    __tablename__ = "mailboxes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    address: Mapped[str] = mapped_column(String(254), unique=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    employee_id: Mapped[str | None] = mapped_column(ForeignKey("employees.id"), nullable=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    kind: Mapped[str] = mapped_column(String(20), default="personal")
    provider: Mapped[str] = mapped_column(String(80), default="")
    plan: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    aliases: Mapped[str] = mapped_column(Text, default="")
    forwarding: Mapped[str] = mapped_column(String(254), default="")
    recovery: Mapped[str] = mapped_column(String(254), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    vault_ref: Mapped[str] = mapped_column(String(120), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class MailboxEvent(Base):
    __tablename__ = "mailbox_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    mailbox_id: Mapped[str] = mapped_column(ForeignKey("mailboxes.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    employee_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


FIELDS = ["id", "code", "address", "company_id", "employee_id", "display_name", "kind", "provider", "plan", "status",
          "is_primary", "mfa_enabled", "aliases", "forwarding", "recovery", "notes", "version", "created_at"]
EDITABLE = {"display_name", "kind", "provider", "plan", "mfa_enabled", "aliases", "forwarding", "recovery", "notes"}


def next_mail_code(db: Session, company_id: str) -> str:
    c = db.get(Company, company_id)
    prefix = f"{c.asset_prefix}-MAIL-" if c and c.asset_prefix else "MAIL-"
    return next_code(db, Mailbox, Mailbox.code, prefix, 3)


def norm_address(v: str) -> str:
    a = (v or "").strip().lower()
    if not EMAIL_RE.match(a):
        raise bad(f"عنوان إيميل غير صحيح: {v}")
    return a


def mailbox_out(db: Session, m: Mailbox) -> dict:
    d = row(m, FIELDS)
    e = db.get(Employee, m.employee_id) if m.employee_id else None
    d["employee_name"] = e.full_name if e else ""
    d["employee_code"] = e.employee_code if e else ""
    c = db.get(Company, m.company_id)
    d["company_name"] = c.name if c else ""
    d["has_password"] = (m.vault_ref or "").startswith("vault:")
    d["kind_label"] = KINDS.get(m.kind, m.kind)
    d["status_label"] = STATUSES.get(m.status, m.status)
    return d


def employee_mailboxes(db: Session, employee_id: str, active_only: bool = False) -> list[dict]:
    s = select(Mailbox).where(Mailbox.employee_id == employee_id).order_by(Mailbox.is_primary.desc(), Mailbox.address)
    if active_only:
        s = s.where(Mailbox.status == "active")
    return [mailbox_out(db, m) for m in db.scalars(s)]


def _event(db: Session, m: Mailbox, kind: str, actor: str | None, employee_id: str | None = None,
           detail: str = "") -> None:
    db.add(MailboxEvent(mailbox_id=m.id, kind=kind, employee_id=employee_id, detail=detail, actor_id=actor))


def _set_primary(db: Session, m: Mailbox) -> None:
    """Only one primary per employee; the employee's e-mail field follows the primary account."""
    if not m.employee_id:
        m.is_primary = False
        return
    for other in db.scalars(select(Mailbox).where(Mailbox.employee_id == m.employee_id, Mailbox.id != m.id,
                                                  Mailbox.is_primary.is_(True))):
        other.is_primary = False
        other.version += 1
    m.is_primary = True
    e = db.get(Employee, m.employee_id)
    if e and e.email != m.address:
        e.email = m.address
        e.version += 1


def suspend_mailbox(db: Session, m: Mailbox, actor: str | None, detail: str = "") -> None:
    """Used by offboarding too: the account stops being active (the employee link stays for history)."""
    prev = m.employee_id
    m.status = "suspended"
    m.is_primary = False
    m.version += 1
    _event(db, m, "suspended", actor, prev, detail)


def load_mailbox(db: Session, p: Principal, mailbox_id: str) -> Mailbox:
    m = db.get(Mailbox, mailbox_id)
    if m is None:
        raise not_found("الإيميل غير موجود")
    ensure_company(p, m.company_id)
    return m


class MailboxIn(BaseModel):
    company_id: str
    address: str = Field(min_length=3, max_length=254)
    code: str = ""
    employee_id: str | None = None
    display_name: str = ""
    kind: str = Field(default="personal", pattern="^(personal|shared|service)$")
    provider: str = ""
    plan: str = ""
    is_primary: bool = False
    mfa_enabled: bool = False
    aliases: str = ""
    forwarding: str = ""
    recovery: str = ""
    notes: str = ""
    password: str = Field(default="", max_length=200)


class MailboxPatch(BaseModel):
    version: int
    address: str | None = None
    display_name: str | None = None
    kind: str | None = Field(default=None, pattern="^(personal|shared|service)$")
    provider: str | None = None
    plan: str | None = None
    mfa_enabled: bool | None = None
    is_primary: bool | None = None
    aliases: str | None = None
    forwarding: str | None = None
    recovery: str | None = None
    notes: str | None = None


class AssignIn(BaseModel):
    employee_id: str
    is_primary: bool = False
    notes: str = ""


class NoteIn(BaseModel):
    notes: str = ""


class PasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)


def _store(request: Request):  # noqa: ANN202
    from ..secrets_store import SecretStoreError, store_for
    try:
        return store_for(request.app.state.settings)
    except SecretStoreError as e:
        raise bad(str(e)) from None


def create_mailbox(db: Session, p: Principal, body: MailboxIn, store=None) -> Mailbox:  # noqa: ANN001
    """Shared by the API and the Excel import."""
    ensure_company(p, body.company_id)
    get_company(db, body.company_id)
    addr = norm_address(body.address)
    if db.scalar(select(Mailbox).where(Mailbox.address == addr)):
        raise conflict(f"الإيميل {addr} مسجل بالفعل")
    code = (body.code or "").strip()
    if code and db.scalar(select(Mailbox).where(Mailbox.code == code)):
        raise conflict(f"الكود {code} مستخدم")
    m = Mailbox(code=code or next_mail_code(db, body.company_id), address=addr, company_id=body.company_id,
                display_name=body.display_name, kind=body.kind, provider=body.provider, plan=body.plan,
                mfa_enabled=body.mfa_enabled, aliases=body.aliases, forwarding=body.forwarding,
                recovery=body.recovery, notes=body.notes)
    db.add(m)
    db.flush()
    _event(db, m, "created", p.user.id)
    if body.employee_id:
        e = assert_can_receive_custody(db, body.employee_id)
        ensure_company(p, e.company_id)
        m.employee_id = e.id
        _event(db, m, "assigned", p.user.id, e.id)
        if body.is_primary or not e.email:
            _set_primary(db, m)
    if body.password:
        if store is None:
            raise bad("مخزن الأسرار غير متاح")
        store.set(PW_PREFIX + m.id, body.password)
        m.vault_ref = "vault:" + PW_PREFIX + m.id
    return m


router = APIRouter(prefix="/api/mailboxes", tags=["mailboxes"])


@router.get("")
def list_mailboxes(company_id: str | None = None, status: str | None = None, employee_id: str | None = None,
                   q: str | None = None, p: Principal = Depends(require("technical:view")),
                   db: Session = Depends(get_db)) -> list[dict]:
    s = scope_filter(select(Mailbox), p, Mailbox.company_id).order_by(Mailbox.code)
    if company_id:
        s = s.where(Mailbox.company_id == company_id)
    if status:
        s = s.where(Mailbox.status == status)
    if employee_id:
        s = s.where(Mailbox.employee_id == employee_id)
    if q:
        like = f"%{q.strip().lower()}%"
        s = s.where(Mailbox.address.like(like) | Mailbox.code.like(f"%{q.strip()}%"))
    return [mailbox_out(db, m) for m in db.scalars(s)]


@router.post("", status_code=201)
def add_mailbox(body: MailboxIn, request: Request, p: Principal = Depends(require("technical:create")),
                db: Session = Depends(get_db)) -> dict:
    if body.password and not p.can("technical:view_secrets"):
        raise bad("حفظ كلمة مرور الإيميل محتاج صلاحية الأسرار")
    m = create_mailbox(db, p, body, _store(request) if body.password else None)
    audit.record(db, "mailbox.created", actor_id=p.user.id, entity="mailbox", entity_id=m.id, company_id=m.company_id,
                 details={"code": m.code, "address": m.address, "employee": m.employee_id}, ip=client_ip(request))
    db.commit()
    return mailbox_out(db, m)


@router.get("/{mailbox_id}")
def mailbox_detail(mailbox_id: str, p: Principal = Depends(require("technical:view")),
                   db: Session = Depends(get_db)) -> dict:
    m = load_mailbox(db, p, mailbox_id)
    d = mailbox_out(db, m)
    names = {}
    events = []
    for ev in db.scalars(select(MailboxEvent).where(MailboxEvent.mailbox_id == m.id).order_by(MailboxEvent.at)):
        if ev.employee_id and ev.employee_id not in names:
            e = db.get(Employee, ev.employee_id)
            names[ev.employee_id] = e.full_name if e else ""
        events.append({"kind": ev.kind, "employee_name": names.get(ev.employee_id, ""), "detail": ev.detail,
                       "at": iso(ev.at)})
    d["events"] = events
    return d


@router.patch("/{mailbox_id}")
def edit_mailbox(mailbox_id: str, body: MailboxPatch, request: Request,
                 p: Principal = Depends(require("technical:edit")), db: Session = Depends(get_db)) -> dict:
    m = load_mailbox(db, p, mailbox_id)
    check_version(m, body.version)
    changes = body.model_dump(exclude_unset=True, exclude={"version", "address", "is_primary"})
    applied = apply_changes(m, changes, EDITABLE)
    bumped = bool(applied)
    if body.address is not None:
        addr = norm_address(body.address)
        if addr != m.address:
            if db.scalar(select(Mailbox).where(Mailbox.address == addr, Mailbox.id != m.id)):
                raise conflict(f"الإيميل {addr} مسجل بالفعل")
            _event(db, m, "renamed", p.user.id, m.employee_id, f"{m.address} → {addr}")
            applied["address"] = addr
            m.address = addr
            if not bumped:
                m.version += 1
                bumped = True
            if m.is_primary and m.employee_id:
                e = db.get(Employee, m.employee_id)
                if e:
                    e.email = addr
                    e.version += 1
    if body.is_primary is True and not m.is_primary:
        if m.status != "active":
            raise conflict("مينفعش تخلي إيميل موقوف هو الأساسي")
        if not m.employee_id:
            raise conflict("الإيميل مش مربوط بموظف")
        _set_primary(db, m)
        if not bumped:
            m.version += 1
        applied["is_primary"] = True
    elif body.is_primary is False and m.is_primary:
        m.is_primary = False
        if not bumped:
            m.version += 1
        applied["is_primary"] = False
    if applied:
        audit.record(db, "mailbox.updated", actor_id=p.user.id, entity="mailbox", entity_id=m.id,
                     company_id=m.company_id, details=applied, ip=client_ip(request))
    db.commit()
    return mailbox_out(db, m)


@router.post("/{mailbox_id}/assign")
def assign_mailbox(mailbox_id: str, body: AssignIn, request: Request,
                   p: Principal = Depends(require("technical:edit")), db: Session = Depends(get_db)) -> dict:
    m = load_mailbox(db, p, mailbox_id)
    if m.status != "active":
        raise conflict("الإيميل موقوف/محذوف — فعّله الأول")
    e = assert_can_receive_custody(db, body.employee_id)
    ensure_company(p, e.company_id)
    prev = m.employee_id
    if prev and prev != e.id:
        _event(db, m, "unassigned", p.user.id, prev, "نقل لموظف آخر")
    m.employee_id = e.id
    m.is_primary = False
    m.version += 1
    _event(db, m, "assigned", p.user.id, e.id, body.notes)
    if body.is_primary or not e.email:
        _set_primary(db, m)
    audit.record(db, "mailbox.assigned", actor_id=p.user.id, entity="mailbox", entity_id=m.id,
                 company_id=m.company_id, details={"code": m.code, "from": prev, "to": e.id}, ip=client_ip(request))
    db.commit()
    return mailbox_out(db, m)


@router.post("/{mailbox_id}/unassign")
def unassign_mailbox(mailbox_id: str, body: NoteIn, request: Request,
                     p: Principal = Depends(require("technical:edit")), db: Session = Depends(get_db)) -> dict:
    m = load_mailbox(db, p, mailbox_id)
    if not m.employee_id:
        raise conflict("الإيميل مش مربوط بموظف")
    prev = m.employee_id
    _event(db, m, "unassigned", p.user.id, prev, body.notes)
    m.employee_id, m.is_primary = None, False
    m.version += 1
    audit.record(db, "mailbox.unassigned", actor_id=p.user.id, entity="mailbox", entity_id=m.id,
                 company_id=m.company_id, details={"code": m.code, "from": prev}, ip=client_ip(request))
    db.commit()
    return mailbox_out(db, m)


@router.post("/{mailbox_id}/status/{new_status}")
def set_mailbox_status(mailbox_id: str, new_status: str, body: NoteIn, request: Request,
                       p: Principal = Depends(require("technical:edit")), db: Session = Depends(get_db)) -> dict:
    if new_status not in STATUSES:
        raise bad("الحالة: active / suspended / deleted")
    m = load_mailbox(db, p, mailbox_id)
    if m.status == new_status:
        return mailbox_out(db, m)
    if new_status == "active" and m.employee_id:
        assert_can_receive_custody(db, m.employee_id)
    old = m.status
    if new_status == "suspended":
        suspend_mailbox(db, m, p.user.id, body.notes)
    else:
        m.status = new_status
        if new_status == "deleted":
            m.is_primary = False
        m.version += 1
        _event(db, m, new_status, p.user.id, m.employee_id, body.notes)
    audit.record(db, "mailbox.status", actor_id=p.user.id, entity="mailbox", entity_id=m.id, company_id=m.company_id,
                 details={"code": m.code, "from": old, "to": new_status}, ip=client_ip(request))
    db.commit()
    return mailbox_out(db, m)


@router.put("/{mailbox_id}/password")
def set_mail_password(mailbox_id: str, body: PasswordIn, request: Request,
                      p: Principal = Depends(require("technical:edit", "technical:view_secrets")),
                      db: Session = Depends(get_db)) -> dict:
    m = load_mailbox(db, p, mailbox_id)
    _store(request).set(PW_PREFIX + m.id, body.password)
    m.vault_ref = "vault:" + PW_PREFIX + m.id
    m.version += 1
    audit.record(db, "mailbox.password_set", actor_id=p.user.id, entity="mailbox", entity_id=m.id,
                 company_id=m.company_id, ip=client_ip(request))
    db.commit()
    return {"has_password": True}


@router.post("/{mailbox_id}/password/reveal")
def reveal_mail_password(mailbox_id: str, request: Request, p: Principal = Depends(require("technical:view_secrets")),
                         db: Session = Depends(get_db)) -> dict:
    m = load_mailbox(db, p, mailbox_id)
    if not (m.vault_ref or "").startswith("vault:"):
        raise not_found("مفيش كلمة مرور محفوظة للإيميل ده")
    value = _store(request).get(m.vault_ref[6:])
    if value is None:
        raise not_found("كلمة المرور مش موجودة في المخزن")
    audit.record(db, "mailbox.password_revealed", actor_id=p.user.id, entity="mailbox", entity_id=m.id,
                 company_id=m.company_id, details={"code": m.code}, ip=client_ip(request))
    db.commit()
    return {"password": value}


@router.delete("/{mailbox_id}/password")
def delete_mail_password(mailbox_id: str, request: Request,
                         p: Principal = Depends(require("technical:edit", "technical:view_secrets")),
                         db: Session = Depends(get_db)) -> dict:
    m = load_mailbox(db, p, mailbox_id)
    if (m.vault_ref or "").startswith("vault:"):
        _store(request).delete(m.vault_ref[6:])
    m.vault_ref = ""
    m.version += 1
    audit.record(db, "mailbox.password_deleted", actor_id=p.user.id, entity="mailbox", entity_id=m.id,
                 company_id=m.company_id, ip=client_ip(request))
    db.commit()
    return {"has_password": False}

