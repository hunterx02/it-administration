"""Cross-module: alerts & notifications, M01 dashboard, report builder (CROSS addendum v1.0).

- Dashboard holds no data of its own: every number is derived from module tables, scoped by RBAC/company.
- Missing data shows as empty/incomplete, never as fake "healthy" values.
- Notifications are deduplicated per (alert key, state): an unchanged state never re-alerts.
- Reports filter on the query itself (RBAC + company scope), state their basis/date axis, and export
  CSV/XLSX with formula-injection protection.
"""
from __future__ import annotations

import csv
import io
import json
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Callable

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .. import audit
from ..common import bad, forbid, iso, not_found, now, safe_cell, scope_filter, today
from ..db import Base
from ..deps import Principal, client_ip, current_principal, get_db, require
from ..models import Department, IntegrationConfig, new_id, utcnow
from .assets import ALLOCATION, CONDITION, Asset, AssetCategory, CustodyEvent, Maintenance
from .finance import EXPENSE_KINDS, PR_STATUS, PurchaseRequest, pr_totals, spend_summary
from .journey import STAGES, Application, ITTask, Offboarding, Onboarding, current_custody
from .people import Employee
from .technical import (BackupConfirmation, Line, RenewalEvent, Sim, Subscription, UsageReading, backup_info,
                        due_info, usage_state)

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2}


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("alert_key", "state"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    alert_key: Mapped[str] = mapped_column(String(120), index=True)
    state: Mapped[str] = mapped_column(String(60))
    module: Mapped[str] = mapped_column(String(20))
    severity: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(300))
    link: Mapped[str] = mapped_column(String(200), default="")
    company_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class NotificationRead(Base):
    __tablename__ = "notification_reads"
    __table_args__ = (UniqueConstraint("notification_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    notification_id: Mapped[str] = mapped_column(ForeignKey("notifications.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(String(36), index=True)


MODULE_PERM = {"technical": "technical:view", "employees": "employees:view", "assets": "assets:view",
               "finance": "finance:view", "system": "system:view"}


# ---------------------------------------------------------------- alerts
def compute_alerts(db: Session, p: Principal | None) -> list[dict]:
    """p=None -> all companies (worker). Otherwise filtered by permission + company scope."""
    out: list[dict] = []

    def can(module: str) -> bool:
        return p is None or p.can(MODULE_PERM[module])

    def scoped(stmt, col):  # noqa: ANN001, ANN202
        return stmt if p is None else scope_filter(stmt, p, col)

    def add(key: str, state: str, module: str, sev: str, title: str, link: str, ref: str, company_id: str | None):
        out.append({"key": key, "state": state, "module": module, "severity": sev, "title": title, "link": link,
                    "ref": ref, "company_id": company_id})

    if can("technical"):
        for ln in db.scalars(scoped(select(Line), Line.company_id)):
            us = usage_state(ln.remaining_gb, ln.warn_gb, ln.critical_gb)
            label = ln.name or ln.landline_number or ln.code
            if us == "urgent":
                add(f"line_usage:{ln.id}", us, "technical", "critical",
                    f"باقة {label}: متبقي {ln.remaining_gb:g} جيجا — جدّد الباقة الآن", f"line/{ln.id}", ln.code,
                    ln.company_id)
            elif us == "prepare":
                add(f"line_usage:{ln.id}", us, "technical", "high",
                    f"باقة {label}: متبقي {ln.remaining_gb:g} جيجا — جهّز رصيد", f"line/{ln.id}", ln.code,
                    ln.company_id)
            for track, ar, info in (("net", "الإنترنت", due_info(ln.net_last_payment, ln.net_period_value,
                                                                  ln.net_period_unit, ln.net_alert_days)),
                                    ("land", "الأرضي", due_info(ln.land_last_payment, ln.land_period_value,
                                                                 ln.land_period_unit, ln.land_alert_days))):
                if info["due_state"] in ("overdue", "due_soon"):
                    add(f"line_due:{track}:{ln.id}", f"{info['due_state']}:{info['next_due']}", "technical",
                        "critical" if info["due_state"] == "overdue" else "high",
                        f"تجديد {ar} لـ {label} " + ("متأخر" if info["days_left"] < 0 else
                                                     f"خلال {info['days_left']} يوم"), f"line/{ln.id}", ln.code,
                        ln.company_id)
        for s in db.scalars(scoped(select(Sim), Sim.company_id).where(Sim.allocation_status != "cancelled")):
            info = due_info(s.last_payment, s.period_value, s.period_unit, s.alert_days)
            name = f"شريحة {'سعودية' if s.country == 'SA' else 'مصرية'} {s.code}"
            if info["due_state"] in ("overdue", "due_soon"):
                add(f"sim_due:{s.id}", f"{info['due_state']}:{info['next_due']}", "technical",
                    "critical" if info["due_state"] == "overdue" else "high",
                    f"{name} تحتاج تجديد " + ("(متأخرة)" if info["days_left"] < 0 else
                                             f"خلال {info['days_left']} يوم"), f"sim/{s.id}", s.code, s.company_id)
            b = backup_info(s)
            if b["backup_state"] == "overdue":
                add(f"sim_backup:{s.id}", f"overdue:{b['backup_due']}", "technical", "high",
                    f"{name}: نسخة WhatsApp الاحتياطية متأخرة", f"sim/{s.id}", s.code, s.company_id)
        for sub in db.scalars(scoped(select(Subscription), Subscription.company_id)
                              .where(Subscription.status == "active")):
            info = due_info(sub.last_payment, sub.period_value, sub.period_unit, sub.alert_days)
            if info["due_state"] in ("overdue", "due_soon"):
                add(f"sub_due:{sub.id}", f"{info['due_state']}:{info['next_due']}", "technical",
                    "critical" if info["due_state"] == "overdue" else "high",
                    f"اشتراك {sub.name} " + ("انتهى" if info["days_left"] < 0 else
                                             f"ينتهي خلال {info['days_left']} يوم"), f"subscription/{sub.id}",
                    sub.code, sub.company_id)
            if sub.usage_unit:
                us = usage_state(sub.remaining, sub.warn_level, sub.critical_level)
                if us in ("urgent", "prepare"):
                    add(f"sub_usage:{sub.id}", us, "technical", "critical" if us == "urgent" else "high",
                        f"رصيد {sub.name}: {sub.remaining:g} {sub.usage_unit} — "
                        + ("استنفاد عاجل" if us == "urgent" else "جهّز رصيد"), f"subscription/{sub.id}", sub.code,
                        sub.company_id)
    if can("employees"):
        for o in db.scalars(scoped(select(Offboarding), Offboarding.company_id).where(Offboarding.status != "closed")):
            e = db.get(Employee, o.employee_id)
            cust = current_custody(db, o.employee_id)
            n = len(cust["assets"]) + len(cust["sims"])
            left = (o.last_day - today()).days
            if n and left < 0:
                add(f"off_custody:{o.id}", f"late:{n}", "employees", "critical",
                    f"عهدة لم تُسترجع ({n}) من موظف مخلي طرف: {e.full_name}", f"offboarding/{o.id}", e.employee_code
                    or "", o.company_id)
            elif left <= 1:
                add(f"off_lastday:{o.id}", f"d{max(left, -1)}", "employees", "high",
                    f"آخر يوم لـ {e.full_name} " + ("النهارده" if left == 0 else "بكرة" if left == 1 else "عدّى"),
                    f"offboarding/{o.id}", e.employee_code or "", o.company_id)
        for ob in db.scalars(scoped(select(Onboarding), Onboarding.company_id).where(Onboarding.status == "open")):
            late = [t for t in db.scalars(select(ITTask).where(ITTask.onboarding_id == ob.id))
                    if t.status in ("pending", "done") and t.due_date and t.due_date < today()]
            if late:
                e = db.get(Employee, ob.employee_id)
                add(f"onb_late:{ob.id}", f"late:{len(late)}", "employees", "high",
                    f"مهام تعيين متأخرة ({len(late)}) لـ {e.full_name}", f"onboarding/{ob.id}", "", ob.company_id)
    if can("assets"):
        soon = today() + timedelta(days=30)
        for a in db.scalars(scoped(select(Asset), Asset.company_id).where(Asset.warranty_end.is_not(None),
                                                                            Asset.warranty_end >= today(),
                                                                            Asset.warranty_end <= soon)):
            add(f"warranty:{a.id}", f"end:{a.warranty_end}", "assets", "medium",
                f"ضمان {a.code} ينتهي {a.warranty_end}", f"asset/{a.id}", a.code, a.company_id)
    if p is None or p.can("system:view"):
        for c in db.scalars(select(IntegrationConfig).where(IntegrationConfig.enabled.is_(True),
                                                            IntegrationConfig.status.in_(("error", "stale")))):
            add(f"integration:{c.key}", c.status, "system", "medium", f"الربط «{c.title_ar}»: {c.status}",
                "integrations", c.key, None)
    out.sort(key=lambda x: SEV_ORDER[x["severity"]])
    return out


def dispatch_notifications(db: Session) -> int:
    """Called by the worker: persist NEW alert states only (no repeated alert for an unchanged state)."""
    created = 0
    for a in compute_alerts(db, None):
        if db.scalar(select(Notification).where(Notification.alert_key == a["key"],
                                                Notification.state == a["state"])) is None:
            db.add(Notification(alert_key=a["key"], state=a["state"], module=a["module"], severity=a["severity"],
                                title=a["title"], link=a["link"], company_id=a["company_id"]))
            created += 1
    db.commit()
    return created


router = APIRouter(prefix="/api", tags=["dashboard & reports"])


@router.get("/alerts")
def alerts(p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> list[dict]:
    return compute_alerts(db, p)


@router.get("/notifications")
def notifications(limit: int = Query(50, le=200), p: Principal = Depends(current_principal),
                  db: Session = Depends(get_db)) -> dict:
    allowed_modules = [m for m, perm in MODULE_PERM.items() if p.can(perm)]
    s = select(Notification).where(Notification.module.in_(allowed_modules)) \
        .order_by(Notification.created_at.desc()).limit(limit)
    comps = p.company_ids()
    if comps is not None:
        s = s.where(Notification.company_id.is_(None) | Notification.company_id.in_(comps or {""}))
    items = list(db.scalars(s))
    read = {r.notification_id for r in db.scalars(select(NotificationRead).where(
        NotificationRead.user_id == p.user.id, NotificationRead.notification_id.in_([n.id for n in items])))}
    return {"unread": sum(n.id not in read for n in items),
            "items": [{"id": n.id, "severity": n.severity, "title": n.title, "link": n.link, "module": n.module,
                       "created_at": n.created_at.isoformat(), "read": n.id in read} for n in items]}


@router.post("/notifications/read-all")
def read_all(p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    data = notifications(200, p, db)
    for it in data["items"]:
        if not it["read"]:
            db.add(NotificationRead(notification_id=it["id"], user_id=p.user.id))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- dashboard
CARD_GROUPS = {"desktops": ("الأجهزة المكتبية", {"CMP", "CMPM", "CMPH", "AIO", "MAC"}),
               "laptops": ("اللابتوبات", {"LTP"}), "mobiles": ("الموبايلات", {"MOB"})}


@router.get("/dashboard")
def dashboard(company_id: str | None = None, p: Principal = Depends(require("dashboard:view")),
              db: Session = Depends(get_db)) -> dict:
    from ..common import ensure_company
    if company_id:
        ensure_company(p, company_id)

    def sc(stmt, col):  # noqa: ANN001, ANN202
        stmt = scope_filter(stmt, p, col)
        return stmt.where(col == company_id) if company_id else stmt

    al = [a for a in compute_alerts(db, p) if not company_id or a["company_id"] in (None, company_id)]
    out: dict = {"generated_at": now().isoformat(),
                 "alerts": {"total": len(al), "top": al[:5],
                            "by_severity": dict(Counter(a["severity"] for a in al))}}
    if p.can("technical:view"):
        lines = list(db.scalars(sc(select(Line), Line.company_id).order_by(Line.is_primary.desc(), Line.code)))
        primary = [ln for ln in lines if ln.is_primary][:3]
        due7 = Counter()
        for kind, objs in (("sims", db.scalars(sc(select(Sim), Sim.company_id)
                                               .where(Sim.allocation_status != "cancelled"))),
                           ("subscriptions", db.scalars(sc(select(Subscription), Subscription.company_id)
                                                        .where(Subscription.status == "active")))):
            for o in objs:
                i = due_info(o.last_payment, o.period_value, o.period_unit, o.alert_days)
                if i["days_left"] is not None and i["days_left"] <= 7:
                    key = f"sims_{o.country}" if kind == "sims" else (o.service_type or "subscription")
                    due7[key] += 1
        for ln in lines:
            for tr in (due_info(ln.net_last_payment, ln.net_period_value, ln.net_period_unit, ln.net_alert_days),
                       due_info(ln.land_last_payment, ln.land_period_value, ln.land_period_unit, ln.land_alert_days)):
                if tr["days_left"] is not None and tr["days_left"] <= 7:
                    due7["internet_landline"] += 1
        spark = {}
        for ln in primary:
            rs = list(db.scalars(select(UsageReading).where(UsageReading.target_type == "line",
                                                            UsageReading.target_id == ln.id,
                                                            UsageReading.applied.is_(True))
                                 .order_by(UsageReading.read_at.desc()).limit(14)))
            spark[ln.id] = [{"at": r.read_at.isoformat(), "remaining": r.remaining} for r in reversed(rs)]
        out["renewals"] = {
            "primary_lines": [{"id": ln.id, "code": ln.code, "name": ln.name or ln.landline_number,
                               "quota_gb": ln.quota_gb, "remaining_gb": ln.remaining_gb,
                               "warn_gb": ln.warn_gb, "critical_gb": ln.critical_gb,
                               "usage_state": usage_state(ln.remaining_gb, ln.warn_gb, ln.critical_gb),
                               "last_reading_at": iso(ln.last_reading_at), "source": ln.last_reading_source,
                               "internet": due_info(ln.net_last_payment, ln.net_period_value, ln.net_period_unit,
                                                    ln.net_alert_days),
                               "landline": due_info(ln.land_last_payment, ln.land_period_value, ln.land_period_unit,
                                                    ln.land_alert_days), "spark": spark.get(ln.id, [])}
                              for ln in primary],
            "lines_total": len(lines), "due_within_7_days": dict(due7), "due_total_7": sum(due7.values())}
    if p.can("assets:view"):
        assets = list(db.scalars(sc(select(Asset), Asset.company_id).where(Asset.allocation_status != "retired")))
        cards = {}
        for key, (label, cats) in CARD_GROUPS.items():
            grp = [a for a in assets if a.category_code in cats]
            cards[key] = {"label": label, "total": len(grp), "categories": sorted(cats),
                          "allocation": dict(Counter(a.allocation_status for a in grp)),
                          "damaged": sum(a.tech_condition == "damaged" for a in grp)}
        others = [a for a in assets if not any(a.category_code in c for _, c in CARD_GROUPS.values())]
        sims_assigned = 0
        if p.can("technical:view"):
            sims_assigned = len(list(db.scalars(sc(select(Sim), Sim.company_id)
                                                .where(Sim.allocation_status == "assigned"))))
        cards["small"] = {"label": "الشرائح والعهد الصغيرة", "total": len(others), "sims_assigned": sims_assigned,
                          "allocation": dict(Counter(a.allocation_status for a in others)),
                          "damaged": sum(a.tech_condition == "damaged" for a in others)}
        deps = {d.id: d.name for d in db.scalars(select(Department))}
        by_dep = Counter(deps.get(a.department_id, "بدون قسم") for a in assets)
        out["assets"] = {"cards": cards, "by_department": dict(by_dep.most_common(12)), "allocation_ar": ALLOCATION,
                         "condition_unknown": sum(a.tech_condition == "unknown" for a in assets),
                         "in_maintenance": sum(a.allocation_status == "in_maintenance" for a in assets)}
    if p.can("employees:view"):
        obs = list(db.scalars(sc(select(Onboarding), Onboarding.company_id).where(Onboarding.status == "open")))
        tasks = [t for ob in obs for t in db.scalars(select(ITTask).where(ITTask.onboarding_id == ob.id))]
        late_t = [t for t in tasks if t.status in ("pending", "done") and t.due_date and t.due_date < today()]
        offs = list(db.scalars(sc(select(Offboarding), Offboarding.company_id).where(Offboarding.status != "closed")))
        blocked = [o for o in offs if any(current_custody(db, o.employee_id).values())]
        apps = Counter(a.stage for a in db.scalars(sc(select(Application), Application.company_id)))
        out["tasks"] = {"open": sum(t.status == "pending" for t in tasks), "late": len(late_t),
                        "awaiting_approval": sum(t.status == "done" for t in tasks),
                        "done_today": sum(t.status == "verified" and t.updated_at.date() == today() for t in tasks),
                        "top": [{"id": t.id, "kind": t.kind, "description": t.description, "status": t.status,
                                 "due_date": iso(t.due_date), "onboarding_id": t.onboarding_id}
                                for t in sorted(tasks, key=lambda t: (t.status != "pending", t.due_date or date.max))
                                if t.status in ("pending", "done")][:5],
                        "source_note": "مهام تجهيز IT في التعيين فقط (مفيش محرك مهام عام)"}
        out["journey"] = {"onboarding_open": len(obs),
                          "onboarding_late": len({t.onboarding_id for t in late_t}),
                          "offboarding_open": len(offs), "offboarding_blocked": len(blocked),
                          "applications": {STAGES[k]: v for k, v in apps.items()}}
    if p.can("finance:view"):
        start = today().replace(day=1)
        out["finance"] = {b: spend_summary(db, p, date_from=start, date_to=today(), basis=b,
                                           company_id=company_id)["totals"]
                          for b in ("committed", "invoiced", "paid")}
        out["finance"]["open_requests"] = len(list(db.scalars(sc(select(PurchaseRequest), PurchaseRequest.company_id)
                                                              .where(PurchaseRequest.status.in_(
                                                                  ("awaiting_approval", "approved", "in_procurement",
                                                                   "partially_received"))))))
    sec: dict = {}
    if p.can("technical:view"):
        sa = list(db.scalars(sc(select(Sim), Sim.company_id).where(Sim.country == "SA",
                                                                     Sim.allocation_status != "cancelled")))
        sec["backup_overdue"] = sum(backup_info(s)["backup_state"] == "overdue" for s in sa)
        sec["backup_unknown"] = sum(backup_info(s)["backup_state"] == "incomplete" for s in sa)
    if p.can("system:view"):
        cards = list(db.scalars(select(IntegrationConfig)))
        sec["integrations"] = dict(Counter(c.status for c in cards))
        from .automation import worker_status
        sec["worker"] = worker_status(db)
    out["security"] = sec
    return out


# ---------------------------------------------------------------- report builder
ReportFn = Callable[[Session, Principal, dict], dict]
REPORTS: dict[str, dict] = {}


def report(key: str, title: str, module_perm: str, filters: list[dict], date_axis: str):  # noqa: ANN201
    def deco(fn: ReportFn) -> ReportFn:
        REPORTS[key] = {"key": key, "title": title, "perm": module_perm, "filters": filters, "date_axis": date_axis,
                        "fn": fn}
        return fn
    return deco


def _d(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def _in_range(d: date | datetime | None, f: dict) -> bool:
    if d is None:
        return not (f.get("date_from") or f.get("date_to"))
    dd = d.date() if isinstance(d, datetime) else d
    return (not f.get("date_from") or dd >= _d(f["date_from"])) and (not f.get("date_to") or dd <= _d(f["date_to"]))


COMMON_FILTERS = [{"key": "company_id", "label": "الشركة", "type": "company"},
                  {"key": "date_from", "label": "من تاريخ", "type": "date"},
                  {"key": "date_to", "label": "إلى تاريخ", "type": "date"}]


def _company(stmt, p: Principal, col, f: dict):  # noqa: ANN001, ANN202
    stmt = scope_filter(stmt, p, col)
    return stmt.where(col == f["company_id"]) if f.get("company_id") else stmt


@report("assets.register", "الأجهزة حسب القسم والفئة والحالة", "assets:view",
        COMMON_FILTERS + [{"key": "department_id", "label": "القسم", "type": "department"},
                          {"key": "category", "label": "الفئة", "type": "asset_category"},
                          {"key": "allocation", "label": "حالة التخصيص", "type": "select", "options": ALLOCATION},
                          {"key": "condition", "label": "الحالة الفنية", "type": "select", "options": CONDITION}],
        "تاريخ الشراء")
def r_assets(db: Session, p: Principal, f: dict) -> dict:
    s = _company(select(Asset), p, Asset.company_id, f).order_by(Asset.code)
    for k, col in (("department_id", Asset.department_id), ("category", Asset.category_code),
                   ("allocation", Asset.allocation_status), ("condition", Asset.tech_condition)):
        if f.get(k):
            s = s.where(col == f[k])
    deps = {d.id: d.name for d in db.scalars(select(Department))}
    rows = []
    for a in db.scalars(s):
        if (f.get("date_from") or f.get("date_to")) and not _in_range(a.purchase_date, f):
            continue
        h = db.get(Employee, a.holder_employee_id) if a.holder_employee_id else None
        rows.append({"code": a.code, "category": a.category_code, "model": f"{a.brand} {a.model}".strip(),
                     "serial": a.serial, "department": deps.get(a.department_id, ""), "holder": h.full_name if h else "",
                     "allocation": ALLOCATION[a.allocation_status], "condition": CONDITION[a.tech_condition],
                     "purchase_date": iso(a.purchase_date)})
    cols = [("code", "الكود"), ("category", "الفئة"), ("model", "الجهاز"), ("serial", "السيريال"),
            ("department", "القسم"), ("holder", "مع"), ("allocation", "التخصيص"), ("condition", "الحالة الفنية"),
            ("purchase_date", "تاريخ الشراء")]
    return {"columns": cols, "rows": rows,
            "summary": {"الإجمالي": len(rows), **{f"تخصيص: {k}": v for k, v in Counter(r["allocation"]
                                                                                         for r in rows).items()}},
            "chart": {"type": "bar", "data": dict(Counter(r["department"] or "بدون قسم" for r in rows))}}


@report("assets.custody", "حركات العهد (تسليم / استرجاع / حجز)", "assets:view",
        COMMON_FILTERS + [{"key": "kind", "label": "نوع الحركة", "type": "select",
                           "options": {"handover": "تسليم", "return": "استرجاع", "reserve": "حجز",
                                       "maintenance_out": "خروج صيانة", "maintenance_in": "عودة صيانة"}}],
        "تاريخ الحركة")
def r_custody(db: Session, p: Principal, f: dict) -> dict:
    s = select(CustodyEvent, Asset).join(Asset, Asset.id == CustodyEvent.asset_id).order_by(CustodyEvent.at.desc())
    s = _company(s, p, Asset.company_id, f)
    if f.get("kind"):
        s = s.where(CustodyEvent.kind == f["kind"])
    rows = []
    for ev, a in db.execute(s):
        if not _in_range(ev.at, f):
            continue
        e = db.get(Employee, ev.employee_id) if ev.employee_id else None
        rows.append({"at": ev.at.strftime("%Y-%m-%d %H:%M"), "asset": a.code, "kind": ev.kind,
                     "employee": e.full_name if e else "", "result": ev.result, "notes": ev.notes})
    return {"columns": [("at", "الوقت"), ("asset", "الأصل"), ("kind", "الحركة"), ("employee", "الموظف"),
                        ("result", "النتيجة"), ("notes", "ملاحظات")], "rows": rows,
            "summary": dict(Counter(r["kind"] for r in rows)), "chart": {"type": "bar",
                                                                         "data": dict(Counter(r["kind"] for r in rows))}}


@report("assets.maintenance", "الصيانات", "assets:view", COMMON_FILTERS, "تاريخ فتح الصيانة")
def r_maint(db: Session, p: Principal, f: dict) -> dict:
    s = _company(select(Maintenance), p, Maintenance.company_id, f).order_by(Maintenance.opened_at.desc())
    rows = []
    for m in db.scalars(s):
        if not _in_range(m.opened_at, f):
            continue
        a = db.get(Asset, m.asset_id)
        rows.append({"asset": a.code, "issue": m.issue, "vendor": m.vendor or "داخلي", "opened": iso(m.opened_at),
                     "closed": iso(m.closed_at) or "", "status": m.status,
                     "cost_linked": "نعم" if m.financial_event_id else "لا"})
    return {"columns": [("asset", "الأصل"), ("issue", "العطل"), ("vendor", "الجهة"), ("opened", "فتح"),
                        ("closed", "قفل"), ("status", "الحالة"), ("cost_linked", "تكلفة مرتبطة")], "rows": rows,
            "summary": {"الإجمالي": len(rows), "مفتوحة": sum(r["status"] == "open" for r in rows)}}


@report("journey.hires", "المعينون خلال الفترة", "employees:view",
        COMMON_FILTERS + [{"key": "department_id", "label": "القسم", "type": "department"}], "تاريخ التعيين")
def r_hires(db: Session, p: Principal, f: dict) -> dict:
    """No national ID, salary or HR evaluation — whatever the caller's role (CROSS §E)."""
    s = _company(select(Employee), p, Employee.company_id, f).order_by(Employee.hire_date)
    if f.get("department_id"):
        s = s.where(Employee.department_id == f["department_id"])
    deps = {d.id: d.name for d in db.scalars(select(Department))}
    rows = []
    for e in db.scalars(s):
        if e.hire_date is None or not _in_range(e.hire_date, f):
            continue
        ob = db.scalar(select(Onboarding).where(Onboarding.employee_id == e.id))
        rows.append({"name": e.full_name, "code": e.employee_code or "", "job_title": e.job_title,
                     "department": deps.get(e.department_id, ""), "hire_date": iso(e.hire_date), "status": e.status,
                     "onboarding": ob.status if ob else ""})
    return {"columns": [("name", "الاسم"), ("code", "الكود"), ("job_title", "الوظيفة"), ("department", "القسم"),
                        ("hire_date", "تاريخ التعيين"), ("status", "الحالة"), ("onboarding", "التعيين")],
            "rows": rows, "summary": {"عدد المعينين": len(rows)},
            "chart": {"type": "bar", "data": dict(Counter(r["department"] or "—" for r in rows))}}


@report("journey.applications", "التقديم والمقابلات حسب المرحلة", "employees:view", COMMON_FILTERS, "تاريخ التقديم")
def r_apps(db: Session, p: Principal, f: dict) -> dict:
    s = _company(select(Application), p, Application.company_id, f)
    rows = [{"candidate_id": a.candidate_id, "position": a.position, "stage": STAGES[a.stage],
             "date": iso(a.application_date)} for a in db.scalars(s) if _in_range(a.application_date, f)]
    return {"columns": [("candidate_id", "كود المرشح"), ("position", "الوظيفة"), ("stage", "المرحلة"),
                        ("date", "تاريخ التقديم")], "rows": rows,
            "summary": dict(Counter(r["stage"] for r in rows)),
            "chart": {"type": "doughnut", "data": dict(Counter(r["stage"] for r in rows))}}


@report("journey.offboarding", "إخلاءات الفترة", "employees:view", COMMON_FILTERS, "آخر يوم عمل")
def r_off(db: Session, p: Principal, f: dict) -> dict:
    s = _company(select(Offboarding), p, Offboarding.company_id, f)
    rows = []
    for o in db.scalars(s):
        if not _in_range(o.last_day, f):
            continue
        e = db.get(Employee, o.employee_id)
        rows.append({"name": e.full_name, "last_day": iso(o.last_day), "status": o.status,
                     "it_approved": "نعم" if o.it_approved_at else "لا",
                     "signed_scan": "نعم" if o.signed_scan_file_id else "لا"})
    return {"columns": [("name", "الموظف"), ("last_day", "آخر يوم"), ("status", "الحالة"),
                        ("it_approved", "اعتماد IT"), ("signed_scan", "النسخة الموقعة")], "rows": rows,
            "summary": dict(Counter(r["status"] for r in rows))}


@report("technical.renewals", "عدد مرات التجديد لكل خدمة", "technical:view",
        COMMON_FILTERS + [{"key": "target_type", "label": "النوع", "type": "select",
                           "options": {"sim": "شريحة", "line_internet": "باقة إنترنت", "line_landline": "أرضي",
                                       "subscription": "اشتراك"}}], "تاريخ التجديد")
def r_renewals(db: Session, p: Principal, f: dict) -> dict:
    s = _company(select(RenewalEvent), p, RenewalEvent.company_id, f).order_by(RenewalEvent.renewed_on.desc())
    if f.get("target_type"):
        s = s.where(RenewalEvent.target_type == f["target_type"])
    rows = []
    for r in db.scalars(s):
        if not _in_range(r.renewed_on, f):
            continue
        model = Sim if r.target_type == "sim" else Line if r.target_type.startswith("line") else Subscription
        t = db.get(model, r.target_id)
        rows.append({"target": t.code if t else r.target_id, "type": r.target_type, "renewed_on": iso(r.renewed_on),
                     "next_due": iso(r.next_due) or "", "cost_linked": "نعم" if r.financial_event_id else "لا"})
    return {"columns": [("target", "الخدمة"), ("type", "النوع"), ("renewed_on", "تاريخ التجديد"),
                        ("next_due", "الاستحقاق التالي"), ("cost_linked", "تكلفة مرتبطة")], "rows": rows,
            "summary": {"عدد مرات التجديد": len(rows), **dict(Counter(r["target"] for r in rows))},
            "chart": {"type": "bar", "data": dict(Counter(r["target"] for r in rows))}}


@report("technical.usage", "رصيد الباقات والتوكنز عبر الفترة", "technical:view",
        COMMON_FILTERS + [{"key": "target_id", "label": "الخط / الاشتراك", "type": "usage_target"}], "تاريخ القراءة")
def r_usage(db: Session, p: Principal, f: dict) -> dict:
    s = select(UsageReading).order_by(UsageReading.read_at)
    if f.get("target_id"):
        s = s.where(UsageReading.target_id == f["target_id"])
    rows = []
    allowed = p.company_ids()
    for r in db.scalars(s):
        t = db.get(Line if r.target_type == "line" else Subscription, r.target_id)
        if t is None or (allowed is not None and t.company_id not in allowed) or not _in_range(r.read_at, f):
            continue
        if f.get("company_id") and t.company_id != f["company_id"]:
            continue
        rows.append({"target": t.code, "read_at": r.read_at.strftime("%Y-%m-%d %H:%M"), "remaining": r.remaining,
                     "unit": r.unit, "source": r.source, "applied": "نعم" if r.applied else "لا (أقدم)"})
    return {"columns": [("target", "الخدمة"), ("read_at", "وقت القراءة"), ("remaining", "المتبقي"),
                        ("unit", "الوحدة"), ("source", "المصدر"), ("applied", "مطبقة")], "rows": rows,
            "summary": {"عدد القراءات": len(rows)},
            "chart": {"type": "line", "data": {r["read_at"]: r["remaining"] for r in rows if r["applied"] == "نعم"}}}


@report("technical.backups", "تأكيدات النسخ الاحتياطي", "technical:view", COMMON_FILTERS, "تاريخ النسخة")
def r_backups(db: Session, p: Principal, f: dict) -> dict:
    rows = []
    for b in db.scalars(select(BackupConfirmation).order_by(BackupConfirmation.performed_on.desc())):
        s = db.get(Sim, b.sim_id)
        allowed = p.company_ids()
        if (allowed is not None and s.company_id not in allowed) or not _in_range(b.performed_on, f):
            continue
        rows.append({"sim": s.code, "performed_on": iso(b.performed_on), "method": b.method, "email": b.backup_email})
    return {"columns": [("sim", "الشريحة"), ("performed_on", "تاريخ النسخة"), ("method", "طريقة التحقق"),
                        ("email", "بريد النسخ")], "rows": rows, "summary": {"عدد التأكيدات": len(rows)}}


@report("finance.spend", "مصروف IT (مدفوع / مفوتر / متوقع)", "finance:view",
        COMMON_FILTERS + [{"key": "basis", "label": "أساس التقرير", "type": "select",
                           "options": {"paid": "مدفوع (تاريخ الدفع)", "invoiced": "مفوتر (تاريخ الفاتورة)",
                                       "committed": "متوقع/ملتزم (تاريخ الواقعة)"}},
                          {"key": "department_id", "label": "القسم", "type": "department"}], "حسب الأساس")
def r_spend(db: Session, p: Principal, f: dict) -> dict:
    df, dt = _d(f.get("date_from")) or today().replace(day=1), _d(f.get("date_to")) or today()
    sm = spend_summary(db, p, date_from=df, date_to=dt, basis=f.get("basis") or "paid",
                       company_id=f.get("company_id"), department_id=f.get("department_id"))
    deps = {d.id: d.name for d in db.scalars(select(Department))}
    by_dep = {deps.get(k, "على مستوى الشركة" if k == "company_wide" else k): v for k, v in sm["by_department"].items()}
    return {"columns": [("event", "العملية"), ("kind", "النوع"), ("description", "الوصف"), ("occurred_on", "التاريخ"),
                        ("currency", "العملة"), ("amount", "المبلغ")], "rows": sm["rows"],
            "summary": {f"الإجمالي ({c})": v for c, v in sm["totals"].items()} | {"الأساس": sm["basis"]},
            "breakdown": {"حسب القسم": by_dep, "حسب النوع": {EXPENSE_KINDS.get(k, k): v
                                                             for k, v in sm["by_kind"].items()}},
            "chart": {"type": "bar", "data": {k: sum(float(x) for x in v.values()) for k, v in by_dep.items()}}}


@report("finance.purchases", "مسار طلبات الشراء", "finance:view",
        COMMON_FILTERS + [{"key": "status", "label": "الحالة", "type": "select", "options": PR_STATUS}], "تاريخ الطلب")
def r_purchases(db: Session, p: Principal, f: dict) -> dict:
    s = _company(select(PurchaseRequest), p, PurchaseRequest.company_id, f).order_by(PurchaseRequest.code)
    if f.get("status"):
        s = s.where(PurchaseRequest.status == f["status"])
    rows = []
    for pr in db.scalars(s):
        if not _in_range(pr.request_date, f):
            continue
        t = pr_totals(db, pr)
        rows.append({"code": pr.code, "date": iso(pr.request_date), "status": PR_STATUS[pr.status],
                     "estimated": str(t["estimated"]), "actual": str(t["actual_received"]), "currency": pr.currency,
                     "approval_evidence": "مرفوعة" if pr.approval_file_id else ("ناقصة" if pr.approval_date else "—")})
    return {"columns": [("code", "الطلب"), ("date", "التاريخ"), ("status", "الحالة"), ("estimated", "تقديري"),
                        ("actual", "فعلي مستلم"), ("currency", "العملة"), ("approval_evidence", "إثبات الاعتماد")],
            "rows": rows, "summary": dict(Counter(r["status"] for r in rows))}


def run_report(db: Session, p: Principal, key: str, filters: dict) -> dict:
    d = REPORTS.get(key)
    if d is None:
        raise not_found("تقرير غير معروف")
    if not (p.can("reports:view") and p.can(d["perm"])):
        raise forbid("ليس لديك صلاحية هذا التقرير")
    from ..common import ensure_company
    if filters.get("company_id"):
        ensure_company(p, filters["company_id"])
    res = d["fn"](db, p, filters)
    res["columns"] = [{"key": k, "label": lbl} for k, lbl in res["columns"]]
    return {"key": key, "title": d["title"], "date_axis": d["date_axis"], "filters": filters,
            "generated_at": now().isoformat(), "generated_by": p.user.name, **res}


@router.get("/reports")
def list_reports(module: str | None = None, p: Principal = Depends(require("reports:view"))) -> list[dict]:
    return [{k: v for k, v in d.items() if k != "fn"} for d in REPORTS.values()
            if p.can(d["perm"]) and (not module or d["key"].startswith(module + "."))]


@router.post("/reports/{key}/run")
def run(key: str, filters: dict, p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    return run_report(db, p, key, {k: v for k, v in filters.items() if v not in (None, "")})


@router.get("/reports/{key}/export")
def export(key: str, request: Request, fmt: str = Query("csv", pattern="^(csv|xlsx)$"), filters: str = "{}",
           p: Principal = Depends(require("reports:export")), db: Session = Depends(get_db)) -> Response:
    try:
        f = json.loads(filters)
    except json.JSONDecodeError:
        raise bad("فلاتر غير صالحة") from None
    res = run_report(db, p, key, {k: v for k, v in f.items() if v not in (None, "")})
    cols = res["columns"]
    audit.record(db, "report.exported", actor_id=p.user.id, entity="report", entity_id=key,
                 details={"format": fmt, "rows": len(res["rows"]), "filters": f}, ip=client_ip(request))
    db.commit()
    stamp = now().strftime("%Y%m%d-%H%M")
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([res["title"]])
        w.writerow([f"أُنشئ {res['generated_at']} بواسطة {res['generated_by']} — محور التاريخ: {res['date_axis']}"])
        w.writerow([c["label"] for c in cols])
        for r in res["rows"]:
            w.writerow([safe_cell(r.get(c["key"], "")) for c in cols])
        return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{key}-{stamp}.csv"'})
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Report"
    ws.sheet_view.rightToLeft = True
    ws.append([res["title"]])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([f"أُنشئ {res['generated_at']} بواسطة {res['generated_by']} — محور التاريخ: {res['date_axis']}"])
    ws.append([c["label"] for c in cols])
    for cell in ws[3]:
        cell.font = Font(bold=True)
    for r in res["rows"]:
        ws.append([safe_cell(r.get(c["key"], "")) for c in cols])
    for i, c in enumerate(cols, 1):
        ws.column_dimensions[ws.cell(3, i).column_letter].width = max(12, len(c["label"]) + 6)
    buf = io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{key}-{stamp}.xlsx"'})


@router.get("/reports/{key}/print", response_class=HTMLResponse)
def print_report(key: str, request: Request, filters: str = "{}", p: Principal = Depends(require("reports:print")),
                 db: Session = Depends(get_db)) -> HTMLResponse:
    import html as h
    res = run_report(db, p, key, {k: v for k, v in json.loads(filters).items() if v not in (None, "")})
    cols = res["columns"]
    head = "".join(f"<th>{h.escape(c['label'])}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{h.escape(str(r.get(c['key'], '') or ''))}</td>" for c in cols) + "</tr>"
                   for r in res["rows"]) or f"<tr><td colspan='{len(cols)}'>لا توجد بيانات للفلاتر المختارة</td></tr>"
    summ = "".join(f"<li>{h.escape(str(k))}: <b>{h.escape(str(v))}</b></li>" for k, v in res.get("summary", {}).items())
    flt = "، ".join(f"{h.escape(k)}={h.escape(str(v))}" for k, v in res["filters"].items()) or "بدون فلاتر"
    audit.record(db, "report.printed", actor_id=p.user.id, entity="report", entity_id=key, ip=client_ip(request))
    db.commit()
    return HTMLResponse(f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>{h.escape(res['title'])}</title>
<style>@page{{size:A4 landscape;margin:12mm}}body{{font-family:Cairo,Tahoma,sans-serif;color:#000}}table{{width:100%;border-collapse:collapse}}
td,th{{border:1px solid #888;padding:5px;font-size:12px}}th{{background:#eee}}@media print{{button{{display:none}}}}</style></head>
<body><button onclick="print()">طباعة / حفظ PDF</button><h2>{h.escape(res['title'])}</h2>
<p>الفلاتر: {flt} — محور التاريخ: {h.escape(res['date_axis'])}<br>أُنشئ: {res['generated_at'][:16]} بواسطة {h.escape(res['generated_by'])}</p>
<ul>{summ}</ul><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></body></html>""")
