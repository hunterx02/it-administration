"""Append-only audit helper. Callers must never pass secrets in `details`."""
from __future__ import annotations

from sqlalchemy.orm import Session

from .models import AuditEvent

_REDACT_KEYS = {"password", "password_hash", "token", "secret", "api_key", "credential", "national_id"}


def _clean(details: dict) -> dict:
    out = {}
    for k, v in details.items():
        if any(s in k.lower() for s in _REDACT_KEYS):
            out[k] = "[REDACTED]"
        elif isinstance(v, dict):
            out[k] = _clean(v)
        else:
            out[k] = v
    return out


def record(db: Session, action: str, *, actor_id: str | None = None, entity: str = "", entity_id: str = "",
           company_id: str | None = None, details: dict | None = None, ip: str = "") -> AuditEvent:
    ev = AuditEvent(action=action, actor_id=actor_id, entity=entity, entity_id=str(entity_id),
                    company_id=company_id, details=_clean(details or {}), ip=ip)
    db.add(ev)
    return ev
