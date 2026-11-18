"""Employee master record (owned by M03) and the custody-freeze rule shared by M02/M04.

Sensitive fields (national ID, salary, birth date) live in a separate table that
only `employees:view_sensitive` can read.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, Session, mapped_column

from ..common import bad, conflict
from ..db import Base
from ..models import new_id, utcnow

EMPLOYEE_STATUSES = ("onboarding", "active", "offboarding", "exited")


class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    employee_code: Mapped[str | None] = mapped_column(String(40), unique=True, nullable=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    branch_id: Mapped[str | None] = mapped_column(ForeignKey("branches.id"), nullable=True)
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    full_name: Mapped[str] = mapped_column(String(200))
    job_title: Mapped[str] = mapped_column(String(200), default="")
    project: Mapped[str] = mapped_column(String(200), default="")
    gender: Mapped[str] = mapped_column(String(10), default="")  # male | female | "" (HR reviews if empty)
    email: Mapped[str] = mapped_column(String(254), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    biometric_code: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(20), default="active")
    custody_frozen: Mapped[bool] = mapped_column(Boolean, default=False)  # set when offboarding opens; irreversible
    hire_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    exit_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    application_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    legacy_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class EmployeeSensitive(Base):
    __tablename__ = "employee_sensitive"
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True)
    national_id: Mapped[str] = mapped_column(String(40), default="")
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    salary: Mapped[str] = mapped_column(String(40), default="")
    address: Mapped[str] = mapped_column(Text, default="")


EMPLOYEE_PUBLIC_FIELDS = ["id", "employee_code", "company_id", "branch_id", "department_id", "full_name", "job_title",
                          "project", "email", "phone", "biometric_code", "status", "custody_frozen", "hire_date",
                          "exit_date", "version"]


def get_employee(db: Session, employee_id: str | None) -> Employee:
    e = db.get(Employee, employee_id) if employee_id else None
    if e is None:
        raise bad("الموظف غير موجود")
    return e


def assert_can_receive_custody(db: Session, employee_id: str) -> Employee:
    """M03/M02/M04 rule: once offboarding opens, NO new custody from any channel (UI, Sheets import, API)."""
    e = get_employee(db, employee_id)
    if e.custody_frozen or e.status in ("offboarding", "exited"):
        raise conflict("ممنوع تسليم عهدة جديدة: الموظف عليه إخلاء طرف مفتوح أو خرج من الشركة")
    return e
