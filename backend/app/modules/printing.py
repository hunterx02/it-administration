"""Unified printouts with each company's own letterhead (header/footer images uploaded by the user).

Custody forms (from Form_Field_Maps_AR F04/F06 — fields only, no logos from reference documents):
- Regular custody statement at onboarding / offboarding (F04): employee + all current custody, accessories,
  linked monitors, SIMs; account rows are references only (never passwords). Offboarding version adds
  Returned / Missing / Damaged / N/A boxes and keeps the original handover listing.
- Separate custody form (F06): one item handed over at any time, with company-specific terms text.
Signatures are always EMPTY boxes — the system never prints a signature or stamp.
"""
from __future__ import annotations

import html
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from .. import audit
from ..common import bad, ensure_company, forbid, not_found, today
from ..db import Base
from ..deps import Principal, client_ip, current_principal, get_db, require
from ..models import Company, Department, new_id, utcnow

TEXT_KEYS = {"custody_terms": "شروط وأحكام العهدة (تظهر في نموذج العهدة المنفصلة)",
             "custody_ack": "نص «إقرار وتعهد» في بيان العهد المسلمة من IT",
             "other_custody_items": "بنود «عهد أخرى» في بيان العهد (سطر لكل بند)",
             "work_start_text": "نص «إقرار استلام عمل» (بعد البيانات وقبل التوقيع)",
             "print_footer_note": "ملاحظة أسفل كل المطبوعات (اختياري)"}
DEFAULT_CUSTODY_ACK = ("أقر بأن العهد المذكورة أعلاه قد تم تسليمها لي في حالة جيدة وكاملة، وأنني أتعهد بالمحافظة عليها "
                       "واستخدامها بالطريقة المناسبة، كما أقر بمسؤوليتي الكاملة عن أي أضرار أو خسائر مالية أو خلافه قد "
                       "تترتب على عدم التزامي أو مخالفتي لما جاء في هذا الإقرار وتعويض الشركة عن كافة الأضرار والخسائر "
                       "المترتبة على ذلك.")
DEFAULT_OTHER_ITEMS = "ارتق\nبصمة الدخول\nبصمة الحضور"
DEFAULT_WORK_START = ("وأُقرّ بصحة ودقة جميع البيانات والمعلومات الواردة الخاصة بي وبتعييني طبقًا للوظيفة المذكورة. "
                      "كما أُقرّ بأنني قد اطلعت على لوائح ونظم العمل الداخلية وسياسات الشركة، ووافقت عليها موافقةً كاملة "
                      "دون أي تحفظ، وذلك وفقًا لأحكام قانون العمل واللوائح المنظمة له.\n"
                      "وأتعهد بالالتزام بكافة التعليمات والقرارات الإدارية الصادرة من الشركة، سواء السابقة أو التي قد تصدر "
                      "مستقبلاً.\n"
                      "كما أتعهد ببذل أقصى جهدي للقيام بواجباتي ومسؤولياتي الوظيفية على أكمل وجه، والحفاظ على ممتلكات "
                      "الشركة وسرية بياناتها، والعمل بما يحقق أهدافها...\n"
                      "وهذا اقرار مني بذلك،")
AR_DAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]


class CompanyText(Base):
    __tablename__ = "company_texts"
    __table_args__ = (UniqueConstraint("company_id", "key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(40))
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


def company_text(db: Session, company_id: str | None, key: str) -> str:
    if not company_id:
        return ""
    t = db.scalar(select(CompanyText).where(CompanyText.company_id == company_id, CompanyText.key == key))
    return t.value if t else ""


esc = html.escape

BASE_CSS = """@page{size:A4;margin:12mm}*{box-sizing:border-box}body{font-family:Cairo,Tahoma,sans-serif;color:#000;margin:0;font-size:13px}
.lh-head img,.lh-foot img{width:100%;max-height:120px;object-fit:contain;display:block}
.lh-foot{margin-top:18px}
@media print{.lh-foot{position:fixed;bottom:0;left:0;right:0}.lh-pad{height:120px}.noprint{display:none}}
h1{font-size:19px;text-align:center;margin:10px 0}table{width:100%;border-collapse:collapse}td,th{border:1px solid #555;padding:6px;font-size:12.5px;vertical-align:top}
th{background:#eee}.m td{border:0;padding:3px 2px}.sig td{height:58px}.box{display:inline-block;width:12px;height:12px;border:1px solid #000;margin-inline-end:4px;vertical-align:middle}
.muted{color:#555;font-size:11px}.noprint button{font:inherit;padding:6px 14px;margin:8px}"""


def doc_page(db: Session, company_id: str | None, title: str, body: str, extra_css: str = "",
             landscape: bool = False) -> str:
    """Wrap any printable body with the company's letterhead (if uploaded) — otherwise a plain page."""
    c = db.get(Company, company_id) if company_id else None
    head = f'<div class="lh-head"><img src="/api/files/{c.letterhead_header_file_id}" alt=""></div>' \
        if c and c.letterhead_header_file_id else (f'<p style="text-align:center;font-weight:700">{esc(c.name)}</p>' if c else "")
    note = company_text(db, company_id, "print_footer_note")
    foot = (f'<div class="lh-pad"></div><div class="lh-foot">{"<p class=muted>" + esc(note) + "</p>" if note else ""}'
            f'{f"<img src=/api/files/{c.letterhead_footer_file_id} alt=>" if c and c.letterhead_footer_file_id else ""}</div>')
    page = "@page{size:A4 landscape;margin:10mm}" if landscape else ""
    return (f'<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>{esc(title)}</title>'
            f'<style>{BASE_CSS}{page}{extra_css}</style></head><body><div class="noprint"><button onclick="print()">طباعة / حفظ PDF</button></div>'
            f'{head}{body}{foot}</body></html>')


router = APIRouter(prefix="/api", tags=["printing"])


class TextIn(BaseModel):
    value: str = Field(max_length=8000)


@router.get("/companies/{company_id}/texts")
def get_texts(company_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict:
    ensure_company(p, company_id)
    return {k: {"label": lbl, "value": company_text(db, company_id, k)} for k, lbl in TEXT_KEYS.items()}


@router.put("/companies/{company_id}/texts/{key}")
def set_text(company_id: str, key: str, body: TextIn, request: Request, p: Principal = Depends(require("system:edit")),
             db: Session = Depends(get_db)) -> dict:
    if key not in TEXT_KEYS:
        raise bad("مفتاح نص غير معروف")
    if db.get(Company, company_id) is None:
        raise not_found("شركة غير موجودة")
    t = db.scalar(select(CompanyText).where(CompanyText.company_id == company_id, CompanyText.key == key))
    if t is None:
        db.add(CompanyText(company_id=company_id, key=key, value=body.value))
    else:
        t.value = body.value
    audit.record(db, "company.text_updated", actor_id=p.user.id, entity="company", entity_id=company_id,
                 company_id=company_id, details={"key": key}, ip=client_ip(request))
    db.commit()
    return {"key": key, "value": body.value}


FORM_KINDS = ("computer_custody", "other_custody", "custody", "clearance")


@router.get("/print/forms")
def company_forms(company_id: str, p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> list[dict]:
    """The company's own uploaded forms (e.g. «بيان عهدة الحاسب الآلي», «العهدة الأخرى») — active versions."""
    from .journey import TEMPLATE_KINDS, DocumentTemplate
    if not (p.can("assets:view") or p.can("employees:view")):
        raise forbid("ليس لديك صلاحية")
    ensure_company(p, company_id)
    rows = db.scalars(select(DocumentTemplate).where(DocumentTemplate.company_id == company_id,
                                                     DocumentTemplate.active.is_(True),
                                                     DocumentTemplate.kind.in_(FORM_KINDS)))
    return [{"kind": t.kind, "label": TEMPLATE_KINDS.get(t.kind, t.kind), "title": t.title, "version": t.version,
             "file_id": t.file_id} for t in rows]


def _emp_block(db: Session, e) -> str:  # noqa: ANN001
    dep = db.get(Department, e.department_id) if e.department_id else None
    c = db.get(Company, e.company_id)
    return (f'<table class="m"><tr><td>الاسم: <b>{esc(e.full_name)}</b></td><td>الرقم الوظيفي: {esc(e.employee_code or "")}</td>'
            f'<td>التاريخ: {today().isoformat()}</td></tr><tr><td>الشركة: {esc(c.name if c else "")}</td>'
            f'<td>الإدارة / القسم: {esc(dep.name if dep else "")}</td><td>المسمى: {esc(e.job_title or "")}</td></tr></table>')


ACC_AR = {"keyboard": "كيبورد", "mouse": "ماوس", "headset": "سماعة", "cables": "كابلات", "pad": "بادة", "charger": "شاحن"}


COMPUTER_CATS = {"LTP", "CMP", "CMPM", "CMPH", "AIO", "MAC"}
FORM_CSS = """.hdr{display:flex;justify-content:space-between;align-items:center;margin:4px 0 10px}
.hdr h1{font-size:16px;margin:0;text-decoration:underline}.kind span{margin-inline-start:18px}
.ln td{border:0;padding:5px 2px}.ln .v{border-bottom:1px dotted #000;min-width:120px;display:inline-block;padding:0 6px;font-weight:700}
.sec{font-weight:700;text-decoration:underline;margin:12px 0 6px}
.cf th{background:#dfe8f3;width:130px;text-align:center}.cf td{height:30px}
.accs{display:flex;flex-wrap:wrap;gap:10px}.accs span{white-space:nowrap}
.ack{border:0;line-height:1.9;text-align:justify}.sg td{border:0;padding:4px 2px}.sg .v{border-bottom:1px dotted #000;display:inline-block;min-width:170px}
.chk{display:inline-block;width:13px;height:13px;border:1px solid #000;vertical-align:middle;text-align:center;line-height:11px;font-size:11px;margin-inline-end:4px}"""


def _chk(on: bool) -> str:
    return f'<span class="chk">{"✓" if on else ""}</span>'


def _v(x: object) -> str:
    return f'<span class="v">{esc(str(x)) if x not in (None, "") else "&nbsp;"}</span>'


@router.get("/print/custody/{employee_id}", response_class=HTMLResponse)
def custody_statement(employee_id: str, request: Request, kind: str = "onboarding", secret: bool = False,
                      p: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> HTMLResponse:
    """«بيان العهد المسلمة/المستلمة من قسم تقنية المعلومات» — the company's own layout, filled from live data.

    Computer + accessories + specs, mobile, e-mails (from the mailbox register), phone numbers (SIMs),
    «عهد أخرى» rows (editable per company), the undertaking text, and empty signature lines.
    The device «الرمز» stays empty unless `secret=1` is asked by someone with assets:view_secrets (audited)."""
    from .assets import Asset, AssetLink
    from .mailboxes import Mailbox
    from .people import get_employee
    from .technical import Sim
    if not (p.can("assets:print") or p.can("employees:print")):
        raise forbid("ليس لديك صلاحية الطباعة")
    if kind not in ("onboarding", "offboarding"):
        raise bad("kind: onboarding | offboarding")
    if secret and not p.can("assets:view_secrets"):
        raise forbid("طباعة رمز الجهاز محتاجة صلاحية كلمات مرور الأجهزة")
    e = get_employee(db, employee_id)
    ensure_company(p, e.company_id)
    off = kind == "offboarding"
    c = db.get(Company, e.company_id)
    dep = db.get(Department, e.department_id) if e.department_id else None
    assets = list(db.scalars(select(Asset).where(Asset.holder_employee_id == e.id).order_by(Asset.code)))
    computers = [a for a in assets if a.category_code in COMPUTER_CATS or a.category_code.upper().startswith("CMP")]
    mobiles = [a for a in assets if a.category_code.upper().startswith("MOB")]
    pc, mob = (computers[0] if computers else None), (mobiles[0] if mobiles else None)
    used = {x.id for x in (pc, mob) if x}
    sims = list(db.scalars(select(Sim).where(Sim.holder_employee_id == e.id).order_by(Sim.code)))
    mails = list(db.scalars(select(Mailbox).where(Mailbox.employee_id == e.id, Mailbox.status == "active")
                            .order_by(Mailbox.is_primary.desc(), Mailbox.address)))
    pin = ""
    if secret and pc and (pc.vault_ref or "").startswith("vault:"):
        from ..secrets_store import store_for
        pin = store_for(request.app.state.settings).get(pc.vault_ref[6:]) or ""
    acc = (pc.accessories or {}) if pc else {}
    specs = " / ".join(x for x in ((pc.brand + " " + pc.model).strip() if pc else "", pc.cpu if pc else "",
                                   f"RAM {pc.ram_gb}GB" if pc and pc.ram_gb else "", pc.storage if pc else "",
                                   pc.gpu if pc else "", f"SN {pc.serial}" if pc and pc.serial else "") if x)
    mob_acc = "، ".join(v for k, v in ACC_AR.items() if (mob.accessories or {}).get(k)) if mob else ""
    pair = lambda xs, i: esc(xs[i]) if len(xs) > i else ""  # noqa: E731
    mail_list, phones = [m.address for m in mails], [s.number for s in sims if s.number]

    # «عهد أخرى»: company-editable rows, then every remaining item in custody (monitors, other devices, SIMs…)
    other_rows = []
    labels = [x.strip() for x in (company_text(db, e.company_id, "other_custody_items") or DEFAULT_OTHER_ITEMS)
              .splitlines() if x.strip()]
    for lab in labels:
        user = e.biometric_code if ("حضور" in lab and e.biometric_code) else ""
        other_rows.append((lab, f"USER: {user}" if user else "USER:", "", ""))
    for a in assets:
        if a.id in used:
            continue
        other_rows.append((f"{a.code} — {(a.brand + ' ' + a.model).strip()}", f"SN: {a.serial}" if a.serial else "", "", ""))
    if pc:
        for lk in db.scalars(select(AssetLink).where(AssetLink.parent_asset_id == pc.id, AssetLink.unlinked_at.is_(None))):
            m = db.get(Asset, lk.child_asset_id)
            if m and m.holder_employee_id != e.id:
                other_rows.append((f"{m.code} — شاشة مربوطة بالجهاز", "", "", ""))
    for s in sims[2:]:
        other_rows.append((f"{s.code} — شريحة", s.number, "", ""))
    other_rows.append(("", "", "", ""))
    other_html = "".join(f"<tr><td style='text-align:center'>{i}</td><td>{esc(a)}</td><td dir=ltr>{esc(b)}</td>"
                         f"<td>{esc(cc)}</td><td>{esc(d)}</td></tr>" for i, (a, b, cc, d) in enumerate(other_rows, 1))
    accs = "".join(f"<span>{_chk(bool(acc.get(k)))}{v}</span>" for k, v in ACC_AR.items())
    ack = company_text(db, e.company_id, "custody_ack") or DEFAULT_CUSTODY_ACK
    title = "بيان العهد المسلمة/المستلمة من قسم تقنية المعلومات - IT"
    body = (f"<div class=hdr><h1>{title}</h1><div class=kind><span>{_chk(not off)} تعيين جديد</span>"
            f"<span>{_chk(off)} إخلاء طرف</span></div></div>"
            f"<table class=ln><tr><td>التاريخ: {_v(today().strftime('%d / %m / %Y'))}</td><td></td></tr>"
            f"<tr><td>اسم الموظف: {_v(e.full_name)}</td><td>الرقم الوظيفي: {_v(e.employee_code)}</td></tr>"
            f"<tr><td>الشركة: {_v(c.name if c else '')}</td><td>الإدارة: {_v('')} &nbsp; القسم: {_v(dep.name if dep else '')}</td></tr></table>"
            f"<div class=sec>قائمة العهد:</div>"
            f"<table class=cf><tr><th rowspan=2>الحاسب الآلي:</th><th style='width:auto'>كود الجهاز</th><th style='width:auto'>الملحقات</th></tr>"
            f"<tr><td><b>{esc(pc.code) if pc else ''}</b></td><td><div class=accs>{accs}</div></td></tr>"
            f"<tr><th>USER</th><td>{esc(pc.hostname) if pc else ''}</td><td>الرمز: <span dir=ltr>{esc(pin)}</span></td></tr>"
            f"<tr><th>المواصفات:</th><td colspan=2 dir=auto>{esc(specs)}</td></tr>"
            f"<tr><th>الموبايل:</th><td>الموديل: {esc(((mob.brand + ' ' + mob.model).strip() + (' — ' + mob.code)) if mob else '')}</td>"
            f"<td>الملحقات: {esc(mob_acc)}</td></tr>"
            f"<tr><th>البريد الالكتروني:</th><td dir=ltr>(1) {pair(mail_list, 0)}</td><td dir=ltr>(2) {pair(mail_list, 1)}</td></tr>"
            f"<tr><th>رقم الهاتف:</th><td dir=ltr>(1) {pair(phones, 0)}</td><td dir=ltr>(2) {pair(phones, 1)}</td></tr></table>"
            f"<div class=sec>عهد أخرى:</div><table><tr><th style='width:30px'>م</th><th>العهـــدة</th><th>اسم المستخدم</th>"
            f"<th>رقم الدخول</th><th>ملاحظات</th></tr>{other_html}</table>"
            + ("<div class=sec>مراجعة الاسترجاع:</div><table><tr><th>العهدة</th><th>استُرجع</th><th>مفقود</th><th>تالف</th>"
               "<th>لا ينطبق</th></tr>" + "".join(
                   f"<tr><td>{esc(a.code)} — {esc((a.brand + ' ' + a.model).strip())}</td>" + "<td><span class=box></span></td>" * 4
                   + "</tr>" for a in assets) + "".join(
                   f"<tr><td>{esc(m.address)}</td>" + "<td><span class=box></span></td>" * 4 + "</tr>" for m in mails)
               + "</table>" if off else "")
            + f"<div class=sec>اقرار و تعهد:</div><p class=ack>{esc(ack)}</p>"
            f"<table class=sg><tr><td><b><u>مسئول قسم تقنية المعلومات:</u></b></td><td><b><u>الموظف المستلم:</u></b></td></tr>"
            f"<tr><td>الاسم: <span class=v></span></td><td>الاسم: <span class=v>{esc(e.full_name)}</span></td></tr>"
            f"<tr><td>المسمى الوظيفي: <span class=v></span></td><td>المسمى الوظيفي: <span class=v>{esc(e.job_title or '')}</span></td></tr>"
            f"<tr><td>التوقيع: <span class=v></span></td><td>التوقيع: <span class=v></span></td></tr></table>")
    audit.record(db, "print.custody_statement", actor_id=p.user.id, entity="employee", entity_id=e.id,
                 company_id=e.company_id, details={"kind": kind, "with_device_pin": bool(pin)}, ip=client_ip(request))
    db.commit()
    return HTMLResponse(doc_page(db, e.company_id, title, body, FORM_CSS))


@router.get("/print/work-start/{employee_id}", response_class=HTMLResponse)
def work_start(employee_id: str, request: Request, p: Principal = Depends(require("employees:print")),
               db: Session = Depends(get_db)) -> HTMLResponse:
    """«إقرار استلام عمل» — national ID, address and salary are filled only for employees:view_sensitive."""
    from .people import EmployeeSensitive, get_employee
    e = get_employee(db, employee_id)
    ensure_company(p, e.company_id)
    c = db.get(Company, e.company_id)
    s = db.get(EmployeeSensitive, e.id) if p.can("employees:view_sensitive") else None
    d = e.hire_date
    text = company_text(db, e.company_id, "work_start_text") or DEFAULT_WORK_START
    paras = "".join(f"<p>{esc(x)}</p>" for x in text.splitlines() if x.strip())
    css = (".ws{font-size:15px;line-height:2.3}.ws h1{font-size:22px;text-decoration:underline}"
           ".ws .v{border-bottom:1px dotted #000;display:inline-block;min-width:150px;padding:0 6px;font-weight:700}"
           ".ws .txt p{margin:4px 0;text-align:justify}.ws .sg{margin-top:26px}")
    body = (f"<div class=ws><h1>إقرار استلام عمل</h1>"
            f"<div>انه فى يوم : {_v(AR_DAYS[d.weekday()] if d else '')} الموافق {_v(d.strftime('%d / %m / %Y') if d else '')}</div>"
            f"<div>اقر انا / {_v(e.full_name)} بطاقة قومى رقم {_v(s.national_id if s else '')}</div>"
            f"<div>والمقيم فى {_v(s.address if s else '')}</div>"
            f"<div>باننى استلمت العمل لدى شركة {_v(c.name if c else '')} للعمل علي مشروع {_v(e.project)}</div>"
            f"<div>بوظيفه {_v(e.job_title)}</div>"
            f"<div>وذلك ابتداء من {_v(d.strftime('%d / %m / %Y') if d else '')} الموافق {_v(AR_DAYS[d.weekday()] if d else '')}</div>"
            f"<div>الراتب المتفق عليه : {_v(s.salary if s else '')}</div>"
            f"<div>الكود الوظيفي : {_v(e.employee_code)} &nbsp; كود البصمة: {_v(e.biometric_code)}</div>"
            f"<div class=txt style='margin-top:14px'>{paras}</div>"
            f"<div class=sg><div>اسم الموظف / {_v(e.full_name)}</div><div>الوظيفة / {_v(e.job_title)}</div>"
            f"<div>التاريخ / {_v('')}</div><div>التوقيع / {_v('')}</div></div></div>")
    audit.record(db, "print.work_start", actor_id=p.user.id, entity="employee", entity_id=e.id,
                 company_id=e.company_id, details={"sensitive": s is not None}, ip=client_ip(request))
    db.commit()
    return HTMLResponse(doc_page(db, e.company_id, "إقرار استلام عمل — " + e.full_name, body, css))


APP_CSS = """.af{font-size:12.5px;direction:ltr}.af .ar,.af h3,.af p{direction:rtl}.af .top{display:flex;justify-content:space-between;align-items:flex-end;border-bottom:3px double #000;padding-bottom:4px}
.af .code{border:1.5px solid #000;border-radius:8px;padding:4px 16px;font-weight:700;min-width:110px;text-align:center}
.af .code b{font-size:20px;margin-inline-end:8px}.af h1{font-size:18px;margin:0}
.af .bi{display:flex;justify-content:space-between;gap:8px;padding:7px 0;border-bottom:1px dotted #999}
.af .bi .en{direction:ltr;font-weight:700;min-width:150px}.af .bi .val{flex:1;text-align:center;font-weight:700}
.af .bi .ar{font-weight:700;min-width:120px;text-align:right}
.af .nid{display:inline-flex;direction:ltr}.af .nid span{border:1px solid #000;width:22px;height:24px;display:inline-grid;place-items:center}
.af th{background:#f2f2f2}.af td{height:26px}.af .pg{page-break-before:always}.af .vert{writing-mode:vertical-rl;transform:rotate(180deg);font-weight:700;width:28px;text-align:center}"""


def _cell(x: object) -> str:
    return esc(str(x)) if x not in (None, "") else ""


def _row_vals(item: object, keys: list[tuple[str, ...]]) -> list[str]:
    """Experience/course/reference rows may be dicts (any of several key spellings) or plain text."""
    if isinstance(item, dict):
        low = {str(k).lower(): v for k, v in item.items()}
        return [_cell(next((low[k] for k in ks if k in low and low[k] not in (None, "")), "")) for ks in keys]
    return [_cell(item)] + [""] * (len(keys) - 1)


def _rows(items: list, keys: list[tuple[str, ...]], min_rows: int) -> str:
    out = ["<tr>" + "".join(f"<td>{v}</td>" for v in _row_vals(it, keys)) + "</tr>" for it in (items or [])]
    out += ["<tr>" + "<td></td>" * len(keys) + "</tr>"] * max(0, min_rows - len(out))
    return "".join(out)


def application_html(db: Session, a, sensitive: bool, interviews: list | None = None,  # noqa: ANN001
                     company_id: str | None = None) -> str:
    """F01 «Application for Employment» — same layout as the company form; empty when `a` is None."""
    g = (lambda f: getattr(a, f, "") or "") if a is not None else (lambda f: "")  # noqa: E731
    sg = (lambda f: g(f)) if sensitive else (lambda f: "")  # noqa: E731
    dob = sg("birth_date")
    nid = str(sg("national_id"))[:14]
    nid_boxes = "".join(f"<span>{esc(nid[i]) if i < len(nid) else ''}</span>" for i in range(14))

    def bi(en: str, ar: str, val: object) -> str:
        return f"<div class=bi><span class=en>{en} :</span><span class=val>{_cell(val)}</span><span class=ar>: {ar}</span></div>"

    exp = _rows(g("experience") or [], [("employer", "name", "company", "name_of_employer"), ("from",), ("to",),
                                        ("position", "duties", "title"), ("salary",), ("reason", "reason_for_leaving")], 4)
    crs = _rows(g("courses") or [], [("course", "name", "title"), ("from",), ("to",), ("trainer",), ("notes", "note")], 3)
    refs = _rows(g("references") or [], [("name",), ("position", "title"), ("phone", "phone_number")], 2)
    hr = next((i for i in (interviews or []) if i.kind == "hr"), None)
    tech = next((i for i in (interviews or []) if i.kind == "technical"), None)
    hf = (hr.hr_fields or {}) if hr and sensitive else {}
    sc = (tech.scores or {}) if tech else {}
    hr_rows = "".join(f"<tr>{f'<td rowspan=6 class=vert>Feedback / interviewer: {_cell(hr.interviewer if hr else chr(32))}</td>' if i == 0 else ''}"
                      f"<td style='height:40px'>{_cell(hf.get(k) if k != 'note' else (hr.notes if hr and sensitive else ''))}</td>"
                      f"<th style='width:110px'>{lbl}</th></tr>"
                      for i, (k, lbl) in enumerate([("past_exp", "Past EXP"), ("why", "Why"), ("strength", "Strength"),
                                                    ("weakness", "Weakness"), ("future_plans", "Future plans"), ("note", "Note")]))
    tech_rows = "".join(f"<tr>{f'<td rowspan=5 class=vert>Feedback / interviewer: {_cell(tech.interviewer if tech else chr(32))}</td>' if i == 0 else ''}"
                        f"<td style='width:60px;text-align:center'>{_cell(sc.get(k))} / 10</td>"
                        f"<td style='height:40px'>{_cell(tech.notes) if (tech and k == 'note') else ''}</td>"
                        f"<th style='width:120px'>{lbl}</th></tr>"
                        for i, (k, lbl) in enumerate([("performance", "Performance"), ("behavior", "Behavior"),
                                                      ("team_dynamic", "Team Dynamic"), ("skills", "Skills"), ("note", "Note")]))
    return (f"<div class=af><div class=top><div></div><h1>Application for Employment</h1>"
            f"<div class=code><b>APP CODE</b>{_cell(g('candidate_id'))}</div></div>"
            f"<table style='margin-top:6px'><tr><th>Date<br>التاريخ</th><th>Position Applied For<br>الوظيفة المطلوبة</th>"
            f"<th>Expected Salary<br>الراتب المتوقع</th></tr><tr><td style='text-align:center'>{_cell(g('application_date'))}</td>"
            f"<td style='text-align:center'>{_cell(g('position'))}</td><td style='text-align:center'>{_cell(sg('expected_salary'))}</td></tr></table>"
            + bi("Name", "الاسم", g("full_name")) + bi("Date of Birth", "تاريخ الميلاد", dob)
            + bi("Status of Military Service", "الموقف التجنيدي", g("military_status"))
            + bi("Marital Status", "الوضع الاجتماعي", g("marital_status")) + bi("Present Address", "العنوان الحالي", sg("address"))
            + f"<div class=bi><span class=en>National ID</span><span class=val><span class=nid>{nid_boxes}</span></span><span class=ar>: الرقم القومي</span></div>"
            + bi("Telephone No.", "رقم التليفون", g("phone")) + bi("E-mail", "البريد الإلكتروني", g("email"))
            + f"<div class=bi><span class=en>Education :</span><span class=val>{_cell(g('qualification'))}</span>"
              f"<span>سنة التخرج : <b>{_cell(g('graduation_year'))}</b></span><span class=ar>: المؤهل</span></div>"
            + f"<h3 style='text-align:center;margin:10px 0 4px'>سجل العمل</h3><table><tr><th>Name of employer<br>اسم جهة العمل</th>"
              f"<th>From</th><th>To</th><th>Position &amp; duties<br>الوظيفه</th><th>Salary<br>المرتب</th>"
              f"<th>Reason for leaving<br>سبب ترك العمل</th></tr>{exp}</table>"
            + f"<h3 style='text-align:center;margin:10px 0 4px'>الدورات التدريبية</h3><table><tr><th>Training Course<br>اسم التدريب</th>"
              f"<th>From</th><th>To</th><th>Trainer المحاضر</th><th>Notes</th></tr>{crs}</table>"
            + f"<div class=pg></div><h3 style='text-align:center;margin:6px 0 4px'>REFRENCE اشخاص يمكن التواصل اليهم من الجهة السابقة</h3>"
              f"<table><tr><th>Name<br>الاسم</th><th>Position<br>الوظيفة</th><th>Phone number<br>رقم التليفون</th></tr>{refs}</table>"
            + f"<p>هل لديك notice period فالشركه الحاليه ؟ماهي مدتها ؟ <b>{_cell(g('notice_period'))}</b></p>"
            + "<hr style='border:1.5px solid #000'><b>FOR COMPANY USE ONLY</b>"
            + f"<h3 style='text-align:center;margin:8px 0 4px'>HR Feedback</h3><table dir=ltr>{hr_rows}</table>"
            + f"<h3 style='text-align:center;margin:8px 0 4px'>Technical Feedback</h3><table dir=ltr>{tech_rows}</table>"
            + "<h3 style='text-align:center;margin:8px 0 4px'>Note <small>(Third interview or another situation)</small>:</h3>"
              "<div style='border:1px solid #000;height:60px'></div></div>")


@router.get("/print/application/{app_id}", response_class=HTMLResponse)
def print_application(app_id: str, request: Request, p: Principal = Depends(require("employees:print")),
                      db: Session = Depends(get_db)) -> HTMLResponse:
    """F01 filled from the candidate record + interviews. Salary/ID/DOB/address/HR notes only for view_sensitive."""
    from .journey import Application, Interview
    a = db.get(Application, app_id)
    if a is None:
        raise not_found("الطلب غير موجود")
    ensure_company(p, a.company_id)
    ivs = list(db.scalars(select(Interview).where(Interview.application_id == a.id).order_by(Interview.created_at.desc())))
    sens = p.can("employees:view_sensitive")
    audit.record(db, "print.application", actor_id=p.user.id, entity="application", entity_id=a.id,
                 company_id=a.company_id, details={"sensitive": sens}, ip=client_ip(request))
    db.commit()
    return HTMLResponse(doc_page(db, a.company_id, "استمارة التقديم " + a.candidate_id,
                                 application_html(db, a, sens, ivs), APP_CSS))


@router.get("/print/application-blank", response_class=HTMLResponse)
def print_application_blank(company_id: str | None = None, p: Principal = Depends(require("employees:print")),
                            db: Session = Depends(get_db)) -> HTMLResponse:
    """Empty F01 for walk-in candidates (with the chosen company's letterhead)."""
    if company_id:
        ensure_company(p, company_id)
    return HTMLResponse(doc_page(db, company_id, "استمارة التقديم", application_html(db, None, False), APP_CSS))


@router.get("/print/custody-item/{asset_id}", response_class=HTMLResponse)
def separate_custody(asset_id: str, request: Request, employee_id: str | None = None,
                     p: Principal = Depends(require("assets:print")), db: Session = Depends(get_db)) -> HTMLResponse:
    """F06 — separate custody form for one item handed over at any time."""
    from .assets import ALLOCATION, CONDITION, Asset
    from .people import get_employee
    a = db.get(Asset, asset_id)
    if a is None:
        raise not_found("الأصل غير موجود")
    ensure_company(p, a.company_id)
    e = get_employee(db, employee_id or a.holder_employee_id) if (employee_id or a.holder_employee_id) else None
    c = db.get(Company, a.company_id)
    terms = company_text(db, a.company_id, "custody_terms")
    body = (f"<h1>نموذج عهدة منفصلة</h1><table class=m><tr><td>التاريخ: {today().isoformat()}</td><td>رقم العنصر: <b>{esc(a.code)}</b></td></tr>"
            f"<tr><td>الطرف الأول: {esc(c.name if c else '')}</td><td>الطرف الثاني: <b>{esc(e.full_name if e else '')}</b> {esc(e.employee_code or '') if e else ''}</td></tr></table><br>"
            f"<table><tr><th>رقم العنصر</th><th>وصف العنصر</th><th>السيريال</th><th>الكمية</th><th>تاريخ التسليم</th><th>حالة الأصل وقت التسليم</th><th>ملاحظات</th></tr>"
            f"<tr><td>{esc(a.code)}</td><td>{esc((a.brand + ' ' + a.model).strip())}</td><td dir=ltr>{esc(a.serial)}</td><td>1</td>"
            f"<td>{today().isoformat()}</td><td>{esc(CONDITION.get(a.tech_condition, ''))} / {esc(ALLOCATION.get(a.allocation_status, ''))}</td><td></td></tr></table>"
            f"<h3>الشروط والأحكام</h3><div style='white-space:pre-wrap;border:1px solid #999;padding:8px;min-height:60px'>"
            f"{esc(terms) if terms else '<span class=muted>لم تُضف شروط لهذه الشركة بعد — من إدارة النظام ← الشركات ← النصوص.</span>'}</div>"
            f"<p class=muted>أي خصومات أو مسؤوليات مذكورة هي مرجع لسياسة الشركة ولا ينفذ النظام خصمًا ماليًا آليًا.</p>"
            f"<table class=sig><tr><th>الطرف الأول (IT)</th><th>الطرف الثاني (المستلم)</th><th>اعتماد</th></tr><tr><td></td><td></td><td></td></tr></table>")
    audit.record(db, "print.separate_custody", actor_id=p.user.id, entity="asset", entity_id=a.id,
                 company_id=a.company_id, ip=client_ip(request))
    db.commit()
    return HTMLResponse(doc_page(db, a.company_id, "نموذج عهدة منفصلة " + a.code, body))

