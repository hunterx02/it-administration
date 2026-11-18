"""Background worker — a separate process (keeps running when the browser is closed, stops when the host
is powered off; on restart it catches up: notifications recomputed, sheets/forms re-synced).

    python -m app.worker            run forever
    python -m app.worker --once     one cycle (used by tests / Task Scheduler)
"""
from __future__ import annotations

import logging
import sys
import time
from types import SimpleNamespace

from .config import load_settings
from .db import Database
from .modules.automation import worker_tick
from .seed import seed

log = logging.getLogger("itadm.worker")


def build_state() -> SimpleNamespace:
    settings = load_settings()
    db = Database(settings.database_url)
    db.create_all()
    with db.SessionLocal() as s:
        seed(s)
    return SimpleNamespace(settings=settings, db=db)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    state = build_state()
    once = "--once" in argv
    while True:
        try:
            summary = worker_tick(state)
            log.info("tick ok: notifications=%s scripts=%s sync=%s", summary.get("notifications_created"),
                     len(summary.get("scripts", [])), summary.get("sync"))
        except Exception:  # noqa: BLE001
            log.exception("tick failed")
        if once:
            return 0
        time.sleep(state.settings.worker_interval_seconds)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
