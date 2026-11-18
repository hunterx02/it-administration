"""Google Sheets connector + sync engine (M06 / RC1 STEP 9).

- Transport is pluggable: `LiveSheetsTransport` (google-api-python-client, service-account JSON kept OUTSIDE
  the repo under data/secrets/<credential_ref>.json) or `MemorySheetsTransport` (tests / local demo).
  If credentials are missing the connector reports `pending_live_credentials` — never "connected".
- Rows are matched by a stable key column (asset code / sim code ...), never by row number.
- Unknown columns are rejected; every value written is formula-injection safe.
- Sheet edits are applied ONLY to safe fields. Custody/holder/status columns are app->sheet only; an edit there
  in the sheet is queued for review, never executed (no handover via a cell).
- Concurrent edits (sheet changed AND app changed since the last sync) go to the conflict queue — no silent
  last-write-wins.
- Google Form applications are imported idempotently by `source_form_response_id`.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from sqlalchemy import JSON, DateTime, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .. import audit
from ..common import iso, now, safe_cell
from ..db import Base
from ..models import Company, IntegrationConfig, new_id, utcnow


class PendingCredentials(Exception):
    """Live Google access is not configured/authorised yet."""


class SheetsTransport(Protocol):
    def read(self, spreadsheet_id: str, tab: str) -> list[list[str]]: ...
    def write_cells(self, spreadsheet_id: str, tab: str, updates: list[tuple[int, int, Any]]) -> None: ...
    def append(self, spreadsheet_id: str, tab: str, rows: list[list[Any]]) -> None: ...


class MemorySheetsTransport:
    """In-memory spreadsheet used by tests and the local demo sandbox."""

    def __init__(self) -> None:
        self.books: dict[str, dict[str, list[list[Any]]]] = {}

    def read(self, spreadsheet_id: str, tab: str) -> list[list[str]]:
        try:
            return [[("" if v is None else str(v)) for v in r] for r in self.books[spreadsheet_id][tab]]
        except KeyError:
            raise FileNotFoundError(f"{spreadsheet_id}/{tab}") from None

    def write_cells(self, spreadsheet_id: str, tab: str, updates: list[tuple[int, int, Any]]) -> None:
        grid = self.books[spreadsheet_id][tab]
        for r, c, v in updates:
            while len(grid) <= r:
                grid.append([])
            while len(grid[r]) <= c:
                grid[r].append("")
            grid[r][c] = v

    def append(self, spreadsheet_id: str, tab: str, rows: list[list[Any]]) -> None:
        self.books[spreadsheet_id][tab].extend([list(r) for r in rows])


class LocalXlsxTransport:
    """A local Excel workbook as the data source, edited IN PLACE with openpyxl: formatting, data validation,
    conditional formatting and formula cells are preserved (formula cells are never overwritten and are read as
    empty, since they are computed). A one-time .bak copy is taken before the first write of each sync."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise PendingCredentials("ملف Excel المحلي مش موجود — ارفعه من إعداد الموصل")
        self._backed_up = False

    @staticmethod
    def _is_formula(v: Any) -> bool:
        return isinstance(v, str) and v.startswith("=")

    def _load(self):  # noqa: ANN202
        from openpyxl import load_workbook
        return load_workbook(self.path)

    def _sheet(self, wb, tab: str):  # noqa: ANN001, ANN202
        if tab not in wb.sheetnames:
            raise FileNotFoundError(f"الورقة «{tab}» مش موجودة في الملف")
        return wb[tab]

    def read(self, spreadsheet_id: str, tab: str) -> list[list[str]]:
        ws = self._sheet(self._load(), tab)
        out = []
        for r in ws.iter_rows(values_only=True):
            row = []
            for v in r:
                if v is None or self._is_formula(v):
                    row.append("")
                elif isinstance(v, (datetime, date)):
                    row.append(v.date().isoformat() if isinstance(v, datetime) else v.isoformat())
                elif isinstance(v, float) and v.is_integer():
                    row.append(str(int(v)))
                else:
                    row.append(str(v))
            out.append(row)
        while out and not any(out[-1]):
            out.pop()
        return out

    def _save(self, wb) -> None:  # noqa: ANN001
        import os
        import shutil
        if not self._backed_up:
            shutil.copy2(self.path, self.path.with_name(self.path.stem + ".bak.xlsx"))
            self._backed_up = True
        tmp = self.path.with_name(self.path.stem + ".tmp.xlsx")
        wb.save(tmp)
        os.replace(tmp, self.path)

    def write_cells(self, spreadsheet_id: str, tab: str, updates: list[tuple[int, int, Any]]) -> None:
        wb = self._load()
        ws = self._sheet(wb, tab)
        for r, c, v in updates:
            cell = ws.cell(row=r + 1, column=c + 1)
            if self._is_formula(cell.value):
                continue  # never overwrite a formula
            cell.value = v
        self._save(wb)

    def append(self, spreadsheet_id: str, tab: str, rows: list[list[Any]]) -> None:
        wb = self._load()
        ws = self._sheet(wb, tab)
        free = [i for i in range(2, ws.max_row + 1)
                if all(c.value is None or self._is_formula(c.value) for c in ws[i])]  # pre-formatted template rows
        for line in rows:
            r = free.pop(0) if free else ws.max_row + 1
            for c, v in enumerate(line, start=1):
                cell = ws.cell(row=r, column=c)
                if not self._is_formula(cell.value):
                    cell.value = v
        self._save(wb)


class LiveSheetsTransport:
    """Real Google Sheets API. Requires `google-api-python-client` + `google-auth` and a service-account key —
    preferably stored in the encrypted secrets store (name = credential_ref); a legacy plain file under
    data/secrets/<ref>.json is still accepted. Nothing here runs until credentials exist."""

    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

    def __init__(self, secrets_dir: Path, credential_ref: str, secret_json: str | None = None) -> None:
        key = secrets_dir / f"{credential_ref}.json"
        if not credential_ref or (not secret_json and not key.is_file()):
            raise PendingCredentials(f"صلاحية Google «{credential_ref or '—'}» مش موجودة في مخزن الأسرار")
        try:
            from google.oauth2 import service_account  # type: ignore
            from googleapiclient.discovery import build  # type: ignore
        except ImportError as e:
            raise PendingCredentials("مكتبات Google غير مثبتة (pip install google-api-python-client google-auth)") from e
        creds = (service_account.Credentials.from_service_account_info(json.loads(secret_json), scopes=self.SCOPES)
                 if secret_json else service_account.Credentials.from_service_account_file(str(key), scopes=self.SCOPES))
        self.svc = build("sheets", "v4", credentials=creds, cache_discovery=False).spreadsheets()

    @staticmethod
    def _a1(r: int, c: int) -> str:
        s = ""
        c += 1
        while c:
            c, rem = divmod(c - 1, 26)
            s = chr(65 + rem) + s
        return f"{s}{r + 1}"

    def read(self, spreadsheet_id: str, tab: str) -> list[list[str]]:
        res = self.svc.values().get(spreadsheetId=spreadsheet_id, range=f"'{tab}'",
                                    valueRenderOption="FORMATTED_VALUE").execute()
        return res.get("values", [])

    def write_cells(self, spreadsheet_id: str, tab: str, updates: list[tuple[int, int, Any]]) -> None:
        data = [{"range": f"'{tab}'!{self._a1(r, c)}", "values": [[v]]} for r, c, v in updates]
        for i in range(0, len(data), 500):  # batching
            self.svc.values().batchUpdate(spreadsheetId=spreadsheet_id, body={
                "valueInputOption": "RAW", "data": data[i:i + 500]}).execute()

    def append(self, spreadsheet_id: str, tab: str, rows: list[list[Any]]) -> None:
        self.svc.values().append(spreadsheetId=spreadsheet_id, range=f"'{tab}'", valueInputOption="RAW",
                                 insertDataOption="INSERT_ROWS", body={"values": rows}).execute()


# ---------------------------------------------------------------- sync state
class SyncState(Base):
    __tablename__ = "sync_state"
    __table_args__ = (UniqueConstraint("connector", "record_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    connector: Mapped[str] = mapped_column(String(60))
    record_key: Mapped[str] = mapped_column(String(120))
    sheet_hash: Mapped[str] = mapped_column(String(64), default="")
    app_version: Mapped[int] = mapped_column(Integer, default=0)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SyncConflict(Base):
    __tablename__ = "sync_conflicts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    connector: Mapped[str] = mapped_column(String(60), index=True)
    record_key: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(30))  # concurrent_edit | protected_field | invalid_row | unknown_record
    sheet_values: Mapped[dict] = mapped_column(JSON, default=dict)
    app_values: Mapped[dict] = mapped_column(JSON, default=dict)
    message: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | resolved
    resolution: Mapped[str] = mapped_column(String(30), default="")
    resolved_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


@dataclass
class Column:
    header: str
    field: str | None                      # model attribute (None = computed/display only)
    mode: str = "both"                     # both | app_only (sheet edits never applied) | sheet_ignored
    to_sheet: Callable[[Any, Any, Session], Any] | None = None   # (value, obj, db) -> cell
    from_sheet: Callable[[str], Any] | None = None


@dataclass
class EntityMapping:
    connector: str          # IntegrationConfig.key
    tab: str
    key_header: str
    model: Any
    key_field: str
    columns: list[Column]
    company_header: str | None = "الشركة"
    extra_allowed_headers: list[str] = field(default_factory=list)

    def headers(self) -> list[str]:
        return [c.header for c in self.columns]


def _bool_in(v: str) -> bool:
    return str(v).strip().lower() in ("true", "1", "✓", "☑", "نعم", "yes", "x")


def _num_in(v: str) -> float | None:
    v = str(v).strip().replace(",", "")
    return float(v) if v else None


def _int_in(v: str) -> int | None:
    n = _num_in(v)
    return int(n) if n is not None else None


def _date_in(v: str) -> date | None:
    v = str(v).strip()
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"تاريخ غير مفهوم: {v}")


UNIT_IN = {"أيام": "days", "يوم": "days", "days": "days", "شهور": "months", "شهر": "months", "months": "months"}
UNIT_OUT = {"days": "أيام", "months": "شهور"}


def _company_name(v, obj, db):  # noqa: ANN001, ANN202
    c = db.get(Company, obj.company_id)
    return c.name if c else ""


def _emp_name(attr: str):  # noqa: ANN202
    def f(v, obj, db):  # noqa: ANN001, ANN202
        from ..modules.people import Employee
        e = db.get(Employee, getattr(obj, attr)) if getattr(obj, attr) else None
        return e.full_name if e else ""
    return f


def _acc(name: str) -> Column:
    return Column({"keyboard": "كيبورد", "mouse": "ماوس", "headset": "سماعة", "cables": "كابلات", "pad": "بادة",
                   "charger": "شاحن"}[name], f"accessories.{name}", "both",
                  to_sheet=lambda v, o, db: bool((o.accessories or {}).get(name)), from_sheet=_bool_in)


def asset_mapping() -> EntityMapping:
    from ..modules.assets import ALLOCATION, CONDITION, Asset
    return EntityMapping(
        connector="m02_assets_sheet", tab="الأصول", key_header="كود الأصل", model=Asset, key_field="code",
        columns=[
            Column("كود الأصل", "code", "app_only"),
            Column("الفئة", "category_code", "app_only"),
            Column("الشركة", "company_id", "app_only", to_sheet=_company_name),
            Column("مواصفات الجهاز", None, "sheet_ignored",
                   to_sheet=lambda v, o, db: " / ".join(x for x in (o.cpu, f"{o.ram_gb}GB" if o.ram_gb else "",
                                                                    o.storage, o.gpu) if x)),
            Column("الشاشة", "screen_info"),
            Column("الحالة", "allocation_status", "app_only",
                   to_sheet=lambda v, o, db: f"{ALLOCATION[o.allocation_status]} / {CONDITION[o.tech_condition]}"),
            *[_acc(n) for n in ("keyboard", "mouse", "headset", "cables", "pad", "charger")],
            Column("اسم الموظف", "holder_employee_id", "app_only", to_sheet=_emp_name("holder_employee_id")),
            Column("PSWD", "vault_ref", "app_only",
                   to_sheet=lambda v, o, db: "مرجع خزنة" if o.vault_ref else ""),  # never a password
            Column("القسم", "department_id", "app_only",
                   to_sheet=lambda v, o, db: _dept_name(db, o.department_id)),
            Column("السيريال / IMEI", "serial"), Column("اسم الجهاز", "hostname"), Column("الماركة", "brand"),
            Column("الموديل", "model"), Column("المعالج", "cpu"), Column("RAM (GB)", "ram_gb"),
            Column("التخزين", "storage"), Column("كارت الشاشة", "gpu"), Column("الموقع", "location"),
            Column("ملاحظات", "notes"),
            Column("آخر تحديث", "updated_at", "app_only", to_sheet=lambda v, o, db: iso(o.updated_at)[:16]),
            Column("التحقق من الكود", None, "sheet_ignored", to_sheet=lambda v, o, db: "سليم"),
            Column("asset_id", "id", "app_only"),
        ],
        extra_allowed_headers=[])


def _dept_name(db: Session, dep_id: str | None) -> str:
    from ..models import Department
    d = db.get(Department, dep_id) if dep_id else None
    return d.name if d else ""


def sim_sa_mapping() -> EntityMapping:
    from ..modules.technical import ALLOCATION, NETWORK, Sim, due_info
    nd = lambda o: due_info(o.last_payment, o.period_value, o.period_unit, o.alert_days)  # noqa: E731
    return EntityMapping(
        connector="m04_sims_sa_sheet", tab="شرائح_السعودية", key_header="كود الشريحة", model=Sim, key_field="code",
        columns=[
            Column("كود الشريحة", "code", "app_only"), Column("الشركة", "company_id", "app_only", to_sheet=_company_name),
            Column("المشغل", "operator"), Column("الرقم", "number"), Column("السيريال ICCID", "iccid"),
            Column("كود المستلم", "holder_employee_id", "app_only", to_sheet=_emp_name("holder_employee_id")),
            Column("القسم", "department_id", "app_only", to_sheet=lambda v, o, db: _dept_name(db, o.department_id)),
            Column("حالة الاستخدام", "allocation_status", "app_only", to_sheet=lambda v, o, db: ALLOCATION.get(v, v)),
            Column("هل تعمل شبكة؟", "network_status", "app_only", to_sheet=lambda v, o, db: NETWORK.get(v, v)),
            Column("مكالمات؟", "calls_enabled", "both", to_sheet=lambda v, o, db: bool(v), from_sheet=_bool_in),
            Column("كود الهاتف M02", "phone_asset_id", "app_only"),
            Column("بريد Backup", "backup_email"),
            Column("آخر Backup مؤكد", "last_backup_at", "app_only"),  # only via explicit confirmation
            Column("آخر دفع", "last_payment", "app_only"),            # only via renewal event
            Column("مدة التجديد", "period_value", "both", from_sheet=_int_in),
            Column("وحدة المدة", "period_unit", "both", to_sheet=lambda v, o, db: UNIT_OUT.get(v, ""),
                   from_sheet=lambda v: UNIT_IN.get(str(v).strip(), "")),
            Column("التجديد القادم", None, "sheet_ignored", to_sheet=lambda v, o, db: nd(o)["next_due"] or ""),
            Column("الأيام المتبقية", None, "sheet_ignored",
                   to_sheet=lambda v, o, db: "" if nd(o)["days_left"] is None else nd(o)["days_left"]),
            Column("التنبيه قبل (يوم)", "alert_days", "both", from_sheet=_int_in),
            Column("مصدر التحديث", "source", "app_only"), Column("ملاحظات", "notes"),
            Column("مكان الحيازة", "possession", "app_only"),  # changes are movement events
            Column("مسؤول الشحن/المتابعة", "charge_owner"),
            Column("تفاصيل الوكيل / الملاحظات", "agent_details"),
        ])


def sim_eg_mapping() -> EntityMapping:
    from ..modules.technical import ALLOCATION, NETWORK, Sim
    return EntityMapping(
        connector="m04_sims_eg_sheet", tab="شرائح_مصر", key_header="كود الشريحة", model=Sim, key_field="code",
        columns=[
            Column("كود الشريحة", "code", "app_only"), Column("الشركة", "company_id", "app_only", to_sheet=_company_name),
            Column("القسم", "department_id", "app_only", to_sheet=lambda v, o, db: _dept_name(db, o.department_id)),
            Column("اسم الشريحة", "name"), Column("رقم الهاتف", "number"), Column("السيريال ICCID", "iccid"),
            Column("المشغل", "operator"),
            Column("كود المستلم", "holder_employee_id", "app_only", to_sheet=_emp_name("holder_employee_id")),
            Column("حالة الاستخدام", "allocation_status", "app_only", to_sheet=lambda v, o, db: ALLOCATION.get(v, v)),
            Column("الحالة الفنية", None, "sheet_ignored"),
            Column("هل تعمل شبكة؟", "network_status", "app_only", to_sheet=lambda v, o, db: NETWORK.get(v, v)),
            Column("مكالمات؟", "calls_enabled", "both", to_sheet=lambda v, o, db: bool(v), from_sheet=_bool_in),
            Column("تاريخ آخر تسليم", "last_handover", "app_only"), Column("مصدر التحديث", "source", "app_only"),
            Column("ملاحظات", "notes"),
        ])


def lines_mapping() -> EntityMapping:
    from ..modules.technical import USAGE_AR, Line, due_info, usage_state
    ui = lambda o: due_info(o.net_last_payment, o.net_period_value, o.net_period_unit, o.net_alert_days)  # noqa: E731
    ul = lambda o: due_info(o.land_last_payment, o.land_period_value, o.land_period_unit, o.land_alert_days)  # noqa: E731
    return EntityMapping(
        connector="m04_services_sheet", tab="الإنترنت_والأرضي", key_header="كود الخط", model=Line, key_field="code",
        columns=[
            Column("كود الخط", "code", "app_only"), Column("الشركة", "company_id", "app_only", to_sheet=_company_name),
            Column("الفرع", None, "sheet_ignored"), Column("رقم الأرضي", "landline_number"), Column("المزود", "provider"),
            Column("سعة الباقة GB", "quota_gb", "both", from_sheet=_num_in),
            Column("المستهلك GB", "used_gb", "app_only"), Column("المتبقي GB", "remaining_gb", "app_only"),  # readings only
            Column("آخر قراءة", "last_reading_at", "app_only", to_sheet=lambda v, o, db: iso(v)[:16] if v else ""),
            Column("مصدر القراءة", "last_reading_source", "app_only"),
            Column("آخر دفع إنترنت", "net_last_payment", "app_only"),  # renewal events only
            Column("مدة إنترنت", "net_period_value", "both", from_sheet=_int_in),
            Column("وحدة مدة الإنترنت", "net_period_unit", "both", to_sheet=lambda v, o, db: UNIT_OUT.get(v, ""),
                   from_sheet=lambda v: UNIT_IN.get(str(v).strip(), "")),
            Column("استحقاق الإنترنت", None, "sheet_ignored", to_sheet=lambda v, o, db: ui(o)["next_due"] or ""),
            Column("تنبيه الإنترنت (يوم)", "net_alert_days", "both", from_sheet=_int_in),
            Column("آخر دفع أرضي", "land_last_payment", "app_only"),
            Column("مدة الأرضي", "land_period_value", "both", from_sheet=_int_in),
            Column("وحدة مدة الأرضي", "land_period_unit", "both", to_sheet=lambda v, o, db: UNIT_OUT.get(v, ""),
                   from_sheet=lambda v: UNIT_IN.get(str(v).strip(), "")),
            Column("استحقاق الأرضي", None, "sheet_ignored", to_sheet=lambda v, o, db: ul(o)["next_due"] or ""),
            Column("تنبيه الأرضي (يوم)", "land_alert_days", "both", from_sheet=_int_in),
            Column("ملاحظات", "notes"),
            Column("عتبة تجهيز الرصيد (GB)", "warn_gb", "both", from_sheet=_num_in),
            Column("عتبة التجديد العاجل (GB)", "critical_gb", "both", from_sheet=_num_in),
            Column("حالة الرصيد", None, "sheet_ignored",
                   to_sheet=lambda v, o, db: USAGE_AR[usage_state(o.remaining_gb, o.warn_gb, o.critical_gb)]),
        ])


MAPPINGS: dict[str, Callable[[], EntityMapping]] = {"m02_assets_sheet": asset_mapping,
                                                    "m04_sims_sa_sheet": sim_sa_mapping,
                                                    "m04_sims_eg_sheet": sim_eg_mapping,
                                                    "m04_services_sheet": lines_mapping}


def _get(obj: Any, fieldname: str) -> Any:
    if fieldname.startswith("accessories."):
        return (obj.accessories or {}).get(fieldname.split(".", 1)[1], False)
    return getattr(obj, fieldname)


def _cell(col: Column, obj: Any, db: Session) -> Any:
    v = _get(obj, col.field) if col.field else None
    v = col.to_sheet(v, obj, db) if col.to_sheet else v
    if isinstance(v, bool):
        return v
    return safe_cell("" if v is None else (iso(v) if isinstance(v, (date, datetime)) else v))


def _row_hash(values: dict) -> str:
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass
class SyncResult:
    pulled: int = 0
    pushed: int = 0
    appended: int = 0
    conflicts: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def sync_entity(db: Session, transport: SheetsTransport, spreadsheet_id: str, mapping: EntityMapping,
                actor_id: str | None = None) -> SyncResult:
    res = SyncResult()
    grid = transport.read(spreadsheet_id, mapping.tab)
    if not grid:
        raise ValueError(f"تبويب «{mapping.tab}» فاضي أو بدون صف عناوين")
    header = [h.strip() for h in grid[0]]
    known = set(mapping.headers()) | set(mapping.extra_allowed_headers)
    unknown = [h for h in header if h and h not in known]
    if unknown:
        raise ValueError("أعمدة غير معروفة في الشيت (مرفوضة): " + "، ".join(unknown))
    if mapping.key_header not in header:
        raise ValueError(f"عمود المفتاح «{mapping.key_header}» غير موجود")
    # add missing mapped columns (e.g. asset_id) at the end — header row only, data never shifted
    missing = [c.header for c in mapping.columns if c.header not in header]
    if missing:
        transport.write_cells(spreadsheet_id, mapping.tab, [(0, len(header) + i, h) for i, h in enumerate(missing)])
        header += missing
    idx = {h: i for i, h in enumerate(header)}
    by_col = {c.header: c for c in mapping.columns}
    key_i = idx[mapping.key_header]
    seen: set[str] = set()
    updates: list[tuple[int, int, Any]] = []

    for r_i, raw in enumerate(grid[1:], start=1):
        row_vals = {h: (raw[i] if i < len(raw) else "") for h, i in idx.items()}
        key = str(row_vals.get(mapping.key_header, "")).strip()
        if not key:
            if any(str(v).strip() for v in row_vals.values()):
                db.add(SyncConflict(connector=mapping.connector, record_key=f"row{r_i + 1}", kind="invalid_row",
                                    sheet_values=row_vals, message="صف بدون كود — الإضافة تتم من النظام عشان الكود "
                                                                   "يتولد مرة واحدة بأمان"))
                res.conflicts += 1
            continue
        if key in seen:
            db.add(SyncConflict(connector=mapping.connector, record_key=key, kind="invalid_row", sheet_values=row_vals,
                                message="كود مكرر في الشيت"))
            res.conflicts += 1
            continue
        seen.add(key)
        obj = db.scalar(select(mapping.model).where(getattr(mapping.model, mapping.key_field) == key))
        if obj is None:
            db.add(SyncConflict(connector=mapping.connector, record_key=key, kind="unknown_record",
                                sheet_values=row_vals, message="الكود مش موجود في النظام"))
            res.conflicts += 1
            continue
        editable_vals = {h: str(row_vals[h]) for h, c in by_col.items() if c.mode == "both" and h in row_vals}
        sheet_hash = _row_hash(editable_vals)
        st = db.scalar(select(SyncState).where(SyncState.connector == mapping.connector, SyncState.record_key == key))
        sheet_changed = st is not None and st.sheet_hash != sheet_hash
        app_changed = st is None or st.app_version != obj.version
        protected_edits = {}
        if st is not None:
            for h, c in by_col.items():
                if c.mode == "app_only" and h in row_vals and str(row_vals[h]) != str(_cell(c, obj, db)):
                    protected_edits[h] = row_vals[h]
        if protected_edits and sheet_changed is not None:
            db.add(SyncConflict(connector=mapping.connector, record_key=key, kind="protected_field",
                                sheet_values=protected_edits, message="تعديل على أعمدة محمية (عهدة/حالة/تواريخ "
                                "حركات) — اتجاهل وبيترجع لقيمة النظام. التنفيذ من أزرار الحركة في النظام فقط"))
            res.conflicts += 1
        if st is not None and sheet_changed and app_changed:
            db.add(SyncConflict(connector=mapping.connector, record_key=key, kind="concurrent_edit",
                                sheet_values=editable_vals,
                                app_values={h: str(_cell(by_col[h], obj, db)) for h in editable_vals},
                                message="اتعدّل في الشيت والنظام بعد آخر مزامنة — محتاج مراجعة"))
            res.conflicts += 1
            continue
        if st is not None and sheet_changed and not app_changed:
            changes = {}
            for h, sval in editable_vals.items():
                c = by_col[h]
                try:
                    new = c.from_sheet(sval) if c.from_sheet else sval
                except ValueError as e:
                    res.errors.append(f"{key}/{h}: {e}")
                    continue
                if c.field.startswith("accessories."):
                    acc = dict(obj.accessories or {})
                    name = c.field.split(".", 1)[1]
                    if bool(acc.get(name)) != bool(new):
                        acc[name] = bool(new)
                        obj.accessories = acc
                        changes[c.field] = new
                elif getattr(obj, c.field) != new:
                    setattr(obj, c.field, new)
                    changes[c.field] = new if not isinstance(new, date) else new.isoformat()
            if changes:
                obj.version += 1
                if hasattr(obj, "source"):
                    obj.source = "sheet"
                audit.record(db, "sync.pulled", actor_id=actor_id, entity=mapping.connector, entity_id=key,
                             company_id=getattr(obj, "company_id", None), details=changes)
                res.pulled += 1
        # push app values (authoritative for app_only + whatever app now holds)
        for h, c in by_col.items():
            if c.mode == "sheet_ignored" and c.to_sheet is None:
                continue
            want = _cell(c, obj, db)
            if str(row_vals.get(h, "")) != str(want):
                updates.append((r_i, idx[h], want))
        if any(u[0] == r_i for u in updates):
            res.pushed += 1
        new_editable = {h: str(_cell(by_col[h], obj, db)) for h in editable_vals}
        if st is None:
            st = SyncState(connector=mapping.connector, record_key=key)
            db.add(st)
        st.sheet_hash, st.app_version, st.synced_at = _row_hash(new_editable), obj.version, now()

    # records that exist in the app but not in the sheet -> append
    new_rows = []
    for obj in db.scalars(select(mapping.model)):
        key = getattr(obj, mapping.key_field)
        if key in seen:
            continue
        line = [""] * len(header)
        for h, c in by_col.items():
            if c.mode == "sheet_ignored" and c.to_sheet is None:
                continue
            line[idx[h]] = _cell(c, obj, db)
        new_rows.append(line)
        st = db.scalar(select(SyncState).where(SyncState.connector == mapping.connector,
                                               SyncState.record_key == key)) or SyncState(connector=mapping.connector,
                                                                                          record_key=key)
        st.sheet_hash = _row_hash({h: str(line[idx[h]]) for h, c in by_col.items() if c.mode == "both"})
        st.app_version, st.synced_at = obj.version, now()
        db.add(st)
    if updates:
        transport.write_cells(spreadsheet_id, mapping.tab, updates)
    if new_rows:
        transport.append(spreadsheet_id, mapping.tab, new_rows)
        res.appended = len(new_rows)
    db.flush()
    return res


# ---------------------------------------------------------------- M03 Google Form applications
GENDER_IN = {"ذكر": "male", "أنثى": "female"}


def import_applications(db: Session, transport: SheetsTransport, spreadsheet_id: str,
                        tab: str = "Applications") -> SyncResult:
    """Read the M03 `Applications` tab filled by the Apps Script trigger; create local applications
    idempotently. National ID stays only in the private raw response sheet (reference kept)."""
    from ..modules.journey import ApplicationIn, create_application
    res = SyncResult()
    grid = transport.read(spreadsheet_id, tab)
    if not grid:
        return res
    head = [h.strip() for h in grid[0]]
    for i, raw in enumerate(grid[1:], start=2):
        r = {h: (raw[j] if j < len(raw) else "") for j, h in enumerate(head)}
        src = str(r.get("source_form_response_id", "")).strip()
        if not src:
            continue
        company_id = str(r.get("company_id", "")).strip()
        if db.get(Company, company_id) is None:
            db.add(SyncConflict(connector="m03_application_form", record_key=src, kind="invalid_row",
                                sheet_values={"row": i, "company_id": company_id},
                                message="company_id في الفورم مش مطابق لشركة في النظام"))
            res.conflicts += 1
            continue
        from ..modules.journey import Application
        if db.scalar(select(Application).where(Application.source_form_response_id == src)):
            continue
        try:
            body = ApplicationIn(
                company_id=company_id, position=r.get("position_applied_for") or "غير محدد",
                full_name=r.get("full_name") or "غير محدد", gender=GENDER_IN.get(r.get("gender_for_document_rule"), ""),
                email=r.get("email_contact", ""), phone=r.get("phone", ""), qualification=r.get("qualification", ""),
                graduation_year=r.get("graduation_year", ""), military_status=r.get("military_status", ""),
                marital_status=r.get("marital_status", ""), notice_period=r.get("notice_period_days", ""),
                experience=[r["experience_text"]] if r.get("experience_text") else [],
                courses=[r["courses_text"]] if r.get("courses_text") else [],
                references=[r["references_text"]] if r.get("references_text") else [],
                expected_salary=r.get("expected_salary", ""), address=r.get("current_address", ""),
                national_id=r.get("national_id_protected_ref", ""), source_form_response_id=src)
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"row {i}: {e}")
            continue
        create_application(db, body, None, source="google_form")
        res.pulled += 1
    db.flush()
    return res


def transport_for(cfg: IntegrationConfig, secrets_dir: Path, override: SheetsTransport | None = None,
                  secret_lookup: Callable[[str], str | None] | None = None) -> SheetsTransport:
    if cfg.mode == "local_xlsx":
        return LocalXlsxTransport(Path(secrets_dir).parent / "workbooks" / cfg.source_ref)
    if override is not None:
        return override
    secret_json = secret_lookup(cfg.credential_ref) if (secret_lookup and cfg.credential_ref) else None
    return LiveSheetsTransport(secrets_dir, cfg.credential_ref, secret_json)


def run_connector(db: Session, cfg: IntegrationConfig, secrets_dir: Path, transport: SheetsTransport | None = None,
                  actor_id: str | None = None, secret_lookup: Callable[[str], str | None] | None = None) -> dict:
    """Run one connector and record an honest status. Never marks healthy without a successful round-trip."""
    if not cfg.enabled:
        return {"status": "disabled"}
    if not cfg.source_ref:
        cfg.status = "not_configured"
        return {"status": cfg.status}
    try:
        t = transport_for(cfg, secrets_dir, transport, secret_lookup)
        if cfg.key in MAPPINGS:
            r = sync_entity(db, t, cfg.source_ref, MAPPINGS[cfg.key](), actor_id)
        elif cfg.key in ("m03_application_form", "m03_hr_sheet"):
            r = import_applications(db, t, cfg.source_ref)
        else:
            cfg.status = "pending_live_credentials"
            cfg.last_error = "الموصل ده لسه بيتشغل يدويًا (مفيش مزامنة آلية متاحة له)"
            return {"status": cfg.status}
        cfg.status, cfg.last_sync_at, cfg.last_error = ("healthy" if not r.errors else "error"), now(), \
            "; ".join(r.errors)[:2000]
        audit.record(db, "integration.synced", actor_id=actor_id, entity="integration", entity_id=cfg.key,
                     details=r.as_dict())
        return {"status": cfg.status, **r.as_dict()}
    except PendingCredentials as e:
        cfg.status, cfg.last_error = "pending_live_credentials", str(e)
        return {"status": cfg.status, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        cfg.status, cfg.last_error = "error", str(e)[:2000]
        return {"status": "error", "error": str(e)}
