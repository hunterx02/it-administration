"""Core acceptance tests: auth, RBAC on the server, audit, integration honesty."""
from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import ADMIN, H, login


def test_health_is_public(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["database"] == "ok" and r.json()["schema_version"] == 4
    assert client.get("/health").status_code == 200


def test_login_wrong_password_and_lockout(client):
    for _ in range(5):
        assert login(client, ADMIN[0], "wrong-pass-123").status_code == 401
    # account now locked even with the right password
    assert login(client, *ADMIN).status_code == 423


def test_session_cookie_is_httponly(client):
    r = login(client, *ADMIN)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_csrf_header_required(admin):
    r = admin.post("/api/companies", json={"name": "X"})  # no header
    assert r.status_code == 403


def test_unauthenticated_blocked(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/users").status_code == 401


def _make_user(admin: TestClient, email: str, role: str, password: str = "Passw0rd123", **kw) -> dict:
    r = admin.post("/api/users", json={"email": email, "name": "U " + role, "password": password,
                                       "role_key": role, **kw}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


def test_rbac_enforced_server_side(app, admin):
    _make_user(admin, "staff@test.local", "it_staff")
    staff = TestClient(app)
    assert login(staff, "staff@test.local", "Passw0rd123").status_code == 200
    me = staff.get("/api/auth/me").json()
    assert "employees:view_sensitive" not in me["permissions"]  # IT never sees HR-sensitive data by default
    assert staff.get("/api/users").status_code == 403
    assert staff.get("/api/integrations/status").status_code == 403
    assert staff.post("/api/companies", json={"name": "Z"}, headers=H).status_code == 403


def test_hr_has_sensitive_it_manager_does_not(app, admin):
    _make_user(admin, "hr@test.local", "hr")
    _make_user(admin, "itm@test.local", "it_manager")
    hr, itm = TestClient(app), TestClient(app)
    login(hr, "hr@test.local", "Passw0rd123"); login(itm, "itm@test.local", "Passw0rd123")
    assert "employees:view_sensitive" in hr.get("/api/auth/me").json()["permissions"]
    assert "employees:view_sensitive" not in itm.get("/api/auth/me").json()["permissions"]


def test_disable_user_revokes_access(app, admin):
    u = _make_user(admin, "fin@test.local", "finance")
    fin = TestClient(app)
    login(fin, "fin@test.local", "Passw0rd123")
    assert fin.get("/api/auth/me").status_code == 200
    assert admin.patch(f"/api/users/{u['id']}", json={"active": False}, headers=H).status_code == 200
    assert fin.get("/api/auth/me").status_code == 401
    assert login(fin, "fin@test.local", "Passw0rd123").status_code == 401


def test_cannot_remove_last_super_admin(admin):
    me = admin.get("/api/auth/me").json()
    r = admin.patch(f"/api/users/{me['id']}", json={"role_key": "it_manager"}, headers=H)
    assert r.status_code == 400


def test_weak_password_rejected(admin):
    r = admin.post("/api/users", json={"email": "w@test.local", "name": "Weak", "password": "12345678",
                                       "role_key": "it_staff"}, headers=H)
    assert r.status_code == 400


def test_company_scope_filters_companies(app, admin):
    a = admin.post("/api/companies", json={"name": "شركة أ", "asset_prefix": "AAA"}, headers=H).json()
    admin.post("/api/companies", json={"name": "شركة ب"}, headers=H)
    _make_user(admin, "scoped@test.local", "management", scopes=[{"company_id": a["id"]}])
    m = TestClient(app)
    login(m, "scoped@test.local", "Passw0rd123")
    names = [c["name"] for c in m.get("/api/companies").json()]
    assert names == ["شركة أ"]


def test_branch_must_belong_to_company(admin):
    a = admin.post("/api/companies", json={"name": "A"}, headers=H).json()
    b = admin.post("/api/companies", json={"name": "B"}, headers=H).json()
    br = admin.post(f"/api/companies/{b['id']}/branches", json={"name": "فرع"}, headers=H).json()
    r = admin.post("/api/users", json={"email": "x@test.local", "name": "XX", "password": "Passw0rd123",
                                       "role_key": "it_staff",
                                       "scopes": [{"company_id": a["id"], "branch_id": br["id"]}]}, headers=H)
    assert r.status_code == 400


def test_integrations_never_claim_connected(admin):
    s = admin.get("/api/integrations/status").json()
    assert s["connected_live"] == 0
    assert all(c["status"] in ("not_configured",) for c in s["cards"])
    r = admin.put("/api/integrations/m02_assets_sheet",
                  json={"enabled": True, "source_ref": "SHEET_ID_PLACEHOLDER", "credential_ref": "google-sa-m02"},
                  headers=H).json()
    assert r["status"] == "pending_live_credentials"  # configured but NOT verified live
    assert admin.get("/api/integrations/status").json()["connected_live"] == 0


def test_credential_ref_rejects_secret_like_values(admin):
    r = admin.put("/api/integrations/m02_assets_sheet", json={"credential_ref": "-----BEGIN PRIVATE KEY-----"},
                  headers=H)
    assert r.status_code == 422


def test_audit_trail_and_no_secrets(admin):
    admin.post("/api/users", json={"email": "aud@test.local", "name": "Aud", "password": "Passw0rd123",
                                   "role_key": "it_staff"}, headers=H)
    events = admin.get("/api/audit").json()
    actions = {e["action"] for e in events}
    assert {"auth.login", "user.created"} <= actions
    dump = str(events)
    assert "Passw0rd123" not in dump and "scrypt$" not in dump


def test_audit_has_no_write_endpoints(admin):
    assert admin.post("/api/audit", json={}, headers=H).status_code == 405
    assert admin.delete("/api/audit", headers=H).status_code == 405


def test_theme_preference_persisted(admin):
    assert admin.put("/api/auth/me/theme", json={"theme": "dark"}, headers=H).status_code == 200
    assert admin.get("/api/auth/me").json()["theme"] == "dark"
    assert admin.put("/api/auth/me/theme", json={"theme": "pink"}, headers=H).status_code == 422


def test_logout_revokes_session_server_side(app, admin):
    token = admin.cookies.get("itadm_session")
    assert admin.post("/api/auth/logout", headers=H).status_code == 200
    replay = TestClient(app)
    replay.cookies.set("itadm_session", token)  # a stolen/copied cookie must be dead too
    assert replay.get("/api/auth/me").status_code == 401
