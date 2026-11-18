"""Permission catalog and default roles.

Permission string = "<module>:<action>". Enforcement happens server-side in
`deps.require()`; hiding a button in the UI is never the protection.
Roles other than super_admin are editable later from M06; these are seed defaults.

Sources: M04 FINAL §0.8 and M05 FINAL §9 — editing technical services and IT
finance is Super Admin + IT Manager only in the current phase. M03: HR owns the
final hiring decision (`employees:approve`); IT approves only its own scope
(`employees:it_approve`) and never sees sensitive HR data by default.
"""
from __future__ import annotations

MODULES: dict[str, str] = {
    "dashboard": "الرئيسية",
    "assets": "الأصول والعهد",
    "employees": "رحلة الموظف",
    "technical": "الخدمات التقنية",
    "finance": "الإدارة والمالية",
    "system": "إدارة النظام",
    "reports": "التقارير",
}

ACTIONS: dict[str, list[str]] = {
    "dashboard": ["view"],
    "assets": ["view", "create", "edit", "delete", "approve", "print", "export"],
    # view_sensitive = national ID, salary, HR evaluations/notes, identity documents.
    # it_approve = IT's own approvals inside onboarding/offboarding.
    "employees": ["view", "create", "edit", "delete", "approve", "it_approve", "print", "export", "view_sensitive"],
    "technical": ["view", "create", "edit", "delete", "approve", "print", "export"],
    "finance": ["view", "create", "edit", "delete", "approve", "print", "export"],
    "system": ["view", "edit", "manage_users", "manage_integration", "manage_secrets"],
    "reports": ["view", "print", "export"],
}

ALL_PERMISSIONS: list[str] = [f"{m}:{a}" for m, acts in ACTIONS.items() for a in acts]


def _all(module: str, exclude: tuple[str, ...] = ()) -> list[str]:
    return [f"{module}:{a}" for a in ACTIONS[module] if a not in exclude]


DEFAULT_ROLES: dict[str, tuple[str, list[str]]] = {
    "super_admin": ("مدير النظام (Super Admin)", list(ALL_PERMISSIONS)),
    "it_manager": ("مدير IT", [
        "dashboard:view", *_all("assets"), *_all("technical"), *_all("finance"),
        "employees:view", "employees:it_approve", "employees:print",
        *_all("reports"), "system:view", "system:manage_integration",
    ]),
    "it_staff": ("موظف IT", [
        "dashboard:view", "assets:view", "assets:create", "assets:edit", "assets:print",
        "technical:view", "employees:view", "reports:view", "reports:print",
    ]),
    "hr": ("الموارد البشرية", [
        "dashboard:view", *_all("employees", exclude=("it_approve",)), "assets:view", *_all("reports"),
    ]),
    "finance": ("المالية", [
        "dashboard:view", "finance:view", "finance:print", "finance:export", "assets:view", "technical:view",
        *_all("reports"),
    ]),
    "management": ("الإدارة العليا", [
        "dashboard:view", "assets:view", "employees:view", "technical:view", "finance:view",
        *_all("reports"),
    ]),
    "custom": ("مخصص", []),
}

SYSTEM_ROLES = {"super_admin"}
