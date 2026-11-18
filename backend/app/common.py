"""Shared helpers for module routers: scope checks, versioned updates, dates, codes."""
from __future__ import annotations

import calendar
from datetime import date, datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from .deps import Principal
from .models import Company


def now() -> datetime:
    return datetime.now(timezone.utc)


def today() -> date:
    return datetime.now().date()


def forbid(msg: str) -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, msg)


def bad(msg: str) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, msg)


def not_found(msg: str = "السجل غير موجود") -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, msg)


def conflict(msg: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, msg)


def ensure_company(p: Principal, company_id: str | None) -> None:
    """Raise 403 when the record's company is outside the user's data scope."""
    allowed = p.company_ids()
    if allowed is None:
        return
    if company_id is None or company_id not in allowed:
        raise forbid("السجل خارج نطاق الشركات المسموح لك بها")


def scope_filter(q: Select, p: Principal, column) -> Select:  # noqa: ANN001
    allowed = p.company_ids()
    if allowed is None:
        return q
    return q.where(column.in_(allowed or {""}))


def get_company(db: Session, company_id: str) -> Company:
    c = db.get(Company, company_id)
    if c is None:
        raise bad("الشركة غير موجودة")
    return c


def check_version(obj: Any, version: int | None) -> None:
    """Optimistic concurrency: a stale edit never silently overwrites a newer one."""
    if version is None:
        raise bad("رقم الإصدار (version) مطلوب للتعديل")
    if obj.version != version:
        raise conflict(f"السجل اتعدّل من مكان تاني (الإصدار الحالي {obj.version}). حدّث الصفحة وراجع التغييرات.")


def apply_changes(obj: Any, changes: dict, allowed: set[str]) -> dict:
    applied = {}
    for k, v in changes.items():
        if k in allowed and getattr(obj, k) != v:
            setattr(obj, k, v)
            applied[k] = v if not isinstance(v, (date, datetime)) else v.isoformat()
    if applied:
        obj.version += 1
    return applied


def add_period(start: date | None, value: int | None, unit: str | None) -> date | None:
    """Calendar-correct period addition (months are calendar months, not x30 days).
    Returns None when any input is missing — no invented defaults (M04 §0.1, §7)."""
    if start is None or not value or unit not in ("days", "months"):
        return None
    if unit == "days":
        from datetime import timedelta
        return start + timedelta(days=int(value))
    month_index = start.month - 1 + int(value)
    year, month = start.year + month_index // 12, month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def days_until(d: date | None) -> int | None:
    return None if d is None else (d - today()).days


def next_code(db: Session, model, column, prefix: str, width: int = 4) -> str:  # noqa: ANN001
    """Sequential human code like PR-2026-0001 (unique column backs it up)."""
    like = f"{prefix}%"
    last = db.scalar(select(func.max(column)).where(column.like(like)))
    n = 1
    if last:
        try:
            n = int(str(last)[len(prefix):]) + 1
        except ValueError:
            n = db.scalar(select(func.count()).select_from(model).where(column.like(like))) + 1
    return f"{prefix}{n:0{width}d}"


FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> Any:
    """Neutralise spreadsheet formula injection for exports/Sheets writes."""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, (date, datetime)) else v


def row(obj: Any, fields: list[str]) -> dict:
    return {f: iso(getattr(obj, f)) for f in fields}
