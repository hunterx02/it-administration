"""M02 — Assets & custody.

Rules implemented (M02 v0.4 + master RC1 §3):
- Code = <CompanyPrefix>-<Category>-<seq> (prefix per company, category code case preserved,
  sequence per company+category, width configurable per category; default 3 like `ZWolf-AIO-001`).
  Codes never change after creation.
- Allocation status (custody/location) is separate from technical condition.
- Reserve != handover. Every custody change is an append-only event; nobody edits the holder in a cell.
- No new custody for an employee whose offboarding is open (freeze), from any channel.
- Return records its result (returned / damaged / missing); return does not "clear" a loss.
- Monitors are separate assets linked one-to-many to a device over time.
"""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from .. import audit
from ..common import (apply_changes, bad, check_version, conflict, ensure_company, get_company, not_found, now,
                      row, scope_filter)
from ..db import Base
from ..deps import Principal, client_ip, get_db, require
from ..models import new_id, utcnow
from .people import Employee, assert_can_receive_custody, get_employee

# ---------------------------------------------------------------- reference data
DEFAULT_CATEGORIES = [  # M02 Appendix A — codes kept exactly as supplied (case preserved)
    ("CMP", "كمبيوتر مكتبي (Desktop)", "Computer"),
    ("CMPM", "كمبيوتر مكتبي (DESIGN)", "Computer DESIGN"),
    ("CMPH", "كمبيوتر مكتبي (MOTION)", "Computer MOTION"),
    ("LTP", "كمبيوتر محمول (Laptop)", "Laptop"),
    ("AIO", "كمبيوتر كامل", "ALL IN ONE"),
    ("PRN", "طابعة", "Printer"),
    ("MOB", "هاتف محمول", "mobile phone"),
    ("MAC", "ماكنتوش", ""),
    ("Hard", "هارد خارجي", ""),
    ("mon", "شاشة", ""),
]
ALLOCATION = {
    "in_stock": "في المخزن", "available": "خالي / متاح", "reserved": "محجوز", "assigned": "مسلّم",
    "in_maintenance": "في الصيانة", "lost": "مفقود", "retired": "مكهّن", "pending_wipe": "بانتظار مسح البيانات",
}
CONDITION = {"unknown": "غير محدد", "good": "سليم", "needs_repair": "يحتاج صيانة", "damaged": "تالف"}
# Legacy single-value statuses (M02 Appendix A) -> (allocation, condition). Condition stays unknown unless the
# legacy value itself states it (no automatic "good").
LEGACY_STATUS = {
    "مستخدم": ("assigned", "unknown"), "تالف": ("in_stock", "damaged"), "تحت الصيانة": ("in_maintenance", "unknown"),
    "يحتاج الي صيانه": ("in_stock", "needs_repair"), "خالي": ("available", "unknown"), "مخزن": ("in_stock", "unknown"),
    "مخزن/تالف": ("in_stock", "damaged"),
}
ACCESSORIES = ["keyboard", "mouse", "headset", "cables", "pad", "charger"]


class AssetCategory(Base):
    __tablename__ = "asset_categories"
    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name_ar: Mapped[str] = mapped_column(String(100))
    name_en: Mapped[str] = mapped_column(String(100), default="")
    seq_width: Mapped[int] = mapped_column(Integer, default=3)
    is_monitor: Mapped[bool] = mapped_column(default=False)


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("company_id", "category_code", "seq"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(60), unique=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    category_code: Mapped[str] = mapped_column(ForeignKey("asset_categories.code"))
    seq: Mapped[int] = mapped_column(Integer)
    hostname: Mapped[str] = mapped_column(String(120), default="")
    brand: Mapped[str] = mapped_column(String(120), default="")
    model: Mapped[str] = mapped_column(String(160), default="")
    serial: Mapped[str] = mapped_column(String(120), default="", index=True)  # SN / IMEI (text)
    cpu: Mapped[str] = mapped_column(String(120), default="")
    ram_gb: Mapped[str] = mapped_column(String(20), default="")
    storage: Mapped[str] = mapped_column(String(120), default="")
    gpu: Mapped[str] = mapped_column(String(120), default="")
    os: Mapped[str] = mapped_column(String(80), default="")
    screen_info: Mapped[str] = mapped_column(String(160), default="")
    branch_id: Mapped[str | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    location: Mapped[str] = mapped_column(String(160), default="")
    holder_employee_id: Mapped[str | None] = mapped_column(ForeignKey("employees.id"), nullable=True, index=True)
    reserved_for_employee_id: Mapped[str | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    allocation_status: Mapped[str] = mapped_column(String(20), default="in_stock")
    tech_condition: Mapped[str] = mapped_column(String(20), default="unknown")
    accessories: Mapped[dict] = mapped_column(JSON, default=dict)
    purchase_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    warranty_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    supplier_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    receipt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)   # M05 receipt that created it
    vault_ref: Mapped[str] = mapped_column(String(120), default="")             # reference only, never a password
    legacy_status: Mapped[str] = mapped_column(String(40), default="")
    legacy_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(20), default="ui")  # ui | sheet | import | receipt
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CustodyEvent(Base):
    """Append-only custody history for assets (M02) — never updated or deleted."""
    __tablename__ = "custody_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # reserve, unreserve, handover, return, status, maintenance_out/in, link, unlink
    employee_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    result: Mapped[str] = mapped_column(String(20), default="")  # return: returned | damaged | missing
    from_status: Mapped[str] = mapped_column(String(20), default="")
    to_status: Mapped[str] = mapped_column(String(20), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    evidence_file_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AssetLink(Base):
    """Monitor (or other peripheral asset) linked to a device; history kept via unlinked_at."""
    __tablename__ = "asset_links"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    parent_asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    child_asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    unlinked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Maintenance(Base):
    __tablename__ = "maintenance"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    company_id: Mapped[str] = mapped_column(String(36))
    issue: Mapped[str] = mapped_column(Text)
    vendor: Mapped[str] = mapped_column(String(160), default="")  # empty = internal
    opened_at: Mapped[date] = mapped_column(Date)
    closed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    result: Mapped[str] = mapped_column(Text, default="")
    prev_allocation: Mapped[str] = mapped_column(String(20), default="in_stock")
    financial_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # ONE M05 event, never two
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | closed


ASSET_FIELDS = ["id", "code", "company_id", "category_code", "seq", "hostname", "brand", "model", "serial", "cpu",
                "ram_gb", "storage", "gpu", "os", "screen_info", "branch_id", "department_id", "location",
                "holder_employee_id", "reserved_for_employee_id", "allocation_status", "tech_condition", "accessories",
                "purchase_date", "warranty_end", "supplier_id", "receipt_id", "vault_ref", "legacy_status", "notes",
                "source", "version", "updated_at"]
EDITABLE = {"hostname", "brand", "model", "serial", "cpu", "ram_gb", "storage", "gpu", "os", "screen_info",
            "branch_id", "department_id", "location", "tech_condition", "accessories", "purchase_date",
            "warranty_end", "supplier_id", "notes"}  # vault_ref only via the password endpoints


def seed_categories(db: Session) -> None:
    for code, ar, en in DEFAULT_CATEGORIES:
        if db.get(AssetCategory, code) is None:
            db.add(AssetCategory(code=code, name_ar=ar, name_en=en, is_monitor=(code == "mon")))


def asset_out(a: Asset, db: Session | None = None) -> dict:
    d = row(a, ASSET_FIELDS)
    d["allocation_ar"], d["condition_ar"] = ALLOCATION.get(a.allocation_status, ""), CONDITION.get(a.tech_condition, "")
    d["has_password"] = (a.vault_ref or "").startswith("vault:")
    d["vault_ref"] = "🔒" if d["has_password"] else (a.vault_ref and "مرجع خارجي")  # never expose the store key
    if db is not None:
        for key, eid in (("holder_name", a.holder_employee_id), ("reserved_for_name", a.reserved_for_employee_id)):
            e = db.get(Employee, eid) if eid else None
            d[key] = e.full_name if e else ""
    return d


def record_event(db: Session, a: Asset, kind: str, actor_id: str | None, **kw) -> CustodyEvent:
    ev = CustodyEvent(asset_id=a.id, kind=kind, actor_id=actor_id, **kw)
    db.add(ev)
    return ev


def assert_serial_unique(db: Session, serial: str | None, exclude_id: str | None = None) -> None:
    """A serial / IMEI belongs to one physical device only (case/space-insensitive)."""
    s = (serial or "").strip()
    if not s:
        return
    q = select(Asset).where(func.lower(func.trim(Asset.serial)) == s.lower())
    if exclude_id:
        q = q.where(Asset.id != exclude_id)
    other = db.scalar(q.limit(1))
    if other is not None:
        raise conflict(f"السيريال «{s}» متسجل بالفعل على الأصل {other.code}")


def create_asset(db: Session, *, company_id: str, category_code: str, actor_id: str | None, source: str = "ui",
                 **fields) -> Asset:
    company = get_company(db, company_id)
    assert_serial_unique(db, fields.get("serial"))
    cat = db.get(AssetCategory, category_code)
    if cat is None:
        raise bad(f"فئة غير معروفة: {category_code}")
    if not company.asset_prefix:
        raise bad("حدد بادئة أكواد الأصول للشركة الأول (إدارة النظام ← الشركات)")
    for attempt in range(5):
        seq = (db.scalar(select(func.max(Asset.seq)).where(Asset.company_id == company_id,
                                                          Asset.category_code == category_code)) or 0) + 1
        code = f"{company.asset_prefix}-{cat.code}-{seq:0{cat.seq_width}d}"
        a = Asset(company_id=company_id, category_code=category_code, seq=seq, code=code, source=source,
                  **{k: v for k, v in fields.items() if k in EDITABLE | {"allocation_status", "legacy_status",
                                                                         "legacy_id", "receipt_id"}})
        if a.allocation_status in (None, ""):
            a.allocation_status = "in_stock"
        if a.tech_condition in (None, ""):
            a.tech_condition = "unknown"  # never "good" by default — condition is verified, not assumed
        if a.accessories is None:
            a.accessories = {}
        if a.allocation_status not in ALLOCATION:
            raise bad("حالة تخصيص غير صحيحة")
        if a.tech_condition not in CONDITION:
            raise bad("حالة فنية غير صحيحة")
        try:
            with db.begin_nested():
                db.add(a)
                db.flush()
            record_event(db, a, "created", actor_id, to_status=a.allocation_status, notes=f"source={source}")
            return a
        except IntegrityError:
            continue  # concurrent code allocation — retry with the next sequence
    raise conflict("تعذر توليد كود فريد، حاول مرة أخرى")


def load_asset(db: Session, p: Principal, asset_id: str) -> Asset:
    a = db.get(Asset, asset_id)
    if a is None:
        raise not_found("الأصل غير موجود")
    ensure_company(p, a.company_id)
    return a


# ---------------------------------------------------------------- API
router = APIRouter(prefix="/api/assets", tags=["M02 assets"])


class AssetIn(BaseModel):
    company_id: str
    category_code: str
    hostname: str = ""
    brand: str = ""
    model: str = ""
    serial: str = ""
    cpu: str = ""
    ram_gb: str = ""
    storage: str = ""
    gpu: str = ""
    os: str = ""
    screen_info: str = ""
    branch_id: str | None = None
    department_id: str | None = None
    location: str = ""
    allocation_status: str = "in_stock"
    tech_condition: str = "unknown"
    accessories: dict = Field(default_factory=dict)
    purchase_date: date | None = None
    warranty_end: date | None = None
    supplier_id: str | None = None
    vault_ref: str = Field(default="", max_length=120)
    notes: str = ""


class AssetPatch(BaseModel):
    version: int
    hostname: str | None = None
    brand: str | None = None
    model: str | None = None
    serial: str | None = None
    cpu: str | None = None
    ram_gb: str | None = None
    storage: str | None = None
    gpu: str | None = None
    os: str | None = None
    screen_info: str | None = None
    branch_id: str | None = None
    department_id: str | None = None
    location: str | None = None
    tech_condition: str | None = None
    accessories: dict | None = None
    purchase_date: date | None = None
    warranty_end: date | None = None
    supplier_id: str | None = None
    vault_ref: str | None = Field(default=None, max_length=120)
    notes: str | None = None


class EmployeeAction(BaseModel):
    employee_id: str
    notes: str = ""
    idempotency_key: str | None = Field(default=None, max_length=80)


class ReturnIn(BaseModel):
    result: str = Field(pattern="^(returned|damaged|missing)$")
    notes: str = ""
    evidence_file_id: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=80)


class StatusIn(BaseModel):
    allocation_status: str = Field(pattern="^(in_stock|available|lost|retired|pending_wipe)$")
    reason: str = Field(min_length=2)


class LinkIn(BaseModel):
    child_asset_id: str


class MaintOpen(BaseModel):
    issue: str = Field(min_length=2)
    vendor: str = ""
    opened_at: date | None = None


class MaintClose(BaseModel):
    result: str = Field(min_length=2)
    tech_condition: str = Field(pattern="^(good|needs_repair|damaged|unknown)$")
    closed_at: date | None = None


class CategoryIn(BaseModel):
    code: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9]+$")
    name_ar: str = Field(min_length=1, max_length=100)
    name_en: str = ""
    seq_width: int = Field(default=3, ge=1, le=6)


@router.get("/meta")
def meta(_: Principal = Depends(require("assets:view")), db: Session = Depends(get_db)) -> dict:
    cats = [{"code": c.code, "name_ar": c.name_ar, "name_en": c.name_en, "seq_width": c.seq_width}
            for c in db.scalars(select(AssetCategory).order_by(AssetCategory.code))]
    return {"categories": cats, "allocation": ALLOCATION, "condition": CONDITION, "accessories": ACCESSORIES,
            "legacy_status": list(LEGACY_STATUS)}


@router.post("/categories", status_code=201)
def add_category(body: CategoryIn, request: Request, p: Principal = Depends(require("system:edit")),
                 db: Session = Depends(get_db)) -> dict:
    if db.get(AssetCategory, body.code):
        raise conflict("كود الفئة موجود")
    db.add(AssetCategory(**body.model_dump()))
    audit.record(db, "asset_category.created", actor_id=p.user.id, entity="asset_category", entity_id=body.code,
                 details=body.model_dump(), ip=client_ip(request))
    db.commit()
    return body.model_dump()


class CategoryPatch(BaseModel):
    name_ar: str | None = Field(default=None, min_length=1, max_length=100)
    name_en: str | None = None
    seq_width: int | None = Field(default=None, ge=1, le=6)


@router.patch("/categories/{code}")
def edit_category(code: str, body: CategoryPatch, request: Request, p: Principal = Depends(require("system:edit")),
                  db: Session = Depends(get_db)) -> dict:
    """Rename a category. The code itself never changes (existing asset codes depend on it)."""
    cat = db.get(AssetCategory, code)
    if cat is None:
        raise not_found("الفئة غير موجودة")
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(cat, k, v)
    audit.record(db, "asset_category.updated", actor_id=p.user.id, entity="asset_category", entity_id=code,
                 details=changes, ip=client_ip(request))
    db.commit()
    return {"code": cat.code, "name_ar": cat.name_ar, "name_en": cat.name_en, "seq_width": cat.seq_width}


@router.get("")
def list_assets(company_id: str | None = None, category: str | None = None, allocation: str | None = None,
                condition: str | None = None, department_id: str | None = None, holder: str | None = None,
                q: str | None = None, limit: int = Query(500, le=5000),
                p: Principal = Depends(require("assets:view")), db: Session = Depends(get_db)) -> list[dict]:
    s = scope_filter(select(Asset), p, Asset.company_id).order_by(Asset.code).limit(limit)
    for col, val in ((Asset.company_id, company_id), (Asset.category_code, category),
                     (Asset.allocation_status, allocation), (Asset.tech_condition, condition),
                     (Asset.department_id, department_id), (Asset.holder_employee_id, holder)):
        if val:
            s = s.where(col == val)
    if q:
        like = f"%{q}%"
        s = s.where(or_(Asset.code.ilike(like), Asset.serial.ilike(like), Asset.model.ilike(like),
                        Asset.hostname.ilike(like), Asset.brand.ilike(like)))
    return [asset_out(a, db) for a in db.scalars(s)]


@router.post("", status_code=201)
def create(body: AssetIn, request: Request, p: Principal = Depends(require("assets:create")),
           db: Session = Depends(get_db)) -> dict:
    ensure_company(p, body.company_id)
    if body.allocation_status in ("assigned", "reserved", "in_maintenance"):
        raise bad("التسليم والحجز والصيانة بيتعملوا من أزرار الحركة، مش وقت الإضافة")
    data = body.model_dump(exclude={"company_id", "category_code"})
    data["accessories"] = {k: bool(v) for k, v in (body.accessories or {}).items()}
    a = create_asset(db, company_id=body.company_id, category_code=body.category_code, actor_id=p.user.id, **data)
    audit.record(db, "asset.created", actor_id=p.user.id, entity="asset", entity_id=a.id, company_id=a.company_id,
                 details={"code": a.code}, ip=client_ip(request))
    db.commit()
    return asset_out(a, db)


@router.get("/{asset_id}")
def detail(asset_id: str, p: Principal = Depends(require("assets:view")), db: Session = Depends(get_db)) -> dict:
    a = load_asset(db, p, asset_id)
    d = asset_out(a, db)
    d["events"] = [row(e, ["id", "kind", "employee_id", "result", "from_status", "to_status", "notes", "at",
                           "actor_id", "evidence_file_id"])
                   for e in db.scalars(select(CustodyEvent).where(CustodyEvent.asset_id == a.id)
                                       .order_by(CustodyEvent.at.desc()))]
    links = db.scalars(select(AssetLink).where(or_(AssetLink.parent_asset_id == a.id, AssetLink.child_asset_id == a.id),
                                               AssetLink.unlinked_at.is_(None)))
    d["links"] = []
    for link in links:
        other = db.get(Asset, link.child_asset_id if link.parent_asset_id == a.id else link.parent_asset_id)
        d["links"].append({"id": link.id, "role": "child" if link.parent_asset_id == a.id else "parent",
                           "asset_id": other.id, "code": other.code, "model": other.model})
    d["maintenance"] = [row(m, ["id", "issue", "vendor", "opened_at", "closed_at", "result", "status",
                                "financial_event_id"])
                        for m in db.scalars(select(Maintenance).where(Maintenance.asset_id == a.id)
                                            .order_by(Maintenance.opened_at.desc()))]
    return d


@router.patch("/{asset_id}")
def update(asset_id: str, body: AssetPatch, request: Request, p: Principal = Depends(require("assets:edit")),
           db: Session = Depends(get_db)) -> dict:
    a = load_asset(db, p, asset_id)
    check_version(a, body.version)
    changes = body.model_dump(exclude_unset=True, exclude={"version"})
    if "tech_condition" in changes and changes["tech_condition"] not in CONDITION:
        raise bad("حالة فنية غير صحيحة")
    if "serial" in changes:
        assert_serial_unique(db, changes["serial"], exclude_id=a.id)
    applied = apply_changes(a, changes, EDITABLE)
    if applied:
        a.source = "ui"
        audit.record(db, "asset.updated", actor_id=p.user.id, entity="asset", entity_id=a.id,
                     company_id=a.company_id, details=applied, ip=client_ip(request))
    db.commit()
    return asset_out(a, db)


def _idempotent(db: Session, key: str | None) -> CustodyEvent | None:
    return db.scalar(select(CustodyEvent).where(CustodyEvent.idempotency_key == key)) if key else None


@router.post("/{asset_id}/reserve")
def reserve(asset_id: str, body: EmployeeAction, request: Request, p: Principal = Depends(require("assets:edit")),
            db: Session = Depends(get_db)) -> dict:
    a = load_asset(db, p, asset_id)
    if _idempotent(db, body.idempotency_key):
        return asset_out(a, db)
    assert_can_receive_custody(db, body.employee_id)
    if a.allocation_status not in ("in_stock", "available"):
        raise conflict(f"الأصل حالته «{ALLOCATION[a.allocation_status]}» ومينفعش يتحجز")
    prev, a.allocation_status, a.reserved_for_employee_id = a.allocation_status, "reserved", body.employee_id
    a.version += 1
    record_event(db, a, "reserve", p.user.id, employee_id=body.employee_id, from_status=prev, to_status="reserved",
                 notes=body.notes, idempotency_key=body.idempotency_key)
    audit.record(db, "asset.reserved", actor_id=p.user.id, entity="asset", entity_id=a.id, company_id=a.company_id,
                 details={"employee_id": body.employee_id}, ip=client_ip(request))
    db.commit()
    return asset_out(a, db)


@router.post("/{asset_id}/unreserve")
def unreserve(asset_id: str, request: Request, p: Principal = Depends(require("assets:edit")),
              db: Session = Depends(get_db)) -> dict:
    a = load_asset(db, p, asset_id)
    if a.allocation_status != "reserved":
        raise conflict("الأصل مش محجوز")
    emp, a.reserved_for_employee_id, a.allocation_status = a.reserved_for_employee_id, None, "available"
    a.version += 1
    record_event(db, a, "unreserve", p.user.id, employee_id=emp, from_status="reserved", to_status="available")
    audit.record(db, "asset.unreserved", actor_id=p.user.id, entity="asset", entity_id=a.id, company_id=a.company_id,
                 ip=client_ip(request))
    db.commit()
    return asset_out(a, db)


def do_handover(db: Session, a: Asset, employee_id: str, actor_id: str | None, notes: str = "",
                idempotency_key: str | None = None, source: str = "ui") -> CustodyEvent:
    """Single entry point for handovers (UI, onboarding, Sheets import) — freeze always enforced."""
    emp = assert_can_receive_custody(db, employee_id)
    if emp.company_id != a.company_id:
        raise bad("الأصل تابع لشركة غير شركة الموظف")
    if a.allocation_status == "reserved" and a.reserved_for_employee_id not in (None, employee_id):
        raise conflict("الأصل محجوز لموظف تاني")
    if a.allocation_status not in ("in_stock", "available", "reserved"):
        raise conflict(f"الأصل حالته «{ALLOCATION[a.allocation_status]}» ومينفعش يتسلم")
    if a.tech_condition == "damaged":
        raise conflict("الأصل متسجل تالف — صلحه أو غيّر حالته الفنية الأول")
    prev = a.allocation_status
    a.allocation_status, a.holder_employee_id, a.reserved_for_employee_id = "assigned", employee_id, None
    a.department_id = a.department_id or emp.department_id
    a.version += 1
    return record_event(db, a, "handover", actor_id, employee_id=employee_id, from_status=prev, to_status="assigned",
                        notes=(notes + (f" [source={source}]" if source != "ui" else "")).strip(),
                        idempotency_key=idempotency_key)


@router.post("/{asset_id}/handover")
def handover(asset_id: str, body: EmployeeAction, request: Request, p: Principal = Depends(require("assets:edit")),
             db: Session = Depends(get_db)) -> dict:
    a = load_asset(db, p, asset_id)
    if _idempotent(db, body.idempotency_key):
        return asset_out(a, db)
    do_handover(db, a, body.employee_id, p.user.id, body.notes, body.idempotency_key)
    audit.record(db, "asset.handover", actor_id=p.user.id, entity="asset", entity_id=a.id, company_id=a.company_id,
                 details={"employee_id": body.employee_id}, ip=client_ip(request))
    db.commit()
    return asset_out(a, db)


def do_return(db: Session, a: Asset, result: str, actor_id: str | None, notes: str = "",
              evidence_file_id: str | None = None, idempotency_key: str | None = None) -> CustodyEvent:
    if a.allocation_status != "assigned" or not a.holder_employee_id:
        raise conflict("الأصل مش مسلّم لحد")
    emp = a.holder_employee_id
    a.holder_employee_id = None
    if result == "missing":
        a.allocation_status = "lost"
    else:
        a.allocation_status = "in_stock"
        if result == "damaged":
            a.tech_condition = "damaged"
    a.version += 1
    return record_event(db, a, "return", actor_id, employee_id=emp, result=result, from_status="assigned",
                        to_status=a.allocation_status, notes=notes, evidence_file_id=evidence_file_id,
                        idempotency_key=idempotency_key)


@router.post("/{asset_id}/return")
def return_asset(asset_id: str, body: ReturnIn, request: Request, p: Principal = Depends(require("assets:edit")),
                 db: Session = Depends(get_db)) -> dict:
    a = load_asset(db, p, asset_id)
    if _idempotent(db, body.idempotency_key):
        return asset_out(a, db)
    ev = do_return(db, a, body.result, p.user.id, body.notes, body.evidence_file_id, body.idempotency_key)
    audit.record(db, "asset.returned", actor_id=p.user.id, entity="asset", entity_id=a.id, company_id=a.company_id,
                 details={"result": body.result, "employee_id": ev.employee_id}, ip=client_ip(request))
    db.commit()
    return asset_out(a, db)


@router.post("/{asset_id}/status")
def set_status(asset_id: str, body: StatusIn, request: Request, p: Principal = Depends(require("assets:edit")),
               db: Session = Depends(get_db)) -> dict:
    a = load_asset(db, p, asset_id)
    if a.allocation_status in ("assigned", "reserved", "in_maintenance"):
        raise conflict("استرجع الأصل أو فك الحجز أو اقفل الصيانة الأول")
    prev, a.allocation_status = a.allocation_status, body.allocation_status
    a.version += 1
    record_event(db, a, "status", p.user.id, from_status=prev, to_status=a.allocation_status, notes=body.reason)
    audit.record(db, "asset.status", actor_id=p.user.id, entity="asset", entity_id=a.id, company_id=a.company_id,
                 details={"from": prev, "to": a.allocation_status, "reason": body.reason}, ip=client_ip(request))
    db.commit()
    return asset_out(a, db)


@router.post("/{asset_id}/links", status_code=201)
def link(asset_id: str, body: LinkIn, request: Request, p: Principal = Depends(require("assets:edit")),
         db: Session = Depends(get_db)) -> dict:
    parent, child = load_asset(db, p, asset_id), load_asset(db, p, body.child_asset_id)
    if parent.id == child.id:
        raise bad("مينفعش تربط الأصل بنفسه")
    if db.scalar(select(AssetLink).where(AssetLink.child_asset_id == child.id, AssetLink.unlinked_at.is_(None))):
        raise conflict("الشاشة/الملحق مربوط بجهاز تاني — فكه الأول")
    lk = AssetLink(parent_asset_id=parent.id, child_asset_id=child.id)
    db.add(lk)
    record_event(db, parent, "link", p.user.id, notes=f"linked {child.code}")
    audit.record(db, "asset.linked", actor_id=p.user.id, entity="asset", entity_id=parent.id,
                 company_id=parent.company_id, details={"child": child.code}, ip=client_ip(request))
    db.commit()
    return {"id": lk.id, "parent": parent.code, "child": child.code}


@router.delete("/links/{link_id}")
def unlink(link_id: str, request: Request, p: Principal = Depends(require("assets:edit")),
           db: Session = Depends(get_db)) -> dict:
    lk = db.get(AssetLink, link_id)
    if lk is None or lk.unlinked_at:
        raise not_found("الربط غير موجود")
    parent = load_asset(db, p, lk.parent_asset_id)
    lk.unlinked_at = now()
    record_event(db, parent, "unlink", p.user.id, notes=f"unlinked {lk.child_asset_id}")
    audit.record(db, "asset.unlinked", actor_id=p.user.id, entity="asset", entity_id=parent.id,
                 company_id=parent.company_id, ip=client_ip(request))
    db.commit()
    return {"ok": True}


@router.post("/{asset_id}/maintenance", status_code=201)
def open_maintenance(asset_id: str, body: MaintOpen, request: Request,
                     p: Principal = Depends(require("assets:edit")), db: Session = Depends(get_db)) -> dict:
    a = load_asset(db, p, asset_id)
    if a.allocation_status in ("in_maintenance", "lost", "retired"):
        raise conflict("الأصل حالته مش مناسبة لفتح صيانة")
    from ..common import today
    m = Maintenance(asset_id=a.id, company_id=a.company_id, issue=body.issue, vendor=body.vendor,
                    opened_at=body.opened_at or today(), prev_allocation=a.allocation_status)
    db.add(m)
    prev, a.allocation_status = a.allocation_status, "in_maintenance"
    a.version += 1
    record_event(db, a, "maintenance_out", p.user.id, employee_id=a.holder_employee_id, from_status=prev,
                 to_status="in_maintenance", notes=body.issue)
    db.flush()
    audit.record(db, "maintenance.opened", actor_id=p.user.id, entity="maintenance", entity_id=m.id,
                 company_id=a.company_id, details={"asset": a.code}, ip=client_ip(request))
    db.commit()
    return row(m, ["id", "asset_id", "issue", "vendor", "opened_at", "status"])


@router.post("/maintenance/{maint_id}/close")
def close_maintenance(maint_id: str, body: MaintClose, request: Request,
                      p: Principal = Depends(require("assets:edit")), db: Session = Depends(get_db)) -> dict:
    m = db.get(Maintenance, maint_id)
    if m is None or m.status != "open":
        raise not_found("أمر صيانة مفتوح غير موجود")
    a = load_asset(db, p, m.asset_id)
    from ..common import today
    m.status, m.result, m.closed_at = "closed", body.result, body.closed_at or today()
    # device goes back to its holder if it was assigned before; holder unchanged by maintenance
    a.allocation_status = "assigned" if a.holder_employee_id else (m.prev_allocation if m.prev_allocation not in
                                                                    ("reserved",) else "available")
    a.tech_condition = body.tech_condition
    a.version += 1
    record_event(db, a, "maintenance_in", p.user.id, employee_id=a.holder_employee_id, from_status="in_maintenance",
                 to_status=a.allocation_status, notes=body.result)
    audit.record(db, "maintenance.closed", actor_id=p.user.id, entity="maintenance", entity_id=m.id,
                 company_id=a.company_id, details={"asset": a.code, "condition": body.tech_condition},
                 ip=client_ip(request))
    db.commit()
    return row(m, ["id", "asset_id", "issue", "vendor", "opened_at", "closed_at", "result", "status"])


# ---------------------------------------------------------------- device passwords (encrypted store)
PW_PREFIX = "asset-pw-"


def _store(request: Request):  # noqa: ANN202
    from ..secrets_store import SecretStoreError, store_for
    try:
        return store_for(request.app.state.settings)
    except SecretStoreError as e:
        raise bad(str(e)) from None


def save_device_password(store, a: Asset, password: str) -> None:  # noqa: ANN001
    """Encrypt and keep the device password outside the DB; the asset only keeps an opaque vault reference."""
    store.set(PW_PREFIX + a.id, password)
    a.vault_ref = "vault:" + PW_PREFIX + a.id


class PasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)


@router.put("/{asset_id}/password")
def set_device_password(asset_id: str, body: PasswordIn, request: Request,
                        p: Principal = Depends(require("assets:edit", "assets:view_secrets")),
                        db: Session = Depends(get_db)) -> dict:
    a = load_asset(db, p, asset_id)
    save_device_password(_store(request), a, body.password)
    a.version += 1
    audit.record(db, "asset.password_set", actor_id=p.user.id, entity="asset", entity_id=a.id, company_id=a.company_id,
                 ip=client_ip(request))
    db.commit()
    return {"has_password": True}


@router.post("/{asset_id}/password/reveal")
def reveal_device_password(asset_id: str, request: Request, p: Principal = Depends(require("assets:view_secrets")),
                           db: Session = Depends(get_db)) -> dict:
    """Returns the password to an authorised user — and leaves an audit trail every single time."""
    a = load_asset(db, p, asset_id)
    if not (a.vault_ref or "").startswith("vault:"):
        raise not_found("مفيش كلمة مرور محفوظة للجهاز ده")
    value = _store(request).get(a.vault_ref[6:])
    if value is None:
        raise not_found("كلمة المرور مش موجودة في المخزن")
    audit.record(db, "asset.password_revealed", actor_id=p.user.id, entity="asset", entity_id=a.id,
                 company_id=a.company_id, details={"code": a.code}, ip=client_ip(request))
    db.commit()
    return {"password": value}


@router.delete("/{asset_id}/password")
def delete_device_password(asset_id: str, request: Request,
                           p: Principal = Depends(require("assets:edit", "assets:view_secrets")),
                           db: Session = Depends(get_db)) -> dict:
    a = load_asset(db, p, asset_id)
    if (a.vault_ref or "").startswith("vault:"):
        _store(request).delete(a.vault_ref[6:])
    a.vault_ref = ""
    a.version += 1
    audit.record(db, "asset.password_deleted", actor_id=p.user.id, entity="asset", entity_id=a.id,
                 company_id=a.company_id, ip=client_ip(request))
    db.commit()
    return {"has_password": False}


# ---------------------------------------------------------------- swap: employee changes device
class SwapIn(BaseModel):
    new_asset_id: str
    old_result: str = Field(default="returned", pattern="^(returned|damaged|missing)$")
    notes: str = ""


@router.post("/{asset_id}/swap")
def swap_device(asset_id: str, body: SwapIn, request: Request, p: Principal = Depends(require("assets:edit")),
                db: Session = Depends(get_db)) -> dict:
    """Atomic: return the employee's current device (with its condition) and hand over the replacement.
    Both custody events are recorded; if the handover is not allowed (e.g. offboarding freeze) nothing changes."""
    old = load_asset(db, p, asset_id)
    new = load_asset(db, p, body.new_asset_id)
    if old.id == new.id:
        raise bad("اختار جهاز تاني")
    if old.allocation_status != "assigned" or not old.holder_employee_id:
        raise conflict("الجهاز الحالي مش مسلّم لموظف")
    emp_id = old.holder_employee_id
    note = (f"استبدال: {old.code} → {new.code}. " + body.notes).strip()
    try:
        with db.begin_nested():
            do_return(db, old, body.old_result, p.user.id, note)
            do_handover(db, new, emp_id, p.user.id, note)
    except HTTPException:
        db.rollback()
        raise
    audit.record(db, "asset.swapped", actor_id=p.user.id, entity="asset", entity_id=new.id, company_id=new.company_id,
                 details={"employee_id": emp_id, "old": old.code, "new": new.code, "old_result": body.old_result},
                 ip=client_ip(request))
    db.commit()
    return {"old": asset_out(old, db), "new": asset_out(new, db)}


# ---------------------------------------------------------------- category change
class RecategorizeIn(BaseModel):
    category_code: str
    reason: str = Field(min_length=3)


@router.post("/{asset_id}/category")
def change_category(asset_id: str, body: RecategorizeIn, request: Request,
                    p: Principal = Depends(require("assets:edit")), db: Session = Depends(get_db)) -> dict:
    """Move a device to another category (e.g. CMP -> CMPM). It gets the next code in the new category; the old
    code is kept in the custody history so labels/paperwork can be traced. Reprint the asset label afterwards."""
    a = load_asset(db, p, asset_id)
    cat = db.get(AssetCategory, body.category_code)
    if cat is None:
        raise bad("فئة غير معروفة")
    if cat.code == a.category_code:
        raise bad("الجهاز في الفئة دي بالفعل")
    company = get_company(db, a.company_id)
    old_code = a.code
    seq = (db.scalar(select(func.max(Asset.seq)).where(Asset.company_id == a.company_id,
                                                       Asset.category_code == cat.code)) or 0) + 1
    a.category_code, a.seq, a.code = cat.code, seq, f"{company.asset_prefix}-{cat.code}-{seq:0{cat.seq_width}d}"
    a.version += 1
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise conflict("تعارض في توليد الكود، حاول مرة تانية") from None
    record_event(db, a, "recategorized", p.user.id, notes=f"{old_code} → {a.code}. السبب: {body.reason}")
    audit.record(db, "asset.recategorized", actor_id=p.user.id, entity="asset", entity_id=a.id, company_id=a.company_id,
                 details={"old_code": old_code, "new_code": a.code, "reason": body.reason}, ip=client_ip(request))
    db.commit()
    return asset_out(a, db)


def employee_assets(db: Session, employee_id: str) -> list[Asset]:
    return list(db.scalars(select(Asset).where(Asset.holder_employee_id == employee_id)))
