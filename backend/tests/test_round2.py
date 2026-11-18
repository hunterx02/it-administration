"""Round 2: editable lists, letterhead printing, custody forms, import/export, sheet links, additive migration."""
from __future__ import annotations

import io

from openpyxl import Workbook, load_workbook

from .conftest import H
from .test_modules import asset, emp, org, upload, user  # noqa: F401  (fixtures/helpers)

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def test_lists_add_rename_soft_delete(app, admin):
    lst = admin.get("/api/lookups/sim_operator_sa").json()
    assert [i["value"] for i in lst["items"]][:4] == ["Mobily", "Zain", "STC", "Friendi"]
    new = admin.post("/api/lookups/sim_operator_sa", json={"value": "Lebara"}, headers=H).json()
    assert admin.post("/api/lookups/sim_operator_sa", json={"value": "lebara"}, headers=H).status_code == 409
    admin.patch(f"/api/lookups/sim_operator_sa/{new['id']}", json={"value": "Lebara KSA"}, headers=H)
    assert "Lebara KSA" in [i["value"] for i in admin.get("/api/lookups/sim_operator_sa").json()["items"]]
    admin.delete(f"/api/lookups/sim_operator_sa/{new['id']}", headers=H)
    assert "Lebara KSA" not in [i["value"] for i in admin.get("/api/lookups/sim_operator_sa").json()["items"]]
    assert "Lebara KSA" in [i["value"] for i in admin.get("/api/lookups/sim_operator_sa?include_inactive=true").json()["items"]]
    assert "Lebara KSA" not in admin.get("/api/technical/meta").json()["lists"]["sim_operator_sa"]
    staff = user(app, admin, "it_staff")
    assert staff.post("/api/lookups/sim_operator_sa", json={"value": "X1"}, headers=H).status_code == 403
    assert staff.post("/api/lookups/location", json={"value": "الدور الثاني"}, headers=H).status_code == 201


def test_department_rename_deactivate_and_category_rename(admin, org):
    r = admin.patch(f"/api/companies/{org['company']}/departments/{org['sales']}", json={"name": "Sales Team"}, headers=H)
    assert r.status_code == 200 and r.json()["name"] == "Sales Team"
    assert admin.patch(f"/api/companies/{org['company']}/departments/{org['sales']}", json={"name": "Marketing"},
                       headers=H).status_code == 409
    assert admin.patch(f"/api/companies/{org['company']}/departments/{org['sales']}", json={"active": False},
                       headers=H).json()["active"] is False
    assert admin.patch("/api/assets/categories/LTP", json={"name_ar": "لابتوب"}, headers=H).json()["name_ar"] == "لابتوب"


def test_letterhead_on_all_printouts(admin, org):
    fid = admin.post("/api/files", data={"category": "letterhead"}, files={"file": ("head.png", PNG, "image/png")},
                     headers=H).json()["id"]
    bad = upload(admin, "invoice")
    assert admin.patch(f"/api/companies/{org['company']}", json={"letterhead_header_file_id": bad},
                       headers=H).status_code == 400
    assert admin.patch(f"/api/companies/{org['company']}", json={"letterhead_header_file_id": fid},
                       headers=H).status_code == 200
    admin.put(f"/api/companies/{org['company']}/texts/print_footer_note", json={"value": "سري — للاستخدام الداخلي"}, headers=H)
    pr = admin.post("/api/finance/purchase-requests", json={"company_id": org["company"], "requester": "IT M",
                                                            "reason": "test", "items": [{"item": "x", "qty": 1,
                                                                                         "est_unit_cost": 1}]},
                    headers=H).json()
    page = admin.get(f"/api/finance/purchase-requests/{pr['id']}/print").text
    assert f"/api/files/{fid}" in page and "سري — للاستخدام الداخلي" in page and "اعتماد الرئيس التنفيذي" in page
    rep = admin.get("/api/reports/assets.register/print", params={"filters": f'{{"company_id":"{org["company"]}"}}'}).text
    assert f"/api/files/{fid}" in rep
    assert admin.get(f"/api/files/{fid}").status_code == 200  # printable by viewers


def test_custody_forms_regular_and_separate(admin, org):
    e = emp(admin, org, name="سارة")
    a = asset(admin, org, "LTP", model="T14", accessories={"mouse": True, "charger": True})
    mon = asset(admin, org, "mon", model="P24")
    admin.post(f"/api/assets/{a['id']}/links", json={"child_asset_id": mon["id"]}, headers=H)
    admin.post(f"/api/assets/{a['id']}/handover", json={"employee_id": e["id"]}, headers=H)
    on = admin.get(f"/api/print/custody/{e['id']}?kind=onboarding").text
    assert "بيان العهد المسلمة" in on and a["code"] in on and mon["code"] in on and "ماوس" in on and "استُرجع" not in on
    off = admin.get(f"/api/print/custody/{e['id']}?kind=offboarding").text
    assert "مراجعة الاسترجاع" in off and "استُرجع" in off and "مفقود" in off
    admin.put(f"/api/companies/{org['company']}/texts/custody_terms", json={"value": "يلتزم المستلم بالمحافظة على العهدة"},
              headers=H)
    sep = admin.get(f"/api/print/custody-item/{a['id']}").text
    assert "نموذج عهدة منفصلة" in sep and "يلتزم المستلم" in sep and "سارة" in sep
    assert "password" not in on.lower()


def _xlsx(rows):
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def test_import_preview_then_commit_and_export_roundtrip(admin, org):
    e = emp(admin, org, name="عمر")
    data = _xlsx([["كود الأصل", "الفئة", "الشركة", "الموديل", "السيريال / IMEI", "القسم", "اسم الموظف", "ماوس"],
                  ["", "LTP", "شركة اختبار", "Dell 5440", "SN1", "IT", "عمر", "نعم"],
                  ["", "CMP", "شركة اختبار", "OptiPlex", "SN2", "Sales", "", ""],
                  ["", "XXX", "شركة اختبار", "bad", "", "", "", ""],
                  ["", "LTP", "شركة مش موجودة", "x", "", "", "", ""],
                  ["ZW-LTP-999", "LTP", "شركة اختبار", "x", "", "", "", ""]])
    f = {"file": ("assets.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    pre = admin.post("/api/import/assets", files=f, data={"commit": "false"}, headers=H).json()
    assert pre["counts"] == {"new": 2, "update": 0, "same": 0, "error": 3} and not pre["committed"]
    assert admin.get("/api/assets").json() == []  # preview writes nothing
    done = admin.post("/api/import/assets", files=f, data={"commit": "true"}, headers=H).json()
    assert done["counts"]["new"] == 2
    lst = admin.get("/api/assets").json()
    lap = [a for a in lst if a["model"] == "Dell 5440"][0]
    assert lap["holder_employee_id"] == e["id"] and lap["accessories"]["mouse"] is True
    assert {"IT", "Sales"} <= {d["name"] for d in admin.get("/api/companies").json()[0]["departments"]}
    # export -> edit -> re-import updates by code
    x = admin.get("/api/export/assets?fmt=xlsx")
    wb = load_workbook(io.BytesIO(x.content))
    ws = wb.active
    hdr = [c.value for c in ws[1]]
    for row in ws.iter_rows(min_row=2):
        if row[hdr.index("الموديل")].value == "OptiPlex":
            row[hdr.index("الموديل")].value = "OptiPlex 7010"
            row[hdr.index("اسم الموظف")].value = "عمر"  # custody change from a file must NOT execute
    b = io.BytesIO()
    wb.save(b)
    r = admin.post("/api/import/assets", files={"file": ("e.xlsx", b.getvalue(), "application/octet-stream")},
                   data={"commit": "true"}, headers=H).json()
    assert r["counts"]["update"] == 1 and r["counts"]["error"] == 0
    opt = [a for a in admin.get("/api/assets").json() if a["model"] == "OptiPlex 7010"][0]
    assert opt["holder_employee_id"] is None
    assert any("تغيير المستلم" in row["message"] for row in r["rows"])


def test_import_employees_suppliers_lines_and_templates(admin, org):
    data = "﻿الاسم,الشركة,القسم,النوع\nمنى,شركة اختبار,HR,أنثى\n,شركة اختبار,,\n".encode()
    r = admin.post("/api/import/employees", files={"file": ("e.csv", data, "text/csv")}, data={"commit": "true"},
                   headers=H).json()
    assert r["counts"]["new"] == 1 and r["counts"]["error"] == 1
    mona = [x for x in admin.get("/api/employees").json() if x["full_name"] == "منى"][0]
    mil = [d for d in admin.get(f"/api/employees/{mona['id']}").json()["joining_documents"] if d["doc_type"] == "military_status"]
    assert mil[0]["status"] == "not_applicable"
    s = admin.post("/api/import/suppliers", files={"file": ("s.csv", "الاسم,النشاط\nB.TECH,أجهزة\n".encode(), "text/csv")},
                   data={"commit": "true"}, headers=H).json()
    assert s["counts"]["new"] == 1
    ln = admin.post("/api/import/lines", files={"file": ("l.csv", "كود الخط,الشركة,الاسم,سعة الباقة GB,عتبة تجهيز الرصيد (GB),عتبة التجديد العاجل (GB)\n,شركة اختبار,خط 1,500,100,20\n,شركة اختبار,خط 2,100,10,50\n".encode(), "text/csv")},
                    data={"commit": "true"}, headers=H).json()
    assert ln["counts"] == {"new": 1, "update": 0, "same": 0, "error": 1}
    t = admin.get("/api/import/sims/template")
    assert t.status_code == 200 and t.content[:2] == b"PK"


def test_import_url_must_be_google_sheets(admin):
    r = admin.post("/api/import/assets", data={"url": "http://127.0.0.1:8000/secret", "commit": "false"}, headers=H)
    assert r.status_code == 400  # no SSRF to arbitrary hosts


def test_import_export_permissions(app, admin, org):
    fin = user(app, admin, "finance")
    assert fin.post("/api/import/assets", files={"file": ("a.csv", b"x", "text/csv")}, headers=H).status_code == 403
    assert fin.get("/api/export/assets").status_code == 403
    assert fin.get("/api/export/suppliers").status_code == 200


def test_sheet_links_visible_per_permission(app, admin):
    assert admin.put("/api/integrations/m02_assets_sheet", json={"link_url": "javascript:alert(1)"},
                     headers=H).status_code == 422
    admin.put("/api/integrations/m02_assets_sheet", json={"link_url": "https://docs.google.com/spreadsheets/d/abc"}, headers=H)
    admin.put("/api/integrations/m05_finance_sheet", json={"link_url": "https://docs.google.com/spreadsheets/d/fin"}, headers=H)
    st = admin.get("/api/integrations/status").json()
    assert [c for c in st["cards"] if c["key"] == "m02_assets_sheet"][0]["status"] == "not_configured"  # link != connection
    staff = user(app, admin, "it_staff")
    keys = {x["key"] for x in staff.get("/api/integrations/links").json()}
    assert keys == {"m02_assets_sheet"}  # finance link hidden from IT staff


def test_line_update_method(admin, org):
    r = admin.post("/api/technical/lines", json={"company_id": org["company"], "update_method": "we_extension"},
                   headers=H).json()
    assert r["update_method"] == "we_extension"
    assert admin.post("/api/technical/lines", json={"company_id": org["company"], "update_method": "magic"},
                      headers=H).status_code == 422


def test_additive_migration_keeps_old_data(tmp_path):
    import sqlite3

    from app.db import Database
    dbf = tmp_path / "old.db"
    con = sqlite3.connect(dbf)
    con.execute("CREATE TABLE companies (id VARCHAR(36) PRIMARY KEY, name VARCHAR(200) UNIQUE, asset_prefix VARCHAR(20),"
                " active BOOLEAN, created_at DATETIME)")
    con.execute("INSERT INTO companies VALUES ('c1','Old Co','OLD',1,'2026-01-01')")
    con.commit()
    con.close()
    d = Database(f"sqlite:///{dbf.as_posix()}")
    d.create_all()
    con = sqlite3.connect(dbf)
    cols = [r[1] for r in con.execute("PRAGMA table_info(companies)")]
    assert "letterhead_header_file_id" in cols
    assert con.execute("SELECT name, asset_prefix FROM companies").fetchone() == ("Old Co", "OLD")
