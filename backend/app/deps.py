"""Request dependencies: DB session, authenticated user, permission checks."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuthSession, User
from .security import token_digest

SESSION_COOKIE = "itadm_session"


def get_db(request: Request) -> Iterator[Session]:
    yield from request.app.state.db.session()


@dataclass
class Principal:
    user: User
    permissions: frozenset[str]
    session_id: str

    def can(self, perm: str) -> bool:
        return perm in self.permissions

    def company_ids(self) -> set[str] | None:
        """None = all companies; otherwise the explicit set the user is scoped to."""
        if self.user.all_companies or self.user.role.key == "super_admin":
            return None
        return {s.company_id for s in self.user.scopes}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "غير مسجل الدخول")
    sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == token_digest(token)))
    now = datetime.now(timezone.utc)
    if sess is None or sess.revoked_at is not None or _aware(sess.expires_at) <= now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "الجلسة انتهت، سجّل الدخول تاني")
    user = db.get(User, sess.user_id)
    if user is None or not user.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "الحساب موقوف")
    perms = frozenset(rp.permission for rp in user.role.permissions)
    return Principal(user=user, permissions=perms, session_id=sess.id)


def require(*perms: str) -> Callable[..., Principal]:
    """Dependency factory: the caller must hold ALL listed permissions."""
    def _dep(p: Principal = Depends(current_principal)) -> Principal:
        missing = [x for x in perms if not p.can(x)]
        if missing:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "ليس لديك صلاحية: " + ", ".join(missing))
        return p
    return _dep


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""
