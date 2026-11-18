"""Round 3 — features ported from the Node.js copy: MFA, encrypted secrets, hash-chained audit, CSP, unique serials,
task evidence, ordered clearance, lookup rename cascade, local Excel data source, workbook templates, new reports."""
from __future__ import annotations

import io
import shutil
import time
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.security import totp_code

from .conftest import ADMIN, H, login
from .test_modules import asset, emp, org, upload, user  # noqa: F401


def test_csp_and_security_headers(client):
    r = client.get("/api/health")
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert r.headers["x-frame-options"] == "DENY"


def test_secrets_store_write_only_encrypted_outside_project(app, admin):
    r = admin.put("/api/system/secrets/google-sa-m02", json={"value": '{"type":"service_account","private_key":"SUPERSECRET"}'},
                  headers=H)
    assert r.status_code == 200
    names = admin.get("/api/system/secrets").json()
    assert [n["name"] for n in names] == ["google-sa-m02"] and "SUPERSECRET" not in str(names)
    path = app.state.settings.secrets_path
    assert path.is_file() and "SUPERSECRET" not in path.read_text(encoding="utf-8")  # encrypted at rest
    assert "outside-project" in str(path)
    assert admin.put("/api/system/secrets/bad name!", json={"value": "x"}, headers=H).status_code == 400
    itm = user(app, admin, "it_manager")
    assert itm.get("/api/system/secrets").status_code == 403  # needs manage_secrets
    assert "SUPERSECRET" not in str(admin.get("/api/audit").json())
    from app.secrets_store import store_for
    assert "SUPERSECRET" in store_for(app.state.settings).get("google-sa-m02")
    assert admin.delete("/api/system/secrets/google-sa-m02", headers=H).status_code == 200


def test_tampered_secret_file_fails_closed(app, admin):
    admin.put("/api/system/secrets/x1", json={"value": "abc"}, headers=H)
    p = app.state.settings.secrets_path
    p.write_text(p.read_text(encoding="utf-8").replace('"v": "', '"v": "AA'), encoding="utf-8")
    from app.secrets_store import SecretStoreError, store_for
    try:
        store_for(app.state.settings).get("x1")
        raise AssertionError("tampering not detected")
    except SecretStoreError:
        pass


def test_mfa_full_flow(app, client):
    assert login(client, *ADMIN).status_code == 200
    s = client.post("/api/auth/me/mfa/setup", headers=H).json()
    assert s["otpauth_uri"].startswith("otpauth://totp/")
    assert client.post("/api/auth/me/mfa/enable", json={"code": "000000"}, headers=H).status_code == 400
    now = int(time.time() // 30)
    assert client.post("/api/auth/me/mfa/enable", json={"code": totp_code(s["secret"], now)}, headers=H).status_code == 200
    assert client.get("/api/auth/me").json()["mfa_enabled"] is True
    fresh = TestClient(app)
    r = login(fresh, *ADMIN)
    assert r.status_code == 401 and r.json()["detail"] == "mfa_required"
    bad = fresh.post("/api/auth/login", json={"email": ADMIN[0], "password": ADMIN[1], "otp": "123456"}, headers=H)
    assert bad.status_code == 401
    code = totp_code(s["secret"], now + 1)
    ok = fresh.post("/api/auth/login", json={"email": ADMIN[0], "password": ADMIN[1], "otp": code}, headers=H)
    assert ok.status_code == 200
    replay = TestClient(app).post("/api/auth/login", json={"email": ADMIN[0], "password": ADMIN[1], "otp": code}, headers=H)
    assert replay.status_code == 401  # same code cannot be used twice


def test_admin_can_reset_lost_mfa(app, admin):
    u = admin.post("/api/users", json={"email": "m@t.local", "name": "MM", "password": "Passw0rd123", "role_key": "it_staff"},
                   headers=H).json()
    c = TestClient(app)
    login(c, "m@t.local", "Passw0rd123")
    s = c.post("/api/auth/me/mfa/setup", headers=H).json()
    c.post("/api/auth/me/mfa/enable", json={"code": totp_code(s["secret"], int(time.time() // 30))}, headers=H)
    assert admin.post(f"/api/users/{u['id']}/mfa-reset", headers=H).status_code == 200
    assert login(TestClient(app), "m@t.local", "Passw0rd123").status_code == 200


def test_audit_hash_chain_detects_tampering(app, admin, org):
    asset(admin, org)
    v = admin.get("/api/audit/verify").json()
    assert v["ok"] and v["checked"] >= 3
    from sqlalchemy import select, update

    from app.models import AuditEvent
    with app.state.db.SessionLocal() as db:
        target = db.scalar(select(AuditEvent).where(AuditEvent.action == "asset.created"))
        db.execute(update(AuditEvent).where(AuditEvent.id == target.id).values(details={"code": "FORGED"}))
        db.commit()
        seq = target.seq
    v = admin.get("/api/audit/verify").json()
    assert v["ok"] is False and v["broken_at"] == seq


def test_serial_must_be_unique(admin, org):
    a = asset(admin, org, serial="SN-DUP-1")
    r = admin.post("/api/assets", json={"company_id": org["company"], "category_code": "LTP", "serial": " sn-dup-1 "}, headers=H)
    assert r.status_code == 409 and a["code"] in r.text
    b = asset(admin, org, serial="SN-2")
    assert admin.patch(f"/api/assets/{b['id']}", json={"version": b["version"], "serial": "SN-DUP-1"}, headers=H).status_code == 409


def test_clearance_signatures_in_order(app, admin, org):
    e = emp(admin, org)
    off = admin.post("/api/journey/offboarding", json={"employee_id": e["id"], "last_day": str(date.today())}, headers=H).json()
    oid = off["id"]
    admin.post(f"/api/journey/offboarding/{oid}/it-check", json={"item_type": "account", "item_ref": "mail",
                                                                  "result": "deprovisioned"}, headers=H)
    admin.post(f"/api/journey/offboarding/{oid}/it-approve", headers=H)
    auths = admin.get(f"/api/journey/offboarding/{oid}").json()["authorities"]
    sig = {"status": "clear", "signer_name": "اسم", "signed_date": str(date.today())}
    r = admin.post(f"/api/journey/offboarding/{oid}/authorities/{auths[2]['id']}", json=sig, headers=H)
    assert r.status_code == 409 and auths[0]["authority"] in r.text
    assert admin.post(f"/api/journey/offboarding/{oid}/authorities/{auths[0]['id']}", json=sig, headers=H).status_code == 200


def test_lookup_rename_updates_all_records(admin, org):
    sim = admin.post("/api/technical/sims", json={"country": "SA", "company_id": org["company"], "operator": "STC"},
                     headers=H).json()
    item = [i for i in admin.get("/api/lookups/sim_operator_sa").json()["items"] if i["value"] == "STC"][0]
    r = admin.patch(f"/api/lookups/sim_operator_sa/{item['id']}", json={"value": "stc pay"}, headers=H).json()
    assert r["records_renamed"] == 1
    assert admin.get(f"/api/technical/sims/{sim['id']}").json()["operator"] == "stc pay"


def test_workbook_templates_available(admin):
    lst = admin.get("/api/templates/workbooks").json()
    assert len(lst) == 5 and any(w["label"].startswith("M02") for w in lst)
    r = admin.get(f"/api/templates/workbooks/{lst[0]['name']}")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert admin.get("/api/templates/workbooks/..%2F..%2Fapp%2Fmain.py").status_code in (400, 404)


def test_local_excel_source_edits_in_place_and_keeps_formulas(app, admin, org, tmp_path):
    src = Path(__file__).resolve().parents[1] / "templates" / "workbooks" / "IT_Assets_Google_Sheets_Template_v0.4.xlsx"
    data = src.read_bytes()
    a = asset(admin, org, "LTP", model="T14", serial="LX-1")
    r = admin.post("/api/system/integrations/m02_assets_sheet/workbook",
                   files={"file": ("assets.xlsx", data, "application/octet-stream")}, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "pending_first_sync"
    admin.put("/api/integrations/m02_assets_sheet", json={"enabled": True}, headers=H)
    res = admin.post("/api/system/integrations/m02_assets_sheet/sync", headers=H).json()
    assert res["status"] == "healthy" and res["appended"] == 1, res
    dl = admin.get("/api/system/integrations/m02_assets_sheet/workbook")
    wb = load_workbook(io.BytesIO(dl.content))
    ws = wb["الأصول"]
    hdr = [c.value for c in ws[1]]
    row2 = [c.value for c in ws[2]]
    assert row2[hdr.index("كود الأصل")] == a["code"]
    formulas = [c.value for c in ws[3] if isinstance(c.value, str) and c.value.startswith("=")]
    assert formulas  # pre-formatted template rows keep their formulas
    assert "معاينة_الشكل" in wb.sheetnames and "ألوان_الاختيارات" in wb.sheetnames  # other sheets untouched
    # edit a safe field in the file and sync back
    ws.cell(row=2, column=hdr.index("الموديل") + 1).value = "T14 Gen 6"
    b = io.BytesIO()
    wb.save(b)
    wbdir = app.state.settings.data_dir / "workbooks"
    (wbdir / "m02_assets_sheet.xlsx").write_bytes(b.getvalue())
    admin.post("/api/system/integrations/m02_assets_sheet/sync", headers=H)
    assert admin.get(f"/api/assets/{a['id']}").json()["model"] == "T14 Gen 6"
    assert (wbdir / "m02_assets_sheet.bak.xlsx").is_file()
    shutil.rmtree(wbdir, ignore_errors=True)


def test_new_reports_registered(admin, org):
    keys = {r["key"] for r in admin.get("/api/reports").json()}
    assert {"assets.warranty", "technical.subscriptions"} <= keys and len(keys) >= 13
    asset(admin, org, warranty_end="2026-12-01")
    r = admin.post("/api/reports/assets.warranty/run", json={}, headers=H).json()
    assert r["rows"] and r["rows"][0]["warranty_end"] == "2026-12-01"
    assert admin.post("/api/reports/technical.subscriptions/run", json={}, headers=H).status_code == 200
