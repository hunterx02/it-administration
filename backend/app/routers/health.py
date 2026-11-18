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
from ..deps import Principal, client_ip, current_principal, get_db, require
from ..models import IntegrationConfig, SchemaVersion

router = APIRouter(tags=["health"])

STATUS_AR = {
    "not_configured": "غير مُعد",
    "pending_live_credentials": "بانتظار صلاحيات الربط الفعلية",
    "pending_first_sync": "بانتظار أول مزامنة",
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
        "enabled": c.enabled, "credential_ref": c.credential_ref, "source_ref": c.source_ref, "link_url": c.link_url,
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
    mode: Literal["manual", "sheets", "api", "script", "local_xlsx"] | None = None
    enabled: bool | None = None
    source_ref: str | None = Field(default=None, max_length=300)
    # A NAME of a secret held outside the DB, e.g. "google-sa-m02". Never the secret itself.
    credential_ref: str | None = Field(default=None, max_length=200, pattern=r"^[A-Za-z0-9_.\-]*$")
    # Human link opened by the "افتح الشيت" buttons. https only (Google Sheets/Drive/Forms or any https page).
    link_url: str | None = Field(default=None, max_length=500, pattern=r"^(https://\S+)?$")


# Which pages show which connector's link (frontend uses this map; access still needs the module's view perm)
LINK_PAGES = {"m02_assets_sheet": ("assets", "assets:view"), "m03_hr_sheet": ("employees", "employees:view"),
              "m03_application_form": ("employees", "employees:view"), "m04_sims_eg_sheet": ("technical", "technical:view"),
              "m04_sims_sa_sheet": ("technical", "technical:view"), "m04_services_sheet": ("technical", "technical:view"),
              "m05_finance_sheet": ("finance", "finance:view"), "m05_drive": ("finance", "finance:view"),
              "m03_drive": ("employees", "employees:view"), "m06_admin_sheet": ("system", "system:view")}


@router.get("/api/integrations/links")
def integration_links(p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> list[dict]:
    out = []
    for c in db.scalars(select(IntegrationConfig).where(IntegrationConfig.link_url != "")):
        page, perm = LINK_PAGES.get(c.key, ("system", "system:view"))
        if p.can(perm):
            out.append({"key": c.key, "module": page, "title_ar": c.title_ar, "url": c.link_url})
    return out


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
    # Any configuration change invalidates previous verification (changing only the display link does not).
    if not (set(changes) - {"link_url"}):
        pass
    elif not c.enabled:
        c.status = "disabled" if c.source_ref else "not_configured"
    elif c.mode == "local_xlsx":
        c.status = "pending_first_sync" if c.source_ref else "not_configured"
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
