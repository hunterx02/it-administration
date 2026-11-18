"""Admin CLI.

    python -m app.cli init-db         create tables + seed roles/connectors
    python -m app.cli create-admin    create the first Super Admin (password typed, never stored in files)
    python -m app.cli backup          create a backup zip (db + files + checksums)
    python -m app.cli restore <zip>   restore (server & worker must be stopped; pre-restore backup kept)
"""
from __future__ import annotations

import getpass
import sys

from sqlalchemy import func, select

from . import audit
from .config import load_settings
from .db import Database
from .models import Role, User
from .security import hash_password, password_problems
from .seed import seed


def _db() -> Database:
    db = Database(load_settings().database_url)
    db.create_all()
    with db.SessionLocal() as s:
        seed(s)
    return db


def init_db() -> int:
    _db()
    print("تم تجهيز قاعدة البيانات.")
    return 0


def create_admin() -> int:
    db = _db()
    email = input("البريد الإلكتروني: ").strip().lower()
    name = input("الاسم: ").strip()
    pw = getpass.getpass("كلمة المرور (مش هتظهر وانت بتكتب): ")
    if pw != getpass.getpass("أكد كلمة المرور: "):
        print("كلمتين المرور مش متطابقين.")
        return 1
    problems = password_problems(pw)
    if problems or not email or "@" not in email or len(name) < 2:
        print("بيانات غير صالحة:", " · ".join(problems) or "راجع البريد والاسم")
        return 1
    with db.SessionLocal() as s:
        if s.scalar(select(User).where(func.lower(User.email) == email)):
            print("البريد مستخدم بالفعل.")
            return 1
        role = s.scalar(select(Role).where(Role.key == "super_admin"))
        u = User(email=email, name=name, password_hash=hash_password(pw), role_id=role.id, all_companies=True)
        s.add(u)
        s.flush()
        audit.record(s, "user.created", entity="user", entity_id=u.id, details={"email": email, "via": "cli"})
        s.commit()
    print(f"تم إنشاء Super Admin: {email}")
    return 0


def backup() -> int:
    from .modules.automation import create_backup
    s = load_settings()
    _db()
    out = create_backup(s.database_url, s.data_dir, "cli")
    print(f"تم: {out}")
    return 0


def restore(name: str | None) -> int:
    """Run ONLY while the server and worker are stopped."""
    from pathlib import Path

    from .modules.automation import backup_dir, restore_backup
    s = load_settings()
    if not name:
        print("حدد اسم ملف النسخة. المتاح:")
        for f in sorted(backup_dir(s.data_dir).glob("*.zip"), reverse=True):
            print("  ", f.name)
        return 2
    path = Path(name) if Path(name).is_file() else backup_dir(s.data_dir) / name
    if input(f"هيتم استبدال البيانات الحالية بـ {path.name}. اكتب YES للتأكيد: ").strip() != "YES":
        print("اتلغى.")
        return 1
    r = restore_backup(path, s.database_url, s.data_dir)
    print(f"تمت الاستعادة. نسخة ما قبل الاستعادة: {r['pre_restore_backup']}")
    return 0


def main(argv: list[str]) -> int:
    cmds = {"init-db": init_db, "create-admin": create_admin, "backup": backup}
    if len(argv) >= 2 and argv[1] == "restore":
        return restore(argv[2] if len(argv) > 2 else None)
    if len(argv) < 2 or argv[1] not in cmds:
        print(__doc__)
        return 2
    return cmds[argv[1]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
