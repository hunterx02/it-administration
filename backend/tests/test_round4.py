"""Round 4 — device passwords from Excel (encrypted, audited reveal), category change, company custody forms."""
from __future__ import annotations

import io
from datetime import date

from openpyxl import Workbook

from .conftest import H
from .test_modules import asset, emp, org, upload, user  # noqa: F401


def _xlsx(rows):
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def test_device_password_set_reveal_audited_and_hidden(app, admin, org):
    a = asset(admin, org, "LTP")
    assert admin.put(f"/api/assets/{a['id']}/password", json={"password": "P@ss-Device-1"}, headers=H).status_code == 200
    d = admin.get(f"/api/assets/{a['id']}").json()
    assert d["has_password"] is True and "P@ss-Device-1" not in str(d) and "asset-pw-" not in str(d)
    assert "P@ss-Device-1" not in admin.get("/api/assets").text
    r = admin.post(f"/api/assets/{a['id']}/password/reveal", headers=H).json()
    assert r["password"] == "P@ss-Device-1"
    actions = [e["action"] for e in admin.get("/api/audit").json()]
    assert "asset.password_revealed" in actions and "P@ss-Device-1" not in str(admin.get("/api/audit").json())
    assert "P@ss-Device-1" not in app.state.settings.secrets_path.read_text(encoding="utf-8")
    staff = user(app, admin, "it_staff")
    assert staff.post(f"/api/assets/{a['id']}/password/reveal", headers=H).status_code == 403
    assert staff.put(f"/api/assets/{a['id']}/password", json={"password": "x"}, headers=H).status_code == 403
    # editing the asset form never clobbers the vault reference
    v = admin.get(f"/api/assets/{a['id']}").json()["version"]
    admin.patch(f"/api/assets/{a['id']}", json={"version": v, "model": "X", "vault_ref": "hack"}, headers=H)
    assert admin.post(f"/api/assets/{a['id']}/password/reveal", headers=H).json()["password"] == "P@ss-Device-1"
    assert admin.delete(f"/api/assets/{a['id']}/password", headers=H).json()["has_password"] is False


def test_passwords_imported_from_excel_encrypted_only_on_commit(app, admin, org):
    data = _xlsx([["كود الأصل", "الفئة", "الشركة", "الموديل", "PSWD"],
                  ["", "LTP", "شركة اختبار", "Dell", "Secret-From-Sheet-1"],
                  ["", "CMP", "شركة اختبار", "HP", ""]])
    f = {"file": ("a.xlsx", data, "application/octet-stream")}
    pre = admin.post("/api/import/assets", files=f, data={"commit": "false"}, headers=H).json()
    assert any("🔑" in r["message"] for r in pre["rows"])
    sp = app.state.settings.secrets_path
    assert not sp.exists() or "asset-pw-" not in sp.read_text(encoding="utf-8")  # preview stores nothing
    admin.post("/api/import/assets", files=f, data={"commit": "true"}, headers=H)
    dell = [a for a in admin.get("/api/assets").json() if a["model"] == "Dell"][0]
    assert dell["has_password"] is True
    assert admin.post(f"/api/assets/{dell['id']}/password/reveal", headers=H).json()["password"] == "Secret-From-Sheet-1"
    assert "Secret-From-Sheet-1" not in sp.read_text(encoding="utf-8")
    exp = admin.get("/api/export/assets?fmt=csv").text
    assert "Secret-From-Sheet-1" not in exp and "🔒 محفوظ" in exp
    # update existing asset's password by re-importing with its code
    upd = _xlsx([["كود الأصل", "PSWD"], [dell["code"], "Rotated-2"]])
    r = admin.post("/api/import/assets", files={"file": ("u.xlsx", upd, "application/octet-stream")},
                   data={"commit": "true"}, headers=H).json()
    assert r["counts"]["update"] == 1
    assert admin.post(f"/api/assets/{dell['id']}/password/reveal", headers=H).json()["password"] == "Rotated-2"


def test_import_passwords_ignored_without_permission(app, admin, org):
    itm = user(app, admin, "it_staff")
    data = _xlsx([["كود الأصل", "الفئة", "الشركة", "الموديل", "PSWD"], ["", "LTP", "شركة اختبار", "Lenovo", "nope-123"]])
    r = itm.post("/api/import/assets", files={"file": ("a.xlsx", data, "application/octet-stream")},
                 data={"commit": "true"}, headers=H).json()
    assert r["counts"]["new"] == 1 and "اتجاهل" in r["rows"][0]["message"]
    a = [x for x in admin.get("/api/assets").json() if x["model"] == "Lenovo"][0]
    assert a["has_password"] is False


def test_change_category_new_code_old_kept(admin, org):
    a = asset(admin, org, "CMP")
    asset(admin, org, "CMPM")
    r = admin.post(f"/api/assets/{a['id']}/category", json={"category_code": "CMPM", "reason": "جهاز تصميم"}, headers=H)
    assert r.status_code == 200 and r.json()["code"] == "ZW-CMPM-002" and r.json()["category_code"] == "CMPM"
    ev = [e for e in admin.get(f"/api/assets/{a['id']}").json()["events"] if e["kind"] == "recategorized"][0]
    assert "ZW-CMP-001" in ev["notes"] and "ZW-CMPM-002" in ev["notes"]
    assert admin.post(f"/api/assets/{a['id']}/category", json={"category_code": "CMPM", "reason": "xxx"},
                      headers=H).status_code == 400
    assert admin.post(f"/api/assets/{a['id']}/category", json={"category_code": "NOPE", "reason": "xxx"},
                      headers=H).status_code == 400


def test_swap_device_atomic(admin, org):
    e = emp(admin, org)
    old, new = asset(admin, org, "CMP"), asset(admin, org, "CMPM")
    admin.post(f"/api/assets/{old['id']}/handover", json={"employee_id": e["id"]}, headers=H)
    r = admin.post(f"/api/assets/{old['id']}/swap", json={"new_asset_id": new["id"], "old_result": "damaged"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["old"]["allocation_status"] == "in_stock" and r.json()["old"]["tech_condition"] == "damaged"
    assert r.json()["new"]["holder_employee_id"] == e["id"]
    # frozen employee: swap refused and NOTHING changes (old device stays with them)
    third = asset(admin, org, "LTP")
    admin.post("/api/journey/offboarding", json={"employee_id": e["id"], "last_day": str(date.today())}, headers=H)
    r = admin.post(f"/api/assets/{new['id']}/swap", json={"new_asset_id": third["id"]}, headers=H)
    assert r.status_code == 409
    assert admin.get(f"/api/assets/{new['id']}").json()["holder_employee_id"] == e["id"]
    assert admin.get(f"/api/assets/{third['id']}").json()["holder_employee_id"] is None


def test_schema_upgrade_on_existing_database_keeps_roles(app):
    """An existing DB from an older schema must upgrade in place (regression: duplicate role permissions)."""
    from sqlalchemy import delete, select

    from app.models import Role, RolePermission, SchemaVersion
    from app.seed import SCHEMA_VERSION, seed
    with app.state.db.SessionLocal() as db:
        db.execute(delete(SchemaVersion).where(SchemaVersion.version == SCHEMA_VERSION))  # pretend: older schema
        staff = db.scalar(select(Role).where(Role.key == "it_staff"))
        db.add(RolePermission(role_id=staff.id, permission="finance:delete"))  # stale permission to be removed
        db.commit()
        seed(db)  # must not raise
        perms = {rp.permission for rp in db.scalar(select(Role).where(Role.key == "it_staff")).permissions}
        assert "finance:delete" not in perms and "assets:view" in perms
        assert db.get(SchemaVersion, SCHEMA_VERSION) is not None


def test_company_custody_forms_uploaded_and_listed(admin, org):
    for kind, title in (("computer_custody", "بيان عهدة الحاسب الآلي"), ("other_custody", "نموذج العهدة الأخرى")):
        fid = upload(admin, "document_template", name="form.docx", data=b"PK\x03\x04docx", company=org["company"])
        r = admin.post("/api/journey/templates", json={"company_id": org["company"], "kind": kind, "title": title,
                                                       "file_id": fid}, headers=H)
        assert r.status_code == 201
    forms = admin.get(f"/api/print/forms?company_id={org['company']}").json()
    assert {f["kind"] for f in forms} == {"computer_custody", "other_custody"}
    assert admin.get(f"/api/files/{forms[0]['file_id']}").status_code == 200
