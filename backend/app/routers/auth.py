"""Login / logout / current user / own preferences."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..deps import SESSION_COOKIE, Principal, client_ip, current_principal, get_db
from ..models import AuthSession, User
from ..security import hash_password, new_session_token, password_problems, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

MAX_FAILED = 5
LOCK_MINUTES = 15
_DUMMY_HASH = hash_password("timing-equaliser-not-a-real-password-1")


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


class ThemeIn(BaseModel):
    theme: Literal["light", "dark", "system"]


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


def user_out(p: Principal) -> dict:
    u = p.user
    return {
        "id": u.id, "email": u.email, "name": u.name, "theme": u.theme,
        "role": {"key": u.role.key, "name_ar": u.role.name_ar},
        "permissions": sorted(p.permissions),
        "all_companies": p.company_ids() is None,
        "scopes": [{"company_id": s.company_id, "branch_id": s.branch_id, "department_id": s.department_id}
                   for s in u.scopes],
    }


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    now = datetime.now(timezone.utc)
    email = body.email.strip().lower()
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    generic = HTTPException(status.HTTP_401_UNAUTHORIZED, "البريد الإلكتروني أو كلمة المرور غير صحيحة")
    if user is None:
        verify_password(body.password, _DUMMY_HASH)  # keep response time similar
        audit.record(db, "auth.login_failed", details={"email": email, "reason": "unknown_user"}, ip=client_ip(request))
        db.commit()
        raise generic
    if user.locked_until and _aware(user.locked_until) > now:
        raise HTTPException(status.HTTP_423_LOCKED, "الحساب مقفول مؤقتًا بسبب محاولات خاطئة، حاول بعد شوية")
    if not user.active:
        audit.record(db, "auth.login_failed", actor_id=user.id, details={"reason": "inactive"}, ip=client_ip(request))
        db.commit()
        raise generic
    if not verify_password(body.password, user.password_hash):
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED:
            user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            user.failed_logins = 0
        audit.record(db, "auth.login_failed", actor_id=user.id, details={"reason": "bad_password"}, ip=client_ip(request))
        db.commit()
        raise generic

    user.failed_logins, user.locked_until = 0, None
    token, digest = new_session_token()
    hours = request.app.state.settings.session_hours
    db.add(AuthSession(user_id=user.id, token_hash=digest, expires_at=now + timedelta(hours=hours)))
    audit.record(db, "auth.login", actor_id=user.id, entity="user", entity_id=user.id, ip=client_ip(request))
    db.commit()
    response.set_cookie(SESSION_COOKIE, token, max_age=hours * 3600, httponly=True, samesite="strict",
                        secure=request.app.state.settings.cookie_secure, path="/")
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response, p: Principal = Depends(current_principal),
           db: Session = Depends(get_db)) -> dict:
    sess = db.get(AuthSession, p.session_id)
    if sess:
        sess.revoked_at = datetime.now(timezone.utc)
    audit.record(db, "auth.logout", actor_id=p.user.id, ip=client_ip(request))
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(p: Principal = Depends(current_principal)) -> dict:
    return user_out(p)


@router.put("/me/theme")
def set_theme(body: ThemeIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    user = db.get(User, p.user.id)
    user.theme = body.theme
    db.commit()
    return {"theme": user.theme}


@router.post("/me/password")
def change_password(body: PasswordChangeIn, request: Request, p: Principal = Depends(current_principal),
                    db: Session = Depends(get_db)) -> dict:
    user = db.get(User, p.user.id)
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "كلمة المرور الحالية غير صحيحة")
    problems = password_problems(body.new_password)
    if problems:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, " · ".join(problems))
    user.password_hash = hash_password(body.new_password)
    # revoke every other session of this user
    now = datetime.now(timezone.utc)
    for s in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.id != p.session_id,
                                                  AuthSession.revoked_at.is_(None))):
        s.revoked_at = now
    audit.record(db, "auth.password_changed", actor_id=user.id, entity="user", entity_id=user.id, ip=client_ip(request))
    db.commit()
    return {"ok": True}
