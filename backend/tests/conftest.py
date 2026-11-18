from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Role, User  # noqa: E402
from app.security import hash_password  # noqa: E402

H = {"X-ITADM-Request": "1"}
ADMIN = ("admin@test.local", "Adm1nPassw0rd")


@pytest.fixture()
def app(tmp_path):
    settings = Settings(env="test", database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}",
                        frontend_dir=tmp_path / "no-frontend", session_hours=1, cookie_secure=False,
                        allowed_origins=(), data_dir=tmp_path)
    app = create_app(settings)
    with app.state.db.SessionLocal() as s:
        role = s.scalar(select(Role).where(Role.key == "super_admin"))
        s.add(User(email=ADMIN[0], name="Admin", password_hash=hash_password(ADMIN[1]), role_id=role.id,
                   all_companies=True))
        s.commit()
    return app


@pytest.fixture()
def client(app):
    return TestClient(app)


def login(client: TestClient, email: str, password: str):
    return client.post("/api/auth/login", json={"email": email, "password": password}, headers=H)


@pytest.fixture()
def admin(client):
    assert login(client, *ADMIN).status_code == 200
    return client
