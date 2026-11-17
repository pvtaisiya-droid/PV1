def test_active_role_catalog_contains_only_current_roles(client):
    from app import crud
    from app.database import SessionLocal

    with SessionLocal() as db:
        roles = crud.list_roles(db)

    assert {role.role_code: role.role_name for role in roles} == {
        "admin": "Системный администратор",
        "pv_responsible": "Специалист по фармаконадзору",
        "qa_reviewer": "Рецензент по качеству",
        "readonly_auditor": "Аудитор / Наблюдатель",
    }


def test_obsolete_roles_are_retired_and_assignments_are_migrated(client):
    from app.database import SessionLocal
    from app.models import Permission, Role, RolePermission, User, UserRole
    from app.rbac import ensure_rbac_defaults, retire_obsolete_roles

    with SessionLocal() as db:
        deputy_role = Role(
            role_code="deputy_pv_responsible",
            role_name="Deputy PV Responsible",
        )
        executive_role = Role(
            role_code="executive_approver",
            role_name="Executive Approver",
        )
        initiator_role = Role(
            role_code="initiator",
            role_name="Initiator",
        )
        deputy_user = User(
            email="deputy@example.com",
            full_name="Deputy",
            role="deputy_pv_responsible",
        )
        executive_user = User(
            email="executive@example.com",
            full_name="Executive",
            role="executive_approver",
        )
        db.add_all(
            [
                deputy_role,
                executive_role,
                initiator_role,
                deputy_user,
                executive_user,
            ]
        )
        db.flush()
        deputy_only_permission = (
            db.query(Permission)
            .filter(Permission.permission_code == "soft_delete")
            .one()
        )
        db.add_all(
            [
                UserRole(user_id=deputy_user.id, role_id=deputy_role.id),
                UserRole(user_id=executive_user.id, role_id=executive_role.id),
                RolePermission(
                    role_id=deputy_role.id,
                    permission_id=deputy_only_permission.id,
                ),
            ]
        )
        db.commit()

        # Role retirement is an explicit maintenance action, never startup.
        roles_by_code = {role.role_code: role for role in db.query(Role).all()}
        retire_obsolete_roles(db, roles_by_code)
        db.commit()
        ensure_rbac_defaults(db)

        retired_roles = (
            db.query(Role)
            .filter(
                Role.role_code.in_(
                    {
                        "deputy_pv_responsible",
                        "executive_approver",
                        "initiator",
                    }
                )
            )
            .all()
        )
        assert all(role.is_deleted and not role.is_active for role in retired_roles)

        db.expire_all()
        deputy_user = db.query(User).filter(User.email == "deputy@example.com").one()
        executive_user = db.query(User).filter(User.email == "executive@example.com").one()
        deputy_codes = {
            link.role.role_code
            for link in deputy_user.user_roles
            if not link.is_deleted and not link.role.is_deleted
        }
        executive_codes = {
            link.role.role_code
            for link in executive_user.user_roles
            if not link.is_deleted and not link.role.is_deleted
        }
        specialist_role = (
            db.query(Role).filter(Role.role_code == "pv_responsible").one()
        )
        specialist_permissions = {
            link.permission.permission_code
            for link in specialist_role.role_permissions
            if not link.is_deleted
        }

    assert deputy_user.role == "pv_responsible"
    assert deputy_codes == {"pv_responsible"}
    assert executive_user.role == "viewer"
    assert executive_codes == {"readonly_auditor"}
    assert "soft_delete" in specialist_permissions
