from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.models import Permission, Role, RolePermission, User, UserRole


PERMISSION_DEFINITIONS = [
    ("view", "View", "View allowed business sections and records."),
    ("create", "Create", "Create new business records."),
    ("edit", "Edit", "Edit existing business records."),
    ("soft_delete", "Soft delete", "Archive records without physical deletion."),
    ("approve", "Approve", "Approve or sign off records and documents."),
    ("comment", "Comment", "Add review comments."),
    ("icsr_workflow", "ICSR workflow", "Move ICSR cases through controlled workflow states."),
    ("create_related_requests", "Create related requests", "Create related partner requests without editing controlled content."),
    ("upload", "Upload", "Upload documents and attachments."),
    ("manage_document_versions", "Manage document versions", "Create and maintain controlled document versions."),
    ("manage_sop_versions", "Manage SOP versions", "Create and maintain SOP and instruction versions."),
    ("export", "Export", "Export reports and datasets."),
    ("audit_view", "Audit view", "View audit trail entries."),
    ("view_sensitive_audit", "View sensitive audit", "View sensitive audit details such as IP address and correlation IDs."),
    ("switch_demo_user", "Switch demo user", "Switch between seeded users when demo mode is enabled."),
    ("manage_users", "Manage users", "Manage users, roles and permissions."),
    ("manage_reference_data", "Manage reference data", "Maintain dictionaries and reference data."),
    ("manage_system_settings", "Manage system settings", "Change system-level settings."),
]

PERMISSION_CODES = [code for code, _, _ in PERMISSION_DEFINITIONS]

ROLE_PRESETS = {
    "admin": {
        "name": "Admin / Системный администратор",
        "description": "Technical system administrator with full MVP access.",
        "permissions": set(PERMISSION_CODES),
    },
    "pv_responsible": {
        "name": "PV Responsible / УЛФ",
        "description": "Primary pharmacovigilance business owner.",
        "permissions": {
            "view",
            "create",
            "edit",
            "icsr_workflow",
            "approve",
            "comment",
            "upload",
            "manage_document_versions",
            "manage_sop_versions",
            "export",
            "audit_view",
            "view_sensitive_audit",
            "switch_demo_user",
            "manage_reference_data",
        },
    },
    "deputy_pv_responsible": {
        "name": "Deputy PV Responsible / Заместитель УЛФ",
        "description": "Deputy PV user for business continuity.",
        "permissions": {
            "view",
            "create",
            "edit",
            "icsr_workflow",
            "comment",
            "upload",
            "manage_document_versions",
            "manage_sop_versions",
            "export",
            "switch_demo_user",
        },
    },
    "qa_reviewer": {
        "name": "QA Reviewer / Руководитель качества",
        "description": "Internal quality reviewer.",
        "permissions": {
            "view",
            "icsr_workflow",
            "approve",
            "comment",
            "export",
            "audit_view",
            "view_sensitive_audit",
            "switch_demo_user",
        },
    },
    "initiator": {
        "name": "Initiator / Request initiator",
        "description": "Can create related requests and comments without editing PSUR content.",
        "permissions": {
            "view",
            "comment",
            "create_related_requests",
            "switch_demo_user",
        },
    },
    "readonly_auditor": {
        "name": "Read-only Auditor / Viewer",
        "description": "Read-only auditor, inspector or temporary viewer.",
        "permissions": {
            "view",
            "audit_view",
            "switch_demo_user",
        },
    },
    "executive_approver": {
        "name": "Executive Approver / Подписант",
        "description": "Minimal future approver role, without full PV database access.",
        "permissions": {
            "approve",
            "comment",
            "switch_demo_user",
        },
    },
}

LEGACY_ROLE_MAP = {
    "admin": "admin",
    "pv_responsible": "pv_responsible",
    "viewer": "readonly_auditor",
    "readonly": "readonly_auditor",
}


def ensure_rbac_defaults(db: Session) -> None:
    permissions_by_code = {}
    for code, name, description in PERMISSION_DEFINITIONS:
        permission = (
            db.query(Permission)
            .filter(Permission.permission_code == code)
            .first()
        )
        if not permission:
            permission = Permission(
                permission_code=code,
                permission_name=name,
                description=description,
            )
            db.add(permission)
            db.flush()
        else:
            permission.permission_name = name
            permission.description = description
        permissions_by_code[code] = permission

    roles_by_code = {}
    for role_code, preset in ROLE_PRESETS.items():
        role = db.query(Role).filter(Role.role_code == role_code).first()
        if not role:
            role = Role(
                role_code=role_code,
                role_name=preset["name"],
                description=preset["description"],
                is_system=True,
            )
            db.add(role)
            db.flush()
        else:
            role.role_name = preset["name"]
            role.description = preset["description"]
        roles_by_code[role_code] = role

        role_permissions_by_code = {
            role_permission.permission.permission_code: role_permission
            for role_permission in role.role_permissions
            if role_permission.permission
        }
        existing_codes = {
            role_permission.permission.permission_code
            for role_permission in role.role_permissions
            if not role_permission.is_deleted and role_permission.permission
        }
        should_seed_role = role_code == "admin" or not existing_codes
        if should_seed_role:
            for permission_code in preset["permissions"]:
                if permission_code in existing_codes:
                    continue
                existing_link = role_permissions_by_code.get(permission_code)
                if existing_link:
                    existing_link.is_deleted = False
                    existing_link.deleted_at = None
                    existing_link.deleted_by = None
                    existing_link.delete_reason = None
                else:
                    db.add(
                        RolePermission(
                            role_id=role.id,
                            permission_id=permissions_by_code[permission_code].id,
                        )
                    )

    users = db.query(User).filter(User.is_deleted.is_(False)).all()
    if not users:
        admin_user = User(
            email="admin@example.com",
            full_name="PV Administrator",
            role="admin",
        )
        db.add(admin_user)
        db.flush()
        users = [admin_user]

    for user in users:
        if any(not user_role.is_deleted for user_role in user.user_roles):
            continue
        role_code = LEGACY_ROLE_MAP.get(user.role, "readonly_auditor")
        role = roles_by_code.get(role_code)
        if role:
            existing_link = next(
                (
                    user_role
                    for user_role in user.user_roles
                    if user_role.role_id == role.id
                ),
                None,
            )
            if existing_link:
                existing_link.is_deleted = False
                existing_link.deleted_at = None
                existing_link.deleted_by = None
                existing_link.delete_reason = None
            else:
                db.add(UserRole(user_id=user.id, role_id=role.id))

    db.commit()


def permission_codes_for_user(user: User | None) -> set[str]:
    if not user:
        return set()

    codes: set[str] = set()
    for user_role in user.user_roles:
        if user_role.is_deleted:
            continue
        role = user_role.role
        if not role or role.is_deleted:
            continue
        for role_permission in role.role_permissions:
            permission = role_permission.permission
            if permission and not permission.is_deleted and permission.is_active:
                codes.add(permission.permission_code)

    if not codes:
        role_code = LEGACY_ROLE_MAP.get(user.role)
        if role_code:
            codes.update(ROLE_PRESETS[role_code]["permissions"])

    return codes


def role_names_for_user(user: User | None) -> list[str]:
    if not user:
        return []
    names = [
        user_role.role.role_name
        for user_role in user.user_roles
        if not user_role.is_deleted and user_role.role and not user_role.role.is_deleted
    ]
    if names:
        return names
    role_code = LEGACY_ROLE_MAP.get(user.role)
    if role_code:
        return [ROLE_PRESETS[role_code]["name"]]
    return []


def user_state(user: User | None) -> SimpleNamespace | None:
    if not user:
        return None
    return SimpleNamespace(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
    )
