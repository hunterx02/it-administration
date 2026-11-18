"""Evidence/document storage.

Local store now (data/files/<sha[:2]>/<id>), Google Drive later through the M06
connector — callers only ever keep the stable `file_id`. File bytes are never
put in DB cells or Sheets. Sensitive categories need `employees:view_sensitive`.
"""
from __future__ import annotations

import hashlib
import mimetypes
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from .. import audit
from ..common import bad, ensure_company, forbid, not_found
from ..db import Base
from ..deps import Principal, client_ip, current_principal, get_db
from ..models import new_id, utcnow

MAX_BYTES = 20 * 1024 * 1024
ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".docx", ".xlsx", ".csv", ".txt", ".py", ".gs", ".js"}

# category -> (permission needed to upload/read, sensitive?)
CATEGORIES: dict[str, tuple[str, bool]] = {
    "joining_document": ("employees:view_sensitive", True),     # national ID, birth cert, photo, degree, military
    "hr_document": ("employees:view_sensitive", True),          # signed contract / acknowledgments scan
    "clearance_scan": ("employees:view", True),                 # signed paper clearance (HR uploads)
    "document_template": ("employees:view", False),             # NDA / regulations / contract templates
    "custody_evidence": ("assets:view", False),
    "invoice": ("finance:view", False),
    "paper_approval": ("finance:view", False),
    "receipt": ("finance:view", False),
    "script": ("system:manage_integration", False),
    "letterhead": ("dashboard:view", False),                    # company header/footer images for printouts
    "import": ("dashboard:view", False),
    "other": ("dashboard:view", False),
}


class FileObject(Base):
    __tablename__ = "file_objects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    category: Mapped[str] = mapped_column(String(40))
    original_name: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(120))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage: Mapped[str] = mapped_column(String(20), default="local")  # local | drive
    storage_ref: Mapped[str] = mapped_column(String(400))               # local path / Drive file id
    sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    uploaded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def files_dir(request: Request) -> Path:
    d = Path(request.app.state.settings.data_dir) / "files"
    d.mkdir(parents=True, exist_ok=True)
    return d


def store_bytes(request: Request, db: Session, *, data: bytes, name: str, category: str, company_id: str | None,
                actor_id: str | None) -> FileObject:
    if category not in CATEGORIES:
        raise bad("تصنيف ملف غير معروف")
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise bad(f"نوع الملف غير مسموح ({ext or 'بدون امتداد'})")
    if not data:
        raise bad("الملف فاضي")
    if len(data) > MAX_BYTES:
        raise bad("حجم الملف أكبر من 20 ميجا")
    sha = hashlib.sha256(data).hexdigest()
    fo = FileObject(company_id=company_id, category=category, original_name=Path(name).name[:255],
                    mime=mimetypes.guess_type(name)[0] or "application/octet-stream", size=len(data), sha256=sha,
                    sensitive=CATEGORIES[category][1], uploaded_by=actor_id, storage_ref="")
    db.add(fo)
    db.flush()
    sub = files_dir(request) / sha[:2]
    sub.mkdir(parents=True, exist_ok=True)
    path = sub / fo.id
    path.write_bytes(data)
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha:  # read-back verification
        raise bad("فشل التحقق من حفظ الملف")
    fo.storage_ref = str(path.relative_to(files_dir(request)))
    return fo


def file_meta(fo: FileObject) -> dict:
    return {"id": fo.id, "name": fo.original_name, "mime": fo.mime, "size": fo.size, "sha256": fo.sha256,
            "category": fo.category, "storage": fo.storage, "created_at": fo.created_at.isoformat()}


def require_file(db: Session, file_id: str | None, category: str | None = None) -> FileObject:
    fo = db.get(FileObject, file_id) if file_id else None
    if fo is None:
        raise bad("الملف المرفق غير موجود — ارفعه الأول")
    if category and fo.category != category:
        raise bad("نوع الملف المرفق غير مناسب لهذه العملية")
    return fo


router = APIRouter(prefix="/api/files", tags=["files"])


@router.post("", status_code=201)
async def upload(request: Request, category: str = Form(...), company_id: str | None = Form(None),
                 file: UploadFile = File(...), p: Principal = Depends(current_principal),
                 db: Session = Depends(get_db)) -> dict:
    perm = CATEGORIES.get(category, ("system:edit", False))[0]
    if category == "letterhead":
        perm = "system:edit"  # anyone may view it on printouts, only admins upload
    if not p.can(perm):
        raise forbid("ليس لديك صلاحية رفع هذا النوع من الملفات")
    if company_id:
        ensure_company(p, company_id)
    data = await file.read(MAX_BYTES + 1)
    fo = store_bytes(request, db, data=data, name=file.filename or "file", category=category,
                     company_id=company_id, actor_id=p.user.id)
    audit.record(db, "file.uploaded", actor_id=p.user.id, entity="file", entity_id=fo.id, company_id=company_id,
                 details={"category": category, "sha256": fo.sha256, "size": fo.size}, ip=client_ip(request))
    db.commit()
    return file_meta(fo)


@router.get("/{file_id}")
def download(file_id: str, request: Request, p: Principal = Depends(current_principal),
             db: Session = Depends(get_db)):  # noqa: ANN201
    fo = db.get(FileObject, file_id)
    if fo is None:
        raise not_found("الملف غير موجود")
    perm = CATEGORIES.get(fo.category, ("system:edit", False))[0]
    if not p.can(perm) or (fo.sensitive and fo.category != "clearance_scan" and not p.can("employees:view_sensitive")):
        raise forbid("ليس لديك صلاحية فتح هذا الملف")
    if fo.company_id:
        ensure_company(p, fo.company_id)
    path = files_dir(request) / fo.storage_ref
    if not path.is_file():
        raise not_found("محتوى الملف غير موجود على التخزين")
    audit.record(db, "file.downloaded", actor_id=p.user.id, entity="file", entity_id=fo.id, ip=client_ip(request))
    db.commit()
    return FileResponse(path, media_type=fo.mime, filename=fo.original_name)
