"""M03 — Employee journey (RC1; partly approved — open policies are configuration with safe defaults).

Approved & enforced:
- Decision matrix (HR owns the final button): T+/HR+ -> onboarding, T+/HR- -> waiting list,
  T-/HR- -> archive, T-/HR+ -> archive. Rejected applications are archived, never deleted.
- HR interview content and sensitive applicant data are hidden from IT (needs employees:view_sensitive).
- Five standard joining documents; military status mandatory for males only, "not applicable" for females.
- Document templates per company are versioned and immutable; acknowledgments pin the exact version.
- A checkbox is never a legal signature: acknowledgments record method (paper scan / e-acknowledgment),
  and legally-binding e-sign stays disabled (policy pending).
- Offboarding opens -> custody freeze immediately and irreversibly (assets & SIMs, every channel).
- IT reverse-audits custody and approves its scope; HR may print clearance WITHOUT pre-signing;
  six authorities sign on paper; final close needs IT approval + all Clear + uploaded signed scan.
  "Not Clear" blocks closing (exception workflow disabled — not an approved policy).
"""
from __future__ import annotations

import html
from datetime import date, datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .. import audit
from ..common import (apply_changes, bad, check_version, conflict, ensure_company, forbid, get_company, iso,
                      next_code, not_found, now, row, scope_filter, today)
from ..db import Base
from ..deps import Principal, client_ip, get_db, require
from ..models import Company, Department, new_id, utcnow
from .assets import ALLOCATION as ASSET_ALLOCATION
from .assets import Asset, do_return, employee_assets
from .files import FileObject, require_file
from .mailboxes import Mailbox, employee_mailboxes, suspend_mailbox
from .people import EMPLOYEE_PUBLIC_FIELDS, Employee, EmployeeSensitive, get_employee, next_employee_code
from .technical import Sim, employee_sims, sim_return

JOINING_DOCS = {"national_id": "صورة البطاقة", "birth_certificate": "شهادة الميلاد", "photo": "صورة شخصية",
                "degree": "المؤهل الدراسي", "military_status": "الموقف من التجنيد"}
ACK_KINDS = {"internal_regulations": "اللائحة الداخلية", "nda": "اتفاقية عدم الإفصاح NDA", "contract": "عقد العمل",
             "custody": "استلام العهدة", "work_start": "إقرار استلام العمل"}
TEMPLATE_KINDS = {**{k: v for k, v in ACK_KINDS.items() if k != "custody"}, "custody": "نموذج العهدة",
                  "computer_custody": "بيان عهدة الحاسب الآلي", "other_custody": "نموذج العهدة الأخرى (المنفصلة)",
                  "clearance": "نموذج إخلاء الطرف"}
DEFAULT_AUTHORITIES = ["مدير الإدارة / القسم", "تقنية المعلومات", "مدير المكتب", "المالية / الحسابات",
                       "الموارد البشرية", "الإدارة العليا / المدير التنفيذي"]
IT_TASK_KINDS = {"asset": "تجهيز جهاز/أصل", "email": "إيميل الشركة", "phone": "تليفون / نظام الاتصال",
                 "sim": "شريحة", "access": "صلاحيات دخول (مرجع فقط)", "software": "برامج وتراخيص",
                 "biometric": "كود البصمة", "other": "أخرى"}
STAGES = {"new": "جديد", "interviewing": "مقابلات", "waiting": "قائمة الانتظار", "onboarding": "تعيين",
          "archived": "أرشيف المرفوضين"}
POLICY = {  # configurable, NOT approved policies (DECISIONS_AND_GAPS_RC1)
    "legal_e_sign_enabled": False,
    "not_clear_exception_enabled": False,
    "external_notifications_enabled": False,
    "auto_delete_hr_records": False,
    "offboarding_alert_days_before": 1,
    "clearance_sequential": True,  # the six authorities sign in their configured order

}


# ---------------------------------------------------------------- tables
class Application(Base):
    __tablename__ = "applications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(String(30), unique=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    application_date: Mapped[date] = mapped_column(Date)
    position: Mapped[str] = mapped_column(String(200))
    full_name: Mapped[str] = mapped_column(String(200))
    gender: Mapped[str] = mapped_column(String(10), default="")
    email: Mapped[str] = mapped_column(String(254), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    qualification: Mapped[str] = mapped_column(String(200), default="")
    graduation_year: Mapped[str] = mapped_column(String(10), default="")
    military_status: Mapped[str] = mapped_column(String(80), default="")
    marital_status: Mapped[str] = mapped_column(String(40), default="")
    notice_period: Mapped[str] = mapped_column(String(60), default="")
    experience: Mapped[list] = mapped_column(JSON, default=list)
    courses: Mapped[list] = mapped_column(JSON, default=list)
    references: Mapped[list] = mapped_column(JSON, default=list)
    # sensitive (HR only)
    expected_salary: Mapped[str] = mapped_column(String(40), default="")
    national_id: Mapped[str] = mapped_column(String(40), default="")
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    address: Mapped[str] = mapped_column(Text, default="")
    stage: Mapped[str] = mapped_column(String(20), default="new")
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual | google_form
    source_form_response_id: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True)
    employee_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Interview(Base):
    __tablename__ = "interviews"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # hr | technical
    interviewer: Mapped[str] = mapped_column(String(200))
    held_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attended: Mapped[bool] = mapped_column(Boolean, default=True)
    scores: Mapped[dict] = mapped_column(JSON, default=dict)      # technical: performance/behavior/team_dynamic/skills /10
    hr_fields: Mapped[dict] = mapped_column(JSON, default=dict)   # past_exp/why/strength/weakness/future_plans
    notes: Mapped[str] = mapped_column(Text, default="")
    recommendation: Mapped[str] = mapped_column(String(20))       # accepted | not_accepted
    recorded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    technical_result: Mapped[str] = mapped_column(String(20))
    hr_result: Mapped[str] = mapped_column(String(20))
    outcome: Mapped[str] = mapped_column(String(20))  # onboarding | waiting | archived
    reason: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[str] = mapped_column(String(36))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Onboarding(Base):
    __tablename__ = "onboardings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), unique=True)
    company_id: Mapped[str] = mapped_column(String(36))
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | completed
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ITTask(Base):
    __tablename__ = "it_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    onboarding_id: Mapped[str] = mapped_column(ForeignKey("onboardings.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(String(300), default="")
    ref: Mapped[str] = mapped_column(String(200), default="")  # asset code / email address / reference only
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | done | verified | skipped
    done_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    evidence_file_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class DocumentTemplate(Base):
    __tablename__ = "document_templates"
    __table_args__ = (UniqueConstraint("company_id", "kind", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    kind: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer)
    file_id: Mapped[str] = mapped_column(String(36))
    sha256: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    uploaded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DocumentAck(Base):
    __tablename__ = "document_acks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    template_id: Mapped[str | None] = mapped_column(String(36), nullable=True)   # exact version acknowledged
    template_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    method: Mapped[str] = mapped_column(String(20))  # paper_scan | e_ack
    evidence_file_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    acknowledged_on: Mapped[date] = mapped_column(Date)
    recorded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JoiningDocument(Base):
    __tablename__ = "joining_documents"
    __table_args__ = (UniqueConstraint("employee_id", "doc_type"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), index=True)
    doc_type: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="missing")  # missing|received|verified|not_applicable|hr_review
    file_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Offboarding(Base):
    __tablename__ = "offboardings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id"), unique=True)
    company_id: Mapped[str] = mapped_column(String(36))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    opened_by: Mapped[str] = mapped_column(String(36))
    last_day: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(30), default="it_review")
    # it_review -> ready_for_paper (IT approved) -> closed
    it_approved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    it_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    printed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_scan_file_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    signed_scan_verified_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class ITCheck(Base):
    __tablename__ = "it_checks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    offboarding_id: Mapped[str] = mapped_column(ForeignKey("offboardings.id"), index=True)
    item_type: Mapped[str] = mapped_column(String(20))  # asset | sim | account | other
    item_ref: Mapped[str] = mapped_column(String(200))
    result: Mapped[str] = mapped_column(String(30))
    serial_checked: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    checked_by: Mapped[str] = mapped_column(String(36))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ClearanceApproval(Base):
    __tablename__ = "clearance_approvals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    offboarding_id: Mapped[str] = mapped_column(ForeignKey("offboardings.id"), index=True)
    order: Mapped[int] = mapped_column(Integer)
    authority: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | clear | not_clear
    signer_name: Mapped[str] = mapped_column(String(200), default="")
    signed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    recorded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


# ---------------------------------------------------------------- matrix & helpers
def decide(technical: str, hr: str) -> str:
    """The approved matrix. Only T+ and HR+ onboard."""
    if technical == "accepted" and hr == "accepted":
        return "onboarding"
    if technical == "accepted" and hr == "not_accepted":
        return "waiting"
    return "archived"


APP_PUBLIC = ["id", "candidate_id", "company_id", "application_date", "position", "full_name", "stage", "source",
              "employee_id", "version", "created_at"]
APP_HR = APP_PUBLIC + ["gender", "email", "phone", "qualification", "graduation_year", "military_status",
                       "marital_status", "notice_period", "experience", "courses", "references", "expected_salary",
                       "national_id", "birth_date", "address"]


def app_out(a: Application, p: Principal) -> dict:
    d = row(a, APP_HR if p.can("employees:view_sensitive") else APP_PUBLIC)
    d["stage_ar"] = STAGES.get(a.stage, a.stage)
    return d


def interview_out(i: Interview, p: Principal) -> dict:
    d = row(i, ["id", "kind", "interviewer", "held_at", "attended", "recommendation", "created_at"])
    if i.kind == "technical" or p.can("employees:view_sensitive"):
        d.update(scores=i.scores, notes=i.notes)
    if i.kind == "hr" and p.can("employees:view_sensitive"):
        d["hr_fields"] = i.hr_fields
    return d


def employee_out(e: Employee, p: Principal, db: Session) -> dict:
    d = row(e, EMPLOYEE_PUBLIC_FIELDS)
    if p.can("employees:view_sensitive"):
        s = db.get(EmployeeSensitive, e.id)
        d["gender"] = e.gender
        d["sensitive"] = row(s, ["national_id", "birth_date", "salary", "address"]) if s else {}
    dep = db.get(Department, e.department_id) if e.department_id else None
    d["department_name"] = dep.name if dep else ""
    return d


def ensure_joining_rows(db: Session, e: Employee) -> None:
    have = {j.doc_type for j in db.scalars(select(JoiningDocument).where(JoiningDocument.employee_id == e.id))}
    for t in JOINING_DOCS:
        if t in have:
            continue
        status = "missing"
        if t == "military_status":
            status = {"female": "not_applicable", "male": "missing"}.get(e.gender, "hr_review")
        db.add(JoiningDocument(employee_id=e.id, doc_type=t, status=status))


def _load_app(db: Session, p: Principal, app_id: str) -> Application:
    a = db.get(Application, app_id)
    if a is None:
        raise not_found("الطلب غير موجود")
    ensure_company(p, a.company_id)
    return a


def _load_emp(db: Session, p: Principal, emp_id: str) -> Employee:
    e = get_employee(db, emp_id)
    ensure_company(p, e.company_id)
    return e


def current_custody(db: Session, employee_id: str) -> dict:
    assets = employee_assets(db, employee_id)
    sims = employee_sims(db, employee_id)
    return {"assets": [{"id": a.id, "code": a.code, "category": a.category_code, "model": a.model,
                        "serial": a.serial, "status_ar": ASSET_ALLOCATION.get(a.allocation_status, "")}
                       for a in assets],
            "sims": [{"id": s.id, "code": s.code, "number": s.number, "country": s.country} for s in sims]}


# ---------------------------------------------------------------- schemas
class EmployeeIn(BaseModel):
    company_id: str
    full_name: str = Field(min_length=2, max_length=200)
    employee_code: str | None = Field(default=None, max_length=40)
    branch_id: str | None = None
    department_id: str | None = None
    job_title: str = ""
    project: str = ""
    gender: str = Field(default="", pattern="^(male|female|)$")
    email: str = ""
    phone: str = ""
    biometric_code: str = ""
    hire_date: date | None = None


class EmployeePatch(BaseModel):
    version: int
    full_name: str | None = Field(default=None, min_length=2)
    employee_code: str | None = None
    branch_id: str | None = None
    department_id: str | None = None
    job_title: str | None = None
    project: str | None = None
    email: str | None = None
    phone: str | None = None
    biometric_code: str | None = None
    hire_date: date | None = None


EMP_EDITABLE = set(EmployeePatch.model_fields) - {"version"}


class SensitiveIn(BaseModel):
    national_id: str = Field(default="", max_length=40)
    birth_date: date | None = None
    salary: str = Field(default="", max_length=40)
    address: str = ""
    gender: str | None = Field(default=None, pattern="^(male|female)$")


class ApplicationIn(BaseModel):
    company_id: str
    position: str = Field(min_length=1)
    full_name: str = Field(min_length=2)
    application_date: date | None = None
    gender: str = Field(default="", pattern="^(male|female|)$")
    email: str = ""
    phone: str = ""
    qualification: str = ""
    graduation_year: str = ""
    military_status: str = ""
    marital_status: str = ""
    notice_period: str = ""
    experience: list = Field(default_factory=list)
    courses: list = Field(default_factory=list)
    references: list = Field(default_factory=list)
    expected_salary: str = ""
    national_id: str = ""
    birth_date: date | None = None
    address: str = ""
    source_form_response_id: str | None = Field(default=None, max_length=120)


class InterviewIn(BaseModel):
    kind: str = Field(pattern="^(hr|technical)$")
    interviewer: str = Field(min_length=2)
    held_at: datetime | None = None
    attended: bool = True
    scores: dict = Field(default_factory=dict)
    hr_fields: dict = Field(default_factory=dict)
    notes: str = ""
    recommendation: str = Field(pattern="^(accepted|not_accepted)$")


class DecisionIn(BaseModel):
    reason: str = ""
    start_date: date | None = None
    department_id: str | None = None
    branch_id: str | None = None
    job_title: str | None = None


class TaskIn(BaseModel):
    kind: str
    description: str = ""
    ref: str = ""
    due_date: date | None = None


class TaskUpdate(BaseModel):
    status: str = Field(pattern="^(done|verified|skipped)$")
    ref: str | None = None
    notes: str = ""
    evidence_file_id: str | None = None  # proof of execution (screenshot / signed form) — required before IT approval


class TemplateIn(BaseModel):
    company_id: str
    kind: str
    title: str = Field(min_length=2)
    file_id: str


class AckIn(BaseModel):
    kind: str
    method: str = Field(pattern="^(paper_scan|e_ack)$")
    evidence_file_id: str | None = None
    acknowledged_on: date | None = None


class JoiningIn(BaseModel):
    file_id: str


class OffboardIn(BaseModel):
    employee_id: str
    last_day: date
    reason: str = ""


class ITCheckIn(BaseModel):
    item_type: str = Field(pattern="^(asset|sim|mailbox|account|other)$")
    item_ref: str = Field(min_length=1)
    result: str = Field(pattern="^(returned|damaged|missing|deprovisioned|pending_verification|not_applicable)$")
    serial_checked: bool = False
    notes: str = ""


class AuthorityIn(BaseModel):
    status: str = Field(pattern="^(clear|not_clear|pending)$")
    signer_name: str = ""
    signed_date: date | None = None
    notes: str = ""


class ScanIn(BaseModel):
    file_id: str


# ---------------------------------------------------------------- API
router = APIRouter(prefix="/api", tags=["M03 journey"])


@router.get("/journey/meta")
def meta(_: Principal = Depends(require("employees:view"))) -> dict:
    return {"joining_docs": JOINING_DOCS, "ack_kinds": ACK_KINDS, "template_kinds": TEMPLATE_KINDS,
            "authorities": DEFAULT_AUTHORITIES, "it_task_kinds": IT_TASK_KINDS, "stages": STAGES, "policy": POLICY}


class FormUrlIn(BaseModel):
    company_id: str
    url: str = Field(max_length=300, pattern=r"^https://(docs\.google\.com/forms/|forms\.gle/)[A-Za-z0-9_\-/?=&.]*$")


@router.get("/journey/form-url")
def get_form_url(company_id: str, p: Principal = Depends(require("employees:view")),
                 db: Session = Depends(get_db)) -> dict:
    from ..models import Lookup
    ensure_company(p, company_id)
    lk = db.scalar(select(Lookup).where(Lookup.list_key == f"application_form_url:{company_id}"))
    return {"url": lk.value if lk else ""}


@router.put("/journey/form-url")
def set_form_url(body: FormUrlIn, request: Request, p: Principal = Depends(require("employees:edit")),
                 db: Session = Depends(get_db)) -> dict:
    """Public Google Form link shown as QR for candidates (Google-hosted; works while the local host is off)."""
    from ..models import Lookup
    ensure_company(p, body.company_id)
    key = f"application_form_url:{body.company_id}"
    lk = db.scalar(select(Lookup).where(Lookup.list_key == key))
    if lk is None:
        db.add(Lookup(list_key=key, value=body.url))
    else:
        lk.value = body.url
    audit.record(db, "journey.form_url_set", actor_id=p.user.id, entity="company", entity_id=body.company_id,
                 company_id=body.company_id, ip=client_ip(request))
    db.commit()
    return {"url": body.url}


# --- employees
@router.get("/employees")
def list_employees(company_id: str | None = None, status: str | None = None, q: str | None = None,
                   p: Principal = Depends(require("employees:view")), db: Session = Depends(get_db)) -> list[dict]:
    s = scope_filter(select(Employee), p, Employee.company_id).order_by(Employee.full_name)
    if company_id:
        s = s.where(Employee.company_id == company_id)
    if status:
        s = s.where(Employee.status == status)
    if q:
        s = s.where(Employee.full_name.ilike(f"%{q}%") | Employee.employee_code.ilike(f"%{q}%"))
    return [employee_out(e, p, db) for e in db.scalars(s)]


@router.post("/employees", status_code=201)
def create_employee(body: EmployeeIn, request: Request, p: Principal = Depends(require("employees:create")),
                    db: Session = Depends(get_db)) -> dict:
    ensure_company(p, body.company_id)
    get_company(db, body.company_id)
    if body.employee_code and db.scalar(select(Employee).where(Employee.employee_code == body.employee_code)):
        raise conflict("الكود الوظيفي مستخدم")
    data = body.model_dump()
    data["employee_code"] = (data.get("employee_code") or "").strip() or next_employee_code(db, body.company_id)
    e = Employee(**data, status="active")
    db.add(e)
    db.flush()
    ensure_joining_rows(db, e)
    audit.record(db, "employee.created", actor_id=p.user.id, entity="employee", entity_id=e.id,
                 company_id=e.company_id, details={"name": e.full_name}, ip=client_ip(request))
    db.commit()
    return employee_out(e, p, db)


@router.post("/employees/assign-codes")
def assign_missing_codes(request: Request, company_id: str | None = None,
                         p: Principal = Depends(require("employees:edit")), db: Session = Depends(get_db)) -> dict:
    """Give an automatic code to every employee that has none (existing codes are never changed)."""
    s = scope_filter(select(Employee), p, Employee.company_id).where(
        (Employee.employee_code.is_(None)) | (Employee.employee_code == "")).order_by(Employee.created_at)
    if company_id:
        s = s.where(Employee.company_id == company_id)
    done = []
    for e in db.scalars(s):
        e.employee_code = next_employee_code(db, e.company_id)
        e.version += 1
        db.flush()
        done.append(e.employee_code)
    audit.record(db, "employee.codes_assigned", actor_id=p.user.id, entity="employee", entity_id="bulk",
                 details={"count": len(done)}, ip=client_ip(request))
    db.commit()
    return {"assigned": len(done), "codes": done[:50]}


@router.get("/employees/{emp_id}")
def employee_detail(emp_id: str, p: Principal = Depends(require("employees:view")),
                    db: Session = Depends(get_db)) -> dict:
    e = _load_emp(db, p, emp_id)
    d = employee_out(e, p, db)
    d["custody"] = current_custody(db, e.id)
    d["mailboxes"] = employee_mailboxes(db, e.id) if p.can("technical:view") else []
    docs = list(db.scalars(select(JoiningDocument).where(JoiningDocument.employee_id == e.id)))
    d["joining_documents"] = [{"doc_type": j.doc_type, "label": JOINING_DOCS.get(j.doc_type, j.doc_type),
                               "status": j.status, "file_id": j.file_id if p.can("employees:view_sensitive") else None}
                              for j in docs]
    d["acks"] = [row(a, ["id", "kind", "template_id", "template_version", "method", "acknowledged_on",
                         "evidence_file_id"])
                 for a in db.scalars(select(DocumentAck).where(DocumentAck.employee_id == e.id)
                                     .order_by(DocumentAck.created_at))]
    ob = db.scalar(select(Onboarding).where(Onboarding.employee_id == e.id))
    off = db.scalar(select(Offboarding).where(Offboarding.employee_id == e.id))
    d["onboarding_id"], d["offboarding_id"] = (ob.id if ob else None), (off.id if off else None)
    return d


@router.patch("/employees/{emp_id}")
def update_employee(emp_id: str, body: EmployeePatch, request: Request,
                    p: Principal = Depends(require("employees:edit")), db: Session = Depends(get_db)) -> dict:
    e = _load_emp(db, p, emp_id)
    check_version(e, body.version)
    applied = apply_changes(e, body.model_dump(exclude_unset=True, exclude={"version"}), EMP_EDITABLE)
    if applied:
        audit.record(db, "employee.updated", actor_id=p.user.id, entity="employee", entity_id=e.id,
                     company_id=e.company_id, details=applied, ip=client_ip(request))
    db.commit()
    return employee_out(e, p, db)


@router.put("/employees/{emp_id}/sensitive")
def set_sensitive(emp_id: str, body: SensitiveIn, request: Request,
                  p: Principal = Depends(require("employees:view_sensitive", "employees:edit")),
                  db: Session = Depends(get_db)) -> dict:
    e = _load_emp(db, p, emp_id)
    s = db.get(EmployeeSensitive, e.id) or EmployeeSensitive(employee_id=e.id)
    s.national_id, s.birth_date, s.salary, s.address = body.national_id, body.birth_date, body.salary, body.address
    db.add(s)
    if body.gender and body.gender != e.gender:
        e.gender = body.gender
        jd = db.scalar(select(JoiningDocument).where(JoiningDocument.employee_id == e.id,
                                                     JoiningDocument.doc_type == "military_status"))
        if jd and jd.status in ("missing", "hr_review", "not_applicable") and not jd.file_id:
            jd.status = "not_applicable" if body.gender == "female" else "missing"
    audit.record(db, "employee.sensitive_updated", actor_id=p.user.id, entity="employee", entity_id=e.id,
                 company_id=e.company_id, details={"fields": ["national_id", "birth_date", "salary", "address"]},
                 ip=client_ip(request))
    db.commit()
    return employee_out(e, p, db)


@router.post("/employees/{emp_id}/joining-docs/{doc_type}")
def upload_joining(emp_id: str, doc_type: str, body: JoiningIn, request: Request,
                   p: Principal = Depends(require("employees:view_sensitive", "employees:edit")),
                   db: Session = Depends(get_db)) -> dict:
    e = _load_emp(db, p, emp_id)
    if doc_type not in JOINING_DOCS:
        raise bad("نوع مستند غير ضمن المستندات الخمسة المعتمدة")
    require_file(db, body.file_id, "joining_document")
    ensure_joining_rows(db, e)
    db.flush()
    jd = db.scalar(select(JoiningDocument).where(JoiningDocument.employee_id == e.id,
                                                 JoiningDocument.doc_type == doc_type))
    jd.file_id, jd.status, jd.verified_by = body.file_id, "received", None
    audit.record(db, "joining_doc.received", actor_id=p.user.id, entity="employee", entity_id=e.id,
                 company_id=e.company_id, details={"doc_type": doc_type}, ip=client_ip(request))
    db.commit()
    return {"doc_type": doc_type, "status": jd.status}


@router.post("/employees/{emp_id}/joining-docs/{doc_type}/verify")
def verify_joining(emp_id: str, doc_type: str, request: Request,
                   p: Principal = Depends(require("employees:view_sensitive", "employees:approve")),
                   db: Session = Depends(get_db)) -> dict:
    e = _load_emp(db, p, emp_id)
    jd = db.scalar(select(JoiningDocument).where(JoiningDocument.employee_id == e.id,
                                                 JoiningDocument.doc_type == doc_type))
    if jd is None or not jd.file_id:
        raise bad("المستند لم يُرفع بعد")
    jd.status, jd.verified_by = "verified", p.user.id
    audit.record(db, "joining_doc.verified", actor_id=p.user.id, entity="employee", entity_id=e.id,
                 company_id=e.company_id, details={"doc_type": doc_type}, ip=client_ip(request))
    db.commit()
    return {"doc_type": doc_type, "status": jd.status}


# --- applications
@router.get("/journey/applications")
def list_applications(stage: str | None = None, company_id: str | None = None,
                      p: Principal = Depends(require("employees:view")), db: Session = Depends(get_db)) -> list[dict]:
    s = scope_filter(select(Application), p, Application.company_id).order_by(Application.application_date.desc())
    if stage:
        s = s.where(Application.stage == stage)
    if company_id:
        s = s.where(Application.company_id == company_id)
    return [app_out(a, p) for a in db.scalars(s)]


def create_application(db: Session, body: ApplicationIn, actor_id: str | None, source: str = "manual") -> Application:
    get_company(db, body.company_id)
    if body.source_form_response_id:
        ex = db.scalar(select(Application).where(Application.source_form_response_id == body.source_form_response_id))
        if ex:
            return ex  # idempotent import from the Google Form response sheet
    data = body.model_dump()
    data["application_date"] = body.application_date or today()
    year = data["application_date"].year
    a = Application(**data, candidate_id=next_code(db, Application, Application.candidate_id, f"CAN-{year}-", 4),
                    source=source)
    db.add(a)
    db.flush()
    return a


@router.post("/journey/applications", status_code=201)
def add_application(body: ApplicationIn, request: Request, p: Principal = Depends(require("employees:create")),
                    db: Session = Depends(get_db)) -> dict:
    ensure_company(p, body.company_id)
    a = create_application(db, body, p.user.id)
    audit.record(db, "application.created", actor_id=p.user.id, entity="application", entity_id=a.id,
                 company_id=a.company_id, details={"candidate_id": a.candidate_id}, ip=client_ip(request))
    db.commit()
    return app_out(a, p)


@router.get("/journey/applications/{app_id}")
def application_detail(app_id: str, p: Principal = Depends(require("employees:view")),
                       db: Session = Depends(get_db)) -> dict:
    a = _load_app(db, p, app_id)
    d = app_out(a, p)
    d["interviews"] = [interview_out(i, p) for i in db.scalars(select(Interview).where(Interview.application_id == a.id)
                                                                .order_by(Interview.created_at))]
    d["decisions"] = [row(x, ["id", "technical_result", "hr_result", "outcome", "decided_at"])
                      | ({"reason": x.reason} if p.can("employees:view_sensitive") else {})
                      for x in db.scalars(select(Decision).where(Decision.application_id == a.id)
                                          .order_by(Decision.decided_at))]
    return d


@router.post("/journey/applications/{app_id}/interviews", status_code=201)
def add_interview(app_id: str, body: InterviewIn, request: Request, p: Principal = Depends(require("employees:view")),
                  db: Session = Depends(get_db)) -> dict:
    a = _load_app(db, p, app_id)
    if body.kind == "hr" and not (p.can("employees:edit") and p.can("employees:view_sensitive")):
        raise forbid("مقابلة HR تسجلها الموارد البشرية فقط")
    if body.kind == "technical" and not (p.can("employees:it_approve") or p.can("employees:edit")):
        raise forbid("ليس لديك صلاحية تسجيل التقييم الفني")
    if a.stage in ("onboarding", "archived"):
        raise conflict("الطلب اتقفل بقرار نهائي")
    if body.kind == "technical":
        allowed = {"performance", "behavior", "team_dynamic", "skills"}
        for k, v in body.scores.items():
            if k not in allowed or not isinstance(v, (int, float)) or not 0 <= v <= 10:
                raise bad("درجات التقييم الفني: Performance / Behavior / Team Dynamic / Skills من 0 لـ 10")
        hr_fields = {}
    else:
        hr_fields = {k: str(v) for k, v in body.hr_fields.items()
                     if k in {"past_exp", "why", "strength", "weakness", "future_plans"}}
    i = Interview(application_id=a.id, kind=body.kind, interviewer=body.interviewer, held_at=body.held_at,
                  attended=body.attended, scores=body.scores if body.kind == "technical" else {}, hr_fields=hr_fields,
                  notes=body.notes, recommendation=body.recommendation, recorded_by=p.user.id)
    db.add(i)
    if a.stage == "new":
        a.stage = "interviewing"
    db.flush()
    audit.record(db, "interview.recorded", actor_id=p.user.id, entity="application", entity_id=a.id,
                 company_id=a.company_id, details={"kind": body.kind, "recommendation": body.recommendation},
                 ip=client_ip(request))
    db.commit()
    return interview_out(i, p)


def _latest(db: Session, app_id: str, kind: str) -> Interview | None:
    return db.scalar(select(Interview).where(Interview.application_id == app_id, Interview.kind == kind)
                     .order_by(Interview.created_at.desc()))


@router.post("/journey/applications/{app_id}/decision")
def final_decision(app_id: str, body: DecisionIn, request: Request,
                   p: Principal = Depends(require("employees:approve")), db: Session = Depends(get_db)) -> dict:
    """HR-only final button. Outcome comes from the matrix, not from free choice."""
    a = _load_app(db, p, app_id)
    if a.stage in ("onboarding", "archived"):
        raise conflict("الطلب عليه قرار نهائي بالفعل")
    tech, hr = _latest(db, a.id, "technical"), _latest(db, a.id, "hr")
    if tech is None or hr is None:
        raise bad("لازم تتسجل المقابلة الفنية ومقابلة HR الأول")
    outcome = decide(tech.recommendation, hr.recommendation)
    db.add(Decision(application_id=a.id, technical_result=tech.recommendation, hr_result=hr.recommendation,
                    outcome=outcome, reason=body.reason, decided_by=p.user.id))
    result: dict = {"outcome": outcome}
    if outcome == "onboarding":
        e = Employee(company_id=a.company_id, employee_code=next_employee_code(db, a.company_id),
                     full_name=a.full_name, gender=a.gender, email="", phone=a.phone,
                     job_title=body.job_title or a.position, department_id=body.department_id,
                     branch_id=body.branch_id, status="onboarding", hire_date=body.start_date, application_id=a.id)
        db.add(e)
        db.flush()
        db.add(EmployeeSensitive(employee_id=e.id, national_id=a.national_id, birth_date=a.birth_date,
                                 salary=a.expected_salary, address=a.address))
        ob = Onboarding(employee_id=e.id, company_id=e.company_id, start_date=body.start_date)
        db.add(ob)
        db.flush()
        for kind in ("asset", "email", "phone", "access", "software", "biometric"):
            db.add(ITTask(onboarding_id=ob.id, kind=kind, description=IT_TASK_KINDS[kind], due_date=body.start_date))
        ensure_joining_rows(db, e)
        a.employee_id = e.id
        result.update(employee_id=e.id, onboarding_id=ob.id)
    a.stage = outcome
    a.version += 1
    audit.record(db, "application.decided", actor_id=p.user.id, entity="application", entity_id=a.id,
                 company_id=a.company_id, details={"technical": tech.recommendation, "hr": hr.recommendation,
                                                   "outcome": outcome}, ip=client_ip(request))
    db.commit()
    return result


# --- onboarding
def _load_ob(db: Session, p: Principal, ob_id: str) -> Onboarding:
    ob = db.get(Onboarding, ob_id)
    if ob is None:
        raise not_found("ملف التعيين غير موجود")
    ensure_company(p, ob.company_id)
    return ob


def onboarding_checklist(db: Session, ob: Onboarding) -> dict:
    tasks = list(db.scalars(select(ITTask).where(ITTask.onboarding_id == ob.id)))
    docs = list(db.scalars(select(JoiningDocument).where(JoiningDocument.employee_id == ob.employee_id)))
    acks = {a.kind for a in db.scalars(select(DocumentAck).where(DocumentAck.employee_id == ob.employee_id))}
    missing = []
    for t in tasks:
        if t.status not in ("verified", "skipped"):
            missing.append(f"مهمة IT غير معتمدة: {IT_TASK_KINDS.get(t.kind, t.kind)}")
    for d in docs:
        if d.status not in ("verified", "not_applicable"):
            missing.append(f"مستند: {JOINING_DOCS[d.doc_type]} ({d.status})")
    for k, label in ACK_KINDS.items():
        if k not in acks:
            missing.append(f"إقرار ناقص: {label}")
    return {"tasks": tasks, "docs": docs, "acks": acks, "missing": missing}


@router.get("/journey/onboarding")
def list_onboarding(status: str | None = "open", p: Principal = Depends(require("employees:view")),
                    db: Session = Depends(get_db)) -> list[dict]:
    s = scope_filter(select(Onboarding), p, Onboarding.company_id)
    if status:
        s = s.where(Onboarding.status == status)
    out = []
    for ob in db.scalars(s):
        e = db.get(Employee, ob.employee_id)
        chk = onboarding_checklist(db, ob)
        out.append(row(ob, ["id", "employee_id", "company_id", "start_date", "status"])
                   | {"employee_name": e.full_name, "job_title": e.job_title, "missing_count": len(chk["missing"]),
                      "tasks_done": sum(t.status in ("verified", "skipped") for t in chk["tasks"]),
                      "tasks_total": len(chk["tasks"])})
    return out


@router.get("/journey/onboarding/{ob_id}")
def onboarding_detail(ob_id: str, p: Principal = Depends(require("employees:view")),
                      db: Session = Depends(get_db)) -> dict:
    ob = _load_ob(db, p, ob_id)
    e = db.get(Employee, ob.employee_id)
    chk = onboarding_checklist(db, ob)
    return row(ob, ["id", "employee_id", "company_id", "start_date", "status", "completed_at"]) | {
        "employee": employee_out(e, p, db),
        "tasks": [row(t, ["id", "kind", "description", "ref", "status", "done_by", "verified_by", "notes",
                          "due_date", "evidence_file_id"]) | {"kind_ar": IT_TASK_KINDS.get(t.kind, t.kind)} for t in chk["tasks"]],
        "joining_documents": [{"doc_type": d.doc_type, "label": JOINING_DOCS[d.doc_type], "status": d.status}
                              for d in chk["docs"]],
        "acks": sorted(chk["acks"]), "missing": chk["missing"], "custody": current_custody(db, e.id),
        "templates": [row(t, ["id", "kind", "title", "version", "file_id", "active"])
                      for t in db.scalars(select(DocumentTemplate).where(DocumentTemplate.company_id == ob.company_id,
                                                                        DocumentTemplate.active.is_(True)))],
    }


@router.post("/journey/onboarding/{ob_id}/tasks", status_code=201)
def add_task(ob_id: str, body: TaskIn, request: Request, p: Principal = Depends(require("employees:it_approve")),
             db: Session = Depends(get_db)) -> dict:
    ob = _load_ob(db, p, ob_id)
    if body.kind not in IT_TASK_KINDS:
        raise bad("نوع مهمة غير معروف")
    if ob.status != "open":
        raise conflict("التعيين مقفول")
    t = ITTask(onboarding_id=ob.id, **body.model_dump())
    db.add(t)
    db.flush()
    audit.record(db, "it_task.added", actor_id=p.user.id, entity="onboarding", entity_id=ob.id,
                 company_id=ob.company_id, details={"kind": body.kind}, ip=client_ip(request))
    db.commit()
    return row(t, ["id", "kind", "description", "ref", "status"])


@router.post("/journey/tasks/{task_id}")
def update_task(task_id: str, body: TaskUpdate, request: Request,
                p: Principal = Depends(require("employees:it_approve")), db: Session = Depends(get_db)) -> dict:
    t = db.get(ITTask, task_id)
    if t is None:
        raise not_found("المهمة غير موجودة")
    ob = _load_ob(db, p, t.onboarding_id)
    if body.status == "verified" and t.status != "done":
        raise conflict("لازم المهمة تتنفذ (done) قبل اعتمادها")
    if body.status == "skipped" and len(body.notes.strip()) < 3:
        raise bad("اكتب سبب تخطي المهمة")
    if body.evidence_file_id:
        require_file(db, body.evidence_file_id)
        t.evidence_file_id = body.evidence_file_id
    if body.status == "verified" and not t.evidence_file_id:
        raise conflict("ارفع دليل التنفيذ (صورة / ملف) قبل اعتماد المهمة")  # execute -> evidence -> approve
    if body.ref is not None:
        if "password" in body.ref.lower() or "pass:" in body.ref.lower():
            raise bad("ممنوع كتابة كلمات مرور — اكتب مرجع فقط")
        t.ref = body.ref
    t.status, t.notes = body.status, body.notes or t.notes
    if body.status == "done":
        t.done_by = p.user.id
    elif body.status == "verified":
        t.verified_by = p.user.id
    audit.record(db, f"it_task.{body.status}", actor_id=p.user.id, entity="onboarding", entity_id=ob.id,
                 company_id=ob.company_id, details={"task": t.kind}, ip=client_ip(request))
    db.commit()
    return row(t, ["id", "kind", "description", "ref", "status", "notes", "evidence_file_id"])


@router.post("/journey/onboarding/{ob_id}/complete")
def complete_onboarding(ob_id: str, request: Request, p: Principal = Depends(require("employees:approve")),
                        db: Session = Depends(get_db)) -> dict:
    ob = _load_ob(db, p, ob_id)
    if ob.status != "open":
        raise conflict("التعيين مقفول بالفعل")
    chk = onboarding_checklist(db, ob)
    if chk["missing"]:
        raise HTTPExceptionWithList(chk["missing"])
    e = db.get(Employee, ob.employee_id)
    ob.status, ob.completed_at = "completed", now()
    e.status = "active"
    e.hire_date = e.hire_date or ob.start_date or today()
    e.version += 1
    audit.record(db, "onboarding.completed", actor_id=p.user.id, entity="onboarding", entity_id=ob.id,
                 company_id=ob.company_id, ip=client_ip(request))
    db.commit()
    return {"ok": True, "employee_id": e.id}


def HTTPExceptionWithList(items: list[str]):  # noqa: N802, ANN201
    from fastapi import HTTPException
    return HTTPException(409, {"message": "لسه في بنود ناقصة", "missing": items})


# --- templates & acknowledgments
@router.get("/journey/templates")
def list_templates(company_id: str | None = None, p: Principal = Depends(require("employees:view")),
                   db: Session = Depends(get_db)) -> list[dict]:
    s = scope_filter(select(DocumentTemplate), p, DocumentTemplate.company_id) \
        .order_by(DocumentTemplate.kind, DocumentTemplate.version.desc())
    if company_id:
        s = s.where(DocumentTemplate.company_id == company_id)
    return [row(t, ["id", "company_id", "kind", "title", "version", "file_id", "sha256", "active", "created_at"])
            | {"kind_ar": TEMPLATE_KINDS.get(t.kind, t.kind)} for t in db.scalars(s)]


@router.post("/journey/templates", status_code=201)
def add_template(body: TemplateIn, request: Request, p: Principal = Depends(require("employees:edit")),
                 db: Session = Depends(get_db)) -> dict:
    ensure_company(p, body.company_id)
    if body.kind not in TEMPLATE_KINDS:
        raise bad("نوع نموذج غير معروف")
    fo = require_file(db, body.file_id, "document_template")
    last = db.scalar(select(DocumentTemplate).where(DocumentTemplate.company_id == body.company_id,
                                                    DocumentTemplate.kind == body.kind)
                     .order_by(DocumentTemplate.version.desc()))
    for old in db.scalars(select(DocumentTemplate).where(DocumentTemplate.company_id == body.company_id,
                                                         DocumentTemplate.kind == body.kind,
                                                         DocumentTemplate.active.is_(True))):
        old.active = False  # previous version kept, just not offered for new acknowledgments
    t = DocumentTemplate(company_id=body.company_id, kind=body.kind, title=body.title,
                         version=(last.version + 1) if last else 1, file_id=fo.id, sha256=fo.sha256,
                         uploaded_by=p.user.id)
    db.add(t)
    db.flush()
    audit.record(db, "template.version_added", actor_id=p.user.id, entity="document_template", entity_id=t.id,
                 company_id=t.company_id, details={"kind": t.kind, "version": t.version, "sha256": t.sha256},
                 ip=client_ip(request))
    db.commit()
    return row(t, ["id", "company_id", "kind", "title", "version", "file_id", "active"])


@router.post("/journey/templates/{tpl_id}/active")
def toggle_template(tpl_id: str, active: bool, request: Request, p: Principal = Depends(require("employees:edit")),
                    db: Session = Depends(get_db)) -> dict:
    t = db.get(DocumentTemplate, tpl_id)
    if t is None:
        raise not_found()
    ensure_company(p, t.company_id)
    if active:
        for o in db.scalars(select(DocumentTemplate).where(DocumentTemplate.company_id == t.company_id,
                                                           DocumentTemplate.kind == t.kind)):
            o.active = False
    t.active = active
    audit.record(db, "template.active" if active else "template.inactive", actor_id=p.user.id,
                 entity="document_template", entity_id=t.id, company_id=t.company_id, ip=client_ip(request))
    db.commit()
    return {"id": t.id, "active": t.active}


@router.post("/employees/{emp_id}/acks", status_code=201)
def add_ack(emp_id: str, body: AckIn, request: Request, p: Principal = Depends(require("employees:edit")),
            db: Session = Depends(get_db)) -> dict:
    e = _load_emp(db, p, emp_id)
    if body.kind not in ACK_KINDS:
        raise bad("نوع إقرار غير معروف")
    tpl = db.scalar(select(DocumentTemplate).where(DocumentTemplate.company_id == e.company_id,
                                                   DocumentTemplate.kind == body.kind,
                                                   DocumentTemplate.active.is_(True)))
    if tpl is None and body.kind != "custody":
        raise bad(f"مفيش ملف «{ACK_KINDS[body.kind]}» مرفوع لشركة الموظف — ارفعه من تبويب الملفات الأول")
    if body.method == "paper_scan":
        require_file(db, body.evidence_file_id)
    elif body.method == "e_ack" and body.kind in ("contract", "nda") and not POLICY["legal_e_sign_enabled"]:
        # e-acknowledgment of reading is recorded, but it is NOT a legal signature (policy pending)
        pass
    if body.kind == "custody" and not current_custody(db, e.id)["assets"] and not current_custody(db, e.id)["sims"]:
        raise bad("مفيش عهدة مسلّمة فعليًا للموظف لإقرار استلامها")
    ack = DocumentAck(employee_id=e.id, kind=body.kind, template_id=tpl.id if tpl else None,
                      template_version=tpl.version if tpl else None, method=body.method,
                      evidence_file_id=body.evidence_file_id, acknowledged_on=body.acknowledged_on or today(),
                      recorded_by=p.user.id)
    db.add(ack)
    db.flush()
    audit.record(db, "ack.recorded", actor_id=p.user.id, entity="employee", entity_id=e.id, company_id=e.company_id,
                 details={"kind": body.kind, "version": ack.template_version, "method": body.method},
                 ip=client_ip(request))
    db.commit()
    return row(ack, ["id", "kind", "template_id", "template_version", "method", "acknowledged_on"]) | {
        "legal_signature": False}


# --- offboarding
def _load_off(db: Session, p: Principal, off_id: str) -> Offboarding:
    o = db.get(Offboarding, off_id)
    if o is None:
        raise not_found("ملف الإخلاء غير موجود")
    ensure_company(p, o.company_id)
    return o


def offboarding_out(db: Session, o: Offboarding) -> dict:
    e = db.get(Employee, o.employee_id)
    d = row(o, ["id", "employee_id", "company_id", "opened_at", "last_day", "reason", "status", "it_approved_at",
                "printed_at", "signed_scan_file_id", "closed_at"])
    d["employee_name"], d["job_title"] = e.full_name, e.job_title
    d["custody"] = current_custody(db, e.id)
    d["mailboxes"] = employee_mailboxes(db, e.id)
    d["checks"] = [row(c, ["id", "item_type", "item_ref", "result", "serial_checked", "notes", "at"])
                   for c in db.scalars(select(ITCheck).where(ITCheck.offboarding_id == o.id).order_by(ITCheck.at))]
    d["authorities"] = [row(a, ["id", "order", "authority", "status", "signer_name", "signed_date", "notes"])
                        for a in db.scalars(select(ClearanceApproval).where(ClearanceApproval.offboarding_id == o.id)
                                            .order_by(ClearanceApproval.order))]
    d["days_to_last_day"] = (o.last_day - today()).days
    return d


@router.get("/journey/offboarding")
def list_offboarding(status: str | None = None, p: Principal = Depends(require("employees:view")),
                     db: Session = Depends(get_db)) -> list[dict]:
    s = scope_filter(select(Offboarding), p, Offboarding.company_id).order_by(Offboarding.last_day)
    if status == "open":
        s = s.where(Offboarding.status != "closed")
    elif status:
        s = s.where(Offboarding.status == status)
    return [offboarding_out(db, o) for o in db.scalars(s)]


@router.post("/journey/offboarding", status_code=201)
def open_offboarding(body: OffboardIn, request: Request, p: Principal = Depends(require("employees:approve")),
                     db: Session = Depends(get_db)) -> dict:
    e = _load_emp(db, p, body.employee_id)
    if db.scalar(select(Offboarding).where(Offboarding.employee_id == e.id)):
        raise conflict("في ملف إخلاء مفتوح للموظف بالفعل")
    if e.status == "exited":
        raise conflict("الموظف خرج بالفعل")
    o = Offboarding(employee_id=e.id, company_id=e.company_id, opened_by=p.user.id, last_day=body.last_day,
                    reason=body.reason)
    db.add(o)
    db.flush()
    e.custody_frozen, e.status, e.exit_date = True, "offboarding", body.last_day  # freeze from this moment
    e.version += 1
    for i, name in enumerate(DEFAULT_AUTHORITIES, 1):
        db.add(ClearanceApproval(offboarding_id=o.id, order=i, authority=name))
    audit.record(db, "offboarding.opened", actor_id=p.user.id, entity="employee", entity_id=e.id,
                 company_id=e.company_id, details={"last_day": body.last_day.isoformat(), "custody_frozen": True},
                 ip=client_ip(request))
    db.commit()
    return offboarding_out(db, o)


@router.get("/journey/offboarding/{off_id}")
def offboarding_detail(off_id: str, p: Principal = Depends(require("employees:view")),
                       db: Session = Depends(get_db)) -> dict:
    return offboarding_out(db, _load_off(db, p, off_id))


@router.post("/journey/offboarding/{off_id}/it-check", status_code=201)
def it_check(off_id: str, body: ITCheckIn, request: Request, p: Principal = Depends(require("employees:it_approve")),
             db: Session = Depends(get_db)) -> dict:
    o = _load_off(db, p, off_id)
    if o.status != "it_review":
        raise conflict("مرحلة مراجعة IT انتهت")
    if body.item_type == "asset":
        a = db.get(Asset, body.item_ref)
        if a is None or a.holder_employee_id != o.employee_id:
            raise bad("الأصل مش ضمن عهدة الموظف الحالية")
        if body.result not in ("returned", "damaged", "missing"):
            raise bad("نتيجة الأصل: returned / damaged / missing")
        do_return(db, a, body.result, p.user.id, f"offboarding {o.id}. {body.notes}".strip())
    elif body.item_type == "sim":
        s = db.get(Sim, body.item_ref)
        if s is None or s.holder_employee_id != o.employee_id:
            raise bad("الشريحة مش ضمن عهدة الموظف الحالية")
        if body.result not in ("returned", "damaged", "missing"):
            raise bad("نتيجة الشريحة: returned / damaged / missing")
        sim_return(db, s, body.result, p.user.id, f"offboarding {o.id}. {body.notes}".strip())
    elif body.item_type == "mailbox":
        mb = db.get(Mailbox, body.item_ref)
        if mb is None or mb.employee_id != o.employee_id:
            raise bad("الإيميل مش مربوط بالموظف ده")
        if body.result != "deprovisioned":
            raise bad("نتيجة الإيميل: deprovisioned (إيقاف الحساب)")
        suspend_mailbox(db, mb, p.user.id, f"إخلاء طرف {o.id}. {body.notes}".strip())
    c = ITCheck(offboarding_id=o.id, item_type=body.item_type, item_ref=body.item_ref, result=body.result,
                serial_checked=body.serial_checked, notes=body.notes, checked_by=p.user.id)
    db.add(c)
    db.flush()
    audit.record(db, "offboarding.it_check", actor_id=p.user.id, entity="offboarding", entity_id=o.id,
                 company_id=o.company_id, details={"type": body.item_type, "ref": body.item_ref,
                                                   "result": body.result}, ip=client_ip(request))
    db.commit()
    return offboarding_out(db, o)


@router.post("/journey/offboarding/{off_id}/it-approve")
def it_approve(off_id: str, request: Request, p: Principal = Depends(require("employees:it_approve")),
               db: Session = Depends(get_db)) -> dict:
    o = _load_off(db, p, off_id)
    if o.status != "it_review":
        raise conflict("IT اعتمد بالفعل")
    cust = current_custody(db, o.employee_id)
    if cust["assets"] or cust["sims"]:
        raise conflict("لسه في عهد مع الموظف لم تُراجع (أصول/شرائح)")
    active_mail = employee_mailboxes(db, o.employee_id, active_only=True)
    if active_mail:
        raise conflict("لسه في إيميلات نشطة باسم الموظف — أوقفها أو انقلها: "
                       + ", ".join(m["address"] for m in active_mail))
    checks = list(db.scalars(select(ITCheck).where(ITCheck.offboarding_id == o.id)))
    if not any(c.item_type in ("account", "mailbox") for c in checks):
        raise bad("سجّل مراجعة إلغاء الحسابات (account) قبل الاعتماد")
    pending = [c.item_ref for c in checks if c.result == "pending_verification"]
    if pending:
        raise conflict("في عناصر لسه بانتظار التحقق: " + ", ".join(pending))
    o.status, o.it_approved_by, o.it_approved_at = "ready_for_paper", p.user.id, now()
    audit.record(db, "offboarding.it_approved", actor_id=p.user.id, entity="offboarding", entity_id=o.id,
                 company_id=o.company_id, ip=client_ip(request))
    db.commit()
    return offboarding_out(db, o)


@router.get("/journey/offboarding/{off_id}/clearance-print", response_class=HTMLResponse)
def clearance_print(off_id: str, request: Request, p: Principal = Depends(require("employees:print")),
                    db: Session = Depends(get_db)) -> HTMLResponse:
    """Official clearance paper. Printable WITHOUT HR pre-signature; no logos from reference forms;
    no fake signatures — empty boxes for the six authorities."""
    o = _load_off(db, p, off_id)
    if o.status == "it_review":
        raise conflict("لازم IT يعتمد الأول قبل طباعة الإخلاء")
    e = db.get(Employee, o.employee_id)
    dep = db.get(Department, e.department_id) if e.department_id else None
    sens = db.get(EmployeeSensitive, e.id) if p.can("employees:view_sensitive") else None
    esc = html.escape
    checks = list(db.scalars(select(ITCheck).where(ITCheck.offboarding_id == o.id)))
    types = {c.item_type for c in checks}
    box = lambda on: f'<span class="chk">{"✓" if on else ""}</span>'  # noqa: E731
    dots = lambda v, w=150: f'<span class="v" style="min-width:{w}px">{esc(str(v)) if v else "&nbsp;"}</span>'  # noqa: E731
    # F05 sections in the company's order (authorities 1..6). Extra tick-boxes per section, as on the paper form.
    english = ["Employ Department", "I T Department", "Office Manager", "Financial / the accounts",
               "Personnel Department", "Higher Management"]
    extras = {2: f"{box(bool(types & {'sim', 'mailbox'}))} وسائل الاتصال telecom &nbsp;&nbsp; "
                 f"{box('asset' in types)} حاسبات آلية Computers",
              4: f"{box(False)} السلف Loans &nbsp;&nbsp; {box(False)} العهدة المالية Financial covenant",
              5: f"{box(False)} البطاقة الوظيفية Emp. Card &nbsp;&nbsp; {box(False)} المفاتيح والاقفال Keys and locks"
                 f"<div style='margin-top:6px'>{box(False)} أخــرى حـــــدد : {dots('', 300)} Reason</div>"}
    auths = list(db.scalars(select(ClearanceApproval).where(ClearanceApproval.offboarding_id == o.id)
                            .order_by(ClearanceApproval.order)))
    sections, top = [], None
    for a in auths:
        if a.order == 6 or "العليا" in a.authority:
            top = a
            continue
        en = english[a.order - 1] if 0 < a.order <= len(english) else ""
        sections.append(
            f"<div class=sec2><div class=st>* {esc(a.authority)} : <span dir=ltr>{en}</span>:</div>"
            + (f"<div class=ex>{extras[a.order]}</div>" if a.order in extras else "")
            + f"<div>الاســم : {dots(a.signer_name)} Name &nbsp; التوقيع : {dots('', 120)} Signature &nbsp; "
              f"التاريخ : {dots(a.signed_date.strftime('%d/%m/%Y') if a.signed_date else '', 90)} Date</div>"
            + f"<div class=ex>{box(a.status == 'clear')} يخـلى طـرفـه Clear &nbsp;&nbsp; {box(a.status == 'not_clear')} "
              f"لا يخلى طرفه Not Clear &nbsp; حـــــدد :</div><div>{dots(a.notes, 520)} : Reason</div></div>")
    reason = o.reason or ""
    other_reason = "" if reason.strip() in ("إجازة", "نهاية خدمة", "") else reason
    css = (".cl{font-size:13px}.cl h2{font-size:15px;text-decoration:underline;margin:12px 0 6px}"
           ".cl .frame{border:1px solid #000;padding:10px 12px}.cl .v{border-bottom:1px dotted #000;display:inline-block;"
           "padding:0 4px;font-weight:700}.cl .sec2{border-bottom:1px solid #000;padding:10px 4px;line-height:2}"
           ".cl .sec2:last-child{border-bottom:0}.cl .st{font-weight:700;text-decoration:underline}"
           ".cl .ex{text-align:center}.chk{display:inline-block;width:13px;height:13px;border:1px solid #000;"
           "vertical-align:middle;text-align:center;line-height:11px;font-size:11px;margin-inline-end:3px}")
    body = (f"<div class=cl><div style='font-weight:700;text-decoration:underline'>إخـلاء طــرف Clearance Form</div>"
            f"<h2>1- بيانات الموظف - Employ Data</h2><div class=frame style='line-height:2.2'>"
            f"<div>الاســم: {dots(e.full_name, 200)} Name: &nbsp; الرقم الوظيفي: {dots(e.employee_code)} No: &nbsp; "
            f"الإدارة: {dots('', 110)}</div>"
            f"<div>الـقـسـم: {dots(dep.name if dep else '')} Section: &nbsp; مسمى الوظيفة: {dots(e.job_title)} Title:</div>"
            f"<div>الرقم القومي: {dots(sens.national_id if sens else '', 230)} National ID</div>"
            f"<div>سبب إخلاء الطرف: {box(reason.strip() == 'إجازة')} إجازة Vacation &nbsp; "
            f"{box(reason.strip() == 'نهاية خدمة')} نهاية خدمة Exit &nbsp; {esc(other_reason)} : Clearance Reason</div>"
            f"<div class=muted>آخر يوم عمل: {o.last_day.strftime('%d/%m/%Y')} — مراجعة IT معتمدة إلكترونيًا "
            f"{iso(o.it_approved_at)[:10] if o.it_approved_at else ''}</div></div>"
            f"<h2>2- الجهات ذات العلاقة - Related Managements</h2><div class=frame>{''.join(sections)}</div>"
            f"<h2>الإدارة العليا - Higher Management</h2><div class=frame style='text-align:center;padding:22px'>"
            f"<b>المدير التنفيذي :</b> {dots(top.signer_name if top else '', 200)} <b>:Signature</b>"
            + (f"<div class=ex style='margin-top:8px'>{box(top.status == 'clear')} Clear &nbsp; "
               f"{box(top.status == 'not_clear')} Not Clear</div>" if top and top.status != 'pending' else "")
            + f"</div><p class=muted>رقم الملف: {o.id} — طُبع {now().strftime('%Y-%m-%d %H:%M')} UTC. الورقة دي مش "
              f"إخلاء نهائي إلا بعد توقيع كل الجهات ورفع النسخة الموقعة.</p></div>")
    if o.printed_at is None:
        o.printed_at = now()
    audit.record(db, "offboarding.clearance_printed", actor_id=p.user.id, entity="offboarding", entity_id=o.id,
                 company_id=o.company_id, details={"sensitive": sens is not None}, ip=client_ip(request))
    db.commit()
    from .printing import doc_page
    return HTMLResponse(doc_page(db, o.company_id, "إخلاء طرف " + e.full_name, body, css))


@router.post("/journey/offboarding/{off_id}/authorities/{auth_id}")
def set_authority(off_id: str, auth_id: str, body: AuthorityIn, request: Request,
                  p: Principal = Depends(require("employees:approve")), db: Session = Depends(get_db)) -> dict:
    o = _load_off(db, p, off_id)
    if o.status == "closed":
        raise conflict("الإخلاء مقفول")
    a = db.get(ClearanceApproval, auth_id)
    if a is None or a.offboarding_id != o.id:
        raise not_found("الجهة غير موجودة")
    if body.status != "pending" and (len(body.signer_name.strip()) < 2 or body.signed_date is None):
        raise bad("سجّل اسم الموقّع وتاريخ التوقيع الورقي")
    if body.status == "not_clear" and len(body.notes.strip()) < 3:
        raise bad("اكتب سبب Not Clear")
    if body.status != "pending" and POLICY["clearance_sequential"]:
        earlier = db.scalar(select(ClearanceApproval).where(ClearanceApproval.offboarding_id == o.id,
                                                            ClearanceApproval.order < a.order,
                                                            ClearanceApproval.status == "pending")
                            .order_by(ClearanceApproval.order).limit(1))
        if earlier is not None:
            raise conflict(f"التوقيعات بالترتيب — لازم «{earlier.authority}» يوقّع الأول")
    a.status, a.signer_name, a.signed_date, a.notes, a.recorded_by = (body.status, body.signer_name,
                                                                      body.signed_date, body.notes, p.user.id)
    audit.record(db, "offboarding.authority", actor_id=p.user.id, entity="offboarding", entity_id=o.id,
                 company_id=o.company_id, details={"authority": a.authority, "status": a.status},
                 ip=client_ip(request))
    db.commit()
    return offboarding_out(db, o)


@router.post("/journey/offboarding/{off_id}/signed-scan")
def upload_signed_scan(off_id: str, body: ScanIn, request: Request,
                       p: Principal = Depends(require("employees:approve")), db: Session = Depends(get_db)) -> dict:
    o = _load_off(db, p, off_id)
    if o.status != "ready_for_paper":
        raise conflict("ارفع النسخة الموقعة بعد اعتماد IT وطباعة النموذج")
    fo: FileObject = require_file(db, body.file_id, "clearance_scan")
    o.signed_scan_file_id, o.signed_scan_verified_by = fo.id, p.user.id
    audit.record(db, "offboarding.signed_scan", actor_id=p.user.id, entity="offboarding", entity_id=o.id,
                 company_id=o.company_id, details={"sha256": fo.sha256}, ip=client_ip(request))
    db.commit()
    return offboarding_out(db, o)


@router.post("/journey/offboarding/{off_id}/close")
def close_offboarding(off_id: str, request: Request, p: Principal = Depends(require("employees:approve")),
                      db: Session = Depends(get_db)) -> dict:
    o = _load_off(db, p, off_id)
    if o.status == "closed":
        raise conflict("مقفول بالفعل")
    problems = []
    if o.status != "ready_for_paper":
        problems.append("اعتماد IT غير مكتمل")
    auths = list(db.scalars(select(ClearanceApproval).where(ClearanceApproval.offboarding_id == o.id)))
    nc = [a.authority for a in auths if a.status == "not_clear"]
    if nc and not POLICY["not_clear_exception_enabled"]:
        problems.append("جهات Not Clear: " + "، ".join(nc))
    pend = [a.authority for a in auths if a.status == "pending"]
    if pend:
        problems.append("جهات لم توقّع: " + "، ".join(pend))
    if not o.signed_scan_file_id:
        problems.append("النسخة الورقية الموقعة لم تُرفع")
    if problems:
        raise HTTPExceptionWithList(problems)
    e = db.get(Employee, o.employee_id)
    o.status, o.closed_at, o.closed_by = "closed", now(), p.user.id
    e.status = "exited"  # freeze stays True forever
    e.version += 1
    audit.record(db, "offboarding.closed", actor_id=p.user.id, entity="employee", entity_id=e.id,
                 company_id=e.company_id, ip=client_ip(request))
    db.commit()
    return offboarding_out(db, o)
