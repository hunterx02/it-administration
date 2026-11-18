"""Append-only, hash-chained audit log. Callers must never pass secrets in `details` (they are redacted anyway).

Each event stores seq, prev_hash and hash = sha256(prev_hash + canonical JSON of the event). Editing or deleting
any stored row breaks the chain, which `verify_chain` reports with the first bad sequence number.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditEvent, utcnow

_REDACT_KEYS = {"password", "password_hash", "token", "secret", "api_key", "credential", "national_id", "otp"}


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


def _ts(at: datetime) -> str:
    return at.replace(tzinfo=None).isoformat(timespec="microseconds")


def canonical(ev: AuditEvent) -> str:
    return json.dumps({"seq": ev.seq, "at": _ts(ev.at), "actor_id": ev.actor_id, "action": ev.action,
                       "entity": ev.entity, "entity_id": ev.entity_id, "company_id": ev.company_id,
                       "details": ev.details, "ip": ev.ip}, sort_keys=True, ensure_ascii=False, default=str)


def _digest(prev_hash: str, ev: AuditEvent) -> str:
    return hashlib.sha256((prev_hash + "|" + canonical(ev)).encode("utf-8")).hexdigest()


def _tail(db: Session) -> tuple[int, str]:
    pending = [o for o in db.new if isinstance(o, AuditEvent) and o.seq is not None]
    if pending:
        last = max(pending, key=lambda o: o.seq)
        return last.seq, last.hash
    last = db.scalar(select(AuditEvent).where(AuditEvent.seq.is_not(None)).order_by(AuditEvent.seq.desc()).limit(1))
    return (last.seq, last.hash) if last else (0, "")


def record(db: Session, action: str, *, actor_id: str | None = None, entity: str = "", entity_id: str = "",
           company_id: str | None = None, details: dict | None = None, ip: str = "") -> AuditEvent:
    seq, prev = _tail(db)
    ev = AuditEvent(action=action, actor_id=actor_id, entity=entity, entity_id=str(entity_id), company_id=company_id,
                    details=json.loads(json.dumps(_clean(details or {}), ensure_ascii=False, default=str)), ip=ip,
                    at=utcnow(), seq=seq + 1, prev_hash=prev)
    ev.hash = _digest(prev, ev)
    db.add(ev)
    return ev


def verify_chain(db: Session) -> dict:
    prev, count, expected_seq = "", 0, None
    for ev in db.scalars(select(AuditEvent).where(AuditEvent.seq.is_not(None)).order_by(AuditEvent.seq)):
        if expected_seq is not None and ev.seq != expected_seq:
            return {"ok": False, "checked": count, "broken_at": expected_seq, "reason": "حدث ناقص أو ممسوح من السجل"}
        if ev.prev_hash != prev:
            return {"ok": False, "checked": count, "broken_at": ev.seq, "reason": "الربط بالحدث السابق مكسور"}
        if _digest(prev, ev) != ev.hash:
            return {"ok": False, "checked": count, "broken_at": ev.seq, "reason": "محتوى الحدث اتعدّل بعد تسجيله"}
        prev, count, expected_seq = ev.hash, count + 1, ev.seq + 1
    return {"ok": True, "checked": count, "last_hash": prev}
