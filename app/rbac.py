from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.models import Permission, Role, RolePermission, User, UserRole, utcnow


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
    ("switch_user", "Switch user", "Switch between active users when the development user switch is enabled."),
    ("manage_users", "Manage users", "Manage users, roles and permissions."),
    ("manage_reference_data", "Manage reference data", "Maintain dictionaries and reference data."),
    ("manage_system_settings", "Manage system settings", "Change system-level settings."),
]

PERMISSION_CODES = [code for code, _, _ in PERMISSION_DEFINITIONS]

ROLE_PRESETS = {
    "admin": {
        "name": "Системный администратор",
        "description": "Полный доступ к системе, включая управление пользователями и настройками.",
        "permissions": set(PERMISSION_CODES),
    },
    "pv_responsible": {
        "name": "Специалист по фармаконадзору",
        "description": "Работа с данными и процессами фармаконадзора.",
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
            "manage_reference_data",
        },
    },
    "qa_reviewer": {
        "name": "Рецензент по качеству",
        "description": "Просмотр, комментирование и подписание.",
        "permissions": {
            "view",
            "approve",
            "comment",
        },
    },
    "readonly_auditor": {
        "name": "Аудитор / Наблюдатель",
        "description": "Доступ только на чтение, включая просмотр журнала аудита.",
        "permissions": {
            "view",
            "audit_view",
        },
    },
}

RETIRED_ROLE_REPLACEMENTS = {
    "deputy_pv_responsible": "pv_responsible",
}

RETIRED_ROLE_CODES = {
    "deputy_pv_responsible",
    "executive_approver",
    "initiator",
}

LEGACY_ROLE_MAP = {
    "admin": "admin",
    "pv_responsible": "pv_responsible",
    "deputy_pv_responsible": "pv_responsible",
    "qa_reviewer": "qa_reviewer",
    "executive_approver": "readonly_auditor",
    "initiator": "readonly_auditor",
    "viewer": "readonly_auditor",
    "readonly": "readonly_auditor",
}


def retire_obsolete_roles(db: Session, roles_by_code: dict[str, Role]) -> None:
    retired_roles = (
        db.query(Role)
        .filter(Role.role_code.in_(RETIRED_ROLE_CODES), Role.is_deleted.is_(False))
        .all()
    )
    if not retired_roles:
        return

    retired_codes_by_id = {role.id: role.role_code for role in retired_roles}
    for retired_role in retired_roles:
        replacement_code = RETIRED_ROLE_REPLACEMENTS.get(retired_role.role_code)
        if not replacement_code:
            continue
        replacement = roles_by_code[replacement_code]
        replacement_links = {
            link.permission_id: link for link in replacement.role_permissions
        }
        for retired_link in retired_role.role_permissions:
            if retired_link.is_deleted or not retired_link.permission:
                continue
            existing_link = replacement_links.get(retired_link.permission_id)
            if existing_link:
                existing_link.is_deleted = False
                existing_link.is_active = True
                existing_link.deleted_at = None
                existing_link.deleted_by = None
                existing_link.delete_reason = None
            else:
                new_link = RolePermission(
                    role_id=replacement.id,
                    permission_id=retired_link.permission_id,
                )
                db.add(new_link)
                replacement_links[retired_link.permission_id] = new_link

    affected_links = (
        db.query(UserRole)
        .filter(
            UserRole.role_id.in_(retired_codes_by_id),
            UserRole.is_deleted.is_(False),
        )
        .all()
    )
    affected_user_ids = {link.user_id for link in affected_links}

    for link in affected_links:
        retired_code = retired_codes_by_id[link.role_id]
        replacement_code = RETIRED_ROLE_REPLACEMENTS.get(retired_code)
        if replacement_code:
            replacement = roles_by_code[replacement_code]
            existing = (
                db.query(UserRole)
                .filter(
                    UserRole.user_id == link.user_id,
                    UserRole.role_id == replacement.id,
                )
                .first()
            )
            if existing:
                existing.is_deleted = False
                existing.is_active = True
                existing.deleted_at = None
                existing.deleted_by = None
                existing.delete_reason = None
            else:
                db.add(UserRole(user_id=link.user_id, role_id=replacement.id))
                db.flush()

        link.is_deleted = True
        link.is_active = False
        link.deleted_at = utcnow()
        link.delete_reason = "Role retired"

    db.flush()
    readonly_role = roles_by_code["readonly_auditor"]
    for user_id in affected_user_ids:
        has_active_role = (
            db.query(UserRole)
            .join(Role, Role.id == UserRole.role_id)
            .filter(
                UserRole.user_id == user_id,
                UserRole.is_deleted.is_(False),
                Role.is_deleted.is_(False),
                ~Role.role_code.in_(RETIRED_ROLE_CODES),
            )
            .first()
        )
        if has_active_role:
            continue
        existing = (
            db.query(UserRole)
            .filter(
                UserRole.user_id == user_id,
                UserRole.role_id == readonly_role.id,
            )
            .first()
        )
        if existing:
            existing.is_deleted = False
            existing.is_active = True
            existing.deleted_at = None
            existing.deleted_by = None
            existing.delete_reason = None
        else:
            db.add(UserRole(user_id=user_id, role_id=readonly_role.id))
            db.flush()

    for role in retired_roles:
        role.is_active = False
        role.is_deleted = True
        role.deleted_at = utcnow()
        role.delete_reason = "Role retired"

    for user in db.query(User).filter(User.role.in_(RETIRED_ROLE_CODES)).all():
        user.role = (
            "pv_responsible"
            if user.role == "deputy_pv_responsible"
            else "viewer"
        )


def ensure_rbac_defaults(db: Session) -> None:
    legacy_user_switch = (
        db.query(Permission)
        .filter(Permission.permission_code == "switch_demo_user")
        .first()
    )
    if legacy_user_switch and not db.query(Permission).filter(Permission.permission_code == "switch_user").first():
        legacy_user_switch.permission_code = "switch_user"
        db.flush()

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
        is_new_role = role is None
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
        should_seed_role = is_new_role
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

    if not db.query(User).first():
        admin_user = User(
            email="pv.admin@company.ru",
            login="pv.admin",
            full_name="Системный администратор",
            role="admin",
        )
        db.add(admin_user)
        db.flush()
        db.add(UserRole(user_id=admin_user.id, role_id=roles_by_code["admin"].id))

    db.commit()


def permission_codes_for_user(user: User | None) -> set[str]:
    if not user or user.is_deleted or not user.is_active:
        return set()

    codes: set[str] = set()
    for user_role in user.user_roles:
        if user_role.is_deleted or not user_role.is_active:
            continue
        role = user_role.role
        if not role or role.is_deleted or not role.is_active:
            continue
        for role_permission in role.role_permissions:
            if role_permission.is_deleted or not role_permission.is_active:
                continue
            permission = role_permission.permission
            if permission and not permission.is_deleted and permission.is_active:
                codes.add(permission.permission_code)

    return codes


def role_names_for_user(user: User | None) -> list[str]:
    if not user or user.is_deleted or not user.is_active:
        return []
    names = [
        user_role.role.role_name
        for user_role in user.user_roles
        if not user_role.is_deleted and user_role.is_active and user_role.role
        and not user_role.role.is_deleted and user_role.role.is_active
    ]
    return names


def user_state(user: User | None) -> SimpleNamespace | None:
    if not user:
        return None
    return SimpleNamespace(
        id=user.id,
        login=user.login or user.email,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
    )
