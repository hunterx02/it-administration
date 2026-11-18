"""Read-only audit log. There is intentionally no write/update/delete endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import Principal, get_db, require
from ..models import AuditEvent

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/verify")
def verify(_: Principal = Depends(require("system:view")), db: Session = Depends(get_db)) -> dict:
    from ..audit import verify_chain
    return verify_chain(db)


@router.get("")
def list_audit(action: str | None = None, entity: str | None = None, actor_id: str | None = None,
               limit: int = Query(default=100, ge=1, le=1000),
               p: Principal = Depends(require("system:view")), db: Session = Depends(get_db)) -> list[dict]:
    q = select(AuditEvent).order_by(AuditEvent.seq.desc(), AuditEvent.at.desc()).limit(limit)
    if action:
        q = q.where(AuditEvent.action == action)
    if entity:
        q = q.where(AuditEvent.entity == entity)
    if actor_id:
        q = q.where(AuditEvent.actor_id == actor_id)
    allowed = p.company_ids()
    if allowed is not None:
        q = q.where((AuditEvent.company_id.is_(None)) | (AuditEvent.company_id.in_(allowed or {""})))
    return [{"id": e.id, "at": e.at.isoformat(), "actor_id": e.actor_id, "action": e.action, "entity": e.entity,
             "entity_id": e.entity_id, "company_id": e.company_id, "details": e.details, "ip": e.ip}
            for e in db.scalars(q)]
