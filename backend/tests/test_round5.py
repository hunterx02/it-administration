"""Round 5 — automatic employee codes and the e-mail accounts register (codes, owners, passwords, offboarding)."""
from __future__ import annotations

import io
from datetime import date, timedelta

from openpyxl import Workbook

from .conftest import H
from .test_modules import emp, org, user  # noqa: F401


def _xlsx(rows):
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def _mail(admin, org, address, **kw):
    r = admin.post("/api/mailboxes", json={"company_id": org["company"], "address": address, **kw}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


def test_employee_codes_automatic_unique_and_backfill(app, admin, org):
    a, b = emp(admin, org, "أحمد"), emp(admin, org, "منى", gender="female")
    assert a["employee_code"] == "ZW-EMP-0001" and b["employee_code"] == "ZW-EMP-0002"
    manual = admin.post("/api/employees", json={"company_id": org["company"], "full_name": "يدوي",
                                                "employee_code": "HR-77"}, headers=H).json()
    assert manual["employee_code"] == "HR-77"
    # legacy rows without a code get one from the backfill; existing codes never change
    from app.modules.people import Employee  # noqa: PLC0415
    with app.state.db.SessionLocal() as db:
        db.get(Employee, b["id"]).employee_code = None
        db.commit()
    r = admin.post("/api/employees/assign-codes", headers=H).json()
    assert r["assigned"] == 1 and r["codes"] == ["ZW-EMP-0002"]
    assert admin.get(f"/api/employees/{a['id']}").json()["employee_code"] == "ZW-EMP-0001"
    # Excel import without a code -> automatic code
    data = _xlsx([["الاسم", "الكود الوظيفي", "الشركة"], ["من الإكسل", "", "شركة اختبار"]])
    admin.post("/api/import/employees", files={"file": ("e.xlsx", data, "application/octet-stream")},
               data={"commit": "true"}, headers=H)
    new = [e for e in admin.get("/api/employees").json() if e["full_name"] == "من الإكسل"][0]
    assert new["employee_code"] == "ZW-EMP-0003"


def test_mailbox_codes_owner_primary_and_unique(app, admin, org):
    e = emp(admin, org, "سارة", gender="female")
    m1 = _mail(admin, org, "Sara@Example.COM", employee_id=e["id"], provider="Google Workspace")
    assert m1["code"] == "ZW-MAIL-001" and m1["address"] == "sara@example.com"
    assert m1["employee_name"] == "سارة" and m1["is_primary"] is True  # employee had no e-mail -> primary
    assert admin.get(f"/api/employees/{e['id']}").json()["email"] == "sara@example.com"
    assert admin.post("/api/mailboxes", json={"company_id": org["company"], "address": "sara@example.com"},
                      headers=H).status_code == 409
    assert admin.post("/api/mailboxes", json={"company_id": org["company"], "address": "not-an-email"},
                      headers=H).status_code == 400
    m2 = _mail(admin, org, "sales@example.com", kind="shared", employee_id=e["id"])
    assert m2["code"] == "ZW-MAIL-002" and m2["is_primary"] is False
    # switch primary -> employee e-mail follows, only one primary
    r = admin.patch(f"/api/mailboxes/{m2['id']}", json={"version": m2["version"], "is_primary": True}, headers=H).json()
    assert r["is_primary"] is True
    assert admin.get(f"/api/employees/{e['id']}").json()["email"] == "sales@example.com"
    assert admin.get(f"/api/mailboxes/{m1['id']}").json()["is_primary"] is False
    # stale version rejected
    assert admin.patch(f"/api/mailboxes/{m2['id']}", json={"version": m2["version"], "notes": "x"},
                       headers=H).status_code == 409
    # move to another employee keeps history
    e2 = emp(admin, org, "خالد")
    admin.post(f"/api/mailboxes/{m1['id']}/assign", json={"employee_id": e2["id"]}, headers=H)
    d = admin.get(f"/api/mailboxes/{m1['id']}").json()
    assert d["employee_name"] == "خالد" and [x["kind"] for x in d["events"]] == ["created", "assigned", "unassigned",
                                                                                 "assigned"]
    assert [m["address"] for m in admin.get(f"/api/employees/{e['id']}").json()["mailboxes"]] == ["sales@example.com"]


def test_mailbox_password_encrypted_and_permissions(app, admin, org):
    m = _mail(admin, org, "ops@example.com", password="Mail-Secret-9")
    assert m["has_password"] is True and "Mail-Secret-9" not in admin.get("/api/mailboxes").text
    assert "Mail-Secret-9" not in app.state.settings.secrets_path.read_text(encoding="utf-8")
    assert admin.post(f"/api/mailboxes/{m['id']}/password/reveal", headers=H).json()["password"] == "Mail-Secret-9"
    assert "mailbox.password_revealed" in [x["action"] for x in admin.get("/api/audit").json()]
    staff = user(app, admin, "it_staff")  # technical:view only
    assert staff.get("/api/mailboxes").status_code == 200
    assert staff.post(f"/api/mailboxes/{m['id']}/password/reveal", headers=H).status_code == 403
    assert staff.post("/api/mailboxes", json={"company_id": org["company"], "address": "x@example.com"},
                      headers=H).status_code == 403
    assert admin.delete(f"/api/mailboxes/{m['id']}/password", headers=H).json()["has_password"] is False


def test_mailbox_blocks_offboarding_until_suspended(app, admin, org):
    e = emp(admin, org)
    m = _mail(admin, org, "leaver@example.com", employee_id=e["id"])
    spare = _mail(admin, org, "spare@example.com")
    off = admin.post("/api/journey/offboarding", json={"employee_id": e["id"],
                                                       "last_day": str(date.today() + timedelta(days=2))},
                     headers=H).json()
    assert [x["address"] for x in off["mailboxes"]] == ["leaver@example.com"]
    # frozen employee cannot receive a new account
    assert admin.post(f"/api/mailboxes/{spare['id']}/assign", json={"employee_id": e["id"]},
                      headers=H).status_code == 409
    oid = off["id"]
    r = admin.post(f"/api/journey/offboarding/{oid}/it-approve", headers=H)
    assert r.status_code == 409 and "leaver@example.com" in r.text
    assert admin.post(f"/api/journey/offboarding/{oid}/it-check",
                      json={"item_type": "mailbox", "item_ref": m["id"], "result": "returned"},
                      headers=H).status_code == 400
    admin.post(f"/api/journey/offboarding/{oid}/it-check",
               json={"item_type": "mailbox", "item_ref": m["id"], "result": "deprovisioned"}, headers=H)
    assert admin.get(f"/api/mailboxes/{m['id']}").json()["status"] == "suspended"
    assert admin.post(f"/api/journey/offboarding/{oid}/it-approve", headers=H).status_code == 200


def test_mailboxes_import_export(app, admin, org):
    e = emp(admin, org, "ياسر")
    data = _xlsx([["الإيميل", "الشركة", "الكود الوظيفي", "النوع", "المزود", "PSWD"],
                  ["yasser@example.com", "شركة اختبار", e["employee_code"], "شخصي", "Microsoft 365", "Imp-Pw-1"],
                  ["bad-address", "شركة اختبار", "", "", "", ""],
                  ["ghost@example.com", "شركة اختبار", "NO-SUCH", "", "", ""]])
    f = {"file": ("m.xlsx", data, "application/octet-stream")}
    pre = admin.post("/api/import/mailboxes", files=f, data={"commit": "false"}, headers=H).json()
    assert pre["counts"] == {"new": 1, "update": 0, "same": 0, "error": 2}
    assert admin.get("/api/mailboxes").json() == []
    admin.post("/api/import/mailboxes", files=f, data={"commit": "true"}, headers=H)
    m = admin.get("/api/mailboxes").json()[0]
    assert m["employee_name"] == "ياسر" and m["code"] == "ZW-MAIL-001" and m["has_password"] is True
    assert admin.post(f"/api/mailboxes/{m['id']}/password/reveal", headers=H).json()["password"] == "Imp-Pw-1"
    exp = admin.get("/api/export/mailboxes?fmt=csv").text
    assert "yasser@example.com" in exp and "ZW-MAIL-001" in exp and "Imp-Pw-1" not in exp


# ---------------------------------------------------------------- company paper forms
def test_it_custody_statement_company_layout(app, admin, org):
    from .test_modules import asset  # noqa: PLC0415
    e = emp(admin, org, "عمر")
    lap = asset(admin, org, "LTP", model="ThinkPad", cpu="i7", ram_gb="16", accessories={"mouse": True, "charger": True})
    admin.post(f"/api/assets/{lap['id']}/handover", json={"employee_id": e["id"]}, headers=H)
    admin.put(f"/api/assets/{lap['id']}/password", json={"password": "Pin-4455"}, headers=H)
    _mail(admin, org, "omar@example.com", employee_id=e["id"])
    sim = admin.post("/api/technical/sims", json={"country": "EG", "company_id": org["company"], "number": "01000000001"},
                     headers=H).json()
    admin.post(f"/api/technical/sims/{sim['id']}/handover", json={"employee_id": e["id"]}, headers=H)
    page = admin.get(f"/api/print/custody/{e['id']}?kind=onboarding").text
    for s in ("بيان العهد المسلمة/المستلمة من قسم تقنية المعلومات", "تعيين جديد", lap["code"], "i7", "omar@example.com",
              "01000000001", "ارتق", "بصمة الحضور", "اقرار و تعهد", "مسئول قسم تقنية المعلومات", e["employee_code"]):
        assert s in page, s
    assert "Pin-4455" not in page  # never by default
    staff = user(app, admin, "it_staff")
    assert staff.get(f"/api/print/custody/{e['id']}?secret=1").status_code == 403
    assert "Pin-4455" in admin.get(f"/api/print/custody/{e['id']}?secret=1").text
    admin.put(f"/api/companies/{org['company']}/texts/other_custody_items", json={"value": "كارت دخول المبنى"}, headers=H)
    p2 = admin.get(f"/api/print/custody/{e['id']}").text
    assert "كارت دخول المبنى" in p2 and "ارتق" not in p2


def test_work_start_ack_hides_sensitive_without_permission(app, admin, org):
    r = admin.post("/api/employees", json={"company_id": org["company"], "full_name": "هالة", "job_title": "محاسبة",
                                           "hire_date": "2026-11-16", "biometric_code": "B-77"}, headers=H).json()
    admin.put(f"/api/employees/{r['id']}/sensitive", json={"national_id": "29801011234567", "salary": "9000"}, headers=H)
    page = admin.get(f"/api/print/work-start/{r['id']}").text
    for s in ("إقرار استلام عمل", "هالة", "محاسبة", "الاثنين", "16 / 11 / 2026", "B-77", r["employee_code"],
              "وهذا اقرار مني بذلك"):
        assert s in page, s
    hr_c = user(app, admin, "hr")  # HR has view_sensitive
    assert "29801011234567" in hr_c.get(f"/api/print/work-start/{r['id']}").text
    mg = user(app, admin, "it_manager")  # employees:print but not view_sensitive
    t = mg.get(f"/api/print/work-start/{r['id']}").text
    assert "هالة" in t and "29801011234567" not in t and "9000" not in t


def test_application_form_f01(app, admin, org):
    a = admin.post("/api/journey/applications", json={
        "company_id": org["company"], "position": "Backend Dev", "full_name": "كريم", "phone": "0111",
        "qualification": "حاسبات", "graduation_year": "2020", "expected_salary": "15000", "national_id": "29901011234567",
        "experience": [{"employer": "ACME", "from": "2021", "to": "2024", "position": "Dev", "reason": "growth"}],
        "references": ["م. سامي 0100"]}, headers=H).json()
    admin.post(f"/api/journey/applications/{a['id']}/interviews", json={
        "kind": "technical", "interviewer": "م. علي", "scores": {"performance": 8, "skills": 9},
        "recommendation": "accepted", "notes": ""}, headers=H)
    page = admin.get(f"/api/print/application/{a['id']}").text
    for s in ("Application for Employment", "APP CODE", a["candidate_id"], "Backend Dev", "كريم", "15000", "ACME",
              "growth", "م. سامي", "Technical Feedback", "م. علي", "8 / 10", "FOR COMPANY USE ONLY"):
        assert s in page, s
    mg = user(app, admin, "it_manager")
    t = mg.get(f"/api/print/application/{a['id']}").text  # can print, but no view_sensitive
    assert "كريم" in t and "15000" not in t and "2990101" not in t
    blank = admin.get(f"/api/print/application-blank?company_id={org['company']}").text
    assert "Application for Employment" in blank and "كريم" not in blank


def test_clearance_form_f05_layout(app, admin, org):
    from .test_modules import asset  # noqa: PLC0415
    e = emp(admin, org, "مروان")
    admin.put(f"/api/employees/{e['id']}/sensitive", json={"national_id": "28801011234567"}, headers=H)
    lap = asset(admin, org, "LTP")
    admin.post(f"/api/assets/{lap['id']}/handover", json={"employee_id": e["id"]}, headers=H)
    off = admin.post("/api/journey/offboarding", json={"employee_id": e["id"], "reason": "نهاية خدمة",
                                                       "last_day": str(date.today() + timedelta(days=1))}, headers=H).json()
    oid = off["id"]
    admin.post(f"/api/journey/offboarding/{oid}/it-check", json={"item_type": "asset", "item_ref": lap["id"],
                                                                 "result": "returned"}, headers=H)
    admin.post(f"/api/journey/offboarding/{oid}/it-check", json={"item_type": "account", "item_ref": "vpn",
                                                                 "result": "deprovisioned"}, headers=H)
    assert admin.post(f"/api/journey/offboarding/{oid}/it-approve", headers=H).status_code == 200
    page = admin.get(f"/api/journey/offboarding/{oid}/clearance-print").text
    for s in ("Clearance Form", "Employ Data", "Related Managements", "Office Manager", "Personnel Department",
              "Financial covenant", "Keys and locks", "حاسبات آلية Computers", "Higher Management", "المدير التنفيذي",
              "مروان", e["employee_code"], "28801011234567", '<span class="chk">✓</span> نهاية خدمة'):
        assert s in page, s
    mg = user(app, admin, "it_manager")  # employees:print without view_sensitive
    assert "28801011234567" not in mg.get(f"/api/journey/offboarding/{oid}/clearance-print").text
