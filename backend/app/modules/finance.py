"""M05 — IT procurement & finance (FINAL v1.0). IT spend only.

- Purchase request -> print -> CEO approves ON PAPER (we record evidence, never a fake signature)
  -> purchase outside the system -> (partial) receipts -> invoice -> payments -> allocations -> close.
- One economic event (`financial_events`) per real-world cost, unique per source; assets/maintenance/renewals
  link to it, nothing is copied. Company totals never double-count allocations.
- Allocations per event must total exactly 100%; "company-wide" = department null.
- Invoices: payments may be partial; overpayment rejected. Invoice != paid.
- Paying a renewal here never moves the M04 due date — only an M04 renewal event does.
- Approved financial records are not deleted; cancellation is a dated correction.
- Original currency kept; reports never sum different currencies without an explicit rate.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .. import audit
from ..common import (bad, conflict, ensure_company, get_company, iso, next_code, not_found, now, row, scope_filter,
                      today)
from ..db import Base
from ..deps import Principal, client_ip, get_db, require
from ..models import Branch, Company, Department, Lookup, new_id, utcnow
from .assets import Asset, Maintenance, create_asset
from .files import require_file

PR_STATUS = {"draft": "مسودة", "awaiting_approval": "بانتظار الطباعة/الاعتماد الورقي",
             "approved": "اعتمد ورقيًا", "rejected": "رفض ورقيًا", "in_procurement": "قيد التوريد",
             "partially_received": "استلام جزئي", "received": "مستلم", "closed": "مغلق", "cancelled": "ملغى"}
EXPENSE_KINDS = {"purchase": "شراء أجهزة/معدات", "maintenance": "صيانة", "renewal": "تجديد خدمة",
                 "subscription": "اشتراك", "consumable": "مستهلكات", "sim_recharge": "شحن شرائح",
                 "internet": "إنترنت/أرضي", "other": "أخرى"}
VENDOR_ACTIVITIES = ["أجهزة", "صيانة", "إنترنت", "شرائح", "اشتراكات", "متعدد", "أخرى"]
D0 = Decimal("0")


def money(v) -> Decimal:  # noqa: ANN001
    return Decimal(str(v or 0)).quantize(Decimal("0.01"))


def seed_finance(db: Session) -> None:
    if db.scalar(select(Lookup).where(Lookup.list_key == "vendor_activity")) is None:
        for i, v in enumerate(VENDOR_ACTIVITIES):
            db.add(Lookup(list_key="vendor_activity", value=v, sort=i))


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    activity: Mapped[str] = mapped_column(String(60), default="")
    company_ids: Mapped[list] = mapped_column(JSON, default=list)  # companies served; empty = all
    contact: Mapped[str] = mapped_column(Text, default="")
    currencies: Mapped[str] = mapped_column(String(60), default="")
    payment_terms: Mapped[str] = mapped_column(String(200), default="")
    active: Mapped[bool] = mapped_column(default=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    legacy_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PurchaseRequest(Base):
    __tablename__ = "purchase_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    branch_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    beneficiary_department_ids: Mapped[list] = mapped_column(JSON, default=list)
    requester: Mapped[str] = mapped_column(String(200))
    request_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    item_type: Mapped[str] = mapped_column(String(40), default="purchase")
    suggested_supplier_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="EGP")
    status: Mapped[str] = mapped_column(String(30), default="draft")
    approval_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    approver_name: Mapped[str] = mapped_column(String(200), default="")
    approval_file_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    approval_recorded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    printed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PurchaseItem(Base):
    __tablename__ = "purchase_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    request_id: Mapped[str] = mapped_column(ForeignKey("purchase_requests.id"), index=True)
    item: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    qty: Mapped[int] = mapped_column(Integer)
    unit: Mapped[str] = mapped_column(String(30), default="قطعة")
    est_unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    asset_category_code: Mapped[str | None] = mapped_column(String(20), nullable=True)  # creates M02 assets on receipt
    received_qty: Mapped[int] = mapped_column(Integer, default=0)


class Receipt(Base):
    __tablename__ = "receipts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    request_id: Mapped[str] = mapped_column(ForeignKey("purchase_requests.id"), index=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("purchase_items.id"))
    qty: Mapped[int] = mapped_column(Integer)
    actual_unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    supplier_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    received_on: Mapped[date] = mapped_column(Date)
    asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    recorded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FinancialEvent(Base):
    """ONE economic event per real cost. Unique source -> no duplicate costs across modules."""
    __tablename__ = "financial_events"
    __table_args__ = (UniqueConstraint("source_module", "source_type", "source_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    branch_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(30))
    description: Mapped[str] = mapped_column(Text, default="")
    source_module: Mapped[str] = mapped_column(String(10))  # M02 | M04 | M05
    source_type: Mapped[str] = mapped_column(String(30))    # purchase_request | maintenance | renewal | direct
    source_id: Mapped[str] = mapped_column(String(36))
    currency: Mapped[str] = mapped_column(String(3))
    committed_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)  # expected / forecast
    occurred_on: Mapped[date] = mapped_column(Date)
    supplier_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | cancelled
    missing_document: Mapped[bool] = mapped_column(default=True)
    cancel_reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("supplier_id", "number"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    financial_event_id: Mapped[str] = mapped_column(ForeignKey("financial_events.id"), index=True)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"))
    number: Mapped[str] = mapped_column(String(80))
    invoice_date: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3))
    base_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    tax: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    shipping: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    discount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    file_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    review_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | reviewed | cancelled
    recorded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    paid_on: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3))
    method: Mapped[str] = mapped_column(String(60), default="")
    receipt_ref: Mapped[str] = mapped_column(String(120), default="")
    receipt_file_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    is_correction: Mapped[bool] = mapped_column(default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    recorded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Allocation(Base):
    __tablename__ = "cost_allocations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    financial_event_id: Mapped[str] = mapped_column(ForeignKey("financial_events.id"), index=True)
    company_id: Mapped[str] = mapped_column(String(36))
    branch_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    department_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # None = company-wide
    percentage: Mapped[Decimal] = mapped_column(Numeric(7, 4))
    reason: Mapped[str] = mapped_column(String(200), default="")


class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("company_id", "year", "month", "kind", "currency"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = whole year
    kind: Mapped[str] = mapped_column(String(30), default="all")
    currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))


# ---------------------------------------------------------------- computations
def invoice_paid(db: Session, inv: Invoice) -> Decimal:
    return money(db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.invoice_id == inv.id)))


def event_totals(db: Session, ev: FinancialEvent) -> dict:
    invs = [i for i in db.scalars(select(Invoice).where(Invoice.financial_event_id == ev.id,
                                                         Invoice.review_status != "cancelled"))]
    invoiced = sum((money(i.total) for i in invs), D0)
    paid = sum((invoice_paid(db, i) for i in invs), D0)
    return {"committed": money(ev.committed_amount), "invoiced": invoiced, "paid": paid,
            "outstanding": invoiced - paid}


def event_out(db: Session, ev: FinancialEvent) -> dict:
    t = event_totals(db, ev)
    d = row(ev, ["id", "code", "company_id", "branch_id", "kind", "description", "source_module", "source_type",
                 "source_id", "currency", "occurred_on", "supplier_id", "status", "missing_document", "cancel_reason"])
    d.update({k: str(v) for k, v in t.items()})
    d["kind_ar"] = EXPENSE_KINDS.get(ev.kind, ev.kind)
    d["allocations"] = [row(a, ["id", "branch_id", "department_id", "reason"]) | {"percentage": str(a.percentage)}
                        for a in db.scalars(select(Allocation).where(Allocation.financial_event_id == ev.id))]
    d["invoices"] = [row(i, ["id", "number", "invoice_date", "currency", "file_id", "review_status", "supplier_id"])
                     | {"total": str(money(i.total)), "paid": str(invoice_paid(db, i))}
                     for i in db.scalars(select(Invoice).where(Invoice.financial_event_id == ev.id))]
    return d


def create_event(db: Session, *, company_id: str, kind: str, source_module: str, source_type: str, source_id: str,
                 currency: str, committed: Decimal, occurred_on: date, actor_id: str | None, description: str = "",
                 supplier_id: str | None = None, branch_id: str | None = None) -> FinancialEvent:
    ex = db.scalar(select(FinancialEvent).where(FinancialEvent.source_module == source_module,
                                                FinancialEvent.source_type == source_type,
                                                FinancialEvent.source_id == source_id))
    if ex:
        raise conflict(f"العملية الاقتصادية مسجلة بالفعل ({ex.code}) — مينفعش تتسجل مرتين")
    ev = FinancialEvent(code=next_code(db, FinancialEvent, FinancialEvent.code, f"FE-{occurred_on.year}-", 5),
                        company_id=company_id, branch_id=branch_id, kind=kind, description=description,
                        source_module=source_module, source_type=source_type, source_id=source_id,
                        currency=currency.upper(), committed_amount=committed, occurred_on=occurred_on,
                        supplier_id=supplier_id, created_by=actor_id)
    db.add(ev)
    db.flush()
    return ev


def pr_totals(db: Session, pr: PurchaseRequest) -> dict:
    items = list(db.scalars(select(PurchaseItem).where(PurchaseItem.request_id == pr.id)))
    est = sum((money(i.est_unit_cost) * i.qty for i in items), D0)
    actual = sum((money(r.actual_unit_cost) * r.qty
                  for r in db.scalars(select(Receipt).where(Receipt.request_id == pr.id))), D0)
    return {"estimated": est, "actual_received": actual, "items": items}


def pr_out(db: Session, pr: PurchaseRequest) -> dict:
    t = pr_totals(db, pr)
    d = row(pr, ["id", "code", "company_id", "branch_id", "beneficiary_department_ids", "requester", "request_date",
                 "reason", "priority", "item_type", "suggested_supplier_id", "currency", "status", "approval_date",
                 "approver_name", "approval_file_id", "printed_at", "notes", "created_at"])
    d["status_ar"] = PR_STATUS[pr.status]
    d["estimated_total"], d["actual_received_total"] = str(t["estimated"]), str(t["actual_received"])
    d["difference"] = str(t["actual_received"] - t["estimated"])
    d["items"] = [row(i, ["id", "item", "description", "qty", "unit", "asset_category_code", "received_qty"])
                  | {"est_unit_cost": str(money(i.est_unit_cost)), "remaining_qty": i.qty - i.received_qty}
                  for i in t["items"]]
    ev = db.scalar(select(FinancialEvent).where(FinancialEvent.source_type == "purchase_request",
                                                FinancialEvent.source_id == pr.id))
    d["financial_event_id"] = ev.id if ev else None
    return d


# ---------------------------------------------------------------- schemas
class SupplierIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    activity: str = ""
    company_ids: list[str] = Field(default_factory=list)
    contact: str = ""
    currencies: str = ""
    payment_terms: str = ""
    notes: str = ""


class ItemIn(BaseModel):
    item: str = Field(min_length=1)
    description: str = ""
    qty: int = Field(ge=1)
    unit: str = "قطعة"
    est_unit_cost: Decimal = Field(ge=0)
    asset_category_code: str | None = None


class PRIn(BaseModel):
    company_id: str
    branch_id: str | None = None
    beneficiary_department_ids: list[str] = Field(default_factory=list)
    requester: str = Field(min_length=2)
    request_date: date | None = None
    reason: str = Field(min_length=3)
    priority: str = Field(default="normal", pattern="^(low|normal|high|urgent)$")
    item_type: str = "purchase"
    suggested_supplier_id: str | None = None
    currency: str = Field(default="EGP", min_length=3, max_length=3)
    notes: str = ""
    items: list[ItemIn] = Field(min_length=1)


class ApprovalIn(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    approval_date: date
    approver_name: str = Field(min_length=2)
    approval_file_id: str | None = None  # scan of the signed paper; status shows "missing evidence" without it


class ReceiveIn(BaseModel):
    item_id: str
    qty: int = Field(ge=1)
    actual_unit_cost: Decimal = Field(ge=0)
    supplier_id: str | None = None
    received_on: date | None = None
    serials: list[str] = Field(default_factory=list)  # one per unit when the item creates assets
    notes: str = ""


class DirectExpenseIn(BaseModel):
    company_id: str
    branch_id: str | None = None
    kind: str
    description: str = Field(min_length=2)
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    occurred_on: date
    supplier_id: str | None = None
    source_module: str = Field(default="M05", pattern="^(M02|M04|M05)$")
    source_type: str = "direct"
    source_id: str | None = None  # maintenance id / renewal event id when linked


class InvoiceIn(BaseModel):
    financial_event_id: str
    supplier_id: str
    number: str = Field(min_length=1, max_length=80)
    invoice_date: date
    currency: str = Field(min_length=3, max_length=3)
    base_amount: Decimal = Field(ge=0)
    tax: Decimal = Field(default=Decimal(0), ge=0)
    shipping: Decimal = Field(default=Decimal(0), ge=0)
    discount: Decimal = Field(default=Decimal(0), ge=0)
    file_id: str | None = None


class PaymentIn(BaseModel):
    paid_on: date
    amount: Decimal = Field(gt=0)
    method: str = ""
    receipt_ref: str = ""
    receipt_file_id: str | None = None
    notes: str = ""


class AllocationLine(BaseModel):
    department_id: str | None = None
    branch_id: str | None = None
    percentage: Decimal = Field(gt=0, le=100)
    reason: str = ""


class AllocationsIn(BaseModel):
    lines: list[AllocationLine] = Field(min_length=1)


class CancelIn(BaseModel):
    reason: str = Field(min_length=3)


class BudgetIn(BaseModel):
    company_id: str
    year: int = Field(ge=2000, le=2100)
    month: int | None = Field(default=None, ge=1, le=12)
    kind: str = "all"
    currency: str = Field(min_length=3, max_length=3)
    amount: Decimal = Field(ge=0)


# ---------------------------------------------------------------- API
router = APIRouter(prefix="/api/finance", tags=["M05 finance"])


def _pr(db: Session, p: Principal, pr_id: str) -> PurchaseRequest:
    pr = db.get(PurchaseRequest, pr_id)
    if pr is None:
        raise not_found("طلب الشراء غير موجود")
    ensure_company(p, pr.company_id)
    return pr


def _ev(db: Session, p: Principal, ev_id: str) -> FinancialEvent:
    ev = db.get(FinancialEvent, ev_id)
    if ev is None:
        raise not_found("العملية المالية غير موجودة")
    ensure_company(p, ev.company_id)
    return ev


@router.get("/meta")
def meta(_: Principal = Depends(require("finance:view")), db: Session = Depends(get_db)) -> dict:
    acts = [lk.value for lk in db.scalars(select(Lookup).where(Lookup.list_key == "vendor_activity")
                                          .order_by(Lookup.sort))]
    return {"pr_status": PR_STATUS, "expense_kinds": EXPENSE_KINDS, "vendor_activities": acts}


@router.get("/suppliers")
def list_suppliers(_: Principal = Depends(require("finance:view")), db: Session = Depends(get_db)) -> list[dict]:
    return [row(s, ["id", "name", "activity", "company_ids", "contact", "currencies", "payment_terms", "active",
                    "notes"]) for s in db.scalars(select(Supplier).order_by(Supplier.name))]


@router.post("/suppliers", status_code=201)
def add_supplier(body: SupplierIn, request: Request, p: Principal = Depends(require("finance:create")),
                 db: Session = Depends(get_db)) -> dict:
    if db.scalar(select(Supplier).where(func.lower(Supplier.name) == body.name.strip().lower())):
        raise conflict("المورد موجود بالفعل")
    s = Supplier(**body.model_dump() | {"name": body.name.strip()})
    db.add(s)
    db.flush()
    audit.record(db, "supplier.created", actor_id=p.user.id, entity="supplier", entity_id=s.id,
                 details={"name": s.name}, ip=client_ip(request))
    db.commit()
    return row(s, ["id", "name", "activity", "active"])


@router.get("/purchase-requests")
def list_prs(status: str | None = None, company_id: str | None = None,
             p: Principal = Depends(require("finance:view")), db: Session = Depends(get_db)) -> list[dict]:
    s = scope_filter(select(PurchaseRequest), p, PurchaseRequest.company_id).order_by(PurchaseRequest.code.desc())
    if status:
        s = s.where(PurchaseRequest.status == status)
    if company_id:
        s = s.where(PurchaseRequest.company_id == company_id)
    return [pr_out(db, pr) for pr in db.scalars(s)]


@router.post("/purchase-requests", status_code=201)
def create_pr(body: PRIn, request: Request, p: Principal = Depends(require("finance:create")),
              db: Session = Depends(get_db)) -> dict:
    ensure_company(p, body.company_id)
    get_company(db, body.company_id)
    d = body.request_date or today()
    pr = PurchaseRequest(code=next_code(db, PurchaseRequest, PurchaseRequest.code, f"PR-{d.year}-", 4),
                         company_id=body.company_id, branch_id=body.branch_id,
                         beneficiary_department_ids=body.beneficiary_department_ids, requester=body.requester,
                         request_date=d, reason=body.reason, priority=body.priority, item_type=body.item_type,
                         suggested_supplier_id=body.suggested_supplier_id, currency=body.currency.upper(),
                         notes=body.notes, created_by=p.user.id, status="awaiting_approval")
    db.add(pr)
    db.flush()
    for it in body.items:
        db.add(PurchaseItem(request_id=pr.id, **it.model_dump()))
    db.flush()
    audit.record(db, "purchase_request.created", actor_id=p.user.id, entity="purchase_request", entity_id=pr.id,
                 company_id=pr.company_id, details={"code": pr.code}, ip=client_ip(request))
    db.commit()
    return pr_out(db, pr)


@router.get("/purchase-requests/{pr_id}")
def pr_detail(pr_id: str, p: Principal = Depends(require("finance:view")), db: Session = Depends(get_db)) -> dict:
    pr = _pr(db, p, pr_id)
    d = pr_out(db, pr)
    d["receipts"] = [row(r, ["id", "item_id", "qty", "received_on", "asset_ids", "notes"])
                     | {"actual_unit_cost": str(money(r.actual_unit_cost))}
                     for r in db.scalars(select(Receipt).where(Receipt.request_id == pr.id)
                                         .order_by(Receipt.received_on))]
    return d


@router.get("/purchase-requests/{pr_id}/print", response_class=HTMLResponse)
def print_pr(pr_id: str, request: Request, p: Principal = Depends(require("finance:print")),
             db: Session = Depends(get_db)) -> HTMLResponse:
    """A4 RTL request for the CEO's PAPER approval. Empty signature box — no fake signature or stamp."""
    import html as h
    pr = _pr(db, p, pr_id)
    c = db.get(Company, pr.company_id)
    br = db.get(Branch, pr.branch_id) if pr.branch_id else None
    deps = [db.get(Department, x) for x in pr.beneficiary_department_ids]
    t = pr_totals(db, pr)
    rows = "".join(f"<tr><td>{n}</td><td>{h.escape(i.item)}</td><td>{h.escape(i.description)}</td><td>{i.qty} "
                   f"{h.escape(i.unit)}</td><td>{money(i.est_unit_cost)}</td><td>{money(i.est_unit_cost) * i.qty}</td>"
                   f"</tr>" for n, i in enumerate(t["items"], 1))
    pr.printed_at = pr.printed_at or now()
    audit.record(db, "purchase_request.printed", actor_id=p.user.id, entity="purchase_request", entity_id=pr.id,
                 company_id=pr.company_id, ip=client_ip(request))
    db.commit()
    page = f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>{pr.code}</title>
<style>@page{{size:A4;margin:18mm}}body{{font-family:Cairo,Tahoma,sans-serif;color:#000}}h1{{font-size:20px;text-align:center}}
table{{width:100%;border-collapse:collapse}}td,th{{border:1px solid #555;padding:7px;font-size:13px}}.m td{{border:0}}
.sig td{{height:60px}}@media print{{button{{display:none}}}}</style></head><body><button onclick="print()">طباعة</button>
<h1>طلب شراء — قسم تقنية المعلومات</h1>
<table class="m"><tr><td>رقم الطلب: <b>{pr.code}</b></td><td>التاريخ: {pr.request_date}</td></tr>
<tr><td>الشركة: {h.escape(c.name)}</td><td>الفرع: {h.escape(br.name if br else '—')}</td></tr>
<tr><td>الأقسام المستفيدة: {h.escape('، '.join(d.name for d in deps if d) or '—')}</td><td>مقدم الطلب: {h.escape(pr.requester)}</td></tr>
<tr><td colspan="2">سبب الشراء: {h.escape(pr.reason)}</td></tr></table><br>
<table><tr><th>#</th><th>الصنف</th><th>الوصف</th><th>الكمية</th><th>سعر تقديري</th><th>الإجمالي</th></tr>{rows}
<tr><td colspan="5"><b>الإجمالي التقديري ({pr.currency})</b></td><td><b>{t['estimated']}</b></td></tr></table><br>
<table class="sig"><tr><th>اعتماد الرئيس التنفيذي (ورقي)</th><th>الاسم</th><th>التوقيع</th><th>التاريخ</th></tr>
<tr><td>☐ موافق &nbsp; ☐ مرفوض</td><td></td><td></td><td></td></tr></table>
<p>ملاحظات: ____________________________________________</p></body></html>"""
    return HTMLResponse(page)


@router.post("/purchase-requests/{pr_id}/approval")
def record_approval(pr_id: str, body: ApprovalIn, request: Request, p: Principal = Depends(require("finance:approve")),
                    db: Session = Depends(get_db)) -> dict:
    pr = _pr(db, p, pr_id)
    if pr.status != "awaiting_approval":
        raise conflict("الطلب مش بانتظار الاعتماد")
    if pr.printed_at is None:
        raise conflict("اطبع الطلب للاعتماد الورقي الأول")
    if body.approval_file_id:
        require_file(db, body.approval_file_id, "paper_approval")
    pr.status = body.decision
    pr.approval_date, pr.approver_name = body.approval_date, body.approver_name
    pr.approval_file_id, pr.approval_recorded_by = body.approval_file_id, p.user.id
    if body.decision == "approved":
        est = pr_totals(db, pr)["estimated"]
        ev = create_event(db, company_id=pr.company_id, branch_id=pr.branch_id, kind="purchase",
                          source_module="M05", source_type="purchase_request", source_id=pr.id, currency=pr.currency,
                          committed=est, occurred_on=body.approval_date, actor_id=p.user.id,
                          description=f"{pr.code}: {pr.reason}", supplier_id=pr.suggested_supplier_id)
        deps = pr.beneficiary_department_ids
        if deps:  # default equal split across beneficiary departments (editable later)
            share = (Decimal(100) / len(deps)).quantize(Decimal("0.0001"))
            for i, dep in enumerate(deps):
                pct = share if i < len(deps) - 1 else Decimal(100) - share * (len(deps) - 1)
                db.add(Allocation(financial_event_id=ev.id, company_id=pr.company_id, branch_id=pr.branch_id,
                                  department_id=dep, percentage=pct, reason="توزيع افتراضي على الأقسام المستفيدة"))
        else:
            db.add(Allocation(financial_event_id=ev.id, company_id=pr.company_id, branch_id=pr.branch_id,
                              department_id=None, percentage=Decimal(100), reason="Company-wide"))
    audit.record(db, f"purchase_request.{body.decision}_on_paper", actor_id=p.user.id, entity="purchase_request",
                 entity_id=pr.id, company_id=pr.company_id,
                 details={"approver": body.approver_name, "evidence": bool(body.approval_file_id)},
                 ip=client_ip(request))
    db.commit()
    d = pr_out(db, pr)
    d["evidence_missing"] = not pr.approval_file_id
    return d


@router.post("/purchase-requests/{pr_id}/receive", status_code=201)
def receive(pr_id: str, body: ReceiveIn, request: Request, p: Principal = Depends(require("finance:edit")),
            db: Session = Depends(get_db)) -> dict:
    pr = _pr(db, p, pr_id)
    if pr.status not in ("approved", "in_procurement", "partially_received"):
        raise conflict("مينفعش تستلم على طلب غير معتمد ورقيًا")
    it = db.get(PurchaseItem, body.item_id)
    if it is None or it.request_id != pr.id:
        raise bad("البند غير تابع للطلب")
    if it.received_qty + body.qty > it.qty:
        raise bad(f"الكمية أكبر من المتبقي ({it.qty - it.received_qty})")
    asset_ids = []
    if it.asset_category_code:
        if len(body.serials) != body.qty:
            raise bad("سجّل سيريال لكل جهاز مستلم (عدد السيريالات = الكمية)")
        r_tmp_id = new_id()
        for sn in body.serials:
            a = create_asset(db, company_id=pr.company_id, category_code=it.asset_category_code, actor_id=p.user.id,
                             source="receipt", serial=sn.strip(), model=it.item, purchase_date=body.received_on or today(),
                             supplier_id=body.supplier_id or pr.suggested_supplier_id, receipt_id=r_tmp_id,
                             allocation_status="in_stock")
            asset_ids.append(a.id)
    r = Receipt(id=r_tmp_id if it.asset_category_code else new_id(), request_id=pr.id, item_id=it.id, qty=body.qty,
                actual_unit_cost=body.actual_unit_cost, supplier_id=body.supplier_id, asset_ids=asset_ids,
                received_on=body.received_on or today(), notes=body.notes, recorded_by=p.user.id)
    db.add(r)
    it.received_qty += body.qty
    items = list(db.scalars(select(PurchaseItem).where(PurchaseItem.request_id == pr.id)))
    pr.status = "received" if all(i.received_qty >= i.qty for i in items) else "partially_received"
    audit.record(db, "purchase_request.received", actor_id=p.user.id, entity="purchase_request", entity_id=pr.id,
                 company_id=pr.company_id, details={"item": it.item, "qty": body.qty, "assets": len(asset_ids)},
                 ip=client_ip(request))
    db.commit()
    return pr_out(db, pr) | {"created_asset_ids": asset_ids}


@router.post("/purchase-requests/{pr_id}/cancel")
def cancel_pr(pr_id: str, body: CancelIn, request: Request, p: Principal = Depends(require("finance:edit")),
              db: Session = Depends(get_db)) -> dict:
    pr = _pr(db, p, pr_id)
    if pr.status in ("received", "closed", "cancelled"):
        raise conflict("مينفعش تلغي الطلب في الحالة دي")
    if db.scalar(select(Receipt).where(Receipt.request_id == pr.id)):
        raise conflict("في استلامات على الطلب — اعمل تصحيح بدل الإلغاء")
    pr.status = "cancelled"
    pr.notes = (pr.notes + f"\n[إلغاء {today()}] {body.reason}").strip()
    ev = db.scalar(select(FinancialEvent).where(FinancialEvent.source_type == "purchase_request",
                                                FinancialEvent.source_id == pr.id))
    if ev:
        ev.status, ev.cancel_reason = "cancelled", body.reason
    audit.record(db, "purchase_request.cancelled", actor_id=p.user.id, entity="purchase_request", entity_id=pr.id,
                 company_id=pr.company_id, details={"reason": body.reason}, ip=client_ip(request))
    db.commit()
    return pr_out(db, pr)


@router.post("/purchase-requests/{pr_id}/close")
def close_pr(pr_id: str, request: Request, p: Principal = Depends(require("finance:edit")),
             db: Session = Depends(get_db)) -> dict:
    pr = _pr(db, p, pr_id)
    if pr.status not in ("received", "partially_received"):
        raise conflict("اقفل الطلب بعد الاستلام")
    pr.status = "closed"
    audit.record(db, "purchase_request.closed", actor_id=p.user.id, entity="purchase_request", entity_id=pr.id,
                 company_id=pr.company_id, ip=client_ip(request))
    db.commit()
    return pr_out(db, pr)


# --- economic events, direct expenses
@router.get("/events")
def list_events(company_id: str | None = None, kind: str | None = None, p: Principal = Depends(require("finance:view")),
                db: Session = Depends(get_db)) -> list[dict]:
    s = scope_filter(select(FinancialEvent), p, FinancialEvent.company_id).order_by(FinancialEvent.occurred_on.desc())
    if company_id:
        s = s.where(FinancialEvent.company_id == company_id)
    if kind:
        s = s.where(FinancialEvent.kind == kind)
    return [event_out(db, e) for e in db.scalars(s)]


@router.get("/events/{ev_id}")
def event_detail(ev_id: str, p: Principal = Depends(require("finance:view")), db: Session = Depends(get_db)) -> dict:
    return event_out(db, _ev(db, p, ev_id))


@router.post("/expenses", status_code=201)
def direct_expense(body: DirectExpenseIn, request: Request, p: Principal = Depends(require("finance:create")),
                   db: Session = Depends(get_db)) -> dict:
    """Direct cost without a purchase request (maintenance, recharge, renewal...).
    Linking to an M02 maintenance or M04 renewal keeps ONE event and writes the link back there."""
    ensure_company(p, body.company_id)
    get_company(db, body.company_id)
    if body.kind not in EXPENSE_KINDS:
        raise bad("نوع مصروف غير معروف")
    src_id = body.source_id or new_id()
    link_obj = None
    if body.source_module == "M02":
        link_obj = db.get(Maintenance, src_id)
        if link_obj is None or body.source_type != "maintenance":
            raise bad("أمر الصيانة المرتبط غير موجود")
        if link_obj.financial_event_id:
            raise conflict("الصيانة دي ليها تكلفة مسجلة بالفعل")
    elif body.source_module == "M04":
        from .technical import RenewalEvent
        link_obj = db.get(RenewalEvent, src_id)
        if link_obj is None or body.source_type != "renewal":
            raise bad("حدث التجديد المرتبط غير موجود في M04")
        if link_obj.financial_event_id:
            raise conflict("التجديد ده ليه تكلفة مسجلة بالفعل")
    ev = create_event(db, company_id=body.company_id, branch_id=body.branch_id, kind=body.kind,
                      source_module=body.source_module, source_type=body.source_type, source_id=src_id,
                      currency=body.currency, committed=money(body.amount), occurred_on=body.occurred_on,
                      actor_id=p.user.id, description=body.description, supplier_id=body.supplier_id)
    if link_obj is not None:
        link_obj.financial_event_id = ev.id
    db.add(Allocation(financial_event_id=ev.id, company_id=body.company_id, branch_id=body.branch_id,
                      department_id=None, percentage=Decimal(100), reason="Company-wide (افتراضي)"))
    audit.record(db, "expense.recorded", actor_id=p.user.id, entity="financial_event", entity_id=ev.id,
                 company_id=ev.company_id, details={"kind": body.kind, "amount": str(body.amount),
                                                    "currency": ev.currency}, ip=client_ip(request))
    db.commit()
    return event_out(db, ev)


@router.put("/events/{ev_id}/allocations")
def set_allocations(ev_id: str, body: AllocationsIn, request: Request, p: Principal = Depends(require("finance:edit")),
                    db: Session = Depends(get_db)) -> dict:
    ev = _ev(db, p, ev_id)
    total = sum((ln.percentage for ln in body.lines), Decimal(0))
    if total != Decimal(100):
        raise bad(f"مجموع النسب لازم يساوي 100% (الحالي {total}%)")
    keys = [(ln.branch_id, ln.department_id) for ln in body.lines]
    if len(set(keys)) != len(keys):
        raise bad("في مستفيد متكرر في التوزيع")
    for ln in body.lines:
        if ln.department_id:
            dep = db.get(Department, ln.department_id)
            if dep is None or dep.company_id != ev.company_id:
                raise bad("القسم لا يتبع شركة العملية")
    for old in db.scalars(select(Allocation).where(Allocation.financial_event_id == ev.id)):
        db.delete(old)
    for ln in body.lines:
        db.add(Allocation(financial_event_id=ev.id, company_id=ev.company_id, **ln.model_dump()))
    audit.record(db, "allocation.set", actor_id=p.user.id, entity="financial_event", entity_id=ev.id,
                 company_id=ev.company_id, details={"lines": len(body.lines)}, ip=client_ip(request))
    db.commit()
    return event_out(db, ev)


@router.post("/events/{ev_id}/cancel")
def cancel_event(ev_id: str, body: CancelIn, request: Request, p: Principal = Depends(require("finance:approve")),
                 db: Session = Depends(get_db)) -> dict:
    ev = _ev(db, p, ev_id)
    if ev.status == "cancelled":
        raise conflict("ملغاة بالفعل")
    if any(Decimal(i["paid"]) > 0 for i in event_out(db, ev)["invoices"]):
        raise conflict("في مدفوعات على العملية — سجّل دفعة تصحيح أولًا")
    ev.status, ev.cancel_reason = "cancelled", f"{today()}: {body.reason}"
    audit.record(db, "financial_event.cancelled", actor_id=p.user.id, entity="financial_event", entity_id=ev.id,
                 company_id=ev.company_id, details={"reason": body.reason}, ip=client_ip(request))
    db.commit()
    return event_out(db, ev)


# --- invoices & payments
@router.post("/invoices", status_code=201)
def add_invoice(body: InvoiceIn, request: Request, p: Principal = Depends(require("finance:create")),
                db: Session = Depends(get_db)) -> dict:
    ev = _ev(db, p, body.financial_event_id)
    if ev.status == "cancelled":
        raise conflict("العملية ملغاة")
    if db.get(Supplier, body.supplier_id) is None:
        raise bad("المورد غير موجود")
    if db.scalar(select(Invoice).where(Invoice.supplier_id == body.supplier_id, Invoice.number == body.number)):
        raise conflict("رقم الفاتورة متسجل بالفعل لنفس المورد")
    if body.currency.upper() != ev.currency:
        raise bad("عملة الفاتورة مختلفة عن عملة العملية — مفيش تحويل تلقائي")
    if body.file_id:
        require_file(db, body.file_id, "invoice")
    total = money(body.base_amount) + money(body.tax) + money(body.shipping) - money(body.discount)
    if total < 0:
        raise bad("إجمالي الفاتورة سالب")
    inv = Invoice(**body.model_dump() | {"currency": body.currency.upper()}, total=total, recorded_by=p.user.id)
    db.add(inv)
    if body.file_id:
        ev.missing_document = False
    db.flush()
    audit.record(db, "invoice.recorded", actor_id=p.user.id, entity="invoice", entity_id=inv.id,
                 company_id=ev.company_id, details={"number": inv.number, "total": str(total)}, ip=client_ip(request))
    db.commit()
    return event_out(db, ev)


@router.post("/invoices/{inv_id}/payments", status_code=201)
def add_payment(inv_id: str, body: PaymentIn, request: Request, p: Principal = Depends(require("finance:create")),
                db: Session = Depends(get_db)) -> dict:
    inv = db.get(Invoice, inv_id)
    if inv is None:
        raise not_found("الفاتورة غير موجودة")
    ev = _ev(db, p, inv.financial_event_id)
    paid = invoice_paid(db, inv)
    if paid + money(body.amount) > money(inv.total):
        raise bad(f"الدفعة أكبر من المتبقي ({money(inv.total) - paid}) — التصحيح يتم بإجراء موثق")
    if body.receipt_file_id:
        require_file(db, body.receipt_file_id, "receipt")
    pay = Payment(invoice_id=inv.id, paid_on=body.paid_on, amount=money(body.amount), currency=inv.currency,
                  method=body.method, receipt_ref=body.receipt_ref, receipt_file_id=body.receipt_file_id,
                  notes=body.notes, recorded_by=p.user.id)
    db.add(pay)
    db.flush()
    audit.record(db, "payment.recorded", actor_id=p.user.id, entity="invoice", entity_id=inv.id,
                 company_id=ev.company_id, details={"amount": str(pay.amount)}, ip=client_ip(request))
    db.commit()
    return event_out(db, ev)


# --- budgets & summary
@router.put("/budgets", status_code=200)
def set_budget(body: BudgetIn, request: Request, p: Principal = Depends(require("finance:edit")),
               db: Session = Depends(get_db)) -> dict:
    ensure_company(p, body.company_id)
    b = db.scalar(select(Budget).where(Budget.company_id == body.company_id, Budget.year == body.year,
                                       Budget.month == body.month if body.month else Budget.month.is_(None),
                                       Budget.kind == body.kind, Budget.currency == body.currency.upper()))
    if b is None:
        b = Budget(**body.model_dump() | {"currency": body.currency.upper()})
        db.add(b)
    else:
        b.amount = body.amount
    audit.record(db, "budget.set", actor_id=p.user.id, entity="budget", entity_id=f"{body.year}-{body.month}",
                 company_id=body.company_id, details={"amount": str(body.amount)}, ip=client_ip(request))
    db.commit()
    return {"ok": True}


def spend_summary(db: Session, p: Principal | None, *, date_from: date, date_to: date, basis: str,
                  company_id: str | None = None, department_id: str | None = None) -> dict:
    """basis: committed (by occurred_on) | invoiced (by invoice_date) | paid (by paid_on).
    Per-department figures use allocation shares; company totals count each event ONCE.
    Currencies are reported separately (no implicit conversion)."""
    s = select(FinancialEvent).where(FinancialEvent.status == "active")
    if p is not None:
        s = scope_filter(s, p, FinancialEvent.company_id)
    if company_id:
        s = s.where(FinancialEvent.company_id == company_id)
    totals: dict[str, Decimal] = {}
    by_dept: dict[str, dict[str, Decimal]] = {}
    by_kind: dict[str, dict[str, Decimal]] = {}
    rows = []
    for ev in db.scalars(s):
        if basis == "committed":
            amt = money(ev.committed_amount) if date_from <= ev.occurred_on <= date_to else D0
        elif basis == "invoiced":
            amt = sum((money(i.total) for i in db.scalars(select(Invoice).where(
                Invoice.financial_event_id == ev.id, Invoice.review_status != "cancelled",
                Invoice.invoice_date >= date_from, Invoice.invoice_date <= date_to))), D0)
        else:
            amt = money(db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).join(Invoice)
                                  .where(Invoice.financial_event_id == ev.id, Payment.paid_on >= date_from,
                                         Payment.paid_on <= date_to)))
        if not amt:
            continue
        allocs = list(db.scalars(select(Allocation).where(Allocation.financial_event_id == ev.id)))
        if department_id:
            share = sum((a.percentage for a in allocs if a.department_id == department_id), Decimal(0))
            if not share:
                continue
            amt = (amt * share / 100).quantize(Decimal("0.01"))
        cur = ev.currency
        totals[cur] = totals.get(cur, D0) + amt
        by_kind.setdefault(ev.kind, {}).setdefault(cur, D0)
        by_kind[ev.kind][cur] += amt
        for a in allocs or [None]:
            key = (a.department_id or "company_wide") if a else "unallocated"
            part = amt if a is None else (amt * a.percentage / 100).quantize(Decimal("0.01"))
            if department_id and key != department_id:
                continue
            if department_id:
                part = amt
            by_dept.setdefault(key, {}).setdefault(cur, D0)
            by_dept[key][cur] += part
        rows.append({"event": ev.code, "kind": EXPENSE_KINDS.get(ev.kind, ev.kind), "description": ev.description,
                     "occurred_on": iso(ev.occurred_on), "currency": cur, "amount": str(amt)})
    fmt = lambda d: {k: {c: str(v) for c, v in x.items()} for k, x in d.items()}  # noqa: E731
    return {"basis": basis, "from": iso(date_from), "to": iso(date_to), "totals": {c: str(v) for c, v in totals.items()},
            "by_department": fmt(by_dept), "by_kind": fmt(by_kind), "rows": rows}


@router.get("/summary")
def summary(date_from: date, date_to: date, basis: str = "paid", company_id: str | None = None,
            department_id: str | None = None, p: Principal = Depends(require("finance:view")),
            db: Session = Depends(get_db)) -> dict:
    if basis not in ("committed", "invoiced", "paid"):
        raise bad("basis: committed | invoiced | paid")
    return spend_summary(db, p, date_from=date_from, date_to=date_to, basis=basis, company_id=company_id,
                         department_id=department_id)
