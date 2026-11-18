"""M06 automation: worker heartbeat, script library, sync runs & conflicts, backups/restore verification.

Script library rules (master RC1 STEP 7): upload != execute; a script runs only after approval by a user
holding `system:manage_secrets`, only the approved version (sha256 re-checked before every run), with a
timeout, isolated interpreter (-I), empty working dir and a minimal environment (no secrets injected).
Rollback = re-activate a previous approved version. NOTE: OS-level network isolation is NOT enforced on a
Windows desktop host; `allow_network` is recorded policy only until the VPS/container runner exists.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .. import audit
from ..common import bad, conflict, forbid, iso, not_found, now
from ..db import Base
from ..deps import Principal, client_ip, get_db, require
from ..models import IntegrationConfig, new_id, utcnow
from .files import FileObject, files_dir, require_file


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeat"
    id: Mapped[str] = mapped_column(String(20), primary_key=True, default="main")
    last_beat: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=60)
    last_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    host: Mapped[str] = mapped_column(String(120), default="")


def worker_status(db: Session) -> dict:
    hb = db.get(WorkerHeartbeat, "main")
    if hb is None:
        return {"state": "never", "state_ar": "الـ Worker لم يعمل بعد", "last_beat": None}
    lb = hb.last_beat if hb.last_beat.tzinfo else hb.last_beat.replace(tzinfo=timezone.utc)
    age = (now() - lb).total_seconds()
    stale = age > max(3 * hb.interval_seconds, 180)
    return {"state": "stale" if stale else "running",
            "state_ar": "متوقف — البيانات قد تكون قديمة (الجهاز مقفول أو الـ Worker واقف)" if stale else "يعمل",
            "last_beat": lb.isoformat(), "age_seconds": int(age), "last_summary": hb.last_summary, "host": hb.host}


class Script(Base):
    __tablename__ = "scripts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), index=True)
    module: Mapped[str] = mapped_column(String(10))
    runner: Mapped[str] = mapped_column(String(20), default="python")  # python | none (reference only, e.g. .gs)
    version: Mapped[int] = mapped_column(Integer)
    file_id: Mapped[str] = mapped_column(String(36))
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="uploaded")  # uploaded|approved|active|disabled|superseded
    schedule_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    allow_network: Mapped[bool] = mapped_column(Boolean, default=False)
    secret_scopes: Mapped[list] = mapped_column(JSON, default=list)  # names only; values never injected yet
    integration_key: Mapped[str] = mapped_column(String(60), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    uploaded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str] = mapped_column(String(20), default="")
    run_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ScriptRun(Base):
    __tablename__ = "script_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    script_id: Mapped[str] = mapped_column(String(36), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20))  # success | failed | timeout | blocked
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output: Mapped[str] = mapped_column(Text, default="")  # redacted tail


_SECRETISH = re.compile(r"(?i)(password|passwd|pwd|token|secret|api[_-]?key|authorization)\s*[:=]\s*\S+")


def redact(text: str) -> str:
    text = _SECRETISH.sub(lambda m: m.group(1) + "=[REDACTED]", text)
    return re.sub(r"[A-Za-z0-9_\-]{32,}", "[REDACTED]", text)[-4000:]


def script_out(s: Script) -> dict:
    return {k: iso(getattr(s, k)) for k in ("id", "name", "module", "runner", "version", "sha256", "status",
                                            "schedule_minutes", "timeout_seconds", "retries", "allow_network",
                                            "secret_scopes", "integration_key", "description", "approved_by",
                                            "approved_at", "last_run_at", "last_status", "run_requested",
                                            "created_at")}


def execute_script(db: Session, s: Script, data_dir: Path) -> ScriptRun:
    run = ScriptRun(script_id=s.id, status="blocked")
    db.add(run)
    fo = db.get(FileObject, s.file_id)
    path = Path(data_dir) / "files" / fo.storage_ref if fo else None
    if s.status != "active" or s.runner != "python":
        run.output = "غير معتمد أو نوع غير قابل للتشغيل"
    elif path is None or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != s.sha256:
        run.output = "بصمة الملف لا تطابق النسخة المعتمدة — التشغيل ممنوع"
    else:
        with tempfile.TemporaryDirectory(prefix="itadm-run-") as work:
            target = Path(work) / "script.py"
            shutil.copyfile(path, target)
            env = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "PATH": os.path.dirname(sys.executable),
                   "PYTHONIOENCODING": "utf-8", "ITADM_SCRIPT_ID": s.id}
            for attempt in range(s.retries + 1):
                try:
                    cp = subprocess.run([sys.executable, "-I", str(target)], cwd=work, env=env, capture_output=True,
                                        text=True, timeout=s.timeout_seconds, encoding="utf-8", errors="replace")
                    run.exit_code = cp.returncode
                    run.status = "success" if cp.returncode == 0 else "failed"
                    run.output = redact((cp.stdout or "") + "\n" + (cp.stderr or ""))
                except subprocess.TimeoutExpired:
                    run.status, run.output = "timeout", f"تجاوز المهلة {s.timeout_seconds} ثانية"
                if run.status == "success":
                    break
    run.finished_at = now()
    s.last_run_at, s.last_status, s.run_requested = run.finished_at, run.status, False
    db.flush()
    return run


# ---------------------------------------------------------------- backups
def backup_dir(data_dir: Path) -> Path:
    d = Path(data_dir) / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_backup(db_url: str, data_dir: Path, label: str = "") -> Path:
    if not db_url.startswith("sqlite:///"):
        raise bad("النسخ المدمج للـ SQLite فقط — على PostgreSQL استخدم pg_dump")
    db_path = Path(db_url.replace("sqlite:///", ""))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"itadmin-backup-{stamp}{('-' + re.sub(r'[^A-Za-z0-9_-]', '', label)) if label else ''}.zip"
    out = backup_dir(data_dir) / name
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "itadmin.db"
        src = sqlite3.connect(str(db_path))
        dst = sqlite3.connect(str(snap))
        with dst:
            src.backup(dst)  # consistent online snapshot
        src.close()
        dst.close()
        manifest = {"created_at": now().isoformat(), "db_sha256": hashlib.sha256(snap.read_bytes()).hexdigest(),
                    "files": {}}
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snap, "itadmin.db")
            fdir = Path(data_dir) / "files"
            if fdir.is_dir():
                for f in fdir.rglob("*"):
                    if f.is_file():
                        rel = f.relative_to(fdir).as_posix()
                        manifest["files"][rel] = hashlib.sha256(f.read_bytes()).hexdigest()
                        z.write(f, "files/" + rel)
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
    return out


def verify_backup(path: Path) -> dict:
    """Real restore test: extract to a temp dir, check hashes, open the DB and run integrity_check."""
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if n.startswith("/") or ".." in Path(n).parts:
                raise bad("ملف النسخة فيه مسارات غير آمنة")
        z.extractall(tmp)
        m = json.loads((Path(tmp) / "manifest.json").read_text(encoding="utf-8"))
        dbf = Path(tmp) / "itadmin.db"
        problems = []
        if hashlib.sha256(dbf.read_bytes()).hexdigest() != m["db_sha256"]:
            problems.append("بصمة قاعدة البيانات لا تطابق")
        for rel, sha in m["files"].items():
            f = Path(tmp) / "files" / rel
            if not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest() != sha:
                problems.append(f"ملف تالف: {rel}")
        con = sqlite3.connect(str(dbf))
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        counts = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                  for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        if integrity != "ok":
            problems.append(f"integrity_check: {integrity}")
    return {"ok": not problems, "problems": problems, "integrity": integrity, "files": len(m["files"]),
            "tables": counts, "created_at": m["created_at"]}


def restore_backup(path: Path, db_url: str, data_dir: Path) -> dict:
    """Used by the CLI while the server is STOPPED. Keeps a pre-restore backup first."""
    v = verify_backup(path)
    if not v["ok"]:
        raise bad("النسخة غير سليمة: " + "; ".join(v["problems"]))
    pre = create_backup(db_url, data_dir, "pre-restore")
    db_path = Path(db_url.replace("sqlite:///", ""))
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(path) as z:
        z.extractall(tmp)
        for suffix in ("-wal", "-shm"):
            Path(str(db_path) + suffix).unlink(missing_ok=True)
        shutil.copyfile(Path(tmp) / "itadmin.db", db_path)
        fdir = Path(data_dir) / "files"
        if fdir.exists():
            shutil.rmtree(fdir)
        if (Path(tmp) / "files").exists():
            shutil.copytree(Path(tmp) / "files", fdir)
    return {"restored_from": path.name, "pre_restore_backup": pre.name, **v}


# ---------------------------------------------------------------- API
router = APIRouter(prefix="/api/system", tags=["M06 automation"])


class ScriptIn(BaseModel):
    name: str = Field(min_length=2, max_length=120, pattern=r"^[A-Za-z0-9_\-. ]+$")
    module: str = Field(pattern="^(M02|M03|M04|M05|M06)$")
    file_id: str
    runner: str = Field(default="python", pattern="^(python|none)$")
    schedule_minutes: int | None = Field(default=None, ge=5, le=10080)
    timeout_seconds: int = Field(default=60, ge=5, le=600)
    retries: int = Field(default=0, ge=0, le=3)
    allow_network: bool = False
    secret_scopes: list[str] = Field(default_factory=list)
    integration_key: str = ""
    description: str = ""


@router.get("/worker")
def get_worker(_: Principal = Depends(require("system:view")), db: Session = Depends(get_db)) -> dict:
    return worker_status(db)


@router.get("/scripts")
def list_scripts(_: Principal = Depends(require("system:view")), db: Session = Depends(get_db)) -> list[dict]:
    return [script_out(s) for s in db.scalars(select(Script).order_by(Script.name, Script.version.desc()))]


@router.post("/scripts", status_code=201)
def upload_script(body: ScriptIn, request: Request, p: Principal = Depends(require("system:manage_integration")),
                  db: Session = Depends(get_db)) -> dict:
    fo = require_file(db, body.file_id, "script")
    if body.runner == "python" and not fo.original_name.endswith(".py"):
        raise bad("السكريبت القابل للتشغيل لازم يكون .py — باقي الأنواع تتسجل كمرجع (runner=none)")
    last = db.scalar(select(Script).where(Script.name == body.name).order_by(Script.version.desc()))
    s = Script(**body.model_dump(), version=(last.version + 1) if last else 1, sha256=fo.sha256, status="uploaded",
               uploaded_by=p.user.id)
    db.add(s)
    db.flush()
    audit.record(db, "script.uploaded", actor_id=p.user.id, entity="script", entity_id=s.id,
                 details={"name": s.name, "version": s.version, "sha256": s.sha256}, ip=client_ip(request))
    db.commit()
    return script_out(s)


def _script(db: Session, sid: str) -> Script:
    s = db.get(Script, sid)
    if s is None:
        raise not_found("السكريبت غير موجود")
    return s


@router.post("/scripts/{sid}/approve")
def approve_script(sid: str, request: Request, p: Principal = Depends(require("system:manage_secrets")),
                   db: Session = Depends(get_db)) -> dict:
    s = _script(db, sid)
    if s.status not in ("uploaded", "disabled", "superseded"):
        raise conflict("السكريبت معتمد بالفعل")
    for other in db.scalars(select(Script).where(Script.name == s.name, Script.status == "active")):
        other.status = "superseded"
    s.status, s.approved_by, s.approved_at = "active", p.user.id, now()
    audit.record(db, "script.approved", actor_id=p.user.id, entity="script", entity_id=s.id,
                 details={"name": s.name, "version": s.version, "sha256": s.sha256}, ip=client_ip(request))
    db.commit()
    return script_out(s)


@router.post("/scripts/{sid}/disable")
def disable_script(sid: str, request: Request, p: Principal = Depends(require("system:manage_integration")),
                   db: Session = Depends(get_db)) -> dict:
    s = _script(db, sid)
    s.status = "disabled"
    audit.record(db, "script.disabled", actor_id=p.user.id, entity="script", entity_id=s.id, ip=client_ip(request))
    db.commit()
    return script_out(s)


@router.post("/scripts/{sid}/run")
def request_run(sid: str, request: Request, p: Principal = Depends(require("system:manage_integration")),
                db: Session = Depends(get_db)) -> dict:
    s = _script(db, sid)
    if s.status != "active":
        raise conflict("مينفعش تشغيل سكريبت غير معتمد")
    s.run_requested = True
    audit.record(db, "script.run_requested", actor_id=p.user.id, entity="script", entity_id=s.id,
                 ip=client_ip(request))
    db.commit()
    return script_out(s) | {"note": "هيتشغل بواسطة الـ Worker في الدورة الجاية (لو الـ Worker شغال)"}


@router.get("/scripts/{sid}/runs")
def script_runs(sid: str, _: Principal = Depends(require("system:view")), db: Session = Depends(get_db)) -> list[dict]:
    return [{k: iso(getattr(r, k)) for k in ("id", "started_at", "finished_at", "status", "exit_code", "output")}
            for r in db.scalars(select(ScriptRun).where(ScriptRun.script_id == sid)
                                .order_by(ScriptRun.started_at.desc()).limit(50))]


# --- sync
@router.post("/integrations/{key}/sync")
def sync_now(key: str, request: Request, p: Principal = Depends(require("system:manage_integration")),
             db: Session = Depends(get_db)) -> dict:
    from ..integrations.google import run_connector
    cfg = db.scalar(select(IntegrationConfig).where(IntegrationConfig.key == key))
    if cfg is None:
        raise not_found("موصل غير موجود")
    transport = getattr(request.app.state, "sheets_transport", None)  # injected only in tests/demo sandbox
    res = run_connector(db, cfg, Path(request.app.state.settings.data_dir) / "secrets", transport, p.user.id,
                        secret_lookup=_secret_lookup(request.app.state.settings))
    db.commit()
    return res


def _secret_lookup(settings):  # noqa: ANN001, ANN202
    from ..secrets_store import SecretStoreError, store_for

    def get(name: str) -> str | None:
        try:
            return store_for(settings).get(name)
        except SecretStoreError:
            return None
    return get


# --- encrypted secrets (values are write-only: never returned to the browser)
class SecretIn(BaseModel):
    value: str = Field(min_length=1, max_length=20000)


SECRET_NAME = r"^[A-Za-z0-9_.\-]{1,80}$"


@router.get("/secrets")
def list_secrets(request: Request, _: Principal = Depends(require("system:manage_secrets"))) -> list[dict]:
    from ..secrets_store import store_for
    return store_for(request.app.state.settings).names()


@router.put("/secrets/{name}")
def put_secret(name: str, body: SecretIn, request: Request, p: Principal = Depends(require("system:manage_secrets")),
               db: Session = Depends(get_db)) -> dict:
    from ..secrets_store import store_for
    if not re.fullmatch(SECRET_NAME, name):
        raise bad("اسم السر: حروف إنجليزية وأرقام و _ . - بس")
    store_for(request.app.state.settings).set(name, body.value)
    audit.record(db, "secret.set", actor_id=p.user.id, entity="secret", entity_id=name, ip=client_ip(request))
    db.commit()
    return {"name": name, "stored": True}


@router.delete("/secrets/{name}")
def delete_secret(name: str, request: Request, p: Principal = Depends(require("system:manage_secrets")),
                  db: Session = Depends(get_db)) -> dict:
    from ..secrets_store import store_for
    if not store_for(request.app.state.settings).delete(name):
        raise not_found("السر غير موجود")
    audit.record(db, "secret.deleted", actor_id=p.user.id, entity="secret", entity_id=name, ip=client_ip(request))
    db.commit()
    return {"ok": True}


# --- local Excel workbook as a connector's data source
def workbooks_dir(settings) -> Path:  # noqa: ANN001
    d = Path(settings.data_dir) / "workbooks"
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.post("/integrations/{key}/workbook")
async def upload_workbook(key: str, request: Request, file: UploadFile = File(...),
                          p: Principal = Depends(require("system:manage_integration")),
                          db: Session = Depends(get_db)) -> dict:
    cfg = db.scalar(select(IntegrationConfig).where(IntegrationConfig.key == key))
    if cfg is None:
        raise not_found("موصل غير موجود")
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise bad("الملف لازم يكون Excel بامتداد xlsx")
    data = await file.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        raise bad("الملف أكبر من 20 ميجا")
    try:
        from openpyxl import load_workbook
        import io as _io
        load_workbook(_io.BytesIO(data))
    except Exception:  # noqa: BLE001
        raise bad("الملف مش Excel صالح") from None
    name = f"{key}.xlsx"
    (workbooks_dir(request.app.state.settings) / name).write_bytes(data)
    cfg.mode, cfg.source_ref, cfg.status, cfg.last_error = "local_xlsx", name, "pending_first_sync", ""
    cfg.version += 1
    audit.record(db, "integration.workbook_uploaded", actor_id=p.user.id, entity="integration", entity_id=key,
                 details={"file": file.filename, "size": len(data)}, ip=client_ip(request))
    db.commit()
    return {"key": key, "mode": cfg.mode, "status": cfg.status}


@router.get("/integrations/{key}/workbook")
def download_workbook(key: str, request: Request, _: Principal = Depends(require("system:view")),
                      db: Session = Depends(get_db)):  # noqa: ANN201
    from fastapi.responses import FileResponse
    cfg = db.scalar(select(IntegrationConfig).where(IntegrationConfig.key == key))
    if cfg is None or cfg.mode != "local_xlsx" or not cfg.source_ref:
        raise not_found("مفيش ملف Excel محلي لهذا الموصل")
    path = workbooks_dir(request.app.state.settings) / cfg.source_ref
    if not path.is_file():
        raise not_found("الملف مش موجود")
    return FileResponse(path, filename=f"{key}.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/sync/conflicts")
def list_conflicts(status: str = "open", _: Principal = Depends(require("system:view")),
                   db: Session = Depends(get_db)) -> list[dict]:
    from ..integrations.google import SyncConflict
    return [{k: iso(getattr(c, k)) for k in ("id", "connector", "record_key", "kind", "sheet_values", "app_values",
                                             "message", "status", "resolution", "created_at")}
            for c in db.scalars(select(SyncConflict).where(SyncConflict.status == status)
                                .order_by(SyncConflict.created_at.desc()).limit(500))]


class ResolveIn(BaseModel):
    resolution: str = Field(pattern="^(keep_app|dismissed)$")


@router.post("/sync/conflicts/{cid}/resolve")
def resolve_conflict(cid: str, body: ResolveIn, request: Request,
                     p: Principal = Depends(require("system:manage_integration")), db: Session = Depends(get_db)) -> dict:
    """keep_app: the app value stays and is pushed again on next sync. Applying sheet values is done by
    editing the record in the app (so validation, version and audit apply)."""
    from ..integrations.google import SyncConflict, SyncState
    c = db.get(SyncConflict, cid)
    if c is None or c.status != "open":
        raise not_found("تعارض مفتوح غير موجود")
    c.status, c.resolution, c.resolved_by = "resolved", body.resolution, p.user.id
    st = db.scalar(select(SyncState).where(SyncState.connector == c.connector, SyncState.record_key == c.record_key))
    if st is not None and c.kind == "concurrent_edit":
        st.app_version = -1  # forces the app values to be re-pushed on next run
    audit.record(db, "sync.conflict_resolved", actor_id=p.user.id, entity=c.connector, entity_id=c.record_key,
                 details={"resolution": body.resolution}, ip=client_ip(request))
    db.commit()
    return {"ok": True}


# --- backups
class BackupIn(BaseModel):
    label: str = Field(default="", max_length=40)


@router.get("/backups")
def list_backups(request: Request, _: Principal = Depends(require("system:view"))) -> list[dict]:
    d = backup_dir(request.app.state.settings.data_dir)
    return [{"name": f.name, "size": f.stat().st_size,
             "created": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")}
            for f in sorted(d.glob("*.zip"), reverse=True)]


@router.post("/backups", status_code=201)
def make_backup(body: BackupIn, request: Request, p: Principal = Depends(require("system:edit")),
                db: Session = Depends(get_db)) -> dict:
    out = create_backup(request.app.state.settings.database_url, request.app.state.settings.data_dir, body.label)
    audit.record(db, "backup.created", actor_id=p.user.id, entity="backup", entity_id=out.name, ip=client_ip(request))
    db.commit()
    return {"name": out.name, "size": out.stat().st_size}


@router.post("/backups/{name}/verify")
def verify(name: str, request: Request, p: Principal = Depends(require("system:edit")),
           db: Session = Depends(get_db)) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+\.zip", name):
        raise bad("اسم غير صالح")
    path = backup_dir(request.app.state.settings.data_dir) / name
    if not path.is_file():
        raise not_found("النسخة غير موجودة")
    res = verify_backup(path)
    audit.record(db, "backup.verified", actor_id=p.user.id, entity="backup", entity_id=name,
                 details={"ok": res["ok"]}, ip=client_ip(request))
    db.commit()
    return res


# ---------------------------------------------------------------- worker loop body
def worker_tick(app_state, transport=None) -> dict:  # noqa: ANN001
    """One worker cycle: heartbeat, notifications, due scripts, enabled sync connectors."""
    import platform
    from ..integrations.google import run_connector
    from .ops import dispatch_notifications
    settings = app_state.settings
    summary: dict = {"at": now().isoformat()}
    with app_state.db.SessionLocal() as db:
        hb = db.get(WorkerHeartbeat, "main") or WorkerHeartbeat(id="main")
        hb.last_beat, hb.interval_seconds, hb.host = now(), settings.worker_interval_seconds, platform.node()
        db.add(hb)
        db.commit()
        summary["notifications_created"] = dispatch_notifications(db)
        ran = []
        for s in db.scalars(select(Script).where(Script.status == "active", Script.runner == "python")):
            due = s.run_requested or (s.schedule_minutes and (
                s.last_run_at is None or
                (s.last_run_at if s.last_run_at.tzinfo else s.last_run_at.replace(tzinfo=timezone.utc))
                + timedelta(minutes=s.schedule_minutes) <= now()))
            if due:
                r = execute_script(db, s, settings.data_dir)
                ran.append({"script": s.name, "status": r.status})
        db.commit()
        summary["scripts"] = ran
        syncs = {}
        for cfg in db.scalars(select(IntegrationConfig).where(IntegrationConfig.enabled.is_(True),
                                                              IntegrationConfig.mode.in_(("sheets", "local_xlsx")))):
            syncs[cfg.key] = run_connector(db, cfg, Path(settings.data_dir) / "secrets", transport,
                                           secret_lookup=_secret_lookup(settings)).get("status")
            db.commit()
        summary["sync"] = syncs
        hb = db.get(WorkerHeartbeat, "main")
        hb.last_summary = summary
        db.commit()
    return summary
