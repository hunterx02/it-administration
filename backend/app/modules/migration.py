"""Import the legacy browser data (`it_assets_v1` JSON export) into the server — dry-run first.

- Idempotent: every imported row keeps `legacy_id`; re-running skips what already exists.
- Nothing is invented: legacy "renewDate" values have no confirmed payment/period, so they are kept as a
  note and the record shows "renewal data incomplete" until a real renewal is recorded.
- Legacy company names are mapped explicitly to companies created by the user (company_map/default).
- Unmapped asset types are reported, not guessed.
- Imported custody reflects existing reality (event kind "imported"), not a new handover.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..common import bad, get_company, next_code
from ..deps import Principal, client_ip, get_db, require
from ..models import Department
from .assets import Asset, create_asset, record_event
from .finance import Supplier
from .people import Employee
from .technical import Line, Sim, SimEvent, Subscription

TYPE_TO_CATEGORY = {"laptop": "LTP", "desktop": "CMP", "mobile": "MOB", "monitor": "mon", "printer": "PRN",
                    "aio": "AIO", "mac": "MAC", "hdd": "Hard", "external_drive": "Hard"}
STATUS_MAP = {"assigned": ("assigned", "unknown"), "stock": ("in_stock", "unknown"), "ready": ("available", "unknown"),
              "repair": ("in_stock", "needs_repair"), "reserved": ("in_stock", "unknown"), "lost": ("lost", "unknown"),
              "retired": ("retired", "unknown"), "wipe": ("pending_wipe", "unknown")}
LINE_STATUS = {"assigned": "assigned", "stock": "available", "ready": "available", "cancelled": "cancelled",
               "suspended": "suspended"}


class LegacyImportIn(BaseModel):
    data: dict
    default_company_id: str
    company_map: dict[str, str] = Field(default_factory=dict)
    dry_run: bool = True


def _d(v) -> date | None:  # noqa: ANN001
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


router = APIRouter(prefix="/api/migration", tags=["migration"])


@router.post("/legacy-import")
def legacy_import(body: LegacyImportIn, request: Request, p: Principal = Depends(require("system:edit")),
                  db: Session = Depends(get_db)) -> dict:
    data = body.data
    comp_ids = {body.default_company_id, *body.company_map.values()}
    for cid in comp_ids:
        get_company(db, cid)
    company_of = lambda name: body.company_map.get(name or "", body.default_company_id)  # noqa: E731
    report: dict = {"employees": 0, "assets": 0, "sims": 0, "lines": 0, "subscriptions": 0, "suppliers": 0,
                    "skipped_existing": 0, "unmapped": [], "warnings": []}
    for cid in comp_ids:
        if not get_company(db, cid).asset_prefix:
            report["warnings"].append(f"الشركة {get_company(db, cid).name} بدون بادئة أكواد — الأصول مش هتتنقل")
    sp = db.begin_nested()
    dept_cache: dict[tuple[str, str], str] = {}

    def dept_id(company_id: str, name: str | None) -> str | None:
        if not name:
            return None
        k = (company_id, name)
        if k not in dept_cache:
            d = db.scalar(select(Department).where(Department.company_id == company_id, Department.name == name))
            if d is None:
                d = Department(company_id=company_id, name=name)
                db.add(d)
                db.flush()
            dept_cache[k] = d.id
        return dept_cache[k]

    emp_map: dict[str, str] = {}
    for m in data.get("team") or []:
        lid = str(m.get("id"))
        ex = db.scalar(select(Employee).where(Employee.legacy_id == lid))
        if ex:
            emp_map[lid] = ex.id
            report["skipped_existing"] += 1
            continue
        cid = company_of(m.get("company"))
        exited = m.get("status") == "exited" or m.get("active") is False
        code = m.get("empNo") or None
        if code and db.scalar(select(Employee).where(Employee.employee_code == code)):
            code = None
        if not code:
            from .people import next_employee_code
            code = next_employee_code(db, cid)
        e = Employee(company_id=cid, full_name=m.get("name") or "بدون اسم", job_title=m.get("role") or "",
                     department_id=dept_id(cid, m.get("dept")), email=m.get("email") or "",
                     phone=m.get("phone") or "", employee_code=code, hire_date=_d(m.get("startDate")),
                     exit_date=_d(m.get("leftAt")), status="exited" if exited else "active",
                     custody_frozen=exited, legacy_id=lid)
        db.add(e)
        db.flush()
        emp_map[lid] = e.id
        report["employees"] += 1

    for a in data.get("assets") or []:
        lid = str(a.get("id"))
        if db.scalar(select(Asset).where(Asset.legacy_id == lid)):
            report["skipped_existing"] += 1
            continue
        cat = TYPE_TO_CATEGORY.get(a.get("type"))
        if cat is None:
            report["unmapped"].append({"type": "asset", "legacy": a.get("tag"), "reason": f"نوع «{a.get('type')}» "
                                       "مالوش فئة معتمدة — أضف فئة أو سجّله يدويًا"})
            continue
        cid = company_of(a.get("company"))
        if not get_company(db, cid).asset_prefix:
            continue
        alloc, cond = STATUS_MAP.get(a.get("status"), ("in_stock", "unknown"))
        holder = emp_map.get(str(a.get("holderId"))) if a.get("holderId") else None
        if alloc == "assigned" and not holder:
            alloc = "in_stock"
            report["warnings"].append(f"{a.get('tag')}: مسلّم لموظف غير موجود — اتسجل في المخزن")
        new = create_asset(db, company_id=cid, category_code=cat, actor_id=p.user.id, source="import",
                           brand=a.get("brand") or "", model=a.get("model") or "", serial=a.get("serial") or "",
                           notes=f"legacy tag {a.get('tag')}. {a.get('specs') or ''} {a.get('notes') or ''}".strip(),
                           purchase_date=_d(a.get("purchaseDate")), warranty_end=_d(a.get("warrantyEnd")),
                           location=a.get("location") or "", tech_condition=cond, allocation_status=alloc,
                           legacy_status=a.get("status") or "", legacy_id=lid)
        if holder:
            new.holder_employee_id = holder
            record_event(db, new, "imported", p.user.id, employee_id=holder, to_status="assigned",
                         notes="عهدة قائمة من النسخة القديمة")
        report["assets"] += 1

    for ln in data.get("lines") or []:
        lid = str(ln.get("id"))
        if db.scalar(select(Sim).where(Sim.legacy_id == lid)):
            report["skipped_existing"] += 1
            continue
        country = ln.get("country") if ln.get("country") in ("EG", "SA") else None
        if country is None:
            report["unmapped"].append({"type": "sim", "legacy": ln.get("number"), "reason": "الدولة غير معروفة"})
            continue
        cid = company_of(ln.get("company"))
        holder = emp_map.get(str(ln.get("holderId"))) if ln.get("holderId") else None
        status = LINE_STATUS.get(ln.get("status"), "available")
        if status == "assigned" and not holder:
            status = "available"
        code = ln.get("code") or next_code(db, Sim, Sim.code, f"SIM-{country}-", 3)
        if db.scalar(select(Sim).where(Sim.code == code)):
            code = next_code(db, Sim, Sim.code, f"SIM-{country}-", 3)
        s = Sim(code=code, country=country, company_id=cid, number=str(ln.get("number") or ""),
                iccid=str(ln.get("iccid") or ""), operator=ln.get("carrier") or "", holder_employee_id=holder,
                allocation_status=status, name=ln.get("purpose") or ln.get("plan") or "", source="import",
                notes=(f"legacy renewDate {ln.get('renewDate')} (غير مؤكد — سجّل آخر دفع ومدة التجديد)"
                       if ln.get("renewDate") else ""), legacy_id=lid)
        db.add(s)
        db.flush()
        if holder:
            db.add(SimEvent(sim_id=s.id, kind="imported", employee_id=holder, detail="عهدة قائمة من النسخة القديمة",
                            actor_id=p.user.id))
        report["sims"] += 1

    for x in data.get("netPackages") or []:
        lid = str(x.get("id"))
        if db.scalar(select(Line).where(Line.legacy_id == lid)):
            report["skipped_existing"] += 1
            continue
        code = x.get("code") or next_code(db, Line, Line.code, "NET-", 3)
        if db.scalar(select(Line).where(Line.code == code)):
            code = next_code(db, Line, Line.code, "NET-", 3)
        db.add(Line(code=code, company_id=company_of(x.get("company")), name=x.get("name") or "",
                    provider=x.get("provider") or "", quota_gb=x.get("quota"),
                    notes=f"legacy renewalDate {x.get('renewalDate')} — أدخل قراءة حقيقية وآخر دفع" if
                    x.get("renewalDate") else "", legacy_id=lid))
        report["lines"] += 1

    def add_sub(x: dict, stype: str, prefix: str) -> None:
        lid = str(x.get("id"))
        if db.scalar(select(Subscription).where(Subscription.legacy_id == lid)):
            report["skipped_existing"] += 1
            return
        code = x.get("code") or next_code(db, Subscription, Subscription.code, prefix, 3)
        if db.scalar(select(Subscription).where(Subscription.code == code)):
            code = next_code(db, Subscription, Subscription.code, prefix, 3)
        quota = x.get("quota")
        db.add(Subscription(code=code, company_id=company_of(x.get("company")), service_type=stype,
                            provider=x.get("vendor") or x.get("registrar") or "", name=x.get("name") or code,
                            plan=x.get("plan") or "", account_owner=x.get("loginEmail") or x.get("account") or "",
                            status="cancelled" if x.get("status") == "cancelled" else "active",
                            usage_unit="Tokens" if quota else "", quota_total=quota,
                            notes=(f"legacy renewal {x.get('renewalDate') or x.get('expiry')} (غير مؤكد)"
                                   if (x.get("renewalDate") or x.get("expiry")) else ""), legacy_id=lid))
        report["subscriptions"] += 1

    for x in data.get("subscriptions") or []:
        add_sub(x, x.get("category") or "اشتراك", "LIC-")
    for x in data.get("tools") or []:
        add_sub(x, "AI", "TOOL-")
    for x in data.get("domains") or []:
        add_sub(x, {"domain": "Domain", "ssl": "SSL"}.get(x.get("kind"), "استضافة"), "DOM-")
    for v in data.get("vendors") or []:
        name = (v.get("name") or "").strip()
        if name and not db.scalar(select(Supplier).where(Supplier.name == name)):
            db.add(Supplier(name=name, contact=v.get("phone") or "", notes=v.get("notes") or "",
                            legacy_id=str(v.get("id"))))
            report["suppliers"] += 1

    if body.dry_run:
        sp.rollback()
        db.rollback()
        report["dry_run"] = True
        return report
    sp.commit()
    audit.record(db, "migration.legacy_import", actor_id=p.user.id, entity="migration", entity_id="it_assets_v1",
                 details={k: v for k, v in report.items() if isinstance(v, int)}, ip=client_ip(request))
    db.commit()
    report["dry_run"] = False
    return report
