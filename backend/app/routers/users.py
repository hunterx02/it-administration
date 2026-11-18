"""Users, roles and permission catalog (M06)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..deps import Principal, client_ip, get_db, require
from ..models import AuthSession, Branch, Company, Department, Role, User, UserScope
from ..rbac import ACTIONS, MODULES
from ..security import hash_password, password_problems

router = APIRouter(prefix="/api", tags=["users"])


class ScopeIn(BaseModel):
    company_id: str
    branch_id: str | None = None
    department_id: str | None = None


class UserCreate(BaseModel):
    email: str = Field(max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    name: str = Field(min_length=2, max_length=200)
    password: str = Field(max_length=200)
    role_key: str
    all_companies: bool = False
    scopes: list[ScopeIn] = []


class UserPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    role_key: str | None = None
    active: bool | None = None
    all_companies: bool | None = None
    scopes: list[ScopeIn] | None = None


class PasswordReset(BaseModel):
    new_password: str = Field(max_length=200)


def _user_dict(u: User) -> dict:
    return {
        "id": u.id, "email": u.email, "name": u.name, "active": u.active,
        "role": {"key": u.role.key, "name_ar": u.role.name_ar}, "all_companies": u.all_companies,
        "scopes": [{"company_id": s.company_id, "branch_id": s.branch_id, "department_id": s.department_id}
                   for s in u.scopes],
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


def _role(db: Session, key: str) -> Role:
    role = db.scalar(select(Role).where(Role.key == key))
    if role is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"دور غير معروف: {key}")
    return role


def _scopes(db: Session, items: list[ScopeIn]) -> list[UserScope]:
    out = []
    for s in items:
        if db.get(Company, s.company_id) is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "شركة غير موجودة في النطاق")
        if s.branch_id:
            b = db.get(Branch, s.branch_id)
            if b is None or b.company_id != s.company_id:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "الفرع لا يتبع الشركة المحددة")
        if s.department_id:
            d = db.get(Department, s.department_id)
            if d is None or d.company_id != s.company_id:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "القسم لا يتبع الشركة المحددة")
        out.append(UserScope(company_id=s.company_id, branch_id=s.branch_id, department_id=s.department_id))
    return out


def _active_super_admins(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User).join(Role)
                     .where(Role.key == "super_admin", User.active.is_(True))) or 0


def _revoke_sessions(db: Session, user_id: str) -> None:
    now = datetime.now(timezone.utc)
    for s in db.scalars(select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))):
        s.revoked_at = now


@router.get("/permissions")
def permission_catalog(_: Principal = Depends(require("system:view"))) -> dict:
    return {"modules": MODULES, "actions": ACTIONS}


@router.get("/roles")
def list_roles(_: Principal = Depends(require("system:view")), db: Session = Depends(get_db)) -> list[dict]:
    return [{"key": r.key, "name_ar": r.name_ar, "is_system": r.is_system,
             "permissions": sorted(p.permission for p in r.permissions)}
            for r in db.scalars(select(Role).order_by(Role.name_ar))]


@router.get("/users")
def list_users(_: Principal = Depends(require("system:manage_users")), db: Session = Depends(get_db)) -> list[dict]:
    return [_user_dict(u) for u in db.scalars(select(User).order_by(User.name))]


@router.post("/users", status_code=201)
def create_user(body: UserCreate, request: Request, p: Principal = Depends(require("system:manage_users")),
                db: Session = Depends(get_db)) -> dict:
    email = body.email.strip().lower()
    if db.scalar(select(User).where(func.lower(User.email) == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "البريد مستخدم بالفعل")
    problems = password_problems(body.password)
    if problems:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, " · ".join(problems))
    role = _role(db, body.role_key)
    if role.key == "super_admin" and p.user.role.key != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "إنشاء Super Admin متاح لـ Super Admin فقط")
    u = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password), role_id=role.id,
             all_companies=body.all_companies, scopes=_scopes(db, body.scopes))
    db.add(u)
    db.flush()
    audit.record(db, "user.created", actor_id=p.user.id, entity="user", entity_id=u.id,
                 details={"email": email, "role": role.key}, ip=client_ip(request))
    db.commit()
    db.refresh(u)
    return _user_dict(u)


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: UserPatch, request: Request, p: Principal = Depends(require("system:manage_users")),
                db: Session = Depends(get_db)) -> dict:
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "مستخدم غير موجود")
    if u.role.key == "super_admin" and p.user.role.key != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "تعديل Super Admin متاح لـ Super Admin فقط")
    changes: dict = {}
    was_super = u.role.key == "super_admin" and u.active
    if body.name is not None:
        u.name = body.name.strip(); changes["name"] = u.name
    if body.role_key is not None and body.role_key != u.role.key:
        role = _role(db, body.role_key)
        if role.key == "super_admin" and p.user.role.key != "super_admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "منح Super Admin متاح لـ Super Admin فقط")
        u.role_id, u.role = role.id, role; changes["role"] = role.key
    if body.active is not None and body.active != u.active:
        if not body.active and u.id == p.user.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "لا يمكنك إيقاف حسابك")
        u.active = body.active; changes["active"] = u.active
        if not u.active:
            _revoke_sessions(db, u.id)
    if body.all_companies is not None:
        u.all_companies = body.all_companies; changes["all_companies"] = u.all_companies
    if body.scopes is not None:
        u.scopes = _scopes(db, body.scopes); changes["scopes"] = len(u.scopes)
    db.flush()
    if was_super and (u.role.key != "super_admin" or not u.active) and _active_super_admins(db) == 0:
        db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "لازم يفضل Super Admin واحد نشط على الأقل")
    if "role" in changes:
        _revoke_sessions(db, u.id)  # permissions changed -> force re-login
    audit.record(db, "user.updated", actor_id=p.user.id, entity="user", entity_id=u.id, details=changes,
                 ip=client_ip(request))
    db.commit()
    db.refresh(u)
    return _user_dict(u)


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: str, body: PasswordReset, request: Request,
                   p: Principal = Depends(require("system:manage_users")), db: Session = Depends(get_db)) -> dict:
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "مستخدم غير موجود")
    if u.role.key == "super_admin" and p.user.role.key != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "متاح لـ Super Admin فقط")
    problems = password_problems(body.new_password)
    if problems:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, " · ".join(problems))
    u.password_hash = hash_password(body.new_password)
    u.failed_logins, u.locked_until = 0, None
    _revoke_sessions(db, u.id)
    audit.record(db, "user.password_reset", actor_id=p.user.id, entity="user", entity_id=u.id, ip=client_ip(request))
    db.commit()
    return {"ok": True}
