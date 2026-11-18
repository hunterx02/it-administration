"""Core tables (M06 foundation). Every record uses a stable UUID string id.

Module tables (M02 assets, M03 journey, M04 services, M05 finance) are added in
their own phases and reference these ids (company_id, employee_id, ...).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    note: Mapped[str] = mapped_column(String(200), default="")


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    asset_prefix: Mapped[str] = mapped_column(String(20), default="")  # e.g. user-defined; never invented
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    branches: Mapped[list[Branch]] = relationship(back_populates="company", cascade="all, delete-orphan")
    departments: Mapped[list[Department]] = relationship(back_populates="company", cascade="all, delete-orphan")


class Branch(Base):
    __tablename__ = "branches"
    __table_args__ = (UniqueConstraint("company_id", "name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    company: Mapped[Company] = relationship(back_populates="branches")


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (UniqueConstraint("company_id", "name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    company: Mapped[Company] = relationship(back_populates="departments")


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(50), unique=True)
    name_ar: Mapped[str] = mapped_column(String(100))
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    permissions: Mapped[list[RolePermission]] = relationship(back_populates="role", cascade="all, delete-orphan")


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"))
    permission: Mapped[str] = mapped_column(String(80))  # "<module>:<action>"
    role: Mapped[Role] = relationship(back_populates="permissions")


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(300))
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    all_companies: Mapped[bool] = mapped_column(Boolean, default=False)  # scope: every company
    theme: Mapped[str] = mapped_column(String(10), default="system")  # light | dark | system
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    role: Mapped[Role] = relationship()
    scopes: Mapped[list[UserScope]] = relationship(back_populates="user", cascade="all, delete-orphan")


class UserScope(Base):
    """Data scope: a company, optionally narrowed to a branch and/or department."""
    __tablename__ = "user_scopes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    branch_id: Mapped[str | None] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), nullable=True)
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id", ondelete="CASCADE"), nullable=True)
    user: Mapped[User] = relationship(back_populates="scopes")


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 of the cookie token
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Lookup(Base):
    """Editable dropdown values (operators, possession places, vendor activities...)."""
    __tablename__ = "lookups"
    __table_args__ = (UniqueConstraint("list_key", "value"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    list_key: Mapped[str] = mapped_column(String(60), index=True)
    value: Mapped[str] = mapped_column(String(120))
    sort: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AuditEvent(Base):
    """Append-only. The API exposes no update/delete for this table."""
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity: Mapped[str] = mapped_column(String(80), default="")
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    company_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)  # never contains secrets
    ip: Mapped[str] = mapped_column(String(64), default="")


class IntegrationConfig(Base):
    """A connector card in the M06 Integration Center.

    `credential_ref` is a NAME pointing to a secret held outside the DB (OS vault /
    secret file); the secret value itself is never stored or returned here.
    """
    __tablename__ = "integration_configs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(String(60), unique=True)
    module: Mapped[str] = mapped_column(String(20))
    title_ar: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(60))
    mode: Mapped[str] = mapped_column(String(20), default="manual")  # manual | sheets | api | script
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    credential_ref: Mapped[str] = mapped_column(String(200), default="")
    source_ref: Mapped[str] = mapped_column(String(300), default="")  # spreadsheet/folder/form id
    status: Mapped[str] = mapped_column(String(40), default="not_configured")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
