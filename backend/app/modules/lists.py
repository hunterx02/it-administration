"""Editable dropdown lists ("القوائم والإعدادات"). Nothing is hardcoded: every list can be added to, renamed,
re-ordered or deactivated by the user. Deleting = deactivating, so older records keep their text."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..common import bad, conflict, forbid, not_found
from ..deps import Principal, client_ip, current_principal, get_db
from ..models import Lookup

# key -> (label, permission needed to edit)
LISTS: dict[str, tuple[str, str]] = {
    "sim_operator_sa": ("مشغلو الشرائح السعودية", "technical:edit"),
    "sim_operator_eg": ("مشغلو الشرائح المصرية", "technical:edit"),
    "sim_possession": ("أماكن حيازة الشرائح", "technical:edit"),
    "service_type": ("أنواع الخدمات والاشتراكات", "technical:edit"),
    "usage_unit": ("وحدات الرصيد", "technical:edit"),
    "internet_provider": ("مزودو الإنترنت", "technical:edit"),
    "mail_provider": ("مزودو الإيميل", "technical:edit"),
    "mail_plan": ("باقات الإيميل", "technical:edit"),
    "vendor_activity": ("أنشطة الموردين", "finance:edit"),
    "payment_method": ("طرق الدفع", "finance:edit"),
    "currency": ("العملات", "finance:edit"),
    "location": ("المواقع", "assets:edit"),
    "screen_type": ("أنواع الشاشات", "assets:edit"),
    "brand": ("الماركات", "assets:edit"),
    "job_title": ("المسميات الوظيفية", "employees:edit"),
    "offboarding_reason": ("أسباب إخلاء الطرف", "employees:edit"),
}
SEED = {"mail_provider": ["Google Workspace", "Microsoft 365"], "payment_method": ["نقدي", "تحويل بنكي", "بطاقة الشركة"], "currency": ["EGP", "SAR", "USD"],
        "offboarding_reason": ["استقالة", "نهاية خدمة", "إجازة"]}


def seed_lists(db: Session) -> None:
    for key, values in SEED.items():
        if db.scalar(select(Lookup).where(Lookup.list_key == key)) is None:
            for i, v in enumerate(values):
                db.add(Lookup(list_key=key, value=v, sort=i))


def _def(key: str) -> tuple[str, str]:
    if key not in LISTS:
        raise not_found("قائمة غير معروفة")
    return LISTS[key]


def _items(db: Session, key: str, include_inactive: bool) -> list[dict]:
    q = select(Lookup).where(Lookup.list_key == key).order_by(Lookup.sort, Lookup.value)
    if not include_inactive:
        q = q.where(Lookup.active.is_(True))
    return [{"id": x.id, "value": x.value, "sort": x.sort, "active": x.active} for x in db.scalars(q)]


class ValueIn(BaseModel):
    value: str = Field(min_length=1, max_length=120)


class ValuePatch(BaseModel):
    value: str | None = Field(default=None, min_length=1, max_length=120)
    active: bool | None = None
    sort: int | None = None


router = APIRouter(prefix="/api/lookups", tags=["lists"])


@router.get("")
def all_lists(include_inactive: bool = False, p: Principal = Depends(current_principal),
              db: Session = Depends(get_db)) -> list[dict]:
    return [{"key": k, "label": lbl, "can_edit": p.can(perm), "items": _items(db, k, include_inactive)}
            for k, (lbl, perm) in LISTS.items()]


@router.get("/{key}")
def one_list(key: str, include_inactive: bool = False, p: Principal = Depends(current_principal),
             db: Session = Depends(get_db)) -> dict:
    lbl, perm = _def(key)
    return {"key": key, "label": lbl, "can_edit": p.can(perm), "items": _items(db, key, include_inactive)}


@router.post("/{key}", status_code=201)
def add_value(key: str, body: ValueIn, request: Request, p: Principal = Depends(current_principal),
              db: Session = Depends(get_db)) -> dict:
    lbl, perm = _def(key)
    if not p.can(perm):
        raise forbid("ليس لديك صلاحية تعديل هذه القائمة")
    v = body.value.strip()
    ex = db.scalar(select(Lookup).where(Lookup.list_key == key, func.lower(Lookup.value) == v.lower()))
    if ex:
        if ex.active:
            raise conflict("القيمة موجودة بالفعل")
        ex.active = True  # re-activate instead of duplicating
        item = ex
    else:
        item = Lookup(list_key=key, value=v, sort=(db.scalar(select(func.max(Lookup.sort))
                                                            .where(Lookup.list_key == key)) or 0) + 1)
        db.add(item)
    db.flush()
    audit.record(db, "lookup.added", actor_id=p.user.id, entity="lookup", entity_id=key, details={"value": v},
                 ip=client_ip(request))
    db.commit()
    return {"id": item.id, "value": item.value, "active": item.active}


@router.patch("/{key}/{item_id}")
def edit_value(key: str, item_id: int, body: ValuePatch, request: Request, p: Principal = Depends(current_principal),
               db: Session = Depends(get_db)) -> dict:
    lbl, perm = _def(key)
    if not p.can(perm):
        raise forbid("ليس لديك صلاحية تعديل هذه القائمة")
    item = db.get(Lookup, item_id)
    if item is None or item.list_key != key:
        raise not_found("القيمة غير موجودة")
    changes = body.model_dump(exclude_none=True)
    if "value" in changes:
        v = changes["value"].strip()
        dup = db.scalar(select(Lookup).where(Lookup.list_key == key, func.lower(Lookup.value) == v.lower(),
                                             Lookup.id != item.id))
        if dup:
            raise conflict("فيه قيمة بنفس الاسم")
        old = item.value
        item.value = v
        changes["records_renamed"] = rename_in_records(db, key, old, v)
    if "active" in changes:
        item.active = changes["active"]
    if "sort" in changes:
        item.sort = changes["sort"]
    audit.record(db, "lookup.edited", actor_id=p.user.id, entity="lookup", entity_id=key,
                 details={"id": item_id, **changes}, ip=client_ip(request))
    db.commit()
    return {"id": item.id, "value": item.value, "active": item.active, "sort": item.sort,
            "records_renamed": changes.get("records_renamed", 0)}


def _targets() -> dict[str, list]:
    """Which record fields store each list's value (renaming a value renames it everywhere).
    Currencies are deliberately NOT cascaded: financial records keep their original currency code."""
    from .assets import Asset
    from .finance import Payment, Supplier
    from .journey import Offboarding
    from .mailboxes import Mailbox
    from .people import Employee
    from .technical import Line, Sim, Subscription
    return {"sim_operator_sa": [(Sim, Sim.operator, Sim.country == "SA")],
            "sim_operator_eg": [(Sim, Sim.operator, Sim.country == "EG")],
            "sim_possession": [(Sim, Sim.possession, None)],
            "service_type": [(Subscription, Subscription.service_type, None)],
            "usage_unit": [(Subscription, Subscription.usage_unit, None)],
            "internet_provider": [(Line, Line.provider, None)],
            "mail_provider": [(Mailbox, Mailbox.provider, None)],
            "mail_plan": [(Mailbox, Mailbox.plan, None)],
            "vendor_activity": [(Supplier, Supplier.activity, None)],
            "payment_method": [(Payment, Payment.method, None)],
            "location": [(Asset, Asset.location, None)],
            "screen_type": [(Asset, Asset.screen_info, None)],
            "brand": [(Asset, Asset.brand, None)],
            "job_title": [(Employee, Employee.job_title, None)],
            "offboarding_reason": [(Offboarding, Offboarding.reason, None)]}


def rename_in_records(db: Session, key: str, old: str, new: str) -> int:
    from sqlalchemy import update
    n = 0
    for model, col, extra in _targets().get(key, []):
        stmt = update(model).where(col == old)
        if extra is not None:
            stmt = stmt.where(extra)
        n += db.execute(stmt.values({col.key: new})).rowcount or 0
    return n


@router.delete("/{key}/{item_id}")
def remove_value(key: str, item_id: int, request: Request, p: Principal = Depends(current_principal),
                 db: Session = Depends(get_db)) -> dict:
    """Soft delete: hidden from new choices; existing records keep their stored text."""
    return edit_value(key, item_id, ValuePatch(active=False), request, p, db)


def require_known(key: str) -> None:
    if key not in LISTS:
        raise bad("قائمة غير معروفة")
