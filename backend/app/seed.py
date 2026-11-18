"""Idempotent seeding: schema version, default roles, Integration Center cards.

No users and no passwords are seeded. The first Super Admin is created with
`python -m app.cli create-admin` (password typed interactively).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import IntegrationConfig, Role, RolePermission, SchemaVersion
from .rbac import DEFAULT_ROLES, SYSTEM_ROLES

SCHEMA_VERSION = 4

# (key, module, title_ar, provider, mode)
CONNECTORS: list[tuple[str, str, str, str, str]] = [
    ("m02_assets_sheet", "M02", "شيت الأصول والعهد", "google_sheets", "sheets"),
    ("m03_application_form", "M03", "استمارة التقديم (Google Form + QR)", "google_forms", "sheets"),
    ("m03_hr_sheet", "M03", "شيت رحلة الموظف (HR فقط)", "google_sheets", "sheets"),
    ("m03_drive", "M03", "مستندات الموظفين على Drive", "google_drive", "api"),
    ("m04_sims_eg_sheet", "M04", "شيت الشرائح المصرية", "google_sheets", "sheets"),
    ("m04_sims_sa_sheet", "M04", "شيت الشرائح السعودية", "google_sheets", "sheets"),
    ("m04_services_sheet", "M04", "شيت الخدمات التقنية والتجديدات", "google_sheets", "sheets"),
    ("m04_we_internet", "M04", "قراءة استهلاك إنترنت WE", "we_portal", "manual"),
    ("m04_ai_subscriptions", "M04", "استهلاك اشتراكات AI والأدوات", "provider_api", "manual"),
    ("m04_backup_confirmations", "M04", "تأكيد نسخ WhatsApp الاحتياطي", "manual", "manual"),
    ("m05_finance_sheet", "M05", "شيت مالية IT", "google_sheets", "sheets"),
    ("m05_drive", "M05", "فواتير واعتمادات المشتريات على Drive", "google_drive", "api"),
    ("m06_admin_sheet", "M06", "شيت إدارة النظام", "google_sheets", "sheets"),
    ("m06_notifications", "M06", "الإشعارات الخارجية (Email / Telegram / WhatsApp)", "none", "manual"),
]


def seed(db: Session) -> None:
    from .modules.assets import seed_categories
    from .modules.finance import seed_finance
    from .modules.technical import seed_lookups
    upgrading = db.get(SchemaVersion, SCHEMA_VERSION) is None
    if upgrading:
        db.add(SchemaVersion(version=SCHEMA_VERSION, note="RC1: M02-M06 modules, reports, sync, automation"))

    for key, (name_ar, perms) in DEFAULT_ROLES.items():
        role = db.scalar(select(Role).where(Role.key == key))
        if role is None:
            role = Role(key=key, name_ar=name_ar, is_system=key in SYSTEM_ROLES)
            role.permissions = [RolePermission(permission=p) for p in perms]
            db.add(role)
        elif upgrading and key not in SYSTEM_ROLES:
            # schema upgrade: default roles move to the new default permission set — as a diff, so unchanged
            # permissions are kept (replacing the whole list would insert duplicates before deleting the old rows)
            want = set(perms)
            for rp in list(role.permissions):
                if rp.permission not in want:
                    role.permissions.remove(rp)
            have = {rp.permission for rp in role.permissions}
            role.permissions.extend(RolePermission(permission=p) for p in perms if p not in have)
        elif key in SYSTEM_ROLES:
            # super_admin always holds the full catalog (new permissions get added on upgrade)
            have = {rp.permission for rp in role.permissions}
            role.permissions.extend(RolePermission(permission=p) for p in perms if p not in have)

    for key, module, title, provider, mode in CONNECTORS:
        if db.scalar(select(IntegrationConfig).where(IntegrationConfig.key == key)) is None:
            db.add(IntegrationConfig(key=key, module=module, title_ar=title, provider=provider, mode=mode,
                                     enabled=False, status="not_configured"))
    seed_categories(db)
    seed_lookups(db)
    seed_finance(db)
    from .modules.lists import seed_lists
    seed_lists(db)
    db.commit()
