"""Companies, branches, departments (data-scope lookups, M06).

Real company names/prefixes are entered by the user; nothing is pre-filled.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit
from ..deps import Principal, client_ip, current_principal, get_db, require
from ..models import Branch, Company, Department

router = APIRouter(prefix="/api/companies", tags=["org"])


class CompanyIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    asset_prefix: str = Field(default="", max_length=20, pattern=r"^[A-Za-z0-9]*$")


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


def _company_dict(c: Company) -> dict:
    return {"id": c.id, "name": c.name, "asset_prefix": c.asset_prefix, "active": c.active,
            "branches": [{"id": b.id, "name": b.name, "active": b.active} for b in c.branches],
            "departments": [{"id": d.id, "name": d.name, "active": d.active} for d in c.departments]}


@router.get("")
def list_companies(p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> list[dict]:
    allowed = p.company_ids()
    q = select(Company).order_by(Company.name)
    if allowed is not None:
        q = q.where(Company.id.in_(allowed or {""}))
    return [_company_dict(c) for c in db.scalars(q)]


@router.post("", status_code=201)
def create_company(body: CompanyIn, request: Request, p: Principal = Depends(require("system:edit")),
                   db: Session = Depends(get_db)) -> dict:
    c = Company(name=body.name.strip(), asset_prefix=body.asset_prefix)
    db.add(c)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "اسم الشركة موجود بالفعل") from None
    audit.record(db, "company.created", actor_id=p.user.id, entity="company", entity_id=c.id, company_id=c.id,
                 details={"name": c.name, "asset_prefix": c.asset_prefix}, ip=client_ip(request))
    db.commit()
    db.refresh(c)
    return _company_dict(c)


class CompanyPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    asset_prefix: str | None = Field(default=None, max_length=20, pattern=r"^[A-Za-z0-9]*$")
    active: bool | None = None


@router.patch("/{company_id}")
def update_company(company_id: str, body: CompanyPatch, request: Request, p: Principal = Depends(require("system:edit")),
                   db: Session = Depends(get_db)) -> dict:
    c = db.get(Company, company_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "شركة غير موجودة")
    changes = body.model_dump(exclude_none=True)
    if "asset_prefix" in changes and changes["asset_prefix"] != c.asset_prefix:
        from ..modules.assets import Asset
        if db.scalar(select(Asset.id).where(Asset.company_id == c.id).limit(1)):
            raise HTTPException(status.HTTP_409_CONFLICT, "مينفعش تغيير البادئة بعد تسجيل أصول — الأكواد ثابتة")
    for k, v in changes.items():
        setattr(c, k, v.strip() if isinstance(v, str) else v)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "اسم الشركة موجود بالفعل") from None
    audit.record(db, "company.updated", actor_id=p.user.id, entity="company", entity_id=c.id, company_id=c.id,
                 details=changes, ip=client_ip(request))
    db.commit()
    db.refresh(c)
    return _company_dict(c)


def _add_child(kind: str, company_id: str, body: NameIn, request: Request, p: Principal, db: Session) -> dict:
    c = db.get(Company, company_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "شركة غير موجودة")
    model = Branch if kind == "branch" else Department
    obj = model(company_id=company_id, name=body.name.strip())
    db.add(obj)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "الاسم موجود بالفعل في نفس الشركة") from None
    audit.record(db, f"{kind}.created", actor_id=p.user.id, entity=kind, entity_id=obj.id, company_id=company_id,
                 details={"name": obj.name}, ip=client_ip(request))
    db.commit()
    return {"id": obj.id, "name": obj.name, "company_id": company_id}


@router.post("/{company_id}/branches", status_code=201)
def add_branch(company_id: str, body: NameIn, request: Request, p: Principal = Depends(require("system:edit")),
               db: Session = Depends(get_db)) -> dict:
    return _add_child("branch", company_id, body, request, p, db)


@router.post("/{company_id}/departments", status_code=201)
def add_department(company_id: str, body: NameIn, request: Request, p: Principal = Depends(require("system:edit")),
                   db: Session = Depends(get_db)) -> dict:
    return _add_child("department", company_id, body, request, p, db)
