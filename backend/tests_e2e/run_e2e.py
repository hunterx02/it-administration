"""Real-browser end-to-end check (Edge/Chrome headless via the DevTools protocol — no extra install).

Runs against a THROWAWAY database: creates a temp data dir + admin, starts the server on a free port, seeds demo data
through the API, logs in through the real login form, opens every page, and fails on any JS exception, visible error
box or missing page title. Your real data/ folder is never touched.

    backend\\.venv\\Scripts\\python.exe tests_e2e\\run_e2e.py        (or run_e2e.bat)
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
import websockets

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
H = {"X-ITADM-Request": "1"}
EMAIL, PASSWORD = "e2e@example.test", "E2eAdminPass1"
BROWSERS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_admin(data_dir: Path) -> None:
    os.environ["ITADM_DATA_DIR"] = str(data_dir)
    os.environ["ITADM_SECRETS_PATH"] = str(data_dir / "secrets.enc.json")
    os.environ.setdefault("ITADM_SECRET_KEY", base64.b64encode(os.urandom(32)).decode())
    from sqlalchemy import select

    from app.config import load_settings
    from app.db import Database
    from app.models import Role, User
    from app.security import hash_password
    from app.seed import seed
    db = Database(load_settings().database_url)
    db.create_all()
    with db.SessionLocal() as s:
        seed(s)
        role = s.scalar(select(Role).where(Role.key == "super_admin"))
        s.add(User(email=EMAIL, name="E2E Admin", password_hash=hash_password(PASSWORD), role_id=role.id, all_companies=True))
        s.commit()


def seed_demo(base: str) -> dict:
    c = httpx.Client(base_url=base, headers=H, timeout=30)
    assert c.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 200
    co = c.post("/api/companies", json={"name": "شركة اختبار E2E", "asset_prefix": "E2E"}).json()
    dep = c.post(f"/api/companies/{co['id']}/departments", json={"name": "IT"}).json()["id"]
    emp = c.post("/api/employees", json={"company_id": co["id"], "full_name": "موظف تجريبي", "gender": "male",
                                         "department_id": dep}).json()
    a = c.post("/api/assets", json={"company_id": co["id"], "category_code": "LTP", "model": "Test", "serial": "E2E-1"}).json()
    c.post(f"/api/assets/{a['id']}/handover", json={"employee_id": emp["id"]})
    ln = c.post("/api/technical/lines", json={"company_id": co["id"], "name": "خط تجريبي", "is_primary": True,
                                              "quota_gb": 500, "warn_gb": 100, "critical_gb": 20}).json()
    for i, v in enumerate((300, 150, 90)):
        c.post("/api/technical/readings", json={"target_type": "line", "target_id": ln["id"], "remaining": v,
                                                 "read_at": f"2026-11-{10 + i}T09:00:00+00:00"})
    sim = c.post("/api/technical/sims", json={"country": "SA", "company_id": co["id"], "number": "+966500000000"}).json()
    pr = c.post("/api/finance/purchase-requests", json={"company_id": co["id"], "requester": "IT", "reason": "اختبار",
                                                         "items": [{"item": "x", "qty": 1, "est_unit_cost": 1}]}).json()
    ev = c.post("/api/finance/expenses", json={"company_id": co["id"], "kind": "other", "description": "اختبار",
                                               "amount": 10, "currency": "EGP", "occurred_on": str(date.today())}).json()
    app_ = c.post("/api/journey/applications", json={"company_id": co["id"], "position": "Dev", "full_name": "مرشح"}).json()
    mb = c.post("/api/mailboxes", json={"company_id": co["id"], "address": "e2e.user@example.com",
                                        "employee_id": emp["id"], "provider": "Google Workspace"}).json()
    off = c.post("/api/journey/offboarding", json={"employee_id": emp["id"], "last_day": str(date.today() + timedelta(days=2))}).json()
    return {"asset": a["id"], "employee": emp["id"], "line": ln["id"], "sim": sim["id"], "purchase": pr["id"],
            "event": ev["id"], "application": app_["id"], "offboard": off["id"], "mailbox": mb["id"]}


PAGES = ["dashboard", "alerts", "assets", "asset/{asset}", "custody", "stock", "maintenance", "employees",
         "employee/{employee}", "recruitment", "application/{application}", "onboarding", "offboarding",
         "offboard/{offboard}", "documents", "renewals", "lines", "line/{line}", "sims-EG", "sims-SA", "sim/{sim}",
         "subscriptions", "mailboxes", "mailbox/{mailbox}", "security", "suppliers", "purchases", "purchase/{purchase}", "expenses", "event/{event}",
         "budgets", "users", "roles", "companies", "excel", "lists", "integrations", "integrations?tab=secrets",
         "integrations?tab=conflicts", "integrations?tab=scripts", "integrations?tab=worker", "appearance", "logs",
         "logs?tab=backups"]


async def browse(base: str, ids: dict, shots: Path | None) -> int:
    exe = next((b for b in BROWSERS if Path(b).is_file()), None)
    if not exe:
        print("SKIP: no Edge/Chrome found")
        return 0
    port = free_port()
    proc = subprocess.Popen([exe, "--headless=new", "--disable-gpu", "--no-first-run", f"--user-data-dir={tempfile.mkdtemp()}",
                             f"--remote-debugging-port={port}", "--window-size=1400,1000", "about:blank"])
    try:
        for _ in range(100):
            try:
                tabs = httpx.get(f"http://127.0.0.1:{port}/json").json()
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        ws_url = [t for t in tabs if t["type"] == "page"][0]["webSocketDebuggerUrl"]
        async with websockets.connect(ws_url, max_size=80_000_000) as w:
            mid, errors = 0, []

            async def cmd(method: str, params: dict | None = None) -> dict:
                nonlocal mid
                mid += 1
                my = mid
                await w.send(json.dumps({"id": my, "method": method, "params": params or {}}))
                while True:
                    m = json.loads(await w.recv())
                    if m.get("method") == "Runtime.exceptionThrown":
                        errors.append(m["params"]["exceptionDetails"].get("exception", {}).get("description", "")[:200])
                    if m.get("id") == my:
                        return m.get("result", {})

            async def ev(expr: str):  # noqa: ANN202
                return (await cmd("Runtime.evaluate", {"expression": expr, "awaitPromise": True,
                                                       "returnByValue": True})).get("result", {}).get("value")

            await cmd("Runtime.enable")
            await cmd("Page.enable")
            await cmd("Page.navigate", {"url": base + "/app.html"})
            await asyncio.sleep(2.5)
            await ev(f"(()=>{{const f=document.querySelector('.login-card2');f.email.value='{EMAIL}';f.password.value='{PASSWORD}';f.requestSubmit();}})()")
            await asyncio.sleep(3)
            fails = 0
            if await ev("document.querySelectorAll('#nav .nav-group').length") != 6:
                print("FAIL navigation does not have 6 groups")
                fails += 1
            for p in PAGES:
                url, before = p.format(**ids), len(errors)
                await ev(f"location.hash='#/{url}'")
                await asyncio.sleep(1.5)
                bad = await ev("Array.from(document.querySelectorAll('#main .notice.bad')).map(x=>x.textContent).join(' | ')")
                h1 = await ev("(document.querySelector('#main h1')||{}).textContent||''")
                ok = not bad and not errors[before:] and h1
                fails += 0 if ok else 1
                print(f"{'OK  ' if ok else 'FAIL'} {url:34} {h1[:40]!r} {bad[:120] if bad else ''} {errors[before:] or ''}")
                if shots and p in ("dashboard", "lines", "excel"):
                    s = await cmd("Page.captureScreenshot", {"format": "png"})
                    (shots / f"{p}.png").write_bytes(base64.b64decode(s["data"]))
            await cmd("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True})
            await ev("location.hash='#/dashboard'; App.render()")
            await asyncio.sleep(2)
            if await ev("document.documentElement.scrollWidth > window.innerWidth + 2"):
                print("FAIL mobile layout has horizontal overflow")
                fails += 1
            print(f"pages: {len(PAGES)}  failures: {fails}")
            return fails
    finally:
        proc.kill()


def main() -> int:
    data_dir = Path(tempfile.mkdtemp(prefix="itadm-e2e-"))
    make_admin(data_dir)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "127.0.0.1",
                            "--port", str(port)], cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                if httpx.get(base + "/api/health").status_code == 200:
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        ids = seed_demo(base)
        shots = Path(os.environ["E2E_SHOTS"]) if os.environ.get("E2E_SHOTS") else None
        return 1 if asyncio.run(browse(base, ids, shots)) else 0
    finally:
        srv.terminate()


if __name__ == "__main__":
    sys.exit(main())
