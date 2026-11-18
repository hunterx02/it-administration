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
from ..security import (hash_password, new_session_token, password_problems, totp_new_secret, totp_uri, totp_verify,
                        verify_password)

router = APIRouter(prefix="/api/auth", tags=["auth"])

MAX_FAILED = 5
LOCK_MINUTES = 15
_DUMMY_HASH = hash_password("timing-equaliser-not-a-real-password-1")


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)
    otp: str | None = Field(default=None, max_length=12)  # required when the account has MFA enabled


class MfaCode(BaseModel):
    code: str = Field(max_length=12)
    password: str | None = Field(default=None, max_length=200)


class ThemeIn(BaseModel):
    theme: Literal["light", "dark", "system"]


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


def user_out(p: Principal) -> dict:
    u = p.user
    return {
        "id": u.id, "email": u.email, "name": u.name, "theme": u.theme,
        "role": {"key": u.role.key, "name_ar": u.role.name_ar}, "mfa_enabled": bool(u.mfa_enabled),
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

    if user.mfa_enabled:
        if not body.otp:
            db.rollback()
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "mfa_required")
        from ..secrets_store import decrypt
        secret = decrypt(request.app.state.settings.secret_key, user.mfa_secret_enc, "mfa:" + user.id)
        counter = totp_verify(secret, body.otp, user.mfa_last_counter or None)
        if counter is None:
            user.failed_logins += 1
            if user.failed_logins >= MAX_FAILED:
                user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
                user.failed_logins = 0
            audit.record(db, "auth.login_failed", actor_id=user.id, details={"reason": "bad_otp"}, ip=client_ip(request))
            db.commit()
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "كود التحقق غير صحيح أو مستخدم قبل كده")
        user.mfa_last_counter = counter
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


@router.post("/me/mfa/setup")
def mfa_setup(request: Request, p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    """Step 1: generate a TOTP secret (not active until confirmed with a valid code)."""
    from ..secrets_store import encrypt
    user = db.get(User, p.user.id)
    if user.mfa_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "التحقق بخطوتين مفعّل بالفعل")
    secret = totp_new_secret()
    user.mfa_pending_enc = encrypt(request.app.state.settings.secret_key, secret, "mfa-pending:" + user.id)
    db.commit()
    return {"secret": secret, "otpauth_uri": totp_uri(secret, user.email)}


@router.post("/me/mfa/enable")
def mfa_enable(body: MfaCode, request: Request, p: Principal = Depends(current_principal),
               db: Session = Depends(get_db)) -> dict:
    from ..secrets_store import decrypt, encrypt
    user = db.get(User, p.user.id)
    if not user.mfa_pending_enc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "ابدأ الإعداد الأول")
    key = request.app.state.settings.secret_key
    secret = decrypt(key, user.mfa_pending_enc, "mfa-pending:" + user.id)
    counter = totp_verify(secret, body.code)
    if counter is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "الكود غير صحيح — اتأكد إن ساعة الموبايل مظبوطة")
    user.mfa_secret_enc, user.mfa_pending_enc = encrypt(key, secret, "mfa:" + user.id), ""
    user.mfa_enabled, user.mfa_last_counter = True, counter
    audit.record(db, "auth.mfa_enabled", actor_id=user.id, entity="user", entity_id=user.id, ip=client_ip(request))
    db.commit()
    return {"mfa_enabled": True}


@router.post("/me/mfa/disable")
def mfa_disable(body: MfaCode, request: Request, p: Principal = Depends(current_principal),
                db: Session = Depends(get_db)) -> dict:
    from ..secrets_store import decrypt
    user = db.get(User, p.user.id)
    if not user.mfa_enabled:
        return {"mfa_enabled": False}
    if not body.password or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "كلمة المرور غير صحيحة")
    secret = decrypt(request.app.state.settings.secret_key, user.mfa_secret_enc, "mfa:" + user.id)
    if totp_verify(secret, body.code, user.mfa_last_counter or None) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "الكود غير صحيح")
    user.mfa_enabled, user.mfa_secret_enc, user.mfa_last_counter = False, "", 0
    audit.record(db, "auth.mfa_disabled", actor_id=user.id, entity="user", entity_id=user.id, ip=client_ip(request))
    db.commit()
    return {"mfa_enabled": False}


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
