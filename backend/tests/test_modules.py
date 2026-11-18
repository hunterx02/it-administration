"""Acceptance tests mapped to the module specs (M02–M06 + CROSS). See docs/TRACEABILITY_MATRIX.md."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from .conftest import H, login

PW = "Passw0rd123"


# ---------------------------------------------------------------- fixtures
@pytest.fixture()
def org(admin):
    c = admin.post("/api/companies", json={"name": "شركة اختبار", "asset_prefix": "ZW"}, headers=H).json()
    sales = admin.post(f"/api/companies/{c['id']}/departments", json={"name": "Sales"}, headers=H).json()
    mkt = admin.post(f"/api/companies/{c['id']}/departments", json={"name": "Marketing"}, headers=H).json()
    return {"company": c["id"], "sales": sales["id"], "mkt": mkt["id"]}


def user(app, admin, role: str, email: str | None = None) -> TestClient:
    email = email or f"{role}@t.local"
    r = admin.post("/api/users", json={"email": email, "name": role, "password": PW, "role_key": role,
                                       "all_companies": True}, headers=H)
    assert r.status_code == 201, r.text
    c = TestClient(app)
    assert login(c, email, PW).status_code == 200
    return c


def emp(admin, org, name="موظف", gender="male", dept=None) -> dict:
    r = admin.post("/api/employees", json={"company_id": org["company"], "full_name": name, "gender": gender,
                                           "department_id": dept}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


def asset(admin, org, cat="LTP", **kw) -> dict:
    r = admin.post("/api/assets", json={"company_id": org["company"], "category_code": cat, **kw}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


def upload(client, category: str, name="f.pdf", data=b"%PDF-1.4 test", company=None) -> str:
    r = client.post("/api/files", data={"category": category, **({"company_id": company} if company else {})},
                    files={"file": (name, data, "application/pdf")}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---------------------------------------------------------------- M02
def test_m02_codes_prefix_category_sequence(admin, org):
    a1, a2 = asset(admin, org, "AIO"), asset(admin, org, "AIO")
    m = asset(admin, org, "mon")
    assert (a1["code"], a2["code"], m["code"]) == ("ZW-AIO-001", "ZW-AIO-002", "ZW-mon-001")
    c2 = admin.post("/api/companies", json={"name": "بدون بادئة"}, headers=H).json()
    r = admin.post("/api/assets", json={"company_id": c2["id"], "category_code": "LTP"}, headers=H)
    assert r.status_code == 400


def test_m02_reserve_is_not_handover_and_return_results(admin, org):
    e = emp(admin, org)
    a = asset(admin, org)
    r = admin.post(f"/api/assets/{a['id']}/reserve", json={"employee_id": e["id"]}, headers=H).json()
    assert r["allocation_status"] == "reserved" and r["holder_employee_id"] is None
    r = admin.post(f"/api/assets/{a['id']}/handover", json={"employee_id": e["id"]}, headers=H).json()
    assert r["allocation_status"] == "assigned" and r["holder_employee_id"] == e["id"]
    r = admin.post(f"/api/assets/{a['id']}/return", json={"result": "damaged"}, headers=H).json()
    assert r["allocation_status"] == "in_stock" and r["tech_condition"] == "damaged"
    b = asset(admin, org)
    admin.post(f"/api/assets/{b['id']}/handover", json={"employee_id": e["id"]}, headers=H)
    r = admin.post(f"/api/assets/{b['id']}/return", json={"result": "missing"}, headers=H).json()
    assert r["allocation_status"] == "lost"  # return never "clears" a loss
    kinds = [x["kind"] for x in admin.get(f"/api/assets/{a['id']}").json()["events"]]
    assert {"created", "reserve", "handover", "return"} <= set(kinds)


def test_m02_idempotent_handover(admin, org):
    e, a = emp(admin, org), asset(admin, org)
    body = {"employee_id": e["id"], "idempotency_key": "k-1"}
    assert admin.post(f"/api/assets/{a['id']}/handover", json=body, headers=H).status_code == 200
    assert admin.post(f"/api/assets/{a['id']}/handover", json=body, headers=H).status_code == 200
    ev = [x for x in admin.get(f"/api/assets/{a['id']}").json()["events"] if x["kind"] == "handover"]
    assert len(ev) == 1


def test_m02_monitor_links_and_version_conflict(admin, org):
    d1, d2, mon = asset(admin, org, "CMP"), asset(admin, org, "CMP"), asset(admin, org, "mon")
    assert admin.post(f"/api/assets/{d1['id']}/links", json={"child_asset_id": mon["id"]}, headers=H).status_code == 201
    assert admin.post(f"/api/assets/{d2['id']}/links", json={"child_asset_id": mon["id"]}, headers=H).status_code == 409
    mon2 = asset(admin, org, "mon")
    assert admin.post(f"/api/assets/{d1['id']}/links", json={"child_asset_id": mon2["id"]}, headers=H).status_code == 201
    assert len(admin.get(f"/api/assets/{d1['id']}").json()["links"]) == 2  # many monitors per device
    v = d1["version"]
    assert admin.patch(f"/api/assets/{d1['id']}", json={"version": v, "model": "X"}, headers=H).status_code == 200
    assert admin.patch(f"/api/assets/{d1['id']}", json={"version": v, "model": "Y"}, headers=H).status_code == 409


def test_m02_maintenance_keeps_holder(admin, org):
    e, a = emp(admin, org), asset(admin, org)
    admin.post(f"/api/assets/{a['id']}/handover", json={"employee_id": e["id"]}, headers=H)
    m = admin.post(f"/api/assets/{a['id']}/maintenance", json={"issue": "شاشة"}, headers=H).json()
    r = admin.post(f"/api/assets/maintenance/{m['id']}/close", json={"result": "اتصلحت", "tech_condition": "good"},
                   headers=H)
    d = admin.get(f"/api/assets/{a['id']}").json()
    assert r.status_code == 200 and d["allocation_status"] == "assigned" and d["holder_employee_id"] == e["id"]


# ---------------------------------------------------------------- M03
def _application(admin, org, gender="male"):
    return admin.post("/api/journey/applications", json={"company_id": org["company"], "position": "Designer",
                                                         "full_name": "مرشح", "gender": gender,
                                                         "national_id": "29901011234567", "expected_salary": "9000"},
                      headers=H).json()


def _interviews(client_hr, client_tech, app_id, tech, hr):
    r1 = client_tech.post(f"/api/journey/applications/{app_id}/interviews",
                          json={"kind": "technical", "interviewer": "IT Lead", "recommendation": tech,
                                "scores": {"performance": 8, "behavior": 7, "team_dynamic": 9, "skills": 8}}, headers=H)
    r2 = client_hr.post(f"/api/journey/applications/{app_id}/interviews",
                        json={"kind": "hr", "interviewer": "HR", "recommendation": hr,
                              "hr_fields": {"why": "x", "strength": "y"}}, headers=H)
    assert r1.status_code == 201 and r2.status_code == 201, (r1.text, r2.text)


@pytest.mark.parametrize("tech,hr,outcome", [("accepted", "accepted", "onboarding"),
                                             ("accepted", "not_accepted", "waiting"),
                                             ("not_accepted", "not_accepted", "archived"),
                                             ("not_accepted", "accepted", "archived")])
def test_m03_decision_matrix(app, admin, org, tech, hr, outcome):
    hr_c, itm = user(app, admin, "hr"), user(app, admin, "it_manager")
    a = _application(admin, org)
    _interviews(hr_c, itm, a["id"], tech, hr)
    assert itm.post(f"/api/journey/applications/{a['id']}/decision", json={}, headers=H).status_code == 403
    r = hr_c.post(f"/api/journey/applications/{a['id']}/decision", json={}, headers=H)
    assert r.status_code == 200 and r.json()["outcome"] == outcome
    stage = hr_c.get(f"/api/journey/applications/{a['id']}").json()["stage"]
    assert stage == outcome  # archived is kept, never deleted


def test_m03_it_cannot_see_hr_private_data(app, admin, org):
    hr_c, itm = user(app, admin, "hr"), user(app, admin, "it_manager")
    a = _application(admin, org)
    _interviews(hr_c, itm, a["id"], "accepted", "accepted")
    d_it = itm.get(f"/api/journey/applications/{a['id']}").json()
    assert "national_id" not in d_it and "expected_salary" not in d_it
    hr_iv = [i for i in d_it["interviews"] if i["kind"] == "hr"][0]
    assert "hr_fields" not in hr_iv and "notes" not in hr_iv
    d_hr = hr_c.get(f"/api/journey/applications/{a['id']}").json()
    assert d_hr["national_id"] == "29901011234567"
    assert itm.post(f"/api/journey/applications/{a['id']}/interviews",
                    json={"kind": "hr", "interviewer": "xx", "recommendation": "accepted"}, headers=H).status_code == 403


def test_m03_onboarding_docs_versions_acks(app, admin, org):
    a = _application(admin, org, gender="female")
    _interviews(admin, admin, a["id"], "accepted", "accepted")
    res = admin.post(f"/api/journey/applications/{a['id']}/decision", json={"start_date": str(date.today())},
                     headers=H).json()
    ob = admin.get(f"/api/journey/onboarding/{res['onboarding_id']}").json()
    mil = [d for d in ob["joining_documents"] if d["doc_type"] == "military_status"][0]
    assert mil["status"] == "not_applicable"  # female -> N/A, never missing
    assert admin.post(f"/api/journey/onboarding/{ob['id']}/complete", headers=H).status_code == 409
    eid = res["employee_id"]
    # acknowledgments need the company's real file first
    r = admin.post(f"/api/employees/{eid}/acks", json={"kind": "nda", "method": "e_ack"}, headers=H)
    assert r.status_code == 400
    tpl_ids = {}
    for kind in ("nda", "internal_regulations", "contract", "work_start"):
        f = upload(admin, "document_template", company=org["company"])
        t = admin.post("/api/journey/templates", json={"company_id": org["company"], "kind": kind, "title": kind,
                                                       "file_id": f}, headers=H).json()
        tpl_ids[kind] = t["id"]
        assert t["version"] == 1
    ack = admin.post(f"/api/employees/{eid}/acks", json={"kind": "nda", "method": "e_ack"}, headers=H).json()
    assert ack["template_version"] == 1 and ack["legal_signature"] is False
    f2 = upload(admin, "document_template", company=org["company"])
    t2 = admin.post("/api/journey/templates", json={"company_id": org["company"], "kind": "nda", "title": "nda v2",
                                                    "file_id": f2}, headers=H).json()
    assert t2["version"] == 2
    acks = admin.get(f"/api/employees/{eid}").json()["acks"]
    assert [x["template_version"] for x in acks if x["kind"] == "nda"] == [1]  # old ack keeps its version
    # paper method requires evidence
    assert admin.post(f"/api/employees/{eid}/acks", json={"kind": "contract", "method": "paper_scan"},
                      headers=H).status_code == 400
    scan = upload(admin, "hr_document")
    for kind in ("internal_regulations", "contract", "work_start"):
        assert admin.post(f"/api/employees/{eid}/acks", json={"kind": kind, "method": "paper_scan",
                                                              "evidence_file_id": scan}, headers=H).status_code == 201
    # custody ack needs real custody
    assert admin.post(f"/api/employees/{eid}/acks", json={"kind": "custody", "method": "paper_scan",
                                                          "evidence_file_id": scan}, headers=H).status_code == 400
    lap = asset(admin, org)
    admin.post(f"/api/assets/{lap['id']}/reserve", json={"employee_id": eid}, headers=H)  # IT prep
    admin.post(f"/api/assets/{lap['id']}/handover", json={"employee_id": eid}, headers=H)  # actual handover
    assert admin.post(f"/api/employees/{eid}/acks", json={"kind": "custody", "method": "paper_scan",
                                                          "evidence_file_id": scan}, headers=H).status_code == 201
    for d in ("national_id", "birth_certificate", "photo", "degree"):
        f = upload(admin, "joining_document", name="id.png")
        assert admin.post(f"/api/employees/{eid}/joining-docs/{d}", json={"file_id": f}, headers=H).status_code == 200
        assert admin.post(f"/api/employees/{eid}/joining-docs/{d}/verify", headers=H).status_code == 200
    ob = admin.get(f"/api/journey/onboarding/{ob['id']}").json()
    for t in ob["tasks"]:
        assert admin.post(f"/api/journey/tasks/{t['id']}", json={"status": "verified"}, headers=H).status_code == 409
        admin.post(f"/api/journey/tasks/{t['id']}", json={"status": "done", "ref": "ref"}, headers=H)
        assert admin.post(f"/api/journey/tasks/{t['id']}", json={"status": "verified"}, headers=H).status_code == 200
    r = admin.post(f"/api/journey/onboarding/{ob['id']}/complete", headers=H)
    assert r.status_code == 200, r.text
    assert admin.get(f"/api/employees/{eid}").json()["status"] == "active"


def test_m03_male_military_required(admin, org):
    e = emp(admin, org, gender="male")
    docs = admin.get(f"/api/employees/{e['id']}").json()["joining_documents"]
    assert [d["status"] for d in docs if d["doc_type"] == "military_status"] == ["missing"]


def test_m03_offboarding_freeze_it_paper_close(app, admin, org):
    hr_c, itm = user(app, admin, "hr"), user(app, admin, "it_manager")
    e = emp(admin, org)
    lap, spare = asset(admin, org), asset(admin, org)
    admin.post(f"/api/assets/{lap['id']}/handover", json={"employee_id": e["id"]}, headers=H)
    sim = admin.post("/api/technical/sims", json={"country": "EG", "company_id": org["company"]}, headers=H).json()
    admin.post(f"/api/technical/sims/{sim['id']}/handover", json={"employee_id": e["id"]}, headers=H)
    off = hr_c.post("/api/journey/offboarding", json={"employee_id": e["id"],
                                                      "last_day": str(date.today() + timedelta(days=3))},
                    headers=H).json()
    assert len(off["authorities"]) == 6
    # freeze: every channel
    assert admin.post(f"/api/assets/{spare['id']}/handover", json={"employee_id": e["id"]}, headers=H).status_code == 409
    assert admin.post(f"/api/assets/{spare['id']}/reserve", json={"employee_id": e["id"]}, headers=H).status_code == 409
    sim2 = admin.post("/api/technical/sims", json={"country": "SA", "company_id": org["company"]}, headers=H).json()
    assert admin.post(f"/api/technical/sims/{sim2['id']}/handover", json={"employee_id": e["id"]},
                      headers=H).status_code == 409
    oid = off["id"]
    assert hr_c.get(f"/api/journey/offboarding/{oid}/clearance-print").status_code == 409  # IT first
    assert itm.post(f"/api/journey/offboarding/{oid}/it-approve", headers=H).status_code == 409  # custody left
    itm.post(f"/api/journey/offboarding/{oid}/it-check", json={"item_type": "asset", "item_ref": lap["id"],
                                                               "result": "returned", "serial_checked": True}, headers=H)
    itm.post(f"/api/journey/offboarding/{oid}/it-check", json={"item_type": "sim", "item_ref": sim["id"],
                                                               "result": "returned"}, headers=H)
    assert itm.post(f"/api/journey/offboarding/{oid}/it-approve", headers=H).status_code == 400  # accounts
    itm.post(f"/api/journey/offboarding/{oid}/it-check", json={"item_type": "account", "item_ref": "email",
                                                               "result": "deprovisioned"}, headers=H)
    assert itm.post(f"/api/journey/offboarding/{oid}/it-approve", headers=H).status_code == 200
    page = hr_c.get(f"/api/journey/offboarding/{oid}/clearance-print")
    assert page.status_code == 200 and "إخلاء طرف" in page.text  # printable without HR pre-signature
    auths = hr_c.get(f"/api/journey/offboarding/{oid}").json()["authorities"]
    for a in auths:
        st = "not_clear" if a["order"] == 4 else "clear"
        r = hr_c.post(f"/api/journey/offboarding/{oid}/authorities/{a['id']}",
                      json={"status": st, "signer_name": "اسم", "signed_date": str(date.today()), "notes": "سلفة"},
                      headers=H)
        assert r.status_code == 200
    assert hr_c.post(f"/api/journey/offboarding/{oid}/close", headers=H).status_code == 409  # Not Clear blocks
    hr_c.post(f"/api/journey/offboarding/{oid}/authorities/{auths[3]['id']}",
              json={"status": "clear", "signer_name": "اسم", "signed_date": str(date.today())}, headers=H)
    r = hr_c.post(f"/api/journey/offboarding/{oid}/close", headers=H)
    assert r.status_code == 409 and "النسخة الورقية" in r.text
    scan = upload(hr_c, "clearance_scan")
    assert hr_c.post(f"/api/journey/offboarding/{oid}/signed-scan", json={"file_id": scan}, headers=H).status_code == 200
    assert hr_c.post(f"/api/journey/offboarding/{oid}/close", headers=H).status_code == 200
    e2 = admin.get(f"/api/employees/{e['id']}").json()
    assert e2["status"] == "exited" and e2["custody_frozen"] is True


def test_m03_sensitive_files_need_hr(app, admin, org):
    itm = user(app, admin, "it_manager")
    fid = upload(admin, "joining_document", name="nid.png")
    assert itm.get(f"/api/files/{fid}").status_code == 403
    assert admin.get(f"/api/files/{fid}").status_code == 200
    r = itm.post("/api/files", data={"category": "joining_document"}, files={"file": ("x.png", b"x", "image/png")},
                 headers=H)
    assert r.status_code == 403


# ---------------------------------------------------------------- M04
def _line(admin, org, **kw):
    r = admin.post("/api/technical/lines", json={"company_id": org["company"], **kw}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


def _reading(admin, line_id, remaining, source="manual", read_at=None):
    body = {"target_type": "line", "target_id": line_id, "remaining": remaining, "source": source}
    if read_at:
        body["read_at"] = read_at
    return admin.post("/api/technical/readings", json=body, headers=H).json()


def test_m04_thresholds_500gb_example(admin, org):
    ln = _line(admin, org, quota_gb=500, warn_gb=100, critical_gb=20)
    states = []
    for v in (110, 99, 20, 0):
        states.append(_reading(admin, ln["id"], v)["target"]["usage_state"])
    assert states == ["normal", "prepare", "urgent", "urgent"]
    ln2 = _line(admin, org, quota_gb=200, warn_gb=50, critical_gb=10)
    assert _reading(admin, ln2["id"], 60)["target"]["usage_state"] == "normal"  # line 1 thresholds not inherited
    ln3 = _line(admin, org, quota_gb=200)
    assert _reading(admin, ln3["id"], 5)["target"]["usage_state"] == "thresholds_missing"
    assert _line(admin, org)["usage_state"] == "incomplete"
    bad = admin.post("/api/technical/lines", json={"company_id": org["company"], "warn_gb": 10, "critical_gb": 50},
                     headers=H)
    assert bad.status_code == 400


def test_m04_no_default_periods_and_calendar_months(admin, org):
    s = admin.post("/api/technical/sims", json={"country": "SA", "company_id": org["company"],
                                                 "last_payment": "2026-01-31"}, headers=H).json()
    assert s["next_due"] is None and s["due_state"] == "incomplete"  # no period -> no invented date
    r = admin.post("/api/technical/renewals", json={"target_type": "sim", "target_id": s["id"],
                                                     "renewed_on": "2026-01-31", "period_value": 1,
                                                     "period_unit": "months"}, headers=H).json()
    assert r["next_due"] == "2026-02-28"  # calendar month, not +30 days


def test_m04_internet_and_landline_independent_and_history(admin, org):
    ln = _line(admin, org, landline_number="0223456789")
    admin.post("/api/technical/renewals", json={"target_type": "line_internet", "target_id": ln["id"],
                                                 "renewed_on": "2026-10-01", "period_value": 1,
                                                 "period_unit": "months"}, headers=H)
    admin.post("/api/technical/renewals", json={"target_type": "line_landline", "target_id": ln["id"],
                                                 "renewed_on": "2026-09-15", "period_value": 3,
                                                 "period_unit": "months"}, headers=H)
    d = admin.get(f"/api/technical/lines/{ln['id']}").json()
    assert d["internet"]["next_due"] == "2026-11-01" and d["landline"]["next_due"] == "2026-12-15"
    admin.post("/api/technical/renewals", json={"target_type": "line_internet", "target_id": ln["id"],
                                                 "renewed_on": "2026-11-03"}, headers=H)
    d = admin.get(f"/api/technical/lines/{ln['id']}").json()
    assert d["internet"]["next_due"] == "2026-12-03"  # from the NEW payment date
    assert len([r for r in d["renewals"] if r["target_type"] == "line_internet"]) == 2  # history kept


def test_m04_stale_api_reading_never_overwrites_manual(admin, org):
    ln = _line(admin, org, quota_gb=500, warn_gb=100, critical_gb=20)
    _reading(admin, ln["id"], 300, "manual", "2026-11-10T10:00:00+00:00")
    r = _reading(admin, ln["id"], 450, "api", "2026-11-10T08:00:00+00:00")
    assert r["reading"]["applied"] is False and r["target"]["remaining_gb"] == 300
    assert len(admin.get(f"/api/technical/lines/{ln['id']}").json()["readings"]) == 2  # both stored


def test_m04_extension_reading_never_renews(admin, org):
    ln = _line(admin, org, quota_gb=500, warn_gb=100, critical_gb=20, net_last_payment="2026-10-01",
               net_period_value=1, net_period_unit="months")
    r = admin.post("/api/technical/readings", json={"target_type": "line", "target_id": ln["id"], "remaining": 95,
                                                     "used": 405, "total": 500, "source": "script",
                                                     "note": "my.te.eg عبر الإضافة · تاريخ تجديد WE 2026-12-20"},
                   headers=H).json()
    assert r["reading"]["applied"] and r["target"]["usage_state"] == "prepare"
    assert "2026-12-20" in r["reading"]["note"] and r["target"]["last_reading_source"] == "script"
    assert r["target"]["internet"]["next_due"] == "2026-11-01"  # WE's date is info only, never a renewal


def test_m04_saudi_agent_backup_and_permissions(app, admin, org):
    s = admin.post("/api/technical/sims", json={"country": "SA", "company_id": org["company"],
                                                 "possession": "مع الوكيل في السعودية", "network_status": "working",
                                                 "backup_interval_days": 30}, headers=H).json()
    e = emp(admin, org)
    s = admin.post(f"/api/technical/sims/{s['id']}/handover", json={"employee_id": e["id"]}, headers=H).json()
    assert s["allocation_status"] == "assigned" and s["possession"].startswith("مع الوكيل") and \
        s["network_status"] == "working"  # with agent != broken
    assert s["last_backup_at"] is None
    admin.get(f"/api/technical/sims/{s['id']}")  # viewing does not confirm a backup
    assert admin.get(f"/api/technical/sims/{s['id']}").json()["last_backup_at"] is None
    s = admin.post(f"/api/technical/sims/{s['id']}/backup-confirm",
                   json={"performed_on": str(date.today()), "method": "فحص Google Drive"}, headers=H).json()
    assert s["last_backup_at"] == str(date.today())
    staff = user(app, admin, "it_staff")
    assert staff.patch(f"/api/technical/sims/{s['id']}", json={"version": s["version"], "notes": "x"},
                       headers=H).status_code == 403
    assert staff.get("/api/technical/sims").status_code == 200


def test_m04_m05_payment_does_not_renew(admin, org):
    sub = admin.post("/api/technical/subscriptions", json={"company_id": org["company"], "name": "Tool",
                                                            "last_payment": "2026-10-01", "period_value": 1,
                                                            "period_unit": "months"}, headers=H).json()
    before = sub["next_due"]
    admin.post("/api/finance/expenses", json={"company_id": org["company"], "kind": "subscription",
                                              "description": "دفع اشتراك", "amount": 100, "currency": "USD",
                                              "occurred_on": "2026-10-30"}, headers=H)
    assert admin.get(f"/api/technical/subscriptions/{sub['id']}").json()["next_due"] == before
    ren = admin.post("/api/technical/renewals", json={"target_type": "subscription", "target_id": sub["id"],
                                                       "renewed_on": "2026-11-01"}, headers=H).json()
    ev = admin.post("/api/finance/expenses", json={"company_id": org["company"], "kind": "renewal",
                                                   "description": "تجديد", "amount": 100, "currency": "USD",
                                                   "occurred_on": "2026-11-01", "source_module": "M04",
                                                   "source_type": "renewal", "source_id": ren["id"]}, headers=H)
    assert ev.status_code == 201
    dup = admin.post("/api/finance/expenses", json={"company_id": org["company"], "kind": "renewal",
                                                    "description": "تكرار", "amount": 100, "currency": "USD",
                                                    "occurred_on": "2026-11-01", "source_module": "M04",
                                                    "source_type": "renewal", "source_id": ren["id"]}, headers=H)
    assert dup.status_code == 409


# ---------------------------------------------------------------- M05
def _pr(admin, org, qty=5):
    r = admin.post("/api/finance/purchase-requests", json={
        "company_id": org["company"], "requester": "IT Manager", "reason": "موبايلات Sales",
        "beneficiary_department_ids": [org["sales"]], "currency": "EGP",
        "items": [{"item": "Galaxy A55", "qty": qty, "est_unit_cost": 15000, "asset_category_code": "MOB"}]},
        headers=H)
    assert r.status_code == 201, r.text
    return r.json()


def test_m05_paper_approval_partial_receipt(admin, org):
    before = len(admin.get("/api/assets").json())
    pr = _pr(admin, org)
    assert pr["status"] == "awaiting_approval"
    ap = {"decision": "approved", "approval_date": str(date.today()), "approver_name": "CEO"}
    assert admin.post(f"/api/finance/purchase-requests/{pr['id']}/approval", json=ap, headers=H).status_code == 409
    page = admin.get(f"/api/finance/purchase-requests/{pr['id']}/print")
    assert page.status_code == 200 and "اعتماد الرئيس التنفيذي" in page.text
    r = admin.post(f"/api/finance/purchase-requests/{pr['id']}/approval", json=ap, headers=H).json()
    assert r["status"] == "approved" and r["evidence_missing"] is True
    assert len(admin.get("/api/assets").json()) == before  # approved, not received -> no assets
    item = r["items"][0]["id"]
    bad = admin.post(f"/api/finance/purchase-requests/{pr['id']}/receive",
                     json={"item_id": item, "qty": 3, "actual_unit_cost": 15500, "serials": ["a", "b"]}, headers=H)
    assert bad.status_code == 400
    r = admin.post(f"/api/finance/purchase-requests/{pr['id']}/receive",
                   json={"item_id": item, "qty": 3, "actual_unit_cost": 15500, "serials": ["S1", "S2", "S3"]},
                   headers=H).json()
    assert r["status"] == "partially_received" and len(r["created_asset_ids"]) == 3
    assert r["items"][0]["remaining_qty"] == 2
    assert len(admin.get("/api/assets").json()) == before + 3


def test_m05_allocation_counted_once(admin, org):
    ev = admin.post("/api/finance/expenses", json={"company_id": org["company"], "kind": "subscription",
                                                   "description": "أداة تصميم", "amount": 10000, "currency": "EGP",
                                                   "occurred_on": str(date.today())}, headers=H).json()
    bad = admin.put(f"/api/finance/events/{ev['id']}/allocations",
                    json={"lines": [{"department_id": org["sales"], "percentage": 60},
                                    {"department_id": org["mkt"], "percentage": 30}]}, headers=H)
    assert bad.status_code == 400
    admin.put(f"/api/finance/events/{ev['id']}/allocations",
              json={"lines": [{"department_id": org["sales"], "percentage": 60},
                              {"department_id": org["mkt"], "percentage": 40}]}, headers=H)
    q = {"date_from": str(date.today() - timedelta(days=1)), "date_to": str(date.today()), "basis": "committed"}
    total = admin.get("/api/finance/summary", params=q).json()
    assert total["totals"] == {"EGP": "10000.00"}
    assert admin.get("/api/finance/summary", params=q | {"department_id": org["sales"]}).json()["totals"] == {
        "EGP": "6000.00"}
    assert admin.get("/api/finance/summary", params=q | {"department_id": org["mkt"]}).json()["totals"] == {
        "EGP": "4000.00"}


def test_m05_invoice_payments_bases(admin, org):
    sup = admin.post("/api/finance/suppliers", json={"name": "مورد"}, headers=H).json()
    ev = admin.post("/api/finance/expenses", json={"company_id": org["company"], "kind": "maintenance",
                                                   "description": "صيانة", "amount": 1200, "currency": "EGP",
                                                   "occurred_on": str(date.today())}, headers=H).json()
    assert admin.post("/api/finance/invoices", json={"financial_event_id": ev["id"], "supplier_id": sup["id"],
                                                     "number": "INV-1", "invoice_date": str(date.today()),
                                                     "currency": "USD", "base_amount": 1000}, headers=H).status_code == 400
    r = admin.post("/api/finance/invoices", json={"financial_event_id": ev["id"], "supplier_id": sup["id"],
                                                  "number": "INV-1", "invoice_date": str(date.today()),
                                                  "currency": "EGP", "base_amount": 1000, "tax": 140},
                   headers=H).json()
    inv = r["invoices"][0]
    assert inv["total"] == "1140.00"
    assert admin.post("/api/finance/invoices", json={"financial_event_id": ev["id"], "supplier_id": sup["id"],
                                                     "number": "INV-1", "invoice_date": str(date.today()),
                                                     "currency": "EGP", "base_amount": 1}, headers=H).status_code == 409
    r = admin.post(f"/api/finance/invoices/{inv['id']}/payments", json={"paid_on": str(date.today()),
                                                                         "amount": 500}, headers=H).json()
    assert r["paid"] == "500.00" and r["outstanding"] == "640.00"
    assert admin.post(f"/api/finance/invoices/{inv['id']}/payments", json={"paid_on": str(date.today()),
                                                                            "amount": 700}, headers=H).status_code == 400
    q = {"date_from": str(date.today()), "date_to": str(date.today())}
    vals = {b: admin.get("/api/finance/summary", params=q | {"basis": b}).json()["totals"]["EGP"]
            for b in ("committed", "invoiced", "paid")}
    assert vals == {"committed": "1200.00", "invoiced": "1140.00", "paid": "500.00"}


def test_m05_maintenance_single_economic_event(admin, org):
    a = asset(admin, org)
    m = admin.post(f"/api/assets/{a['id']}/maintenance", json={"issue": "عطل", "vendor": "مركز"}, headers=H).json()
    body = {"company_id": org["company"], "kind": "maintenance", "description": "صيانة", "amount": 500,
            "currency": "EGP", "occurred_on": str(date.today()), "source_module": "M02", "source_type": "maintenance",
            "source_id": m["id"]}
    assert admin.post("/api/finance/expenses", json=body, headers=H).status_code == 201
    assert admin.post("/api/finance/expenses", json=body, headers=H).status_code == 409
    assert admin.get(f"/api/assets/{a['id']}").json()["maintenance"][0]["financial_event_id"]


def test_m05_finance_role_cannot_create(app, admin, org):
    fin = user(app, admin, "finance")
    assert fin.get("/api/finance/events").status_code == 200
    assert fin.post("/api/finance/suppliers", json={"name": "x y"}, headers=H).status_code == 403


# ---------------------------------------------------------------- reports & dashboard
def test_reports_rbac_no_pii_and_safe_export(app, admin, org):
    e = admin.post("/api/employees", json={"company_id": org["company"], "full_name": "=HYPERLINK(\"x\")",
                                           "hire_date": str(date.today())}, headers=H).json()
    admin.put(f"/api/employees/{e['id']}/sensitive", json={"national_id": "29901019999999"}, headers=H)
    staff = user(app, admin, "it_staff")
    rep = staff.post("/api/reports/journey.hires/run", json={}, headers=H)
    assert rep.status_code == 200 and "29901019999999" not in rep.text
    assert staff.post("/api/reports/finance.spend/run", json={}, headers=H).status_code == 403
    keys = {r["key"] for r in staff.get("/api/reports").json()}
    assert "finance.spend" not in keys and "assets.register" in keys
    assert staff.get("/api/reports/assets.register/export").status_code == 403  # export permission
    csv_ = admin.get("/api/reports/journey.hires/export", params={"fmt": "csv"})
    assert csv_.status_code == 200 and "'=HYPERLINK" in csv_.text
    xl = admin.get("/api/reports/assets.register/export", params={"fmt": "xlsx"})
    assert xl.status_code == 200 and xl.content[:2] == b"PK"
    assert admin.get("/api/reports/assets.register/print").status_code == 200


def test_dashboard_real_data_only_and_role_blocks(app, admin, org):
    d = admin.get("/api/dashboard").json()
    assert d["assets"]["cards"]["laptops"]["total"] == 0 and d["renewals"]["primary_lines"] == []
    asset(admin, org, "LTP")
    asset(admin, org, "CMP")
    d = admin.get("/api/dashboard").json()
    assert d["assets"]["cards"]["laptops"]["total"] == 1 and d["assets"]["cards"]["desktops"]["total"] == 1
    hr_c = user(app, admin, "hr")
    dh = hr_c.get("/api/dashboard").json()
    assert "finance" not in dh and "renewals" not in dh and "journey" in dh


# ---------------------------------------------------------------- sync (Google Sheets, memory transport)
def test_sheet_sync_rules(app, admin, org):
    from app.integrations.google import MemorySheetsTransport, asset_mapping
    t = MemorySheetsTransport()
    t.books["SHEET"] = {"الأصول": [asset_mapping().headers()[:-1]]}  # template headers (no asset_id yet)
    app.state.sheets_transport = t
    a = asset(admin, org, "LTP", model="T14")
    e = emp(admin, org, name="سارة")
    admin.put("/api/integrations/m02_assets_sheet", json={"enabled": True, "source_ref": "SHEET",
                                                          "mode": "sheets"}, headers=H)
    r = admin.post("/api/system/integrations/m02_assets_sheet/sync", headers=H).json()
    assert r["status"] == "healthy" and r["appended"] == 1
    grid = t.books["SHEET"]["الأصول"]
    hdr = grid[0]
    assert "asset_id" in hdr and grid[1][hdr.index("كود الأصل")] == "ZW-LTP-001"
    # safe field edited in the sheet -> pulled into the app
    grid[1][hdr.index("الموديل")] = "T14 Gen 5"
    # protected field edited in the sheet -> NOT applied, queued
    grid[1][hdr.index("اسم الموظف")] = "سارة"
    r = admin.post("/api/system/integrations/m02_assets_sheet/sync", headers=H).json()
    got = admin.get(f"/api/assets/{a['id']}").json()
    assert got["model"] == "T14 Gen 5" and got["holder_employee_id"] is None
    kinds = {c["kind"] for c in admin.get("/api/system/sync/conflicts").json()}
    assert "protected_field" in kinds
    assert grid[1][hdr.index("اسم الموظف")] == ""  # app value pushed back
    # concurrent edit -> conflict, no silent overwrite
    v = admin.get(f"/api/assets/{a['id']}").json()["version"]
    admin.patch(f"/api/assets/{a['id']}", json={"version": v, "model": "App edit"}, headers=H)
    grid[1][hdr.index("الموديل")] = "Sheet edit"
    admin.post("/api/system/integrations/m02_assets_sheet/sync", headers=H)
    assert admin.get(f"/api/assets/{a['id']}").json()["model"] == "App edit"
    assert "concurrent_edit" in {c["kind"] for c in admin.get("/api/system/sync/conflicts").json()}
    # unknown column -> rejected
    grid[0].append("عمود غريب")
    r = admin.post("/api/system/integrations/m02_assets_sheet/sync", headers=H).json()
    assert r["status"] == "error"
    assert e  # employee existed but sheet could not hand over to them


def test_sync_without_credentials_is_pending(admin):
    admin.put("/api/integrations/m02_assets_sheet", json={"enabled": True, "source_ref": "REAL_ID",
                                                          "credential_ref": "missing-key", "mode": "sheets"}, headers=H)
    r = admin.post("/api/system/integrations/m02_assets_sheet/sync", headers=H).json()
    assert r["status"] == "pending_live_credentials"
    assert admin.get("/api/integrations/status").json()["connected_live"] == 0


def test_form_import_idempotent(app, admin, org):
    from app.integrations.google import MemorySheetsTransport
    t = MemorySheetsTransport()
    head = ["candidate_id", "company_id", "position_applied_for", "full_name", "gender_for_document_rule", "phone",
            "source_form_response_id"]
    t.books["M03"] = {"Applications": [head, ["CAN-x", org["company"], "Designer", "مرشحة", "أنثى", "010", "raw:1:2"],
                                       ["CAN-y", "bad-company", "Dev", "x", "ذكر", "011", "raw:1:3"]]}
    app.state.sheets_transport = t
    admin.put("/api/integrations/m03_application_form", json={"enabled": True, "source_ref": "M03",
                                                              "mode": "sheets"}, headers=H)
    r1 = admin.post("/api/system/integrations/m03_application_form/sync", headers=H).json()
    r2 = admin.post("/api/system/integrations/m03_application_form/sync", headers=H).json()
    assert r1["pulled"] == 1 and r2["pulled"] == 0 and r1["conflicts"] == 1
    apps = admin.get("/api/journey/applications").json()
    assert len(apps) == 1 and apps[0]["candidate_id"].startswith("CAN-")


# ---------------------------------------------------------------- automation
def test_script_library_upload_is_not_execute(app, admin):
    from app.modules.automation import worker_tick
    fid = upload(admin, "script", name="hello.py", data=b"print('ok token=abc123secretvalue')")
    s = admin.post("/api/system/scripts", json={"name": "hello", "module": "M06", "file_id": fid}, headers=H).json()
    assert s["status"] == "uploaded"
    assert admin.post(f"/api/system/scripts/{s['id']}/run", headers=H).status_code == 409
    itm = user(app, admin, "it_manager")
    assert itm.post(f"/api/system/scripts/{s['id']}/approve", headers=H).status_code == 403  # needs manage_secrets
    admin.post(f"/api/system/scripts/{s['id']}/approve", headers=H)
    admin.post(f"/api/system/scripts/{s['id']}/run", headers=H)
    worker_tick(app.state)
    runs = admin.get(f"/api/system/scripts/{s['id']}/runs").json()
    assert runs[0]["status"] == "success" and "abc123secretvalue" not in runs[0]["output"]
    # tamper with the stored file -> blocked
    from app.modules.files import FileObject
    with app.state.db.SessionLocal() as db:
        fo = db.get(FileObject, fid)
        (app.state.settings.data_dir / "files" / fo.storage_ref).write_bytes(b"print('evil')")
    admin.post(f"/api/system/scripts/{s['id']}/run", headers=H)
    worker_tick(app.state)
    assert admin.get(f"/api/system/scripts/{s['id']}/runs").json()[0]["status"] == "blocked"


def test_worker_heartbeat_and_dedup_notifications(app, admin, org):
    from app.modules.automation import worker_tick
    assert admin.get("/api/system/worker").json()["state"] == "never"
    ln = _line(admin, org, quota_gb=500, warn_gb=100, critical_gb=20)
    _reading(admin, ln["id"], 10)
    s1 = worker_tick(app.state)
    s2 = worker_tick(app.state)
    assert s1["notifications_created"] >= 1 and s2["notifications_created"] == 0  # unchanged state: no re-alert
    assert admin.get("/api/system/worker").json()["state"] == "running"
    n = admin.get("/api/notifications").json()
    assert n["unread"] >= 1
    admin.post("/api/notifications/read-all", headers=H)
    assert admin.get("/api/notifications").json()["unread"] == 0


def test_backup_and_verified_restore(admin, org):
    asset(admin, org)
    upload(admin, "invoice")
    b = admin.post("/api/system/backups", json={"label": "t"}, headers=H).json()
    v = admin.post(f"/api/system/backups/{b['name']}/verify", headers=H).json()
    assert v["ok"] and v["integrity"] == "ok" and v["tables"]["assets"] == 1 and v["files"] >= 1


# ---------------------------------------------------------------- migration
def test_legacy_import_dry_run_then_idempotent(admin, org):
    legacy = {"team": [{"id": "t1", "name": "سارة", "dept": "التسويق", "role": "مصممة", "status": "active"},
                       {"id": "t2", "name": "طارق", "active": False}],
              "assets": [{"id": "a1", "tag": "DEV-0001", "type": "laptop", "brand": "Dell", "model": "XPS",
                          "status": "assigned", "holderId": "t1"},
                         {"id": "a2", "tag": "IT-9", "type": "camera", "status": "stock"}],
              "lines": [{"id": "l1", "country": "SA", "number": "+9665", "carrier": "STC", "status": "assigned",
                         "holderId": "t1", "renewDate": "2026-12-01"}],
              "netPackages": [{"id": "n1", "name": "خط 1", "quota": 200, "provider": "WE"}],
              "subscriptions": [{"id": "s1", "name": "Canva", "vendor": "Canva", "category": "تصميم"}],
              "vendors": [{"id": "v1", "name": "B.TECH"}]}
    body = {"data": legacy, "default_company_id": org["company"]}
    dry = admin.post("/api/migration/legacy-import", json=body, headers=H).json()
    assert dry["dry_run"] and dry["assets"] == 1 and dry["employees"] == 2 and len(dry["unmapped"]) == 1
    assert admin.get("/api/assets").json() == []  # nothing written
    real = admin.post("/api/migration/legacy-import", json=body | {"dry_run": False}, headers=H).json()
    assert real["assets"] == 1 and real["sims"] == 1
    sims = admin.get("/api/technical/sims").json()
    assert sims[0]["due_state"] == "incomplete"  # legacy renewDate not turned into a fake payment
    again = admin.post("/api/migration/legacy-import", json=body | {"dry_run": False}, headers=H).json()
    assert again["assets"] == 0 and again["skipped_existing"] >= 4
    exited = [e for e in admin.get("/api/employees").json() if e["full_name"] == "طارق"][0]
    assert exited["custody_frozen"] is True
    assert json.dumps(real)
