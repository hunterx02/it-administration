"""Runtime settings.

Values come from environment variables, optionally loaded from a local `.env` file
that lives OUTSIDE version control. No secret has a default value here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE per line). Existing env vars win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    env: str
    database_url: str
    frontend_dir: Path
    session_hours: int
    cookie_secure: bool
    allowed_origins: tuple[str, ...]
    data_dir: Path = BACKEND_DIR / "data"
    worker_interval_seconds: int = 60


def load_settings() -> Settings:
    _load_dotenv(BACKEND_DIR / ".env")
    data_dir = Path(os.environ.get("ITADM_DATA_DIR", BACKEND_DIR / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        env=os.environ.get("ITADM_ENV", "local"),
        database_url=os.environ.get("ITADM_DATABASE_URL", f"sqlite:///{(data_dir / 'itadmin.db').as_posix()}"),
        frontend_dir=Path(os.environ.get("ITADM_FRONTEND_DIR", PROJECT_DIR / "frontend")),
        session_hours=int(os.environ.get("ITADM_SESSION_HOURS", "12")),
        cookie_secure=os.environ.get("ITADM_COOKIE_SECURE", "false").lower() == "true",
        allowed_origins=tuple(o for o in os.environ.get("ITADM_ALLOWED_ORIGINS", "").split(",") if o),
        data_dir=data_dir,
        worker_interval_seconds=int(os.environ.get("ITADM_WORKER_INTERVAL", "60")),
    )
