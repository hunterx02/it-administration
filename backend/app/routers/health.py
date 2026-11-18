"""Health and Integration Center status (M06).

A connector is NEVER reported as connected until a live verification has
actually succeeded. Without credentials the status is `pending_live_credentials`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .. import __version__, audit
from ..deps import Principal, client_ip, get_db, require
from ..models import IntegrationConfig, SchemaVersion

router = APIRouter(tags=["health"])

STATUS_AR = {
    "not_configured": "غير مُعد",
    "pending_live_credentials": "بانتظار صلاحيات الربط الفعلية",
    "disabled": "متوقف",
    "healthy": "متصل ومُتحقق منه",
    "stale": "بيانات قديمة",
    "error": "خطأ",
}


@router.get("/api/health")
@router.get("/health", include_in_schema=False)
def health(db: Session = Depends(get_db)) -> dict:
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
        schema = db.scalar(select(SchemaVersion.version).order_by(SchemaVersion.version.desc()))
    except Exception:  # noqa: BLE001
        db_ok, schema = False, None
    return {"status": "ok" if db_ok else "degraded", "version": __version__, "schema_version": schema,
            "database": "ok" if db_ok else "error", "time": datetime.now(timezone.utc).isoformat()}


def card(c: IntegrationConfig) -> dict:
    return {
        "key": c.key, "module": c.module, "title_ar": c.title_ar, "provider": c.provider, "mode": c.mode,
        "enabled": c.enabled, "credential_ref": c.credential_ref, "source_ref": c.source_ref,
        "status": c.status, "status_ar": STATUS_AR.get(c.status, c.status),
        "last_sync_at": c.last_sync_at.isoformat() if c.last_sync_at else None,
        "last_error": c.last_error, "version": c.version,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


@router.get("/api/integrations/status")
def integrations_status(_: Principal = Depends(require("system:view")), db: Session = Depends(get_db)) -> dict:
    cards = [card(c) for c in db.scalars(select(IntegrationConfig).order_by(IntegrationConfig.module, IntegrationConfig.key))]
    return {"connected_live": sum(1 for c in cards if c["status"] == "healthy"), "cards": cards}


class IntegrationUpdate(BaseModel):
    mode: Literal["manual", "sheets", "api", "script"] | None = None
    enabled: bool | None = None
    source_ref: str | None = Field(default=None, max_length=300)
    # A NAME of a secret held outside the DB, e.g. "google-sa-m02". Never the secret itself.
    credential_ref: str | None = Field(default=None, max_length=200, pattern=r"^[A-Za-z0-9_.\-]*$")


@router.put("/api/integrations/{key}")
def update_integration(key: str, body: IntegrationUpdate, request: Request,
                       p: Principal = Depends(require("system:manage_integration")),
                       db: Session = Depends(get_db)) -> dict:
    c = db.scalar(select(IntegrationConfig).where(IntegrationConfig.key == key))
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "موصل غير موجود")
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(c, k, v)
    # Any configuration change invalidates previous verification.
    if not c.enabled:
        c.status = "disabled" if c.source_ref else "not_configured"
    elif c.mode == "manual":
        c.status = "not_configured" if c.provider == "none" else "pending_live_credentials"
    else:
        c.status = "pending_live_credentials"
    c.version += 1
    c.updated_by = p.user.id
    audit.record(db, "integration.updated", actor_id=p.user.id, entity="integration", entity_id=key,
                 details={"changes": changes}, ip=client_ip(request))
    db.commit()
    return card(c)
