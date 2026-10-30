import csv
import io
import json
from datetime import date, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy.orm.exc import StaleDataError
from app.concurrency import require_version

from app import schemas
from app.audit import log_audit
from app.auth import get_request_user_id, hash_password, normalize_login
from app.causality import (
    ASSESSOR_ROLE_VALUES,
    NARANJO_ANSWER_VALUES,
    WHO_UMC_VALUES,
    calculate_naranjo_score,
    classify_naranjo_score,
)
from app.case_numbering import (
    add_case_identifier as add_numbering_identifier,
    add_case_partner_link as add_numbering_partner_link,
    assign_case_number,
    normalize_partner_code,
    validate_partner_code,
)
from app.models import (
    AccountingDataLine,
    AccountingDataPeriod,
    AccountingService,
    AuthSession,
    AuditTrail,
    Attachment,
    Case,
    CaseIdentifier,
    CasePartnerLink,
    CaseDrugSubstance,
    CaseLiteratureReference,
    CaseNarrative,
    CaseProduct,
    CaseProductReactionAssessment,
    Contract,
    ContractAccountingTariff,
    ContractContact,
    ContractProduct,
    DosageForm,
    DocumentVersion,
    E2BImportBatch,
    E2BImportIssue,
    E2BImportItem,
    E2BTransmission,
    FollowUp,
    IncomingRequest,
    IncomingRequestCaseLink,
    IncomingRequestProduct,
    LiteratureMonitoringPlan,
    LiteratureMonitoringPlanProduct,
    LiteratureResult,
    LiteratureResultProduct,
    LiteratureSearchLog,
    LiteratureSearchLogProduct,
    Partner,
    PartnerAccountingInfo,
    PartnerOnboardingWizard,
    PartnerProductRegistrationLink,
    PartnerReconciliation,
    PartnerReconciliationItem,
    Patient,
    Permission,
    Product,
    ProductRegistrationCertificate,
    ProductSubstance,
    PSMFBuildDocument,
    PSMFComponent,
    PSMFDocumentLink,
    PSURCase,
    PSURDocument,
    PSURPartnerRequest,
    PSURPlan,
    PSURProduct,
    PSURSection,
    Reaction,
    Role,
    RolePermission,
    SafetyReport,
    SafetyReportProduct,
    SOP,
    SOPVersion,
    Submission,
    Substance,
    Task,
    User,
    UserRole,
    utcnow,
)
from app.rbac import LEGACY_ROLE_MAP
from app.reconciliation_excel import build_xlsx
from app.workflow import validate_case_status, validate_icsr_transition


def normalize_text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value.lower() if value else None


def model_data(payload: Any) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    return dict(payload)


def empty_to_none(value: Any) -> Any:
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


def clean_form_data(data: dict[str, Any]) -> dict[str, Any]:
    return {key: empty_to_none(value) for key, value in data.items()}


AUDIT_EXCLUDED_FIELDS = {
    "product_name_normalized",
    "substance_name_normalized",
}
CASE_LOCKED_STATUSES = {"submitted", "closed"}


def normalized_status(value: str | None) -> str:
    return (value or "").strip().lower()


def case_is_controlled(case: Case) -> bool:
    return bool(case.is_locked) or normalized_status(case.workflow_status) in CASE_LOCKED_STATUSES


def assert_case_editable(case: Case) -> None:
    if case_is_controlled(case):
        raise ValueError(
            "Case is submitted, closed, or locked. Reopen it with a change reason before editing."
        )
    # Child changes must also conflict with a concurrent closure of the parent.
    case.version += 1


def apply_case_lock_state(case: Case, next_status: str, actor_id: str | None) -> None:
    status_code = normalized_status(next_status)
    if status_code in CASE_LOCKED_STATUSES:
        case.is_locked = True
        case.locked_by_user_id = actor_id
        case.locked_at = utcnow()
    elif status_code == "reopened":
        case.is_locked = False
        case.locked_by_user_id = None
        case.locked_at = None


def audit_user_id(db: Session) -> str | None:
    request_user_id = get_request_user_id()
    if request_user_id:
        return request_user_id
    user = get_current_user(db)
    return user.id if user else None


def audit_summary(data: dict[str, Any]) -> str:
    parts = [
        f"{field}={value}"
        for field, value in data.items()
        if field not in AUDIT_EXCLUDED_FIELDS and value is not None
    ]
    return "; ".join(parts)


def log_create_event(
    db: Session,
    *,
    entity_type: str,
    entity_id: str,
    data: dict[str, Any] | None = None,
    case_id: str | None = None,
    user_id: str | None = None,
    comment: str | None = None,
) -> None:
    log_audit(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        action="create",
        case_id=case_id,
        field_name="record",
        new_value=audit_summary(data or {}),
        comment=comment,
        user_id=user_id or audit_user_id(db),
    )


def log_delete_event(
    db: Session,
    *,
    entity_type: str,
    entity_id: str,
    case_id: str | None = None,
    user_id: str | None = None,
    delete_reason: str | None = None,
) -> None:
    log_audit(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        action="soft_delete",
        case_id=case_id,
        field_name="is_deleted",
        old_value=False,
        new_value=True,
        change_reason=delete_reason,
        comment=delete_reason,
        user_id=user_id or audit_user_id(db),
    )


def log_field_changes(
    db: Session,
    *,
    entity_type: str,
    entity_id: str,
    old_values: dict[str, Any],
    new_values: dict[str, Any],
    action: str = "edit",
    case_id: str | None = None,
    user_id: str | None = None,
    comment: str | None = None,
) -> int:
    changed_count = 0
    actor_id = user_id or audit_user_id(db)
    for field, new_value in new_values.items():
        if field in AUDIT_EXCLUDED_FIELDS:
            continue
        old_value = old_values.get(field)
        if old_value == new_value:
            continue
        changed_count += 1
        log_audit(
            db,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            case_id=case_id,
            field_name=field,
            old_value=old_value,
            new_value=new_value,
            comment=comment,
            user_id=actor_id,
        )
    return changed_count


def unique_version_label(
    db: Session,
    model: type,
    parent_field: str,
    parent_id: str,
    requested_label: str | None,
) -> str:
    base_label = (requested_label or "1.0").strip() or "1.0"
    existing_labels = {
        row[0]
        for row in db.query(model.version_label)
        .filter(getattr(model, parent_field) == parent_id)
        .all()
    }
    if base_label not in existing_labels:
        return base_label

    suffix = 2
    while f"{base_label}.{suffix}" in existing_labels:
        suffix += 1
    return f"{base_label}.{suffix}"


def create_document_version_snapshot(
    db: Session,
    attachment: Attachment,
    *,
    actor_id: str | None = None,
    change_reason: str | None = None,
) -> DocumentVersion:
    version = DocumentVersion(
        attachment_id=attachment.id,
        version_label=unique_version_label(
            db,
            DocumentVersion,
            "attachment_id",
            attachment.id,
            attachment.document_version or f"v{attachment.version}",
        ),
        file_name=attachment.file_name,
        document_title=attachment.document_title,
        document_type=attachment.document_type,
        status=attachment.status,
        storage_path=attachment.storage_path,
        file_url=attachment.file_url,
        checksum_sha256=attachment.checksum_sha256,
        file_size_bytes=attachment.file_size_bytes,
        document_date=attachment.document_date,
        valid_until=attachment.valid_until,
        is_signed=attachment.is_signed,
        page_count=attachment.page_count,
        change_reason=change_reason or attachment.comment,
        created_by=actor_id or audit_user_id(db),
    )
    db.add(version)
    db.flush()
    log_create_event(
        db,
        entity_type="DocumentVersion",
        entity_id=version.id,
        data={
            "attachment_id": attachment.id,
            "version_label": version.version_label,
            "file_name": version.file_name,
            "status": version.status,
        },
        user_id=version.created_by,
        comment=version.change_reason,
    )
    return version


def create_sop_version_snapshot(
    db: Session,
    sop: SOP,
    *,
    actor_id: str | None = None,
    revision_reason: str | None = None,
) -> SOPVersion:
    version = SOPVersion(
        sop_id=sop.id,
        version_label=unique_version_label(
            db,
            SOPVersion,
            "sop_id",
            sop.id,
            sop.version,
        ),
        status=sop.status,
        effective_date=sop.effective_date,
        next_review_date=sop.next_review_date,
        file_path=sop.file_path,
        file_url=sop.file_url,
        revision_reason=revision_reason or sop.revision_reason,
        created_by=actor_id or audit_user_id(db),
    )
    db.add(version)
    db.flush()
    log_create_event(
        db,
        entity_type="SOPVersion",
        entity_id=version.id,
        data={
            "sop_id": sop.id,
            "version_label": version.version_label,
            "status": version.status,
            "effective_date": version.effective_date,
            "next_review_date": version.next_review_date,
        },
        user_id=version.created_by,
        comment=version.revision_reason,
    )
    return version


def get_current_user(db: Session) -> User | None:
    request_user_id = get_request_user_id()
    if request_user_id:
        user = (
            db.query(User)
            .filter(
                User.id == request_user_id,
                User.is_deleted.is_(False),
                User.is_active.is_(True),
            )
            .first()
        )
        if user:
            return user
    return (
        db.query(User)
        .filter(User.is_deleted.is_(False))
        .order_by(User.created_at.asc())
        .first()
    )


def generate_number(db: Session, model: type, field_name: str, prefix: str) -> str:
    year = date.today().year
    stem = f"{prefix}-{year}-"
    existing_count = db.query(model).filter(getattr(model, field_name).like(f"{stem}%")).count()
    candidate_number = existing_count + 1
    while True:
        candidate = f"{stem}{candidate_number:04d}"
        exists = (
            db.query(model)
            .filter(getattr(model, field_name) == candidate)
            .first()
            is not None
        )
        if not exists:
            return candidate
        candidate_number += 1


def create_user(db: Session, login: str, full_name: str, role: str = "viewer") -> User:
    normalized_login = normalize_login(login)
    existing = (
        db.query(User)
        .filter(or_(User.login == normalized_login, User.email == login))
        .first()
    )
    if existing:
        if not existing.user_roles:
            role_code = LEGACY_ROLE_MAP.get(role, "readonly_auditor")
            role_record = get_role_by_code(db, role_code)
            if role_record:
                db.add(UserRole(user_id=existing.id, role_id=role_record.id))
                db.commit()
                db.refresh(existing)
        return existing
    user = User(
        email=normalized_login,
        login=normalized_login,
        full_name=full_name,
        role=role,
    )
    db.add(user)
    db.flush()
    role_code = LEGACY_ROLE_MAP.get(role, "readonly_auditor")
    role_record = get_role_by_code(db, role_code)
    if role_record:
        db.add(UserRole(user_id=user.id, role_id=role_record.id))
    db.commit()
    db.refresh(user)
    return user


def list_users(db: Session, include_deleted: bool = False) -> list[User]:
    query = db.query(User).options(
        selectinload(User.user_roles).joinedload(UserRole.role)
    )
    if not include_deleted:
        query = query.filter(User.is_deleted.is_(False))
    return query.order_by(User.full_name, User.login).all()


def get_user(db: Session, user_id: str) -> User | None:
    return (
        db.query(User)
        .options(selectinload(User.user_roles).joinedload(UserRole.role))
        .filter(User.id == user_id, User.is_deleted.is_(False))
        .first()
    )


def create_app_user(
    db: Session,
    *,
    login: str,
    password: str,
    full_name: str | None,
    role_ids: list[str],
    created_by_user_id: str | None = None,
) -> User:
    normalized_login = normalize_login(login)
    user = User(
        email=normalized_login,
        login=normalized_login,
        password_hash=hash_password(password),
        full_name=full_name,
        role="viewer",
    )
    db.add(user)
    db.flush()
    for role_id in role_ids:
        if get_role(db, role_id):
            db.add(
                UserRole(
                    user_id=user.id,
                    role_id=role_id,
                    assigned_by_user_id=created_by_user_id,
                )
            )
    log_create_event(
        db,
        entity_type="User",
        entity_id=user.id,
        data={"login": normalized_login, "full_name": full_name, "roles": ", ".join(role_ids)},
        user_id=created_by_user_id,
    )
    db.commit()
    db.refresh(user)
    return user


def update_app_user(
    db: Session,
    user: User,
    *,
    login: str,
    full_name: str | None,
    changed_by_user_id: str | None = None,
) -> User:
    old_values = {"login": user.login, "full_name": user.full_name}
    user.login = normalize_login(login)
    user.email = user.login
    user.full_name = full_name
    user.version += 1
    log_field_changes(
        db,
        entity_type="User",
        entity_id=user.id,
        old_values=old_values,
        new_values={"login": user.login, "full_name": user.full_name},
        user_id=changed_by_user_id,
    )
    db.commit()
    db.refresh(user)
    return user


def set_user_password(
    db: Session,
    user: User,
    *,
    password: str,
    changed_by_user_id: str | None = None,
) -> User:
    user.password_hash = hash_password(password)
    user.version += 1
    now = utcnow()
    (
        db.query(AuthSession)
        .filter(
            AuthSession.user_id == user.id,
            AuthSession.is_deleted.is_(False),
        )
        .update(
            {
                AuthSession.is_active: False,
                AuthSession.is_deleted: True,
                AuthSession.deleted_at: now,
                AuthSession.version: AuthSession.version + 1,
            },
            synchronize_session=False,
        )
    )
    log_audit(
        db,
        entity_type="User",
        entity_id=user.id,
        action="password_reset",
        field_name="password",
        user_id=changed_by_user_id,
    )
    db.commit()
    db.refresh(user)
    return user


def archive_user(
    db: Session,
    user: User,
    *,
    deleted_by_user_id: str | None,
    delete_reason: str | None,
) -> User:
    user.is_deleted = True
    user.is_active = False
    user.deleted_at = utcnow()
    user.deleted_by = deleted_by_user_id
    user.delete_reason = delete_reason
    user.version += 1
    log_delete_event(
        db,
        entity_type="User",
        entity_id=user.id,
        user_id=deleted_by_user_id,
        delete_reason=delete_reason,
    )
    db.commit()
    db.refresh(user)
    return user


def list_roles(db: Session) -> list[Role]:
    return (
        db.query(Role)
        .options(
            selectinload(Role.role_permissions).joinedload(RolePermission.permission),
            selectinload(Role.user_roles),
        )
        .filter(Role.is_deleted.is_(False))
        .order_by(Role.role_name)
        .all()
    )


def get_role(db: Session, role_id: str) -> Role | None:
    return db.query(Role).filter(Role.id == role_id, Role.is_deleted.is_(False)).first()


def get_role_by_code(db: Session, role_code: str) -> Role | None:
    return (
        db.query(Role)
        .filter(Role.role_code == role_code, Role.is_deleted.is_(False))
        .first()
    )


def list_permissions(db: Session) -> list[Permission]:
    return (
        db.query(Permission)
        .filter(Permission.is_deleted.is_(False))
        .order_by(Permission.permission_code)
        .all()
    )


def assign_user_role(
    db: Session,
    *,
    user_id: str,
    role_id: str,
    assigned_by_user_id: str | None = None,
) -> UserRole:
    existing = (
        db.query(UserRole)
        .filter(
            UserRole.user_id == user_id,
            UserRole.role_id == role_id,
        )
        .first()
    )
    if existing:
        if existing.is_deleted:
            role = get_role(db, role_id)
            existing.is_deleted = False
            existing.deleted_at = None
            existing.deleted_by = None
            existing.delete_reason = None
            existing.assigned_by_user_id = assigned_by_user_id
            existing.assigned_at = utcnow()
            existing.version += 1
            log_audit(
                db,
                entity_type="User",
                entity_id=user_id,
                action="role_assigned",
                field_name="roles",
                new_value=role.role_code if role else role_id,
                user_id=assigned_by_user_id,
            )
            db.commit()
            db.refresh(existing)
        return existing
    user_role = UserRole(
        user_id=user_id,
        role_id=role_id,
        assigned_by_user_id=assigned_by_user_id,
    )
    db.add(user_role)
    db.flush()
    role = get_role(db, role_id)
    log_audit(
        db,
        entity_type="User",
        entity_id=user_id,
        action="role_assigned",
        field_name="roles",
        new_value=role.role_code if role else role_id,
        user_id=assigned_by_user_id,
    )
    db.commit()
    db.refresh(user_role)
    return user_role


def remove_user_role(
    db: Session,
    *,
    user_id: str,
    role_id: str,
    removed_by_user_id: str | None = None,
) -> None:
    user_role = (
        db.query(UserRole)
        .filter(
            UserRole.user_id == user_id,
            UserRole.role_id == role_id,
            UserRole.is_deleted.is_(False),
        )
        .first()
    )
    if not user_role:
        return
    role = user_role.role
    user_role.is_deleted = True
    user_role.deleted_at = utcnow()
    user_role.deleted_by = removed_by_user_id
    user_role.version += 1
    log_audit(
        db,
        entity_type="User",
        entity_id=user_id,
        action="role_removed",
        field_name="roles",
        old_value=role.role_code if role else role_id,
        user_id=removed_by_user_id,
    )
    db.commit()


def update_role_permissions(
    db: Session,
    *,
    role_id: str,
    permission_ids: list[str],
    changed_by_user_id: str | None = None,
) -> Role:
    role = get_role(db, role_id)
    if not role:
        raise ValueError("Role not found")
    permission_ids_set = set(permission_ids)
    all_links = list(role.role_permissions)
    existing_links = [
        link for link in role.role_permissions if not link.is_deleted and link.permission
    ]
    old_codes = sorted(link.permission.permission_code for link in existing_links)
    all_by_permission_id = {link.permission_id: link for link in all_links}

    for link in existing_links:
        if link.permission_id not in permission_ids_set:
            link.is_deleted = True
            link.deleted_at = utcnow()
            link.deleted_by = changed_by_user_id
            link.version += 1

    for permission_id in permission_ids_set:
        existing_link = all_by_permission_id.get(permission_id)
        if existing_link:
            existing_link.is_deleted = False
            existing_link.deleted_at = None
            existing_link.deleted_by = None
            existing_link.delete_reason = None
            existing_link.version += 1
            continue
        if db.query(Permission).filter(Permission.id == permission_id).first():
            db.add(RolePermission(role_id=role_id, permission_id=permission_id))

    db.flush()
    new_codes = []
    if permission_ids_set:
        new_codes = sorted(
            permission.permission_code
            for permission in db.query(Permission)
            .filter(Permission.id.in_(permission_ids_set))
            .all()
        )
    log_audit(
        db,
        entity_type="Role",
        entity_id=role.id,
        action="permission_changed",
        field_name="permissions",
        old_value=", ".join(old_codes),
        new_value=", ".join(new_codes),
        user_id=changed_by_user_id,
    )
    db.commit()
    db.refresh(role)
    return role


def list_partners(db: Session) -> list[Partner]:
    status_order = case(
        (Partner.partner_type == "fn", 0),
        (Partner.partner_type == "registration_in_progress", 1),
        (Partner.partner_type == "archive", 2),
        else_=1,
    )
    return (
        db.query(Partner)
        .filter(Partner.is_deleted.is_(False))
        .order_by(status_order, Partner.partner_name)
        .all()
    )


def get_partner(db: Session, partner_id: str) -> Partner | None:
    return db.query(Partner).filter(Partner.id == partner_id, Partner.is_deleted.is_(False)).first()


def create_partner(db: Session, payload: schemas.PartnerCreate) -> Partner:
    data = clean_form_data(model_data(payload))
    data["partner_code"] = validate_partner_code(data.get("partner_code") or "")
    actor_id = audit_user_id(db)
    partner = Partner(**data, created_by=actor_id, updated_by=actor_id)
    db.add(partner)
    db.flush()
    log_create_event(
        db,
        entity_type="Partner",
        entity_id=partner.id,
        data=data,
    )
    db.commit()
    db.refresh(partner)
    return partner


def update_partner(db: Session, partner: Partner, payload: schemas.PartnerCreate) -> Partner:
    data = clean_form_data(model_data(payload))
    requested_code = normalize_partner_code(data.get("partner_code") or "")
    if requested_code == normalize_partner_code(partner.partner_code):
        # Historical partner codes may contain Cyrillic or other legacy
        # characters. Editing unrelated partner fields must not rewrite them.
        requested_code = partner.partner_code
    else:
        requested_code = validate_partner_code(requested_code)
    data["partner_code"] = requested_code
    old_values = {field: getattr(partner, field, None) for field in data}
    for field, value in data.items():
        setattr(partner, field, value)
    partner.updated_by = audit_user_id(db)
    partner.version += 1
    log_field_changes(
        db,
        entity_type="Partner",
        entity_id=partner.id,
        old_values=old_values,
        new_values=data,
    )
    db.commit()
    db.refresh(partner)
    return partner


def create_partner_onboarding_wizard(
    db: Session,
    *,
    partner_id: str | None = None,
    user_id: str | None = None,
) -> PartnerOnboardingWizard:
    wizard = PartnerOnboardingWizard(
        partner_id=partner_id,
        current_step=1,
        completed_steps="",
        status="draft",
        user_id=user_id,
    )
    db.add(wizard)
    db.commit()
    db.refresh(wizard)
    return wizard


def get_partner_onboarding_wizard(
    db: Session,
    wizard_id: str,
) -> PartnerOnboardingWizard | None:
    return (
        db.query(PartnerOnboardingWizard)
        .options(joinedload(PartnerOnboardingWizard.partner))
        .filter(
            PartnerOnboardingWizard.id == wizard_id,
            PartnerOnboardingWizard.is_deleted.is_(False),
        )
        .first()
    )


def get_latest_partner_onboarding_wizard(
    db: Session,
    partner_id: str,
) -> PartnerOnboardingWizard | None:
    return (
        db.query(PartnerOnboardingWizard)
        .filter(
            PartnerOnboardingWizard.partner_id == partner_id,
            PartnerOnboardingWizard.is_deleted.is_(False),
        )
        .order_by(PartnerOnboardingWizard.created_at.desc())
        .first()
    )


def list_partner_onboarding_wizards(
    db: Session,
    *,
    status: str | None = None,
) -> list[PartnerOnboardingWizard]:
    query = (
        db.query(PartnerOnboardingWizard)
        .options(joinedload(PartnerOnboardingWizard.partner))
        .filter(PartnerOnboardingWizard.is_deleted.is_(False))
    )
    if status:
        query = query.filter(PartnerOnboardingWizard.status == status)
    return query.order_by(PartnerOnboardingWizard.updated_at.desc()).all()


def wizard_completed_steps(wizard: PartnerOnboardingWizard) -> set[int]:
    completed: set[int] = set()
    for raw_value in (wizard.completed_steps or "").split(","):
        raw_value = raw_value.strip()
        if raw_value.isdigit():
            completed.add(int(raw_value))
    return completed


def update_partner_onboarding_wizard(
    db: Session,
    wizard: PartnerOnboardingWizard,
    *,
    partner_id: str | None = None,
    current_step: int | None = None,
    completed_step: int | None = None,
    status: str | None = None,
) -> PartnerOnboardingWizard:
    if partner_id is not None:
        wizard.partner_id = partner_id
    if current_step is not None:
        wizard.current_step = max(1, min(7, current_step))
    if completed_step is not None:
        completed = wizard_completed_steps(wizard)
        completed.add(completed_step)
        wizard.completed_steps = ",".join(str(step) for step in sorted(completed))
    if status is not None:
        wizard.status = status
    wizard.version += 1
    db.commit()
    db.refresh(wizard)
    return wizard


def get_partner_accounting_info(
    db: Session,
    partner_id: str,
) -> PartnerAccountingInfo | None:
    return (
        db.query(PartnerAccountingInfo)
        .filter(
            PartnerAccountingInfo.partner_id == partner_id,
            PartnerAccountingInfo.is_deleted.is_(False),
        )
        .first()
    )


def upsert_partner_accounting_info(
    db: Session,
    partner_id: str,
    data: dict[str, Any],
    *,
    commit: bool = True,
) -> PartnerAccountingInfo:
    info = get_partner_accounting_info(db, partner_id)
    clean_data = clean_form_data(data)
    if info:
        old_values = {field: getattr(info, field, None) for field in clean_data}
        for field, value in clean_data.items():
            setattr(info, field, value)
        info.version += 1
        log_field_changes(
            db,
            entity_type="PartnerAccountingInfo",
            entity_id=info.id,
            old_values=old_values,
            new_values=clean_data,
        )
    else:
        info = PartnerAccountingInfo(partner_id=partner_id, **clean_data)
        db.add(info)
        db.flush()
        log_create_event(
            db,
            entity_type="PartnerAccountingInfo",
            entity_id=info.id,
            data={"partner_id": partner_id, **clean_data},
        )
    if commit:
        db.commit()
        db.refresh(info)
    return info


def delete_partner(
    db: Session,
    partner: Partner,
    *,
    deleted_by_user_id: str | None = None,
    delete_reason: str | None = None,
) -> Partner:
    partner.is_deleted = True
    partner.is_active = False
    partner.deleted_at = utcnow()
    partner.deleted_by = deleted_by_user_id
    partner.delete_reason = delete_reason
    partner.version += 1
    log_delete_event(
        db,
        entity_type="Partner",
        entity_id=partner.id,
        user_id=deleted_by_user_id,
        delete_reason=delete_reason,
    )
    db.commit()
    db.refresh(partner)
    return partner


def list_substances(db: Session) -> list[Substance]:
    return (
        db.query(Substance)
        .filter(Substance.is_deleted.is_(False))
        .order_by(Substance.substance_name)
        .all()
    )


def get_substance(db: Session, substance_id: str) -> Substance | None:
    return (
        db.query(Substance)
        .filter(Substance.id == substance_id, Substance.is_deleted.is_(False))
        .first()
    )


def get_or_create_substance(
    db: Session,
    name: str,
    *,
    atc_code: str | None = None,
    inn_name: str | None = None,
) -> Substance:
    normalized = normalize_text(name)
    substance = (
        db.query(Substance)
        .filter(
            Substance.substance_name_normalized == normalized,
            Substance.is_deleted.is_(False),
        )
        .first()
    )
    if substance:
        return substance
    substance = Substance(
        substance_name=name.strip(),
        substance_name_normalized=normalized,
        inn_name=inn_name,
        atc_code=atc_code,
        substance_type="active",
    )
    db.add(substance)
    db.flush()
    log_create_event(
        db,
        entity_type="Substance",
        entity_id=substance.id,
        data={
            "substance_name": substance.substance_name,
            "inn_name": substance.inn_name,
            "atc_code": substance.atc_code,
            "substance_type": substance.substance_type,
        },
    )
    return substance


def create_substance(db: Session, payload: schemas.SubstanceCreate) -> Substance:
    data = clean_form_data(model_data(payload))
    data["substance_name_normalized"] = data.get("substance_name_normalized") or normalize_text(
        data.get("substance_name")
    )
    substance = Substance(**data)
    db.add(substance)
    db.flush()
    log_create_event(
        db,
        entity_type="Substance",
        entity_id=substance.id,
        data=data,
    )
    db.commit()
    db.refresh(substance)
    return substance


def update_substance(
    db: Session,
    substance: Substance,
    payload: schemas.SubstanceCreate,
) -> Substance:
    data = clean_form_data(model_data(payload))
    data["substance_name_normalized"] = data.get("substance_name_normalized") or normalize_text(
        data.get("substance_name")
    )
    old_values = {field: getattr(substance, field, None) for field in data}
    for field, value in data.items():
        setattr(substance, field, value)
    substance.version += 1
    log_field_changes(
        db,
        entity_type="Substance",
        entity_id=substance.id,
        old_values=old_values,
        new_values=data,
    )
    db.commit()
    db.refresh(substance)
    return substance


def delete_substance(
    db: Session,
    substance: Substance,
    *,
    deleted_by_user_id: str | None = None,
    delete_reason: str | None = None,
) -> Substance:
    substance.is_deleted = True
    substance.is_active = False
    substance.deleted_at = utcnow()
    substance.deleted_by = deleted_by_user_id
    substance.delete_reason = delete_reason
    substance.version += 1

    active_links = (
        db.query(ProductSubstance)
        .filter(
            ProductSubstance.substance_id == substance.id,
            ProductSubstance.is_deleted.is_(False),
        )
        .all()
    )
    for link in active_links:
        link.is_deleted = True
        link.is_active = False
        link.deleted_at = substance.deleted_at
        link.deleted_by = deleted_by_user_id
        link.delete_reason = delete_reason
        link.version += 1

    log_delete_event(
        db,
        entity_type="Substance",
        entity_id=substance.id,
        user_id=deleted_by_user_id,
        delete_reason=delete_reason,
    )
    db.commit()
    db.refresh(substance)
    return substance


def list_products(db: Session) -> list[Product]:
    return (
        db.query(Product)
        .options(
            joinedload(Product.dosage_form_ref),
            joinedload(Product.mah_partner),
            selectinload(Product.contracts).joinedload(Contract.partner),
            selectinload(Product.registration_certificates),
            selectinload(Product.partner_registration_links)
            .joinedload(PartnerProductRegistrationLink.partner),
            selectinload(Product.substance_links).joinedload(ProductSubstance.substance),
            selectinload(Product.contract_links)
            .joinedload(ContractProduct.contract)
            .joinedload(Contract.partner),
        )
        .filter(Product.is_deleted.is_(False))
        .order_by(Product.product_name)
        .all()
    )


def product_partner_ids(product: Product) -> set[str]:
    partner_ids: set[str] = set()
    if product.mah_partner_id and product.mah_partner and not product.mah_partner.is_deleted:
        partner_ids.add(product.mah_partner_id)
    for contract in product.contracts:
        if (
            contract.is_deleted
            or not contract.partner_id
            or not contract.partner
            or contract.partner.is_deleted
        ):
            continue
        partner_ids.add(contract.partner_id)
    for link in product.contract_links:
        contract = link.contract
        if (
            link.is_deleted
            or not contract
            or contract.is_deleted
            or not contract.partner_id
            or not contract.partner
            or contract.partner.is_deleted
        ):
            continue
        partner_ids.add(contract.partner_id)
    for link in product.partner_registration_links:
        if (
            link.is_deleted
            or not link.partner_id
            or not link.partner
            or link.partner.is_deleted
        ):
            continue
        partner_ids.add(link.partner_id)
    return partner_ids


def list_products_for_partner(db: Session, partner_id: str) -> list[Product]:
    return [
        product
        for product in list_products(db)
        if partner_id in product_partner_ids(product)
    ]


def get_product(db: Session, product_id: str) -> Product | None:
    return (
        db.query(Product)
        .options(
            joinedload(Product.dosage_form_ref),
            selectinload(Product.registration_certificates),
            selectinload(Product.partner_registration_links)
            .joinedload(PartnerProductRegistrationLink.partner),
            selectinload(Product.contract_links)
            .joinedload(ContractProduct.contract)
            .joinedload(Contract.partner),
        )
        .filter(Product.id == product_id)
        .first()
    )


def product_contract_links(product: Product) -> list[ContractProduct]:
    return sorted(
        [
            link
            for link in product.contract_links
            if not link.is_deleted and link.contract and not link.contract.is_deleted
        ],
        key=lambda link: contract_sort_key(link.contract),
    )


def product_registration_certificates(product: Product) -> list[ProductRegistrationCertificate]:
    return sorted(
        [
            certificate
            for certificate in product.registration_certificates
            if not certificate.is_deleted
        ],
        key=lambda certificate: (
            certificate.status not in {"active", "registration_active", "authorized"},
            certificate.registration_date or date.min,
            certificate.created_at or datetime.min,
        ),
        reverse=True,
    )


def current_product_registration(product: Product) -> ProductRegistrationCertificate | None:
    certificates = product_registration_certificates(product)
    for certificate in certificates:
        if certificate.status in {"active", "registration_active", "authorized"}:
            return certificate
    return certificates[0] if certificates else None


PARTNER_PRODUCT_EXPORT_HEADERS = [
    "Код препарата",
    "Торговое наименование",
    "МНН / действующее вещество",
    "Лекарственная форма",
    "Дозировка",
    "ДРУ",
    "Основание связи с ДРУ",
    "Номер РУ",
    "Дата РУ",
    "Действует до",
    "Владелец РУ",
    "Страна РУ",
    "Статус РУ",
    "Договоры",
    "Комментарий",
]


def partner_product_export_rows(
    products: list[Product],
    partner: Partner,
) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for product in products:
        registration = current_product_registration(product)
        substances = sorted(
            {
                link.substance.inn_name or link.substance.substance_name
                for link in product.substance_links
                if not link.is_deleted and link.substance and not link.substance.is_deleted
            }
        )
        contracts_by_id = {
            contract.id: contract
            for contract in product.contracts
            if not contract.is_deleted and contract.partner_id == partner.id
        }
        for link in product.contract_links:
            contract = link.contract
            if (
                not link.is_deleted
                and contract
                and not contract.is_deleted
                and contract.partner_id == partner.id
            ):
                contracts_by_id[contract.id] = contract
        contracts = sorted(
            contracts_by_id.values(),
            key=contract_sort_key,
        )

        relationship_reasons = []
        if product.mah_partner_id == partner.id:
            relationship_reasons.append("ДРУ препарата")
        if any(
            not link.is_deleted and link.partner_id == partner.id
            for link in product.partner_registration_links
        ):
            relationship_reasons.append("Регистрационная связь")
        if contracts:
            relationship_reasons.append("Договор")

        rows.append(
            [
                product.product_code,
                product.product_name,
                "; ".join(substances),
                product_dosage_form_name(product, "ru"),
                product.strength or (registration.strength if registration else "") or "",
                partner.partner_name,
                "; ".join(relationship_reasons),
                (registration.registration_number if registration else product.authorization_number) or "",
                registration.registration_date if registration else "",
                registration.valid_until if registration else "",
                registration.holder if registration else "",
                (
                    registration.country_code
                    if registration and registration.country_code
                    else product.authorization_country_code
                )
                or "",
                (registration.status if registration else product.authorization_status) or "",
                "; ".join(contract.contract_number for contract in contracts),
                product.comment or "",
            ]
        )
    return rows


def export_partner_products_xlsx(db: Session, partner: Partner) -> bytes:
    products = list_products_for_partner(db, partner.id)
    return build_xlsx(
        [
            (
                "Препараты ДРУ",
                [
                    PARTNER_PRODUCT_EXPORT_HEADERS,
                    *partner_product_export_rows(products, partner),
                ],
            )
        ]
    )


def _sync_product_registration_legacy_fields(product: Product) -> None:
    current = current_product_registration(product)
    if current:
        product.authorization_number = current.registration_number
        product.authorization_status = current.status


def list_product_registration_certificates(
    db: Session,
    *,
    product_id: str | None = None,
) -> list[ProductRegistrationCertificate]:
    query = (
        db.query(ProductRegistrationCertificate)
        .options(joinedload(ProductRegistrationCertificate.product))
        .filter(ProductRegistrationCertificate.is_deleted.is_(False))
    )
    if product_id:
        query = query.filter(ProductRegistrationCertificate.product_id == product_id)
    return (
        query.order_by(
            ProductRegistrationCertificate.registration_date.desc().nullslast(),
            ProductRegistrationCertificate.created_at.desc(),
        )
        .all()
    )


def get_product_registration_certificate(
    db: Session,
    certificate_id: str,
) -> ProductRegistrationCertificate | None:
    return (
        db.query(ProductRegistrationCertificate)
        .options(joinedload(ProductRegistrationCertificate.product))
        .filter(
            ProductRegistrationCertificate.id == certificate_id,
            ProductRegistrationCertificate.is_deleted.is_(False),
        )
        .first()
    )


def list_partner_product_registration_links(
    db: Session,
    *,
    partner_id: str | None = None,
    product_id: str | None = None,
) -> list[PartnerProductRegistrationLink]:
    query = (
        db.query(PartnerProductRegistrationLink)
        .options(
            joinedload(PartnerProductRegistrationLink.partner),
            joinedload(PartnerProductRegistrationLink.product),
            joinedload(PartnerProductRegistrationLink.registration_certificate),
            joinedload(PartnerProductRegistrationLink.contract),
        )
        .filter(PartnerProductRegistrationLink.is_deleted.is_(False))
    )
    if partner_id:
        query = query.filter(PartnerProductRegistrationLink.partner_id == partner_id)
    if product_id:
        query = query.filter(PartnerProductRegistrationLink.product_id == product_id)
    return query.order_by(PartnerProductRegistrationLink.created_at.desc()).all()


def upsert_partner_product_registration_link(
    db: Session,
    payload: schemas.PartnerProductRegistrationLinkCreate,
) -> PartnerProductRegistrationLink:
    data = clean_form_data(model_data(payload))
    partner = get_partner(db, data.get("partner_id"))
    product = get_product(db, data.get("product_id"))
    if not partner:
        raise ValueError("Partner not found.")
    if not product or product.is_deleted:
        raise ValueError("Product not found.")

    certificate_id = data.get("registration_certificate_id")
    if certificate_id:
        certificate = get_product_registration_certificate(db, certificate_id)
        if not certificate or certificate.product_id != product.id:
            raise ValueError("Registration certificate not found for selected product.")

    contract_id = data.get("contract_id")
    if contract_id:
        contract = get_contract(db, contract_id)
        if not contract or contract.partner_id != partner.id:
            raise ValueError("Contract not found for selected partner.")
        if product.id not in contract.product_ids:
            link_product_to_contract(db, product, contract)

    data["partner_role"] = (data.get("partner_role") or "mah").strip() or "mah"
    actor_id = audit_user_id(db)
    existing = (
        db.query(PartnerProductRegistrationLink)
        .filter(
            PartnerProductRegistrationLink.partner_id == partner.id,
            PartnerProductRegistrationLink.product_id == product.id,
            PartnerProductRegistrationLink.registration_certificate_id == certificate_id,
            PartnerProductRegistrationLink.contract_id == contract_id,
            PartnerProductRegistrationLink.partner_role == data["partner_role"],
        )
        .first()
    )
    if existing and existing.is_deleted:
        existing.is_deleted = False
        existing.is_active = True
        existing.deleted_at = None
        existing.deleted_by = None
        existing.delete_reason = None
    if existing:
        old_values = {
            field: getattr(existing, field, None)
            for field in ["comment", "is_active", "is_deleted"]
        }
        existing.comment = data.get("comment")
        existing.updated_by = actor_id
        existing.version += 1
        log_field_changes(
            db,
            entity_type="PartnerProductRegistrationLink",
            entity_id=existing.id,
            old_values=old_values,
            new_values={"comment": existing.comment, "is_active": True, "is_deleted": False},
        )
        db.commit()
        db.refresh(existing)
        return existing

    link = PartnerProductRegistrationLink(**data, created_by=actor_id, updated_by=actor_id)
    db.add(link)
    db.flush()
    log_create_event(
        db,
        entity_type="PartnerProductRegistrationLink",
        entity_id=link.id,
        data=data,
    )
    db.commit()
    db.refresh(link)
    return link


def _clean_registration_certificate_payload(
    db: Session,
    payload: schemas.ProductRegistrationCertificateCreate,
    *,
    current_id: str | None = None,
) -> dict[str, Any]:
    data = clean_form_data(model_data(payload))
    registration_number = (data.get("registration_number") or "").strip()
    if not registration_number:
        raise ValueError("Registration certificate number is required.")
    product_id = data.get("product_id")
    product = get_product(db, product_id)
    if not product or product.is_deleted:
        raise ValueError("Product not found.")
    duplicate = (
        db.query(ProductRegistrationCertificate)
        .filter(
            ProductRegistrationCertificate.product_id == product_id,
            func.lower(ProductRegistrationCertificate.registration_number) == registration_number.lower(),
            ProductRegistrationCertificate.is_deleted.is_(False),
        )
        .first()
    )
    if duplicate and duplicate.id != current_id:
        raise ValueError("Registration certificate already exists for this product.")
    data["registration_number"] = registration_number
    data["status"] = (data.get("status") or "active").strip()
    data["country_code"] = (data.get("country_code") or "").strip().upper() or None
    return data


def create_product_registration_certificate(
    db: Session,
    payload: schemas.ProductRegistrationCertificateCreate,
) -> ProductRegistrationCertificate:
    data = _clean_registration_certificate_payload(db, payload)
    certificate = ProductRegistrationCertificate(**data)
    db.add(certificate)
    db.flush()
    product = get_product(db, certificate.product_id)
    if product and not product.authorization_number:
        _sync_product_registration_legacy_fields(product)
    log_create_event(
        db,
        entity_type="ProductRegistrationCertificate",
        entity_id=certificate.id,
        data=data,
    )
    db.commit()
    db.refresh(certificate)
    return certificate


def update_product_registration_certificate(
    db: Session,
    certificate: ProductRegistrationCertificate,
    payload: schemas.ProductRegistrationCertificateCreate,
) -> ProductRegistrationCertificate:
    data = _clean_registration_certificate_payload(db, payload, current_id=certificate.id)
    old_values = {field: getattr(certificate, field, None) for field in data}
    for field, value in data.items():
        setattr(certificate, field, value)
    product = get_product(db, certificate.product_id)
    if product and product.authorization_number == old_values.get("registration_number"):
        _sync_product_registration_legacy_fields(product)
    log_field_changes(
        db,
        entity_type="ProductRegistrationCertificate",
        entity_id=certificate.id,
        old_values=old_values,
        new_values=data,
    )
    db.commit()
    db.refresh(certificate)
    return certificate


def delete_product_registration_certificate(
    db: Session,
    certificate: ProductRegistrationCertificate,
    *,
    deleted_by_user_id: str | None = None,
    delete_reason: str | None = None,
) -> ProductRegistrationCertificate:
    product = get_product(db, certificate.product_id)
    soft_delete_object(
        certificate,
        deleted_by_user_id=deleted_by_user_id,
        delete_reason=delete_reason,
    )
    if product and product.authorization_number == certificate.registration_number:
        product.authorization_number = None
        product.authorization_status = None
        _sync_product_registration_legacy_fields(product)
    log_soft_delete_event(
        db,
        entity_type="ProductRegistrationCertificate",
        entity_id=certificate.id,
        user_id=deleted_by_user_id,
        delete_reason=delete_reason,
    )
    db.commit()
    db.refresh(certificate)
    return certificate


def generate_product_code(db: Session) -> str:
    for _ in range(20):
        code = f"PRD-{uuid4().hex[:10].upper()}"
        exists = db.query(Product.id).filter(Product.product_code == code).first()
        if not exists:
            return code
    return f"PRD-{uuid4().hex.upper()}"


def _payload_active_substance(
    db: Session,
    active_substance_id: str | None,
    active_substance: str | None,
) -> Substance | None:
    if active_substance_id:
        substance = get_substance(db, active_substance_id)
        if not substance or substance.is_deleted:
            raise ValueError("Substance not found.")
        return substance
    if active_substance:
        return get_or_create_substance(db, active_substance)
    return None


def _new_product_substance_link(product: Product, substance: Substance) -> ProductSubstance:
    return ProductSubstance(
        product_id=product.id,
        substance_id=substance.id,
        substance_role="active",
        is_primary=True,
    )


def list_dosage_forms(db: Session, *, include_inactive: bool = True) -> list[DosageForm]:
    query = db.query(DosageForm)
    if not include_inactive:
        query = query.filter(DosageForm.is_active.is_(True))
    return (
        query.order_by(
            DosageForm.name_ru,
            DosageForm.name_en,
        )
        .all()
    )


def list_active_dosage_forms(db: Session) -> list[DosageForm]:
    return list_dosage_forms(db, include_inactive=False)


def get_dosage_form(db: Session, dosage_form_id: int | str | None) -> DosageForm | None:
    if dosage_form_id in (None, ""):
        return None
    try:
        form_id = int(dosage_form_id)
    except (TypeError, ValueError):
        return None
    return db.query(DosageForm).filter(DosageForm.id == form_id).first()


def _clean_dosage_form_payload(data: dict[str, Any]) -> dict[str, Any]:
    name_ru = (data.get("name_ru") or "").strip()
    if not name_ru:
        raise ValueError("Dosage form name is required.")
    cleaned = clean_form_data(data)
    cleaned["name_ru"] = name_ru
    cleaned["is_active"] = bool(cleaned.get("is_active", True))
    return cleaned


def create_dosage_form(db: Session, payload: schemas.DosageFormCreate) -> DosageForm:
    data = _clean_dosage_form_payload(model_data(payload))
    dosage_form = DosageForm(**data)
    db.add(dosage_form)
    db.flush()
    log_create_event(
        db,
        entity_type="DosageForm",
        entity_id=str(dosage_form.id),
        data=data,
    )
    db.commit()
    db.refresh(dosage_form)
    return dosage_form


def update_dosage_form(
    db: Session,
    dosage_form: DosageForm,
    payload: schemas.DosageFormCreate,
) -> DosageForm:
    data = _clean_dosage_form_payload(model_data(payload))
    old_values = {field: getattr(dosage_form, field, None) for field in data}
    for field, value in data.items():
        setattr(dosage_form, field, value)
    log_field_changes(
        db,
        entity_type="DosageForm",
        entity_id=str(dosage_form.id),
        old_values=old_values,
        new_values=data,
    )
    db.commit()
    db.refresh(dosage_form)
    return dosage_form


def deactivate_dosage_form(db: Session, dosage_form: DosageForm) -> DosageForm:
    old_values = {"is_active": dosage_form.is_active}
    dosage_form.is_active = False
    log_field_changes(
        db,
        entity_type="DosageForm",
        entity_id=str(dosage_form.id),
        old_values=old_values,
        new_values={"is_active": False},
        action="deactivate",
    )
    db.commit()
    db.refresh(dosage_form)
    return dosage_form


def product_dosage_form_name(product: Product | None, language: str = "ru") -> str:
    if not product:
        return ""
    normalized_language = "en" if language == "en" else "ru"
    return product.dosage_form_name(normalized_language)


def _sync_product_dosage_form(db: Session, data: dict[str, Any]) -> None:
    if "dosage_form_id" not in data:
        return
    dosage_form_id = data.get("dosage_form_id")
    if dosage_form_id in (None, ""):
        data["dosage_form_id"] = None
        data["dosage_form"] = None
        return
    dosage_form = get_dosage_form(db, dosage_form_id)
    if not dosage_form:
        raise ValueError("Dosage form not found.")
    data["dosage_form_id"] = dosage_form.id
    data["dosage_form"] = dosage_form.name_ru


def link_product_to_contract(
    db: Session,
    product: Product,
    contract: Contract,
) -> ContractProduct:
    if contract.is_deleted:
        raise ValueError("Contract not found.")
    if product.is_deleted:
        raise ValueError("Product not found.")

    existing = (
        db.query(ContractProduct)
        .filter(
            ContractProduct.product_id == product.id,
            ContractProduct.contract_id == contract.id,
        )
        .first()
    )
    if existing and not existing.is_deleted:
        raise ValueError("Product is already linked to this contract.")
    if existing:
        existing.is_deleted = False
        existing.is_active = True
        existing.deleted_at = None
        existing.deleted_by = None
        existing.delete_reason = None
        existing.version += 1
        link = existing
    else:
        link = ContractProduct(product_id=product.id, contract_id=contract.id)
        db.add(link)

    if not contract.product_id:
        contract.product_id = product.id
    product.version += 1
    contract.updated_by = audit_user_id(db)
    contract.version += 1
    db.flush()
    log_create_event(
        db,
        entity_type="ContractProduct",
        entity_id=link.id,
        data={"product_id": product.id, "contract_id": contract.id},
    )
    db.commit()
    db.refresh(link)
    return link


def unlink_product_from_contract(
    db: Session,
    product: Product,
    contract: Contract,
    *,
    deleted_by_user_id: str | None = None,
) -> ContractProduct:
    link = (
        db.query(ContractProduct)
        .filter(
            ContractProduct.product_id == product.id,
            ContractProduct.contract_id == contract.id,
            ContractProduct.is_deleted.is_(False),
        )
        .first()
    )
    if not link:
        raise ValueError("Product is not linked to this contract.")

    remaining_product_ids = [
        row[0]
        for row in db.query(ContractProduct.product_id)
        .filter(
            ContractProduct.contract_id == contract.id,
            ContractProduct.product_id != product.id,
            ContractProduct.is_deleted.is_(False),
        )
        .all()
    ]
    if contract.product_id == product.id:
        contract.product_id = remaining_product_ids[0] if remaining_product_ids else None
    product.version += 1

    link.is_deleted = True
    link.is_active = False
    link.deleted_at = utcnow()
    link.deleted_by = deleted_by_user_id
    link.version += 1
    contract.updated_by = audit_user_id(db)
    contract.version += 1
    log_delete_event(
        db,
        entity_type="ContractProduct",
        entity_id=link.id,
        user_id=deleted_by_user_id,
    )
    db.commit()
    db.refresh(link)
    return link


def create_product(db: Session, payload: schemas.ProductCreate) -> Product:
    data = clean_form_data(model_data(payload))
    active_substance = data.pop("active_substance", None)
    active_substance_id = data.pop("active_substance_id", None)
    _sync_product_dosage_form(db, data)
    if not data.get("product_code"):
        data["product_code"] = generate_product_code(db)
    data["product_name_normalized"] = data.get("product_name_normalized") or normalize_text(
        data.get("product_name")
    )
    actor_id = audit_user_id(db)
    product = Product(**data, created_by=actor_id, updated_by=actor_id)
    db.add(product)
    db.flush()

    substance = _payload_active_substance(db, active_substance_id, active_substance)
    if substance:
        link = _new_product_substance_link(product, substance)
        db.add(link)
        db.flush()
        log_create_event(
            db,
            entity_type="ProductSubstance",
            entity_id=link.id,
            data={
                "product_id": product.id,
                "substance_id": substance.id,
                "substance_role": link.substance_role,
                "is_primary": link.is_primary,
            },
        )

    audit_data = dict(data)
    if substance:
        audit_data["active_substance_id"] = substance.id
    log_create_event(
        db,
        entity_type="Product",
        entity_id=product.id,
        data=audit_data,
    )
    db.commit()
    db.refresh(product)
    return product


def update_product(
    db: Session, product: Product, payload: schemas.ProductCreate, *, commit: bool = True
) -> Product:
    data = clean_form_data(model_data(payload))
    data.pop("active_substance", None)
    data.pop("active_substance_id", None)
    _sync_product_dosage_form(db, data)
    data["product_name_normalized"] = data.get("product_name_normalized") or normalize_text(
        data.get("product_name")
    )
    if not data.get("product_code"):
        data["product_code"] = product.product_code or generate_product_code(db)
    old_values = {field: getattr(product, field, None) for field in data}
    for field, value in data.items():
        setattr(product, field, value)
    product.updated_by = audit_user_id(db)
    product.version += 1
    log_field_changes(
        db,
        entity_type="Product",
        entity_id=product.id,
        old_values=old_values,
        new_values=data,
    )
    if commit:
        db.commit()
        db.refresh(product)
    else:
        db.flush()
    return product


def set_product_active_substance(
    db: Session,
    product: Product,
    substance_id: str | None,
    *,
    commit: bool = True,
) -> Product:
    substance = None
    if substance_id:
        substance = get_substance(db, substance_id)
        if not substance or substance.is_deleted:
            raise ValueError("Substance not found.")

    current_primary_links = (
        db.query(ProductSubstance)
        .filter(
            ProductSubstance.product_id == product.id,
            ProductSubstance.substance_role == "active",
            ProductSubstance.is_primary.is_(True),
            ProductSubstance.is_deleted.is_(False),
        )
        .all()
    )
    target_link = None
    if substance:
        target_link = (
            db.query(ProductSubstance)
            .filter(
                ProductSubstance.product_id == product.id,
                ProductSubstance.substance_id == substance.id,
            )
            .first()
        )

    actor_id = audit_user_id(db)
    changed = False
    for link in current_primary_links:
        if target_link and link.id == target_link.id:
            continue
        link.is_deleted = True
        link.is_active = False
        link.deleted_at = utcnow()
        link.deleted_by = actor_id
        link.version += 1
        log_delete_event(
            db,
            entity_type="ProductSubstance",
            entity_id=link.id,
            user_id=actor_id,
        )
        changed = True

    if substance:
        if target_link:
            old_values = {
                "substance_role": target_link.substance_role,
                "is_primary": target_link.is_primary,
                "is_deleted": target_link.is_deleted,
                "is_active": target_link.is_active,
            }
            new_values = {
                "substance_role": "active",
                "is_primary": True,
                "is_deleted": False,
                "is_active": True,
            }
            if old_values != new_values:
                target_link.substance_role = "active"
                target_link.is_primary = True
                target_link.is_deleted = False
                target_link.is_active = True
                target_link.deleted_at = None
                target_link.deleted_by = None
                target_link.delete_reason = None
                target_link.version += 1
                log_field_changes(
                    db,
                    entity_type="ProductSubstance",
                    entity_id=target_link.id,
                    old_values=old_values,
                    new_values=new_values,
                )
                changed = True
        else:
            target_link = _new_product_substance_link(product, substance)
            db.add(target_link)
            db.flush()
            log_create_event(
                db,
                entity_type="ProductSubstance",
                entity_id=target_link.id,
                data={
                    "product_id": product.id,
                    "substance_id": substance.id,
                    "substance_role": target_link.substance_role,
                    "is_primary": target_link.is_primary,
                },
            )
            changed = True

    if changed:
        product.updated_by = actor_id
        product.version += 1
        if commit:
            db.commit()
            db.refresh(product)
        else:
            db.flush()
    return product


def delete_product(
    db: Session,
    product: Product,
    *,
    deleted_by_user_id: str | None = None,
    delete_reason: str | None = None,
) -> Product:
    product.is_deleted = True
    product.is_active = False
    product.deleted_at = utcnow()
    product.deleted_by = deleted_by_user_id
    product.delete_reason = delete_reason
    product.version += 1
    log_delete_event(
        db,
        entity_type="Product",
        entity_id=product.id,
        user_id=deleted_by_user_id,
        delete_reason=delete_reason,
    )
    db.commit()
    db.refresh(product)
    return product


def contract_sort_key(contract: Contract) -> tuple[bool, int, int, str]:
    return (
        not contract.is_current,
        -(contract.valid_until.toordinal() if contract.valid_until else 0),
        -(contract.contract_date.toordinal() if contract.contract_date else 0),
        contract.contract_number or "",
    )


def contract_product_ids(contract: Contract) -> list[str]:
    product_ids = [contract.product_id] if contract.product_id else []
    for link in contract.product_links:
        if not link.is_deleted and link.product_id and link.product_id not in product_ids:
            product_ids.append(link.product_id)
    return product_ids


def contract_search_text(contract: Contract) -> str:
    products = " ".join(
        product.product_name
        for product in contract.linked_products
        if product and product.product_name
    )
    return " ".join(
        [
            contract.contract_number or "",
            contract.contract_type or "",
            contract.partner.partner_name if contract.partner else "",
            products,
            contract.parent_contract.contract_number if contract.parent_contract else "",
        ]
    ).lower()


def list_contracts(
    db: Session,
    *,
    partner_id: str | None = None,
    search: str | None = None,
) -> list[Contract]:
    query = (
        db.query(Contract)
        .options(
            joinedload(Contract.partner),
            joinedload(Contract.product),
            selectinload(Contract.product_links).joinedload(ContractProduct.product),
            joinedload(Contract.parent_contract),
        )
        .filter(Contract.is_deleted.is_(False))
    )
    if partner_id:
        query = query.filter(Contract.partner_id == partner_id)
    contracts = query.all()
    search = (search or "").strip().lower()
    if search:
        contracts = [
            contract
            for contract in contracts
            if search in contract_search_text(contract)
        ]
    return sorted(contracts, key=contract_sort_key)


def list_base_contracts(
    db: Session,
    *,
    exclude_contract_id: str | None = None,
) -> list[Contract]:
    query = (
        db.query(Contract)
        .options(
            joinedload(Contract.partner),
            joinedload(Contract.product),
            selectinload(Contract.product_links).joinedload(ContractProduct.product),
        )
        .filter(
            Contract.is_deleted.is_(False),
            Contract.contract_type == "pharmacovigilance_agreement",
        )
    )
    if exclude_contract_id:
        query = query.filter(Contract.id != exclude_contract_id)
    return sorted(query.all(), key=contract_sort_key)


def get_contract(db: Session, contract_id: str) -> Contract | None:
    return (
        db.query(Contract)
        .options(
            joinedload(Contract.partner),
            joinedload(Contract.product),
            selectinload(Contract.product_links).joinedload(ContractProduct.product),
            joinedload(Contract.parent_contract),
        )
        .filter(Contract.id == contract_id, Contract.is_deleted.is_(False))
        .first()
    )


def contract_has_additional_agreements(db: Session, contract_id: str) -> bool:
    return (
        db.query(Contract.id)
        .filter(
            Contract.parent_contract_id == contract_id,
            Contract.is_deleted.is_(False),
        )
        .first()
        is not None
    )


def contract_payload_data(payload: schemas.ContractCreate | schemas.ContractUpdate) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return clean_form_data(payload.model_dump())
    return clean_form_data(model_data(payload))


def normalize_contract_product_ids(data: dict[str, Any]) -> list[str]:
    raw_product_ids = data.get("product_ids") or []
    if isinstance(raw_product_ids, str):
        raw_product_ids = [raw_product_ids]
    product_ids: list[str] = []
    for product_id in raw_product_ids:
        product_id = (product_id or "").strip()
        if product_id and product_id not in product_ids:
            product_ids.append(product_id)
    legacy_product_id = data.get("product_id")
    if legacy_product_id and legacy_product_id not in product_ids:
        product_ids.insert(0, legacy_product_id)
    return product_ids


def validate_contract_product_ids(db: Session, product_ids: list[str]) -> None:
    if not product_ids:
        return
    existing_ids = {
        row[0]
        for row in db.query(Product.id)
        .filter(Product.id.in_(product_ids), Product.is_deleted.is_(False))
        .all()
    }
    missing_ids = [product_id for product_id in product_ids if product_id not in existing_ids]
    if missing_ids:
        raise ValueError("Product not found.")


def sync_contract_products(
    db: Session,
    contract: Contract,
    product_ids: list[str],
) -> None:
    current_links = {
        link.product_id: link
        for link in contract.product_links
        if not link.is_deleted and link.product_id
    }
    for link in list(contract.product_links):
        if link.product_id not in product_ids:
            contract.product_links.remove(link)
    for product_id in product_ids:
        if product_id not in current_links:
            contract.product_links.append(ContractProduct(product_id=product_id))
    db.flush()


def prepare_contract_data(
    db: Session,
    data: dict[str, Any],
    *,
    current_contract_id: str | None = None,
) -> dict[str, Any]:
    if data.get("contract_date") and data.get("valid_until"):
        if data["valid_until"] < data["contract_date"]:
            raise ValueError("Valid until must be on or after contract date.")

    contract_type = data.get("contract_type") or "pharmacovigilance_agreement"
    data["contract_type"] = contract_type

    if contract_type == "additional_agreement":
        parent_contract_id = data.get("parent_contract_id")
        if not parent_contract_id:
            raise ValueError("Select parent contract for additional agreement.")
        if parent_contract_id == current_contract_id:
            raise ValueError("Additional agreement cannot be attached to itself.")

        parent_contract = (
            db.query(Contract)
            .filter(
                Contract.id == parent_contract_id,
                Contract.is_deleted.is_(False),
            )
            .first()
        )
        if not parent_contract:
            raise ValueError("Parent contract not found.")
        if parent_contract.contract_type != "pharmacovigilance_agreement":
            raise ValueError("Additional agreement can be attached only to a base contract.")

        duplicate = (
            db.query(Contract)
            .filter(
                Contract.parent_contract_id == parent_contract_id,
                func.lower(Contract.contract_number)
                == str(data.get("contract_number") or "").strip().lower(),
                Contract.is_deleted.is_(False),
            )
        )
        if current_contract_id:
            duplicate = duplicate.filter(Contract.id != current_contract_id)
        if duplicate.first():
            raise ValueError("Additional agreement already exists for this contract number.")

        current_contract = get_contract(db, current_contract_id) if current_contract_id else None
        parent_product_ids = (
            contract_product_ids(current_contract)
            if current_contract
            else contract_product_ids(parent_contract)
        )
        data["partner_id"] = parent_contract.partner_id
        data["product_ids"] = parent_product_ids
        data["product_id"] = parent_product_ids[0] if parent_product_ids else parent_contract.product_id
        return data

    data["parent_contract_id"] = None
    product_ids = normalize_contract_product_ids(data)
    validate_contract_product_ids(db, product_ids)
    data["product_ids"] = product_ids
    data["product_id"] = product_ids[0] if product_ids else None
    if product_ids:
        existing = (
            db.query(Contract)
            .outerjoin(
                ContractProduct,
                ContractProduct.contract_id == Contract.id,
            )
            .filter(
                Contract.partner_id == data.get("partner_id"),
                Contract.contract_type == "pharmacovigilance_agreement",
                Contract.is_deleted.is_(False),
                or_(
                    Contract.product_id.in_(product_ids),
                    ContractProduct.product_id.in_(product_ids),
                ),
            )
        )
        if current_contract_id:
            existing = existing.filter(Contract.id != current_contract_id)
        if existing.first():
            raise ValueError("Contract already exists for this partner and product.")
    return data


def create_contract(db: Session, payload: schemas.ContractCreate) -> Contract:
    data = prepare_contract_data(db, contract_payload_data(payload))
    product_ids = data.pop("product_ids")
    actor_id = audit_user_id(db)
    contract = Contract(**data, created_by=actor_id, updated_by=actor_id)
    db.add(contract)
    db.flush()
    sync_contract_products(db, contract, product_ids)
    log_create_event(
        db,
        entity_type="Contract",
        entity_id=contract.id,
        data={**data, "product_ids": ", ".join(product_ids)},
    )
    db.commit()
    db.refresh(contract)
    return contract


def update_contract(
    db: Session,
    contract: Contract,
    payload: schemas.ContractUpdate,
    *,
    is_active: bool | None = None,
) -> Contract:
    data = prepare_contract_data(
        db,
        contract_payload_data(payload),
        current_contract_id=contract.id,
    )
    if is_active is not None:
        data["is_active"] = is_active
    product_ids = data.pop("product_ids")
    old_product_ids = contract_product_ids(contract)
    if contract_has_additional_agreements(db, contract.id) and (
        data.get("contract_type") != contract.contract_type
        or data.get("partner_id") != contract.partner_id
        or sorted(product_ids) != sorted(old_product_ids)
    ):
        raise ValueError(
            "Contract has attached additional agreements. Delete or move them first."
        )

    old_values = {field: getattr(contract, field, None) for field in data}
    for field, value in data.items():
        setattr(contract, field, value)
    sync_contract_products(db, contract, product_ids)
    contract.updated_by = audit_user_id(db)
    contract.version += 1
    old_values["product_ids"] = ", ".join(old_product_ids)
    data["product_ids"] = ", ".join(product_ids)
    log_field_changes(
        db,
        entity_type="Contract",
        entity_id=contract.id,
        old_values=old_values,
        new_values=data,
    )
    db.commit()
    db.refresh(contract)
    return contract


ACCOUNTING_STATUSES = {
    "draft": "Черновик",
    "needs_review": "Требует проверки",
    "ready_for_accounting": "Готово для бухгалтерии",
    "sent_to_accounting": "Передано в бухгалтерию",
}


def accounting_quarter_dates(year: int, quarter: int) -> tuple[date, date]:
    ranges = {
        1: (date(year, 1, 1), date(year, 3, 31)),
        2: (date(year, 4, 1), date(year, 6, 30)),
        3: (date(year, 7, 1), date(year, 9, 30)),
        4: (date(year, 10, 1), date(year, 12, 31)),
    }
    if quarter not in ranges:
        raise ValueError("Quarter must be between 1 and 4.")
    return ranges[quarter]


def list_accounting_services(db: Session, *, active_only: bool = True) -> list[AccountingService]:
    query = db.query(AccountingService)
    if active_only:
        query = query.filter(AccountingService.is_active.is_(True))
    return query.order_by(AccountingService.sort_order, AccountingService.name).all()


def get_accounting_service(db: Session, service_id: str) -> AccountingService | None:
    return db.query(AccountingService).filter(AccountingService.id == service_id).first()


def list_accounting_periods(
    db: Session,
    *,
    partner_id: str | None = None,
    contract_id: str | None = None,
    year: int | None = None,
    quarter: int | None = None,
    status: str | None = None,
    responsible_user_id: str | None = None,
) -> list[AccountingDataPeriod]:
    query = (
        db.query(AccountingDataPeriod)
        .options(
            joinedload(AccountingDataPeriod.partner),
            joinedload(AccountingDataPeriod.contract),
            joinedload(AccountingDataPeriod.responsible_user),
            selectinload(AccountingDataPeriod.lines),
        )
        .filter(AccountingDataPeriod.is_deleted.is_(False))
    )
    if partner_id:
        query = query.filter(AccountingDataPeriod.partner_id == partner_id)
    if contract_id:
        query = query.filter(AccountingDataPeriod.contract_id == contract_id)
    if year:
        query = query.filter(AccountingDataPeriod.year == year)
    if quarter:
        query = query.filter(AccountingDataPeriod.quarter == quarter)
    if status:
        query = query.filter(AccountingDataPeriod.status == status)
    if responsible_user_id:
        query = query.filter(AccountingDataPeriod.responsible_user_id == responsible_user_id)
    return (
        query.order_by(
            AccountingDataPeriod.year.desc(),
            AccountingDataPeriod.quarter.desc(),
            AccountingDataPeriod.created_at.desc(),
        )
        .all()
    )


def get_accounting_period(db: Session, period_id: str) -> AccountingDataPeriod | None:
    return (
        db.query(AccountingDataPeriod)
        .options(
            joinedload(AccountingDataPeriod.partner).joinedload(Partner.accounting_info),
            joinedload(AccountingDataPeriod.contract),
            joinedload(AccountingDataPeriod.responsible_user),
            selectinload(AccountingDataPeriod.lines).joinedload(AccountingDataLine.service),
        )
        .filter(
            AccountingDataPeriod.id == period_id,
            AccountingDataPeriod.is_deleted.is_(False),
        )
        .first()
    )


def create_accounting_period(
    db: Session,
    *,
    partner_id: str,
    contract_id: str,
    year: int,
    quarter: int,
    responsible_user_id: str | None = None,
    comment: str | None = None,
) -> AccountingDataPeriod:
    partner = get_partner(db, partner_id)
    contract = get_contract(db, contract_id)
    if not partner:
        raise ValueError("Partner not found.")
    if not contract or contract.partner_id != partner_id:
        raise ValueError("Contract not found for selected partner.")
    period_start, period_end = accounting_quarter_dates(year, quarter)
    actor_id = audit_user_id(db)
    period = AccountingDataPeriod(
        partner_id=partner_id,
        contract_id=contract_id,
        year=year,
        quarter=quarter,
        period_start=period_start,
        period_end=period_end,
        responsible_user_id=responsible_user_id or None,
        prepared_at=utcnow(),
        comment=(comment or "").strip() or None,
        total_contract_amount=0,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(period)
    db.flush()
    log_create_event(
        db,
        entity_type="AccountingDataPeriod",
        entity_id=period.id,
        data={
            "partner_id": partner_id,
            "contract_id": contract_id,
            "year": year,
            "quarter": quarter,
        },
    )
    db.commit()
    db.refresh(period)
    return period


def recalculate_accounting_period(
    db: Session,
    period: AccountingDataPeriod,
    *,
    commit: bool = True,
) -> AccountingDataPeriod:
    total = sum(line.contract_amount or 0 for line in period.lines if not line.is_deleted)
    has_missing_price = any(
        (line.contract_price is None or line.contract_price <= 0)
        for line in period.lines
        if not line.is_deleted
    )
    period.total_contract_amount = round(total, 2)
    if has_missing_price and period.status == "draft":
        period.status = "needs_review"
    period.version += 1
    if commit:
        db.commit()
        db.refresh(period)
    return period


def get_contract_accounting_tariff(
    db: Session,
    contract_id: str,
    service_id: str,
) -> ContractAccountingTariff | None:
    return (
        db.query(ContractAccountingTariff)
        .filter(
            ContractAccountingTariff.contract_id == contract_id,
            ContractAccountingTariff.service_id == service_id,
            ContractAccountingTariff.is_deleted.is_(False),
            ContractAccountingTariff.is_active.is_(True),
        )
        .first()
    )


def list_contract_accounting_tariffs(
    db: Session,
    contract_id: str,
) -> list[ContractAccountingTariff]:
    return (
        db.query(ContractAccountingTariff)
        .options(joinedload(ContractAccountingTariff.service))
        .filter(
            ContractAccountingTariff.contract_id == contract_id,
            ContractAccountingTariff.is_deleted.is_(False),
        )
        .order_by(ContractAccountingTariff.created_at.desc())
        .all()
    )


def upsert_contract_accounting_tariff(
    db: Session,
    *,
    partner_id: str,
    contract_id: str,
    service_id: str,
    unit: str,
    price: float,
    comment: str | None = None,
    tariff_id: str | None = None,
    is_active: bool = True,
) -> ContractAccountingTariff:
    contract = get_contract(db, contract_id)
    if not contract or contract.partner_id != partner_id:
        raise ValueError("Contract not found for selected partner.")
    contract.version += 1
    if not get_accounting_service(db, service_id):
        raise ValueError("Service not found.")
    tariff = None
    if tariff_id:
        tariff = (
            db.query(ContractAccountingTariff)
            .filter(ContractAccountingTariff.id == tariff_id, ContractAccountingTariff.is_deleted.is_(False))
            .first()
        )
    if not tariff:
        tariff = get_contract_accounting_tariff(db, contract_id, service_id)
    data = {
        "partner_id": partner_id,
        "contract_id": contract_id,
        "service_id": service_id,
        "unit": unit,
        "price": price,
        "comment": (comment or "").strip() or None,
        "is_active": is_active,
    }
    if tariff:
        old_values = {field: getattr(tariff, field, None) for field in data}
        for field, value in data.items():
            setattr(tariff, field, value)
        tariff.version += 1
        log_field_changes(
            db,
            entity_type="ContractAccountingTariff",
            entity_id=tariff.id,
            old_values=old_values,
            new_values=data,
        )
    else:
        tariff = ContractAccountingTariff(**data)
        db.add(tariff)
        db.flush()
        log_create_event(
            db,
            entity_type="ContractAccountingTariff",
            entity_id=tariff.id,
            data=data,
        )
    db.commit()
    db.refresh(tariff)
    return tariff


def delete_contract_accounting_tariff(
    db: Session,
    tariff_id: str,
) -> ContractAccountingTariff | None:
    tariff = (
        db.query(ContractAccountingTariff)
        .filter(ContractAccountingTariff.id == tariff_id, ContractAccountingTariff.is_deleted.is_(False))
        .first()
    )
    if not tariff:
        return None
    contract = get_contract(db, tariff.contract_id)
    if contract:
        contract.version += 1
    tariff.is_deleted = True
    tariff.is_active = False
    tariff.deleted_at = utcnow()
    tariff.version += 1
    log_delete_event(db, entity_type="ContractAccountingTariff", entity_id=tariff.id)
    db.commit()
    db.refresh(tariff)
    return tariff


def add_accounting_line(
    db: Session,
    period: AccountingDataPeriod,
    *,
    service_id: str,
    object_type: str | None = None,
    product_or_inn: str | None = None,
    quantity: float,
    unit: str | None = None,
    contract_price: float | None = None,
    contract_amount: float | None = None,
    source: str | None = None,
    comment: str | None = None,
) -> AccountingDataLine:
    service = get_accounting_service(db, service_id)
    if not service:
        raise ValueError("Service not found.")
    tariff = get_contract_accounting_tariff(db, period.contract_id, service_id)
    resolved_unit = (unit or "").strip() or (tariff.unit if tariff else None) or service.default_unit or "услуга"
    resolved_price = contract_price if contract_price is not None else (tariff.price if tariff else None)
    if resolved_price is None:
        resolved_price = 0
    resolved_amount = (
        contract_amount
        if contract_amount is not None
        else round(float(quantity or 0) * float(resolved_price or 0), 2)
    )
    line = AccountingDataLine(
        accounting_period_id=period.id,
        service_id=service_id,
        object_type=(object_type or "").strip() or None,
        product_or_inn=(product_or_inn or "").strip() or None,
        quantity=quantity,
        unit=resolved_unit,
        contract_price=resolved_price,
        contract_amount=resolved_amount,
        source=(source or "").strip() or None,
        comment=(comment or "").strip() or None,
    )
    # The detail handler has already loaded lines. Keep that collection current
    # so the same transaction calculates the total including the new row.
    period.lines.append(line)
    db.flush()
    log_create_event(
        db,
        entity_type="AccountingDataLine",
        entity_id=line.id,
        data={
            "accounting_period_id": period.id,
            "service_id": service_id,
            "quantity": quantity,
            "contract_amount": resolved_amount,
        },
    )
    recalculate_accounting_period(db, period, commit=False)
    db.commit()
    db.refresh(line)
    return line


def delete_accounting_line(
    db: Session, line_id: str, *, accounting_period_id: str | None = None,
) -> AccountingDataLine | None:
    line = (
        db.query(AccountingDataLine)
        .options(joinedload(AccountingDataLine.accounting_period))
        .filter(AccountingDataLine.id == line_id, AccountingDataLine.is_deleted.is_(False))
        .first()
    )
    if not line:
        return None
    if accounting_period_id is not None and line.accounting_period_id != accounting_period_id:
        raise ValueError("Line does not belong to the selected accounting period.")
    line.is_deleted = True
    line.is_active = False
    line.deleted_at = utcnow()
    line.version += 1
    log_delete_event(db, entity_type="AccountingDataLine", entity_id=line.id)
    recalculate_accounting_period(db, line.accounting_period, commit=False)
    db.commit()
    db.refresh(line)
    return line


def update_accounting_period_status(
    db: Session,
    period: AccountingDataPeriod,
    status: str,
) -> AccountingDataPeriod:
    if status not in ACCOUNTING_STATUSES:
        raise ValueError("Unsupported accounting status.")
    old_status = period.status
    period.status = status
    if status == "sent_to_accounting" and not period.sent_to_accounting_at:
        period.sent_to_accounting_at = utcnow()
    period.version += 1
    log_field_changes(
        db,
        entity_type="AccountingDataPeriod",
        entity_id=period.id,
        old_values={"status": old_status},
        new_values={"status": status},
        action="status_change",
    )
    db.commit()
    db.refresh(period)
    return period


def accounting_period_export_rows(period: AccountingDataPeriod) -> list[list[Any]]:
    info = period.partner.accounting_info if period.partner else None
    headers = [
        "Партнер",
        "ИНН",
        "КПП",
        "Договор",
        "Год",
        "Квартал",
        "Период",
        "Услуга",
        "Объект",
        "Препарат / МНН",
        "Количество",
        "Единица",
        "Цена по договору",
        "Сумма по договору",
        "Основание",
        "Комментарий",
    ]
    rows = [headers]
    for line in period.lines:
        if line.is_deleted:
            continue
        rows.append(
            [
                period.partner.partner_name if period.partner else "",
                info.inn if info else "",
                info.kpp if info else "",
                period.contract.contract_number if period.contract else "",
                period.year,
                period.quarter,
                f"{period.period_start} - {period.period_end}",
                line.service.name if line.service else "",
                line.object_type or "",
                line.product_or_inn or "",
                line.quantity,
                line.unit,
                line.contract_price,
                line.contract_amount,
                line.source or "",
                line.comment or "",
            ]
        )
    rows.append(["Итого сумма по договору", "", "", "", "", "", "", "", "", "", "", "", "", period.total_contract_amount, "", ""])
    return rows


def export_accounting_period_xlsx(period: AccountingDataPeriod) -> bytes:
    return build_xlsx(
        [
            (
                "Данные для бухгалтерии",
                accounting_period_export_rows(period),
            )
        ]
    )


def delete_contract(
    db: Session,
    contract: Contract,
    *,
    deleted_by_user_id: str | None = None,
    delete_reason: str | None = None,
) -> Contract:
    if contract_has_additional_agreements(db, contract.id):
        raise ValueError("Delete attached additional agreements before deleting this contract.")
    contract.is_deleted = True
    contract.is_active = False
    contract.deleted_at = utcnow()
    contract.deleted_by = deleted_by_user_id
    contract.delete_reason = delete_reason
    contract.version += 1
    log_delete_event(
        db,
        entity_type="Contract",
        entity_id=contract.id,
        user_id=deleted_by_user_id,
        delete_reason=delete_reason,
    )
    db.commit()
    db.refresh(contract)
    return contract


def list_contract_contacts(db: Session) -> list[ContractContact]:
    return (
        db.query(ContractContact)
        .options(joinedload(ContractContact.partner))
        .filter(ContractContact.is_deleted.is_(False))
        .order_by(
            ContractContact.last_name,
            ContractContact.first_name,
            ContractContact.patronymic,
        )
        .all()
    )


def get_contract_contact(db: Session, contact_id: str) -> ContractContact | None:
    return (
        db.query(ContractContact)
        .options(joinedload(ContractContact.partner))
        .filter(ContractContact.id == contact_id, ContractContact.is_deleted.is_(False))
        .first()
    )


def create_contract_contact(
    db: Session,
    payload: schemas.ContractContactCreate,
) -> ContractContact:
    data = clean_form_data(model_data(payload))
    email = (data.get("email") or "").strip()
    if email:
        existing = (
            db.query(ContractContact)
            .filter(
                ContractContact.partner_id == data.get("partner_id"),
                func.lower(ContractContact.email) == email.lower(),
                ContractContact.is_deleted.is_(False),
            )
            .first()
        )
        if existing:
            raise ValueError("Contact already exists for this partner.")
    contact = ContractContact(**data)
    db.add(contact)
    db.flush()
    log_create_event(
        db,
        entity_type="ContractContact",
        entity_id=contact.id,
        data=data,
    )
    db.commit()
    db.refresh(contact)
    return contact


def update_contract_contact(
    db: Session,
    contact: ContractContact,
    payload: schemas.ContractContactCreate,
) -> ContractContact:
    data = clean_form_data(model_data(payload))
    email = (data.get("email") or "").strip()
    if email:
        existing = (
            db.query(ContractContact)
            .filter(
                ContractContact.id != contact.id,
                ContractContact.partner_id == data.get("partner_id"),
                func.lower(ContractContact.email) == email.lower(),
                ContractContact.is_deleted.is_(False),
            )
            .first()
        )
        if existing:
            raise ValueError("Contact already exists for this partner.")
    old_values = {field: getattr(contact, field, None) for field in data}
    for field, value in data.items():
        setattr(contact, field, value)
    contact.version += 1
    log_field_changes(
        db,
        entity_type="ContractContact",
        entity_id=contact.id,
        old_values=old_values,
        new_values=data,
    )
    db.commit()
    db.refresh(contact)
    return contact


def delete_contract_contact(
    db: Session,
    contact: ContractContact,
    *,
    deleted_by_user_id: str | None = None,
    delete_reason: str | None = None,
) -> ContractContact:
    contact.is_deleted = True
    contact.is_active = False
    contact.deleted_at = utcnow()
    contact.deleted_by = deleted_by_user_id
    contact.delete_reason = delete_reason
    contact.version += 1
    log_delete_event(
        db,
        entity_type="ContractContact",
        entity_id=contact.id,
        user_id=deleted_by_user_id,
        delete_reason=delete_reason,
    )
    db.commit()
    db.refresh(contact)
    return contact


def list_partner_reconciliations(db: Session) -> list[PartnerReconciliation]:
    return (
        db.query(PartnerReconciliation)
        .options(
            joinedload(PartnerReconciliation.partner),
            joinedload(PartnerReconciliation.contact),
        )
        .filter(PartnerReconciliation.is_deleted.is_(False))
        .order_by(PartnerReconciliation.created_at.desc())
        .all()
    )


def get_partner_reconciliation(
    db: Session,
    reconciliation_id: str,
) -> PartnerReconciliation | None:
    return (
        db.query(PartnerReconciliation)
        .options(
            joinedload(PartnerReconciliation.partner),
            joinedload(PartnerReconciliation.contact),
            selectinload(PartnerReconciliation.items).joinedload(PartnerReconciliationItem.internal_case),
        )
        .filter(
            PartnerReconciliation.id == reconciliation_id,
            PartnerReconciliation.is_deleted.is_(False),
        )
        .first()
    )


def get_latest_partner_reconciliation(db: Session) -> PartnerReconciliation | None:
    return (
        db.query(PartnerReconciliation)
        .filter(PartnerReconciliation.is_deleted.is_(False))
        .order_by(PartnerReconciliation.created_at.desc())
        .first()
    )


def create_partner_reconciliation(
    db: Session,
    payload: schemas.PartnerReconciliationCreate,
    items: list[dict[str, Any]],
    *,
    commit: bool = True,
) -> PartnerReconciliation:
    data = clean_form_data(model_data(payload))
    from app.concurrency import lock_snapshot_inputs
    lock_snapshot_inputs(db, ('PartnerReconciliation',))
    existing = db.query(PartnerReconciliation).filter(
        PartnerReconciliation.partner_id == data['partner_id'],
        PartnerReconciliation.period_start == data['period_start'],
        PartnerReconciliation.period_end == data['period_end'],
        PartnerReconciliation.is_deleted.is_(False),
    ).first()
    if existing:
        raise StaleDataError('A reconciliation already exists for this period. Reload before saving.')
    reconciliation_date = data.pop("reconciliation_date", None) or date.today()
    contact = None
    if data.get("contact_id"):
        contact = get_contract_contact(db, data["contact_id"])

    actor_id = audit_user_id(db)
    reconciliation = PartnerReconciliation(
        **data,
        reconciliation_date=reconciliation_date,
        reconciliation_status="draft",
        created_by=actor_id,
        updated_by=actor_id,
        contact_name=(
            " ".join(
                part
                for part in [
                    contact.last_name if contact else None,
                    contact.first_name if contact else None,
                    contact.patronymic if contact else None,
                ]
                if part
            )
            or None
        ),
        contact_email=contact.email if contact else None,
        our_case_count=sum(1 for item in items if item.get("source_side") == "our_company"),
        partner_case_count=sum(1 for item in items if item.get("source_side") == "partner"),
        matched_count=sum(
            1
            for item in items
            if item.get("reconciliation_status") in {"matched", "confirmed"}
        ),
        discrepancy_count=sum(
            1
            for item in items
            if item.get("reconciliation_status") not in {"matched", "confirmed"}
        ),
    )
    db.add(reconciliation)
    db.flush()

    for item in items:
        db.add(
            PartnerReconciliationItem(
                reconciliation_id=reconciliation.id,
                **clean_form_data(item),
            )
        )

    log_create_event(
        db,
        entity_type="PartnerReconciliation",
        entity_id=reconciliation.id,
        data={
            "partner_id": reconciliation.partner_id,
            "contact_id": reconciliation.contact_id,
            "period_start": reconciliation.period_start,
            "period_end": reconciliation.period_end,
            "language": reconciliation.language,
            "reconciliation_status": reconciliation.reconciliation_status,
        },
    )
    if commit:
        db.commit()
        db.refresh(reconciliation)
    else:
        db.flush()
    return reconciliation


def update_partner_reconciliation_item(
    db: Session,
    item: PartnerReconciliationItem,
    payload: schemas.PartnerReconciliationItemUpdate,
) -> PartnerReconciliationItem:
    data = clean_form_data(model_data(payload))
    old_values = {
        "internal_case_id": item.internal_case_id,
        "reconciliation_status": item.reconciliation_status,
        "reviewer_comment": item.reviewer_comment,
        "confirmed_by_user": item.confirmed_by_user,
    }
    if "internal_case_id" in data:
        item.internal_case_id = data.get("internal_case_id")
    item.reconciliation_status = data["reconciliation_status"]
    item.reviewer_comment = data.get("reviewer_comment")
    item.confirmed_by_user = data.get("confirmed_by_user")
    item.version += 1
    log_field_changes(
        db,
        entity_type="PartnerReconciliationItem",
        entity_id=item.id,
        action="status_change",
        old_values=old_values,
        new_values={
            "internal_case_id": item.internal_case_id,
            "reconciliation_status": item.reconciliation_status,
            "reviewer_comment": item.reviewer_comment,
            "confirmed_by_user": item.confirmed_by_user,
        },
        comment=item.reviewer_comment,
    )
    refresh_partner_reconciliation_counts(item.reconciliation)
    # Always update the aggregate, even when counts stay the same. Otherwise
    # a late parent change could evade the mapper's version predicate.
    item.reconciliation.version += 1
    db.commit()
    db.refresh(item)
    return item


def get_partner_reconciliation_item(
    db: Session,
    item_id: str,
) -> PartnerReconciliationItem | None:
    return (
        db.query(PartnerReconciliationItem)
        .options(joinedload(PartnerReconciliationItem.reconciliation))
        .filter(
            PartnerReconciliationItem.id == item_id,
            PartnerReconciliationItem.is_deleted.is_(False),
        )
        .first()
    )


def confirm_partner_reconciliation(
    db: Session,
    reconciliation: PartnerReconciliation,
    confirmed_by_user: str | None = None,
) -> PartnerReconciliation:
    old_status = reconciliation.reconciliation_status
    reconciliation.reconciliation_status = "confirmed"
    reconciliation.confirmed_by_user = confirmed_by_user
    reconciliation.confirmed_at = utcnow()
    reconciliation.updated_by = audit_user_id(db)
    reconciliation.version += 1
    for item in reconciliation.items:
        if not item.is_deleted and item.reconciliation_status == "matched":
            item.reconciliation_status = "confirmed"
            item.confirmed_by_user = confirmed_by_user
    refresh_partner_reconciliation_counts(reconciliation)
    log_audit(
        db,
        entity_type="PartnerReconciliation",
        entity_id=reconciliation.id,
        action="confirm",
        field_name="reconciliation_status",
        old_value=old_status,
        new_value=reconciliation.reconciliation_status,
        comment=confirmed_by_user,
        user_id=audit_user_id(db),
    )
    db.commit()
    db.refresh(reconciliation)
    return reconciliation


def update_partner_reconciliation_status(
    db: Session,
    reconciliation: PartnerReconciliation,
    *,
    reconciliation_status: str,
    sent_date: date | None = None,
    response_date: date | None = None,
    discrepancy_description: str | None = None,
    document_id: str | None = None,
    products: str | None = None,
    comments: str | None = None,
    changed_by_user_id: str | None = None,
    commit: bool = True,
) -> PartnerReconciliation:
    old_values = {
        "reconciliation_status": reconciliation.reconciliation_status,
        "sent_date": reconciliation.sent_date,
        "response_date": reconciliation.response_date,
        "discrepancy_description": reconciliation.discrepancy_description,
        "document_id": reconciliation.document_id,
        "products": reconciliation.products,
        "comments": reconciliation.comments,
    }
    reconciliation.reconciliation_status = reconciliation_status
    if sent_date is not None:
        reconciliation.sent_date = sent_date
    elif reconciliation_status == "sent" and not reconciliation.sent_date:
        reconciliation.sent_date = date.today()
    if response_date is not None:
        reconciliation.response_date = response_date
    elif reconciliation_status in {"response_received", "discrepancy_found"} and not reconciliation.response_date:
        reconciliation.response_date = date.today()
    if discrepancy_description is not None:
        reconciliation.discrepancy_description = discrepancy_description
    if document_id is not None:
        reconciliation.document_id = document_id
    if products is not None:
        reconciliation.products = products
    if comments is not None:
        reconciliation.comments = comments
    reconciliation.updated_by = changed_by_user_id or audit_user_id(db)
    reconciliation.version += 1
    log_field_changes(
        db,
        entity_type="PartnerReconciliation",
        entity_id=reconciliation.id,
        old_values=old_values,
        new_values={
            "reconciliation_status": reconciliation.reconciliation_status,
            "sent_date": reconciliation.sent_date,
            "response_date": reconciliation.response_date,
            "discrepancy_description": reconciliation.discrepancy_description,
            "document_id": reconciliation.document_id,
            "products": reconciliation.products,
            "comments": reconciliation.comments,
        },
        action="status_change",
        user_id=reconciliation.updated_by,
        comment=reconciliation.discrepancy_description,
    )
    if commit:
        db.commit()
        db.refresh(reconciliation)
    else:
        db.flush()
    return reconciliation


def save_partner_reconciliation_document(
    db: Session,
    reconciliation: PartnerReconciliation,
    document_info: dict[str, Any],
    *,
    changed_by_user_id: str | None = None,
    commit: bool = True,
) -> PartnerReconciliation:
    old_values = {
        "reconciliation_status": reconciliation.reconciliation_status,
        "document_path": reconciliation.document_path,
        "document_filename": reconciliation.document_filename,
        "document_format": reconciliation.document_format,
        "generated_at": reconciliation.generated_at,
    }
    reconciliation.document_path = document_info["document_path"]
    reconciliation.document_filename = document_info["document_filename"]
    reconciliation.document_format = document_info["document_format"]
    reconciliation.generated_at = utcnow()
    if reconciliation.reconciliation_status in {"draft", "error"}:
        reconciliation.reconciliation_status = "generated"
    reconciliation.updated_by = changed_by_user_id or audit_user_id(db)
    reconciliation.version += 1
    log_field_changes(
        db,
        entity_type="PartnerReconciliation",
        entity_id=reconciliation.id,
        old_values=old_values,
        new_values={
            "reconciliation_status": reconciliation.reconciliation_status,
            "document_path": reconciliation.document_path,
            "document_filename": reconciliation.document_filename,
            "document_format": reconciliation.document_format,
            "generated_at": reconciliation.generated_at,
        },
        action="generate_document",
        user_id=reconciliation.updated_by,
        comment=reconciliation.document_filename,
    )
    if commit:
        db.commit()
        db.refresh(reconciliation)
    else:
        db.flush()
    return reconciliation


def update_partner_reconciliation_email_preview(
    db: Session,
    reconciliation: PartnerReconciliation,
    *,
    email_to: str,
    email_cc: str | None,
    email_subject: str,
    email_body: str,
    changed_by_user_id: str | None = None,
) -> PartnerReconciliation:
    old_values = {
        "email_to": reconciliation.email_to,
        "email_cc": reconciliation.email_cc,
        "email_subject": reconciliation.email_subject,
        "email_body": reconciliation.email_body,
    }
    reconciliation.email_to = email_to
    reconciliation.email_cc = email_cc
    reconciliation.email_subject = email_subject
    reconciliation.email_body = email_body
    reconciliation.updated_by = changed_by_user_id or audit_user_id(db)
    reconciliation.version += 1
    log_field_changes(
        db,
        entity_type="PartnerReconciliation",
        entity_id=reconciliation.id,
        old_values=old_values,
        new_values={
            "email_to": reconciliation.email_to,
            "email_cc": reconciliation.email_cc,
            "email_subject": reconciliation.email_subject,
            "email_body": reconciliation.email_body,
        },
        action="email_preview_saved",
        user_id=reconciliation.updated_by,
        comment="Outlook email preview prepared.",
    )
    db.commit()
    db.refresh(reconciliation)
    return reconciliation


def mark_partner_reconciliation_outlook_draft_created(
    db: Session,
    reconciliation: PartnerReconciliation,
    *,
    outlook_message_id: str,
    outlook_draft_web_link: str | None,
    email_to: str,
    email_cc: str | None,
    email_subject: str,
    email_body: str,
    changed_by_user_id: str | None = None,
) -> PartnerReconciliation:
    old_values = {
        "reconciliation_status": reconciliation.reconciliation_status,
        "outlook_message_id": reconciliation.outlook_message_id,
        "outlook_draft_web_link": reconciliation.outlook_draft_web_link,
        "outlook_status": reconciliation.outlook_status,
        "outlook_error": reconciliation.outlook_error,
        "draft_created_at": reconciliation.draft_created_at,
        "email_to": reconciliation.email_to,
        "email_cc": reconciliation.email_cc,
        "email_subject": reconciliation.email_subject,
        "email_body": reconciliation.email_body,
    }
    reconciliation.reconciliation_status = "outlook_draft_created"
    reconciliation.outlook_message_id = outlook_message_id
    reconciliation.outlook_draft_web_link = outlook_draft_web_link
    reconciliation.outlook_status = "draft_created"
    reconciliation.outlook_error = None
    reconciliation.draft_created_at = utcnow()
    reconciliation.email_to = email_to
    reconciliation.email_cc = email_cc
    reconciliation.email_subject = email_subject
    reconciliation.email_body = email_body
    reconciliation.updated_by = changed_by_user_id or audit_user_id(db)
    reconciliation.version += 1
    log_field_changes(
        db,
        entity_type="PartnerReconciliation",
        entity_id=reconciliation.id,
        old_values=old_values,
        new_values={
            "reconciliation_status": reconciliation.reconciliation_status,
            "outlook_message_id": reconciliation.outlook_message_id,
            "outlook_draft_web_link": reconciliation.outlook_draft_web_link,
            "outlook_status": reconciliation.outlook_status,
            "outlook_error": reconciliation.outlook_error,
            "draft_created_at": reconciliation.draft_created_at,
            "email_to": reconciliation.email_to,
            "email_cc": reconciliation.email_cc,
            "email_subject": reconciliation.email_subject,
            "email_body": reconciliation.email_body,
        },
        action="outlook_draft_created",
        user_id=reconciliation.updated_by,
        comment=f"recipients={email_to}; cc={email_cc or ''}",
    )
    db.commit()
    db.refresh(reconciliation)
    return reconciliation


def mark_partner_reconciliation_outlook_sent(
    db: Session,
    reconciliation: PartnerReconciliation,
    *,
    changed_by_user_id: str | None = None,
) -> PartnerReconciliation:
    old_values = {
        "reconciliation_status": reconciliation.reconciliation_status,
        "outlook_status": reconciliation.outlook_status,
        "outlook_error": reconciliation.outlook_error,
        "sent_at": reconciliation.sent_at,
        "sent_date": reconciliation.sent_date,
    }
    reconciliation.reconciliation_status = "sent"
    reconciliation.outlook_status = "sent"
    reconciliation.outlook_error = None
    reconciliation.sent_at = utcnow()
    reconciliation.sent_date = reconciliation.sent_at.date()
    reconciliation.updated_by = changed_by_user_id or audit_user_id(db)
    reconciliation.version += 1
    log_field_changes(
        db,
        entity_type="PartnerReconciliation",
        entity_id=reconciliation.id,
        old_values=old_values,
        new_values={
            "reconciliation_status": reconciliation.reconciliation_status,
            "outlook_status": reconciliation.outlook_status,
            "outlook_error": reconciliation.outlook_error,
            "sent_at": reconciliation.sent_at,
            "sent_date": reconciliation.sent_date,
        },
        action="outlook_sent",
        user_id=reconciliation.updated_by,
        comment=f"recipients={reconciliation.email_to or ''}; cc={reconciliation.email_cc or ''}",
    )
    db.commit()
    db.refresh(reconciliation)
    return reconciliation


def mark_partner_reconciliation_outlook_error(
    db: Session,
    reconciliation: PartnerReconciliation,
    *,
    error_message: str,
    action: str,
    changed_by_user_id: str | None = None,
) -> PartnerReconciliation:
    old_values = {
        "reconciliation_status": reconciliation.reconciliation_status,
        "outlook_status": reconciliation.outlook_status,
        "outlook_error": reconciliation.outlook_error,
    }
    if reconciliation.reconciliation_status not in {"sent", "closed"}:
        reconciliation.reconciliation_status = "error"
    reconciliation.outlook_status = "error"
    reconciliation.outlook_error = error_message
    reconciliation.updated_by = changed_by_user_id or audit_user_id(db)
    reconciliation.version += 1
    log_field_changes(
        db,
        entity_type="PartnerReconciliation",
        entity_id=reconciliation.id,
        old_values=old_values,
        new_values={
            "reconciliation_status": reconciliation.reconciliation_status,
            "outlook_status": reconciliation.outlook_status,
            "outlook_error": reconciliation.outlook_error,
        },
        action=action,
        user_id=reconciliation.updated_by,
        comment=error_message,
    )
    db.commit()
    db.refresh(reconciliation)
    return reconciliation


def refresh_partner_reconciliation_counts(reconciliation: PartnerReconciliation) -> None:
    items = [item for item in reconciliation.items if not item.is_deleted]
    reconciliation.our_case_count = sum(1 for item in items if item.source_side == "our_company")
    reconciliation.partner_case_count = sum(1 for item in items if item.source_side == "partner")
    reconciliation.matched_count = sum(
        1 for item in items if item.reconciliation_status in {"matched", "confirmed"}
    )
    reconciliation.discrepancy_count = sum(
        1 for item in items if item.reconciliation_status not in {"matched", "confirmed"}
    )


def create_product_substance(
    db: Session,
    payload: schemas.ProductSubstanceCreate,
) -> ProductSubstance:
    existing_link = (
        db.query(ProductSubstance)
        .filter(
            ProductSubstance.product_id == payload.product_id,
            ProductSubstance.substance_id == payload.substance_id,
            ProductSubstance.is_deleted.is_(False),
        )
        .first()
    )
    if existing_link:
        return existing_link

    link = ProductSubstance(**clean_form_data(model_data(payload)))
    db.add(link)
    db.flush()
    log_create_event(
        db,
        entity_type="ProductSubstance",
        entity_id=link.id,
        data=clean_form_data(model_data(payload)),
    )
    db.commit()
    db.refresh(link)
    return link


def safety_reports_query(db: Session, *, search=None, status_filter=None, partner_id=None,
                         date_from=None, date_to=None):
    query = db.query(SafetyReport).filter(SafetyReport.is_deleted.is_(False))
    if status_filter:
        query = query.filter(SafetyReport.triage_status == status_filter)
    if partner_id:
        query = query.filter(SafetyReport.partner_id == partner_id)
    if date_from:
        query = query.filter(SafetyReport.received_date >= date_from)
    if date_to:
        query = query.filter(SafetyReport.received_date <= date_to)
    if search and search.strip():
        def matches(column):
            return func.lower(column).contains(search.strip().lower(), autoescape=True)
        query = query.filter(or_(
            *(matches(column) for column in (SafetyReport.safety_report_number, SafetyReport.raw_subject,
                SafetyReport.raw_text, SafetyReport.reporter_name, SafetyReport.reporter_email)),
            SafetyReport.partner.has(matches(Partner.partner_name)),
            SafetyReport.product_links.any(or_(matches(SafetyReportProduct.reported_product_name),
                SafetyReportProduct.product.has(matches(Product.product_name)))),
        ))
    return (
        query
        .options(
            joinedload(SafetyReport.partner),
            selectinload(SafetyReport.product_links).joinedload(SafetyReportProduct.product),
        )
        .filter(SafetyReport.is_deleted.is_(False))
        .order_by(SafetyReport.received_at.desc(), SafetyReport.id)
    )


def list_safety_reports(db: Session) -> list[SafetyReport]:
    return safety_reports_query(db).all()


def get_safety_report(db: Session, report_id: str) -> SafetyReport | None:
    return (
        db.query(SafetyReport)
        .options(
            joinedload(SafetyReport.partner),
            joinedload(SafetyReport.case),
            selectinload(SafetyReport.product_links).joinedload(SafetyReportProduct.product),
        )
        .filter(SafetyReport.id == report_id, SafetyReport.is_deleted.is_(False))
        .first()
    )


def create_safety_report(db: Session, payload: schemas.SafetyReportCreate) -> SafetyReport:
    data = clean_form_data(model_data(payload))
    product_ids = validate_message_product_ids(db, data.pop("product_ids", []))
    data["safety_report_number"] = data.get("safety_report_number") or generate_number(
        db,
        SafetyReport,
        "safety_report_number",
        "SR",
    )
    data["received_at"] = data.get("received_at") or utcnow()
    data["received_date"] = data.get("received_date") or date.today()
    if product_ids:
        data["minimum_criteria_product"] = True
    report = SafetyReport(**data)
    db.add(report)
    db.flush()
    sync_message_product_links(db, report, SafetyReportProduct, product_ids)
    log_create_event(
        db,
        entity_type="SafetyReport",
        entity_id=report.id,
        data={**data, "product_ids": ", ".join(product_ids)},
    )
    db.commit()
    db.refresh(report)
    return report


def validate_message_product_ids(db: Session, product_ids: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for product_id in product_ids or []:
        product_id = (product_id or "").strip()
        if product_id and product_id not in normalized:
            normalized.append(product_id)
    for product_id in normalized:
        if not get_product(db, product_id):
            raise ValueError("Selected product was not found.")
    return normalized


def sync_message_product_links(
    db: Session,
    message: SafetyReport | IncomingRequest,
    link_model: type[SafetyReportProduct] | type[IncomingRequestProduct],
    product_ids: list[str] | None,
) -> list[str]:
    product_ids = validate_message_product_ids(db, product_ids)
    selected = set(product_ids)
    existing_by_product_id = {
        link.product_id: link
        for link in message.product_links
        if link.product_id
    }
    for link in list(message.product_links):
        if link.product_id and link.product_id not in selected:
            message.product_links.remove(link)
    for product_id in product_ids:
        product = get_product(db, product_id)
        link = existing_by_product_id.get(product_id)
        if link:
            link.is_deleted = False
            link.is_active = True
            link.reported_product_name = product.product_name if product else link.reported_product_name
            continue
        message.product_links.append(
            link_model(
                product_id=product_id,
                reported_product_name=product.product_name if product else None,
                drug_role="suspect",
            )
        )
    db.flush()
    return product_ids


def update_safety_report_products(
    db: Session,
    report: SafetyReport,
    product_ids: list[str] | None,
) -> SafetyReport:
    old_product_ids = report.product_ids
    new_product_ids = sync_message_product_links(
        db,
        report,
        SafetyReportProduct,
        product_ids,
    )
    report.minimum_criteria_product = bool(report.product_names)
    report.version += 1
    log_audit(
        db,
        entity_type="SafetyReport",
        entity_id=report.id,
        action="edit",
        field_name="product_ids",
        old_value=", ".join(old_product_ids),
        new_value=", ".join(new_product_ids),
        user_id=audit_user_id(db),
    )
    db.commit()
    db.refresh(report)
    return report


def triage_safety_report(db: Session, report: SafetyReport, payload: schemas.TriageUpdate) -> SafetyReport:
    data = clean_form_data(model_data(payload))
    old_status = report.triage_status
    report.triage_status = data["triage_status"]
    report.triage_comment = data.get("triage_comment")
    report.is_valid_icsr = bool(data.get("is_valid_icsr"))
    report.minimum_criteria_patient = bool(data.get("minimum_criteria_patient"))
    report.minimum_criteria_reporter = bool(data.get("minimum_criteria_reporter"))
    report.minimum_criteria_product = bool(data.get("minimum_criteria_product"))
    report.minimum_criteria_event = bool(data.get("minimum_criteria_event"))
    user_id = audit_user_id(db)
    report.triaged_by_user_id = user_id
    report.triaged_at = utcnow()
    report.version += 1
    log_audit(
        db,
        entity_type="SafetyReport",
        entity_id=report.id,
        action="triage",
        field_name="triage_status",
        old_value=old_status,
        new_value=report.triage_status,
        change_reason=data.get("change_reason"),
        user_id=user_id,
    )
    db.commit()
    db.refresh(report)
    return report


def update_safety_report_direction(
    db: Session,
    report: SafetyReport,
    payload: schemas.MessageDirectionUpdate,
) -> SafetyReport:
    old_direction = report.message_direction
    report.message_direction = payload.message_direction
    report.version += 1
    log_audit(
        db,
        entity_type="SafetyReport",
        entity_id=report.id,
        action="update",
        field_name="message_direction",
        old_value=old_direction,
        new_value=report.message_direction,
        user_id=audit_user_id(db),
    )
    db.commit()
    db.refresh(report)
    return report


def cases_query(db: Session):
    return (
        db.query(Case)
        .options(
            joinedload(Case.partner),
            joinedload(Case.numbering_partner),
            selectinload(Case.identifiers).joinedload(CaseIdentifier.partner),
            selectinload(Case.partner_links).joinedload(CasePartnerLink.partner),
            joinedload(Case.safety_report),
            selectinload(Case.case_products).joinedload(CaseProduct.product),
            selectinload(Case.reactions),
        )
        .filter(Case.is_deleted.is_(False))
        .order_by(Case.created_at.desc(), Case.id)
    )


def list_cases(db: Session) -> list[Case]:
    return cases_query(db).all()


def filtered_cases_query(db: Session, *, search=None, status_filter=None, partner_id=None,
                         product_id=None, date_from=None, date_to=None):
    query = cases_query(db)
    if status_filter:
        query = query.filter(Case.workflow_status == status_filter)
    if partner_id:
        query = query.filter(Case.numbering_partner_id == partner_id)
    if product_id:
        query = query.filter(Case.case_products.any(CaseProduct.product_id == product_id))
    if date_from:
        query = query.filter(Case.initial_received_date >= date_from)
    if date_to:
        query = query.filter(Case.initial_received_date <= date_to)
    if search and search.strip():
        needle = search.strip().lower()
        def matches(column):
            return func.lower(column).contains(needle, autoescape=True)
        # EXISTS keeps the case row unique regardless of collection sizes.
        from sqlalchemy import select
        partner_code = select(Partner.partner_code).where(Partner.id == Case.numbering_partner_id).scalar_subquery()
        full_code = case(
            ((Case.number_is_provisional.is_(True) | Case.numbering_partner_id.is_(None)),
             case((Case.case_number.startswith("UNASSIGNED-"), Case.case_number), else_="UNASSIGNED-" + Case.case_number)),
            else_=func.coalesce(partner_code, "PARTNER" + Case.numbering_partner_id) + "-" + Case.case_number,
        )
        query = query.filter(or_(
            *(matches(column) for column in (Case.case_number, full_code, Case.worldwide_case_id,
                                             Case.local_sender_case_id, Case.narrative)),
            Case.partner.has(matches(Partner.partner_name)),
            Case.numbering_partner.has(or_(matches(Partner.partner_code), matches(Partner.partner_name))),
            Case.identifiers.any((CaseIdentifier.is_deleted.is_(False)) &
                                 (CaseIdentifier.identifier_type != "previous_internal_number") & matches(CaseIdentifier.identifier_value)),
            Case.case_products.any(or_(matches(CaseProduct.reported_product_name),
                                       CaseProduct.product.has(matches(Product.product_name)))),
            Case.reactions.any(matches(Reaction.reported_term)),
        ))
    return query


def case_audit_query(db: Session, case_id: str):
    return db.query(AuditTrail).options(joinedload(AuditTrail.user)).filter(
        AuditTrail.case_id == case_id
    ).order_by(AuditTrail.timestamp.desc(), AuditTrail.id)


def get_case(db: Session, case_id: str) -> Case | None:
    return (
        db.query(Case)
        .options(
            joinedload(Case.partner),
            joinedload(Case.numbering_partner),
            selectinload(Case.identifiers).joinedload(CaseIdentifier.partner),
            selectinload(Case.partner_links).joinedload(CasePartnerLink.partner),
            joinedload(Case.safety_report),
            selectinload(Case.patients),
            selectinload(Case.case_products).joinedload(CaseProduct.product),
            selectinload(Case.case_products)
            .selectinload(CaseProduct.assessments)
            .joinedload(CaseProductReactionAssessment.reaction),
            selectinload(Case.case_products).selectinload(CaseProduct.drug_substances).joinedload(CaseDrugSubstance.substance),
            selectinload(Case.reactions),
            selectinload(Case.followups),
            selectinload(Case.submissions).joinedload(Submission.recipient_partner),
            selectinload(Case.e2b_transmissions),
            selectinload(Case.literature_references),
            selectinload(Case.narratives),
            selectinload(Case.incoming_request_links).joinedload(
                IncomingRequestCaseLink.incoming_request
            ),
        )
        .filter(Case.id == case_id)
        .first()
    )


def add_case_partner(
    db: Session,
    case: Case,
    partner: Partner,
    *,
    relationship_type: str = "related",
) -> CasePartnerLink:
    link = add_numbering_partner_link(
        db,
        case,
        partner,
        relationship_type=relationship_type,
    )
    db.flush()
    log_create_event(
        db,
        entity_type="CasePartnerLink",
        entity_id=link.id,
        case_id=case.id,
        data={
            "partner_id": partner.id,
            "relationship_type": link.relationship_type,
        },
        user_id=audit_user_id(db),
        comment="Связанный партнёр добавлен без изменения номера случая.",
    )
    db.commit()
    db.refresh(link)
    return link


def create_case_identifier(
    db: Session,
    case: Case,
    payload: schemas.CaseIdentifierCreate,
) -> tuple[CaseIdentifier, bool]:
    data = clean_form_data(model_data(payload))
    data.setdefault("identifier_type", "partner_case_number")
    data.setdefault("is_primary", False)
    if data.get("partner_id") and not get_partner(db, data["partner_id"]):
        raise ValueError("Выбранный партнёр не найден.")
    identifier, possible_duplicate = add_numbering_identifier(db, case, **data)
    db.flush()
    log_create_event(
        db,
        entity_type="CaseIdentifier",
        entity_id=identifier.id,
        case_id=case.id,
        data=data,
        user_id=audit_user_id(db),
        comment=(
            "Возможный дубликат внешнего номера у выбранного партнёра."
            if possible_duplicate
            else "Дополнительный номер добавлен."
        ),
    )
    db.commit()
    db.refresh(identifier)
    return identifier, possible_duplicate


def create_case(db: Session, payload: schemas.CaseCreate) -> Case:
    data = clean_form_data(model_data(payload))
    supplied_number = data.pop("case_number", None)
    data["workflow_status"] = validate_case_status(data.get("workflow_status") or "new")
    actor_id = audit_user_id(db)
    case = Case(case_number="", **data, created_by=actor_id, updated_by=actor_id)
    assign_case_number(
        db,
        case,
        partner_id=data.get("partner_id"),
        supplied_number=supplied_number,
    )
    data["case_number"] = case.case_number
    apply_case_lock_state(case, case.workflow_status, actor_id)
    db.add(case)
    db.flush()

    if case.safety_report_id:
        report = db.query(SafetyReport).filter(SafetyReport.id == case.safety_report_id).first()
        if report:
            report.case_id = case.id
            report.triage_status = "converted_to_case"

    log_create_event(
        db,
        entity_type="Case",
        entity_id=case.id,
        case_id=case.id,
        data=data,
        user_id=actor_id,
    )
    db.commit()
    db.refresh(case)
    return case


def create_manual_case(
    db: Session,
    case_payload: schemas.CaseCreate,
    *,
    reporter_name: str | None,
    reporter_email: str | None,
    reporter_country_code: str | None,
    source_type: str | None,
    patient_payload: schemas.PatientCreate,
    product_payload: schemas.CaseProductCreate,
    reaction_payload: schemas.ReactionCreate,
) -> Case:
    """Create a complete manually entered ICSR as one database transaction."""
    case_data = clean_form_data(model_data(case_payload))
    patient_data = clean_form_data(model_data(patient_payload))
    product_data = clean_form_data(model_data(product_payload))
    reaction_data = clean_form_data(model_data(reaction_payload))

    existing_report = None
    if case_data.get("safety_report_id"):
        existing_report = get_safety_report(db, case_data["safety_report_id"])
        if not existing_report:
            raise ValueError("Selected safety report was not found.")
        if existing_report.case:
            raise ValueError("Selected safety report is already linked to a case.")

    patient_identified = bool(
        patient_data.get("patient_initials") or patient_data.get("patient_identifier")
    )
    reporter_identified = bool(
        reporter_name
        or reporter_email
        or (
            existing_report
            and (existing_report.reporter_name or existing_report.reporter_email)
        )
    )
    product_identified = bool(
        product_data.get("product_id") or product_data.get("reported_product_name")
    )
    event_identified = bool(reaction_data.get("reported_term"))
    missing_criteria = []
    if not patient_identified:
        missing_criteria.append("an identifiable patient (initials or identifier)")
    if not reporter_identified:
        missing_criteria.append("an identifiable reporter (name or email)")
    if not product_identified:
        missing_criteria.append("a suspect product")
    if not event_identified:
        missing_criteria.append("an adverse reaction")
    if missing_criteria:
        raise ValueError("Manual ICSR requires " + ", ".join(missing_criteria) + ".")

    product = None
    if product_data.get("product_id"):
        product = get_product(db, product_data["product_id"])
        if not product:
            raise ValueError("Selected product was not found.")
        product_data["reported_product_name"] = (
            product_data.get("reported_product_name") or product.product_name
        )
        product_data["route"] = product_data.get("route") or product.route
        if not product_data.get("active_substance_text") and product.substance_links:
            product_data["active_substance_text"] = ", ".join(
                link.substance.substance_name
                for link in product.substance_links
                if link.substance
            )

    serious_fields = (
        "seriousness_death",
        "seriousness_life_threatening",
        "seriousness_hospitalization",
        "seriousness_disability",
        "seriousness_congenital_anomaly",
        "seriousness_other_medically_important",
    )
    reaction_is_serious = bool(
        reaction_data.get("is_serious")
        or any(reaction_data.get(field) for field in serious_fields)
    )
    reaction_data["is_serious"] = reaction_is_serious

    actor_id = audit_user_id(db)
    try:
        report = existing_report
        if not report:
            received_date = case_data.get("initial_received_date") or date.today()
            report = SafetyReport(
                safety_report_number=generate_number(
                    db,
                    SafetyReport,
                    "safety_report_number",
                    "SR",
                ),
                received_at=utcnow(),
                received_date=received_date,
                source_type=source_type or "manual",
                message_direction="needs_review",
                partner_id=case_data.get("partner_id"),
                reporter_name=empty_to_none(reporter_name),
                reporter_email=empty_to_none(reporter_email),
                reporter_country_code=empty_to_none(reporter_country_code),
                raw_subject="Manual ICSR entry",
                raw_text=case_data.get("narrative"),
                triage_status="converted_to_case",
                is_valid_icsr=True,
                minimum_criteria_patient=True,
                minimum_criteria_reporter=True,
                minimum_criteria_product=True,
                minimum_criteria_event=True,
            )
            db.add(report)
            db.flush()
            log_create_event(
                db,
                entity_type="SafetyReport",
                entity_id=report.id,
                data={
                    "safety_report_number": report.safety_report_number,
                    "source_type": report.source_type,
                    "reporter_name": report.reporter_name,
                    "reporter_email": report.reporter_email,
                    "triage_status": report.triage_status,
                    "is_valid_icsr": True,
                },
                user_id=actor_id,
                comment="Created by manual ICSR entry",
            )
        else:
            report.is_valid_icsr = True
            report.minimum_criteria_patient = True
            report.minimum_criteria_reporter = True
            report.minimum_criteria_product = True
            report.minimum_criteria_event = True
            report.triage_status = "converted_to_case"

        product_link_exists = any(
            not link.is_deleted
            and (
                (product_data.get("product_id") and link.product_id == product_data["product_id"])
                or (
                    not product_data.get("product_id")
                    and (link.reported_product_name or "").strip().casefold()
                    == (product_data.get("reported_product_name") or "").strip().casefold()
                )
            )
            for link in report.product_links
        )
        if not product_link_exists:
            report.product_links.append(
                SafetyReportProduct(
                    product_id=product_data.get("product_id"),
                    reported_product_name=product_data.get("reported_product_name"),
                    drug_role=product_data.get("drug_role") or "suspect",
                )
            )

        supplied_number = case_data.pop("case_number", None)
        case_data["safety_report_id"] = report.id
        case_data["partner_id"] = case_data.get("partner_id") or report.partner_id
        case_data["case_type"] = case_data.get("case_type") or "spontaneous"
        case_data["report_type"] = (
            case_data.get("report_type") or source_type or report.source_type or "manual"
        )
        case_data["initial_received_date"] = (
            case_data.get("initial_received_date") or report.received_date or date.today()
        )
        case_data["latest_received_date"] = (
            case_data.get("latest_received_date") or case_data["initial_received_date"]
        )
        case_data["country_of_occurrence"] = (
            case_data.get("country_of_occurrence") or report.reporter_country_code
        )
        case_data["seriousness"] = "serious" if reaction_is_serious else "non-serious"
        case_data["workflow_status"] = validate_case_status(
            case_data.get("workflow_status") or "data_entry"
        )
        case = Case(case_number="", **case_data, created_by=actor_id, updated_by=actor_id)
        assign_case_number(
            db,
            case,
            partner_id=case_data.get("partner_id"),
            supplied_number=supplied_number,
        )
        case_data["case_number"] = case.case_number
        apply_case_lock_state(case, case.workflow_status, actor_id)
        db.add(case)
        db.flush()
        report.case_id = case.id

        patient = Patient(case_id=case.id, **patient_data)
        case_product = CaseProduct(case_id=case.id, **product_data)
        reaction = Reaction(case_id=case.id, **reaction_data)
        db.add_all([patient, case_product, reaction])
        db.flush()

        log_create_event(
            db,
            entity_type="Case",
            entity_id=case.id,
            case_id=case.id,
            data=case_data,
            user_id=actor_id,
            comment="Complete manual ICSR entry",
        )
        log_create_event(
            db,
            entity_type="Patient",
            entity_id=patient.id,
            case_id=case.id,
            data=patient_data,
            user_id=actor_id,
        )
        log_create_event(
            db,
            entity_type="CaseProduct",
            entity_id=case_product.id,
            case_id=case.id,
            data=product_data,
            user_id=actor_id,
        )
        log_create_event(
            db,
            entity_type="Reaction",
            entity_id=reaction.id,
            case_id=case.id,
            data=reaction_data,
            user_id=actor_id,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(case)
    return case


def create_case_from_report(db: Session, report: SafetyReport) -> Case:
    if report.case:
        return report.case

    case = Case(
        case_number="",
        safety_report_id=report.id,
        partner_id=report.partner_id,
        case_type="spontaneous",
        report_type=report.source_type or "spontaneous",
        initial_received_date=report.received_date,
        latest_received_date=report.received_date,
        country_of_occurrence=report.reporter_country_code,
        seriousness="non-serious",
        narrative=report.raw_text,
        workflow_status="data_entry",
    )
    assign_case_number(db, case, partner_id=report.partner_id)
    db.add(case)
    db.flush()
    for link in report.product_links:
        if link.is_deleted:
            continue
        db.add(
            CaseProduct(
                case_id=case.id,
                product_id=link.product_id,
                reported_product_name=link.reported_product_name
                or (link.product.product_name if link.product else None),
                drug_role=link.drug_role or "suspect",
            )
        )
    report.case_id = case.id
    report.triage_status = "converted_to_case"
    report.is_valid_icsr = True
    report.version += 1
    user_id = audit_user_id(db)
    log_create_event(
        db,
        entity_type="Case",
        entity_id=case.id,
        case_id=case.id,
        data={
            "case_number": case.case_number,
            "safety_report_id": report.id,
            "workflow_status": case.workflow_status,
        },
        user_id=user_id,
        comment="Created from safety report",
    )
    log_audit(
        db,
        entity_type="SafetyReport",
        entity_id=report.id,
        action="status_change",
        field_name="triage_status",
        old_value="valid_icsr",
        new_value="converted_to_case",
        case_id=case.id,
        user_id=user_id,
    )
    db.commit()
    db.refresh(case)
    return case


def update_case_status(db: Session, case: Case, payload: schemas.CaseStatusUpdate) -> Case:
    if payload.expected_version != case.version:
        raise StaleDataError("Case version has changed. Reload before saving.")
    next_status_code = validate_icsr_transition(case.workflow_status, payload.workflow_status)
    current_status_code = normalized_status(case.workflow_status)
    if case_is_controlled(case) and next_status_code not in {
        "reopened",
        "closed",
        current_status_code,
    }:
        raise ValueError(
            "Case is submitted, closed, or locked. Reopen it before changing status."
        )
    if case_is_controlled(case) and next_status_code == "reopened" and not (
        payload.change_reason or ""
    ).strip():
        raise ValueError("Change reason is required to reopen a controlled case.")

    actor_id = audit_user_id(db)
    old_status = case.workflow_status
    old_locked = case.is_locked
    case.workflow_status = next_status_code
    apply_case_lock_state(case, next_status_code, actor_id)
    case.updated_by = actor_id
    case.version += 1
    log_audit(
        db,
        entity_type="Case",
        entity_id=case.id,
        action="status_change",
        case_id=case.id,
        field_name="workflow_status",
        old_value=old_status,
        new_value=case.workflow_status,
        change_reason=payload.change_reason,
        user_id=actor_id,
    )
    if old_locked != case.is_locked:
        log_audit(
            db,
            entity_type="Case",
            entity_id=case.id,
            action="lock" if case.is_locked else "unlock",
            case_id=case.id,
            field_name="is_locked",
            old_value=old_locked,
            new_value=case.is_locked,
            change_reason=payload.change_reason,
            user_id=actor_id,
        )
    db.commit()
    db.refresh(case)
    return case


def delete_case(
    db: Session,
    case: Case,
    *,
    deleted_by_user_id: str | None = None,
    delete_reason: str | None = None,
) -> Case:
    if case_is_controlled(case) and not (delete_reason or "").strip():
        raise ValueError("Delete reason is required for submitted, closed, or locked cases.")
    case.is_deleted = True
    case.is_active = False
    case.deleted_at = utcnow()
    case.deleted_by = deleted_by_user_id
    case.delete_reason = delete_reason
    case.version += 1
    if case.safety_report:
        case.safety_report.case_id = None
    log_delete_event(
        db,
        entity_type="Case",
        entity_id=case.id,
        case_id=case.id,
        user_id=deleted_by_user_id,
        delete_reason=delete_reason,
    )
    db.commit()
    db.refresh(case)
    return case


def add_patient(db: Session, case: Case, payload: schemas.PatientCreate) -> Patient:
    assert_case_editable(case)
    data = clean_form_data(model_data(payload))
    patient = Patient(case_id=case.id, **data)
    db.add(patient)
    db.flush()
    log_create_event(
        db,
        entity_type="Patient",
        entity_id=patient.id,
        case_id=case.id,
        data=data,
    )
    db.commit()
    db.refresh(patient)
    return patient


def add_case_product(db: Session, case: Case, payload: schemas.CaseProductCreate) -> CaseProduct:
    assert_case_editable(case)
    data = clean_form_data(model_data(payload))
    product = None
    if data.get("product_id"):
        product = get_product(db, data["product_id"])
    if product:
        data["reported_product_name"] = data.get("reported_product_name") or product.product_name
        data["route"] = data.get("route") or product.route
        if not data.get("active_substance_text") and product.substance_links:
            data["active_substance_text"] = ", ".join(
                link.substance.substance_name
                for link in product.substance_links
                if link.substance
            )
    case_product = CaseProduct(case_id=case.id, **data)
    db.add(case_product)
    db.flush()
    log_create_event(
        db,
        entity_type="CaseProduct",
        entity_id=case_product.id,
        case_id=case.id,
        data=data,
    )
    db.commit()
    db.refresh(case_product)
    return case_product


def add_reaction(db: Session, case: Case, payload: schemas.ReactionCreate) -> Reaction:
    assert_case_editable(case)
    data = clean_form_data(model_data(payload))
    reaction = Reaction(case_id=case.id, **data)
    db.add(reaction)
    if reaction.is_serious:
        case.seriousness = "serious"
    db.flush()
    log_create_event(
        db,
        entity_type="Reaction",
        entity_id=reaction.id,
        case_id=case.id,
        data=data,
    )
    db.commit()
    db.refresh(reaction)
    return reaction


def list_case_causality_assessments(
    db: Session,
    case: Case,
) -> list[CaseProductReactionAssessment]:
    product_ids = [row.id for row in case.case_products]
    reaction_ids = [row.id for row in case.reactions]
    if not product_ids or not reaction_ids:
        return []
    return (
        db.query(CaseProductReactionAssessment)
        .options(
            joinedload(CaseProductReactionAssessment.case_product).joinedload(CaseProduct.product),
            joinedload(CaseProductReactionAssessment.reaction),
            joinedload(CaseProductReactionAssessment.creator),
        )
        .filter(
            CaseProductReactionAssessment.case_product_id.in_(product_ids),
            CaseProductReactionAssessment.reaction_id.in_(reaction_ids),
            CaseProductReactionAssessment.is_deleted.is_(False),
        )
        .order_by(
            CaseProductReactionAssessment.assessment_date.desc(),
            CaseProductReactionAssessment.created_at.desc(),
        )
        .all()
    )


def add_causality_assessment(
    db: Session,
    case: Case,
    payload: schemas.CausalityAssessmentCreate,
) -> CaseProductReactionAssessment:
    assert_case_editable(case)
    data = clean_form_data(model_data(payload))
    answers = data.pop("naranjo_answers", {}) or {}
    method = (data.get("causality_method") or "").strip().lower()
    result = (data.get("relatedness") or "").strip().lower()
    assessor_role = (data.get("assessor_role") or "").strip().lower()
    assessor_name = (data.get("assessor_name") or "").strip()

    case_product = (
        db.query(CaseProduct)
        .filter(
            CaseProduct.id == data.get("case_product_id"),
            CaseProduct.case_id == case.id,
            CaseProduct.is_deleted.is_(False),
        )
        .first()
    )
    if not case_product:
        raise ValueError("Selected product does not belong to this case.")
    reaction = (
        db.query(Reaction)
        .filter(
            Reaction.id == data.get("reaction_id"),
            Reaction.case_id == case.id,
            Reaction.is_deleted.is_(False),
        )
        .first()
    )
    if not reaction:
        raise ValueError("Selected reaction does not belong to this case.")
    if assessor_role not in ASSESSOR_ROLE_VALUES:
        raise ValueError("Select who performed the assessment.")
    if not assessor_name:
        raise ValueError("Enter the assessor name.")

    naranjo_score = None
    naranjo_answers_json = None
    if method == "who_umc":
        if result not in WHO_UMC_VALUES:
            raise ValueError("Select a valid WHO-UMC category.")
    elif method == "naranjo":
        if set(answers.values()) - NARANJO_ANSWER_VALUES:
            raise ValueError("Naranjo answers must be yes, no, or unknown.")
        naranjo_score = calculate_naranjo_score(answers)
        expected_result = classify_naranjo_score(naranjo_score)
        if result and result != expected_result:
            raise ValueError("Naranjo result does not match the calculated score.")
        result = expected_result
        naranjo_answers_json = json.dumps(answers, ensure_ascii=False, sort_keys=True)
    else:
        raise ValueError("Assessment method must be WHO-UMC or Naranjo.")

    actor_id = audit_user_id(db)
    assessment = CaseProductReactionAssessment(
        case_product_id=case_product.id,
        reaction_id=reaction.id,
        causality_method=method,
        relatedness=result,
        causality_company=result if assessor_role == "ars" else None,
        naranjo_score=naranjo_score,
        naranjo_answers_json=naranjo_answers_json,
        assessor_role=assessor_role,
        assessor_name=assessor_name,
        assessor_organization=data.get("assessor_organization"),
        assessment_date=data.get("assessment_date") or date.today(),
        assessment_comment=data.get("assessment_comment"),
        created_by_user_id=actor_id,
    )
    db.add(assessment)
    db.flush()
    log_create_event(
        db,
        entity_type="CaseProductReactionAssessment",
        entity_id=assessment.id,
        case_id=case.id,
        data={
            "case_product_id": case_product.id,
            "reaction_id": reaction.id,
            "causality_method": method,
            "relatedness": result,
            "naranjo_score": naranjo_score,
            "assessor_role": assessor_role,
            "assessor_name": assessor_name,
            "assessor_organization": assessment.assessor_organization,
            "assessment_date": assessment.assessment_date,
        },
        user_id=actor_id,
    )
    db.commit()
    db.refresh(assessment)
    return assessment


def add_followup(db: Session, case: Case, payload: schemas.FollowUpCreate) -> FollowUp:
    assert_case_editable(case)
    data = clean_form_data(model_data(payload))
    if not data.get("follow_up_number"):
        current_count = db.query(FollowUp).filter(FollowUp.case_id == case.id).count()
        data["follow_up_number"] = current_count + 1
    if data.get("received_date"):
        case.latest_received_date = data["received_date"]
    case.case_version += 1
    data["case_version_after_follow_up"] = data.get("case_version_after_follow_up") or case.case_version
    followup = FollowUp(case_id=case.id, **data)
    db.add(followup)
    db.flush()
    log_create_event(
        db,
        entity_type="FollowUp",
        entity_id=followup.id,
        case_id=case.id,
        data=data,
    )
    db.commit()
    db.refresh(followup)
    return followup


def get_followup(db: Session, followup_id: str) -> FollowUp | None:
    return (
        db.query(FollowUp)
        .filter(FollowUp.id == followup_id, FollowUp.is_deleted.is_(False))
        .first()
    )


def update_followup_direction(
    db: Session,
    followup: FollowUp,
    payload: schemas.MessageDirectionUpdate,
) -> FollowUp:
    old_direction = followup.message_direction
    followup.message_direction = payload.message_direction
    followup.version += 1
    log_audit(
        db,
        entity_type="FollowUp",
        entity_id=followup.id,
        case_id=followup.case_id,
        action="update",
        field_name="message_direction",
        old_value=old_direction,
        new_value=followup.message_direction,
        user_id=audit_user_id(db),
    )
    db.commit()
    db.refresh(followup)
    return followup


def list_attachments(db: Session) -> list[Attachment]:
    return (
        db.query(Attachment)
        .options(
            joinedload(Attachment.case).joinedload(Case.partner),
            joinedload(Attachment.case).selectinload(Case.case_products).joinedload(CaseProduct.product),
            joinedload(Attachment.safety_report).joinedload(SafetyReport.partner),
            joinedload(Attachment.partner),
            joinedload(Attachment.product),
            joinedload(Attachment.uploaded_by),
            selectinload(Attachment.versions).joinedload(DocumentVersion.creator),
            selectinload(Attachment.psmf_links).joinedload(PSMFDocumentLink.document_version),
            selectinload(Attachment.build_inclusions).joinedload(PSMFBuildDocument.build),
        )
        .filter(Attachment.is_deleted.is_(False))
        .order_by(Attachment.uploaded_at.desc(), Attachment.created_at.desc())
        .all()
    )


def get_attachment(db: Session, attachment_id: str) -> Attachment | None:
    return (
        db.query(Attachment)
        .options(
            joinedload(Attachment.case).joinedload(Case.partner),
            joinedload(Attachment.case).selectinload(Case.case_products).joinedload(CaseProduct.product),
            joinedload(Attachment.safety_report).joinedload(SafetyReport.partner),
            joinedload(Attachment.partner),
            joinedload(Attachment.product),
            joinedload(Attachment.uploaded_by),
            selectinload(Attachment.versions).joinedload(DocumentVersion.creator),
            selectinload(Attachment.psmf_links).joinedload(PSMFDocumentLink.document_version),
            selectinload(Attachment.build_inclusions).joinedload(PSMFBuildDocument.build),
        )
        .filter(Attachment.id == attachment_id, Attachment.is_deleted.is_(False))
        .first()
    )


def create_attachment(
    db: Session,
    *,
    file_name: str,
    attachment_type: str | None = None,
    case_id: str | None = None,
    safety_report_id: str | None = None,
    document_title: str | None = None,
    document_type: str | None = None,
    related_object_type: str | None = None,
    related_object_id: str | None = None,
    partner_id: str | None = None,
    product_id: str | None = None,
    mime_type: str | None = None,
    file_size_bytes: int | None = None,
    storage_path: str | None = None,
    file_url: str | None = None,
    document_version: str | None = None,
    document_date: date | None = None,
    valid_until: date | None = None,
    status: str | None = None,
    is_signed: bool = False,
    page_count: int | None = None,
    scope: str = "GLOBAL",
    psmf_annex_code: str | None = None,
    include_in_psmf: bool = False,
    inclusion_order: int = 0,
    comment: str | None = None,
    checksum_sha256: str | None = None,
    uploaded_by_user_id: str | None = None,
    commit: bool = True,
) -> Attachment:
    actor_id = uploaded_by_user_id or audit_user_id(db)
    attachment = Attachment(
        file_name=file_name,
        attachment_type=attachment_type,
        case_id=case_id,
        safety_report_id=safety_report_id,
        document_title=document_title or file_name,
        document_type=document_type or attachment_type,
        related_object_type=related_object_type,
        related_object_id=related_object_id,
        partner_id=partner_id,
        product_id=product_id,
        mime_type=mime_type,
        file_size_bytes=file_size_bytes,
        storage_path=storage_path,
        file_url=file_url,
        document_version=document_version,
        document_date=document_date,
        valid_until=valid_until,
        status=status or "draft",
        is_signed=is_signed,
        page_count=page_count,
        scope=scope,
        psmf_annex_code=psmf_annex_code,
        include_in_psmf=include_in_psmf,
        inclusion_order=inclusion_order,
        comment=comment,
        checksum_sha256=checksum_sha256,
        uploaded_by_user_id=uploaded_by_user_id,
        uploaded_at=utcnow(),
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(attachment)
    db.flush()
    log_create_event(
        db,
        entity_type="Attachment",
        entity_id=attachment.id,
        case_id=case_id,
        data={
            "file_name": file_name,
            "attachment_type": attachment_type,
            "case_id": case_id,
            "safety_report_id": safety_report_id,
            "document_title": document_title or file_name,
            "document_type": document_type or attachment_type,
            "related_object_type": related_object_type,
            "related_object_id": related_object_id,
            "partner_id": partner_id,
            "product_id": product_id,
            "mime_type": mime_type,
            "file_size_bytes": file_size_bytes,
            "storage_path": storage_path,
            "file_url": file_url,
            "document_version": document_version,
            "document_date": document_date,
            "valid_until": valid_until,
            "status": status or "draft",
            "is_signed": is_signed,
            "page_count": page_count,
            "scope": scope,
            "psmf_annex_code": psmf_annex_code,
            "include_in_psmf": include_in_psmf,
            "inclusion_order": inclusion_order,
            "comment": comment,
            "checksum_sha256": checksum_sha256,
        },
        user_id=actor_id,
    )
    version = create_document_version_snapshot(
        db,
        attachment,
        actor_id=actor_id,
        change_reason=comment,
    )
    if psmf_annex_code:
        upsert_psmf_document_link(
            db,
            attachment,
            document_version=version,
            annex_code=psmf_annex_code,
            partner_id=partner_id,
            scope=scope,
            is_included=include_in_psmf,
            inclusion_order=inclusion_order,
        )
    if commit:
        db.commit()
    else:
        db.flush()
    if commit:
        db.refresh(attachment)
    return attachment


def list_document_versions(db: Session, attachment_id: str) -> list[DocumentVersion]:
    return (
        db.query(DocumentVersion)
        .options(joinedload(DocumentVersion.creator))
        .filter(
            DocumentVersion.attachment_id == attachment_id,
            DocumentVersion.is_deleted.is_(False),
        )
        .order_by(DocumentVersion.created_at.desc())
        .all()
    )


def create_new_document_version(
    db: Session,
    attachment: Attachment,
    *,
    file_name: str,
    mime_type: str | None,
    file_size_bytes: int | None,
    storage_path: str | None,
    checksum_sha256: str | None,
    version_label: str | None,
    document_date: date | None,
    valid_until: date | None,
    status: str | None,
    is_signed: bool,
    page_count: int | None,
    change_reason: str | None,
    actor_id: str | None,
) -> DocumentVersion:
    """Append an immutable file version while keeping the logical registry record."""

    attachment.file_name = file_name
    attachment.mime_type = mime_type
    attachment.file_size_bytes = file_size_bytes
    attachment.storage_path = storage_path
    attachment.checksum_sha256 = checksum_sha256
    attachment.document_version = (version_label or "").strip() or f"v{attachment.version + 1}"
    attachment.document_date = document_date
    attachment.valid_until = valid_until
    attachment.status = status or attachment.status or "draft"
    attachment.is_signed = is_signed
    attachment.page_count = page_count
    attachment.comment = change_reason
    attachment.uploaded_at = utcnow()
    attachment.uploaded_by_user_id = actor_id
    attachment.updated_by = actor_id or audit_user_id(db)
    attachment.version += 1
    version = create_document_version_snapshot(
        db,
        attachment,
        actor_id=attachment.updated_by,
        change_reason=change_reason,
    )
    attachment.document_version = version.version_label
    for link in attachment.psmf_links:
        if not link.is_deleted:
            link.document_version_id = version.id
            link.version += 1
    log_audit(
        db,
        entity_type="Attachment",
        entity_id=attachment.id,
        action="new_version",
        field_name="document_version",
        new_value=version.version_label,
        user_id=attachment.updated_by,
        source_module="Documents",
        comment=change_reason,
    )
    db.commit()
    db.refresh(version)
    return version


def upsert_psmf_document_link(
    db: Session,
    attachment: Attachment,
    *,
    document_version: DocumentVersion,
    annex_code: str,
    partner_id: str | None,
    scope: str,
    is_included: bool,
    inclusion_order: int,
) -> PSMFDocumentLink:
    normalized_annex = annex_code.strip()
    link = (
        db.query(PSMFDocumentLink)
        .filter(
            PSMFDocumentLink.attachment_id == attachment.id,
            PSMFDocumentLink.annex_code == normalized_annex,
            PSMFDocumentLink.partner_id == partner_id,
            PSMFDocumentLink.is_deleted.is_(False),
        )
        .first()
    )
    if not link:
        link = PSMFDocumentLink(
            attachment_id=attachment.id,
            annex_code=normalized_annex,
            partner_id=partner_id,
        )
        db.add(link)
    link.document_version_id = document_version.id
    link.scope = scope
    link.is_included = is_included
    link.inclusion_order = inclusion_order
    db.flush()
    return link


def set_attachment_psmf_inclusion(
    db: Session,
    attachment: Attachment,
    *,
    is_included: bool,
    actor_id: str | None = None,
) -> Attachment:
    old_value = attachment.include_in_psmf
    attachment.include_in_psmf = is_included
    attachment.updated_by = actor_id or audit_user_id(db)
    attachment.version += 1
    for link in attachment.psmf_links:
        if not link.is_deleted:
            link.is_included = is_included
            link.version += 1
    log_audit(
        db,
        entity_type="Attachment",
        entity_id=attachment.id,
        action="include_in_psmf" if is_included else "exclude_from_psmf",
        field_name="include_in_psmf",
        old_value=str(old_value),
        new_value=str(is_included),
        user_id=attachment.updated_by,
        source_module="Documents",
    )
    db.commit()
    db.refresh(attachment)
    return attachment


def delete_attachment(
    db: Session,
    attachment: Attachment,
    *,
    deleted_by_user_id: str | None = None,
    delete_reason: str | None = None,
) -> Attachment:
    attachment.is_deleted = True
    attachment.is_active = False
    attachment.deleted_at = utcnow()
    attachment.deleted_by = deleted_by_user_id
    attachment.delete_reason = delete_reason
    attachment.version += 1
    log_delete_event(
        db,
        entity_type="Attachment",
        entity_id=attachment.id,
        case_id=attachment.case_id,
        user_id=deleted_by_user_id,
        delete_reason=delete_reason,
    )
    db.commit()
    db.refresh(attachment)
    return attachment


def list_audit_entries(db: Session) -> list[AuditTrail]:
    return (
        db.query(AuditTrail)
        .options(joinedload(AuditTrail.user), joinedload(AuditTrail.case))
        .filter(AuditTrail.is_deleted.is_(False))
        .order_by(func.coalesce(AuditTrail.changed_at, AuditTrail.timestamp).desc())
        .all()
    )


def validate_submission_references(data: dict[str, Any]) -> None:
    refs = [data.get("case_id"), data.get("pbrer_id"), data.get("rmp_id")]
    if sum(1 for ref in refs if ref) != 1:
        raise ValueError("Exactly one of case_id, pbrer_id, or rmp_id must be provided.")


def list_submissions(db: Session) -> list[Submission]:
    return (
        db.query(Submission)
        .options(joinedload(Submission.case), joinedload(Submission.recipient_partner))
        .filter(Submission.is_deleted.is_(False))
        .order_by(Submission.created_at.desc())
        .all()
    )


def get_submission(db: Session, submission_id: str) -> Submission | None:
    return db.query(Submission).filter(
        Submission.id == submission_id, Submission.is_deleted.is_(False)
    ).first()


def create_submission(db: Session, payload: schemas.SubmissionCreate) -> Submission:
    data = clean_form_data(model_data(payload))
    expected_object_version = data.pop("expected_object_version", None)
    validate_submission_references(data)
    if data.get("case_id"):
        if data.get("submission_object_type", "case") != "case":
            raise ValueError("Submission object type does not match the selected case.")
        case = get_case(db, data["case_id"])
        if not case or case.is_deleted:
            raise ValueError("Selected case was not found.")
        if expected_object_version is not None:
            require_version(case, expected_object_version)
        if case.number_is_provisional or not case.numbering_partner_id:
            raise ValueError(
                "Нельзя сформировать регуляторную отправку для случая с номером UNASSIGNED. "
                "Сначала выберите партнёра нумерации."
            )
        assert_case_editable(case)
    elif data.get("pbrer_id"):
        if data.get("submission_object_type", "case") != "pbrer":
            raise ValueError("Submission object type does not match the selected PSUR.")
        plan = db.get(PSURPlan, data["pbrer_id"])
        if not plan or plan.is_deleted:
            raise ValueError("Selected PSUR was not found.")
        if expected_object_version is not None:
            require_version(plan, expected_object_version)
        # Reserve the parent version in the same transaction as the submission.
        # This catches a concurrent status change or deletion at flush.
        plan.version += 1
    elif expected_object_version is not None:
        raise ValueError("Versioned submissions support cases and PSUR plans only.")
    data["submission_number"] = data.get("submission_number") or generate_number(
        db,
        Submission,
        "submission_number",
        "SUB",
    )
    if data.get("submission_status") in {"submitted", "acknowledged"} and not data.get("submitted_at"):
        data["submitted_at"] = utcnow()
    if data.get("submission_status") == "acknowledged" and not data.get("acknowledged_at"):
        data["acknowledged_at"] = utcnow()
    submission = Submission(**data)
    db.add(submission)
    db.flush()
    log_create_event(
        db,
        entity_type="Submission",
        entity_id=submission.id,
        case_id=submission.case_id,
        data=data,
    )
    db.commit()
    db.refresh(submission)
    return submission


def create_submission_for_case(
    db: Session,
    case: Case,
    payload: schemas.SubmissionCreate,
) -> Submission:
    data = model_data(payload)
    data["case_id"] = case.id
    data["submission_object_type"] = "case"
    return create_submission(db, type(payload)(**data))


def update_submission_status(
    db: Session,
    submission: Submission,
    payload: schemas.SubmissionStatusUpdate,
) -> Submission:
    require_version(submission, payload.expected_version)
    old_values = {
        "submission_status": submission.submission_status,
        "submission_number": submission.submission_number,
        "submitted_at": submission.submitted_at,
        "acknowledged_at": submission.acknowledged_at,
        "acknowledgement_code": submission.acknowledgement_code,
        "error_message": submission.error_message,
    }
    requested_number = (payload.submission_number or "").strip()
    if requested_number and requested_number != submission.submission_number:
        duplicate = (
            db.query(Submission)
            .filter(
                Submission.submission_number == requested_number,
                Submission.id != submission.id,
                Submission.is_deleted.is_(False),
            )
            .first()
        )
        if duplicate:
            raise ValueError("Submission number already exists.")
        submission.submission_number = requested_number
    submission.submission_status = payload.submission_status
    submission.error_message = payload.error_message
    if payload.submitted_at is not None:
        submission.submitted_at = payload.submitted_at
    if payload.acknowledged_at is not None:
        submission.acknowledged_at = payload.acknowledged_at
    if payload.acknowledgement_code is not None:
        submission.acknowledgement_code = payload.acknowledgement_code.strip() or None
    if submission.submission_status in {"submitted", "acknowledged"} and not submission.submitted_at:
        submission.submitted_at = utcnow()
    if submission.submission_status == "acknowledged" and not submission.acknowledged_at:
        submission.acknowledged_at = utcnow()
    submission.version += 1
    new_values = {
        "submission_status": submission.submission_status,
        "submission_number": submission.submission_number,
        "submitted_at": submission.submitted_at,
        "acknowledged_at": submission.acknowledged_at,
        "acknowledgement_code": submission.acknowledgement_code,
        "error_message": submission.error_message,
    }
    log_field_changes(
        db,
        entity_type="Submission",
        entity_id=submission.id,
        action="status_change" if old_values["submission_status"] != submission.submission_status else "edit",
        case_id=submission.case_id,
        old_values=old_values,
        new_values=new_values,
        comment=payload.error_message,
    )
    db.commit()
    db.refresh(submission)
    return submission


PSUR_DEFAULT_SECTIONS = [
    ("1", "General product information"),
    ("2", "Reporting period and data lock point"),
    ("3", "Products covered by the report"),
    ("4", "Summary of ICSR cases"),
    ("5", "Serious cases"),
    ("6", "Non-serious cases"),
    ("7", "New safety information"),
    ("8", "Signals"),
    ("9", "Regulatory actions"),
    ("10", "Product information changes"),
    ("11", "Benefit-risk conclusion"),
    ("12", "Appendices"),
]

PSUR_ENTITY_TYPES = {
    "PSURPlan",
    "PSURProduct",
    "PSURCase",
    "PSURPartnerRequest",
    "PSURSection",
    "PSURDocument",
    "Task",
}


def list_psur_plans(db: Session) -> list[PSURPlan]:
    return (
        db.query(PSURPlan)
        .options(
            joinedload(PSURPlan.active_substance),
            joinedload(PSURPlan.product).joinedload(Product.dosage_form_ref),
            joinedload(PSURPlan.responsible_user),
            joinedload(PSURPlan.reviewer_user),
            selectinload(PSURPlan.psur_products)
            .joinedload(PSURProduct.product)
            .joinedload(Product.dosage_form_ref),
            selectinload(PSURPlan.psur_cases).joinedload(PSURCase.case),
            selectinload(PSURPlan.partner_requests).joinedload(PSURPartnerRequest.partner),
            selectinload(PSURPlan.sections),
            selectinload(PSURPlan.documents),
        )
        .filter(PSURPlan.is_deleted.is_(False))
        .order_by(PSURPlan.reporting_period_end.desc(), PSURPlan.created_at.desc())
        .all()
    )


def get_psur_plan(db: Session, psur_plan_id: str) -> PSURPlan | None:
    return (
        db.query(PSURPlan)
        .options(
            joinedload(PSURPlan.active_substance),
            joinedload(PSURPlan.product).joinedload(Product.dosage_form_ref),
            joinedload(PSURPlan.responsible_user),
            joinedload(PSURPlan.reviewer_user),
            selectinload(PSURPlan.psur_products)
            .joinedload(PSURProduct.product)
            .joinedload(Product.dosage_form_ref),
            selectinload(PSURPlan.psur_cases).joinedload(PSURCase.case).joinedload(Case.partner),
            selectinload(PSURPlan.psur_cases)
            .joinedload(PSURCase.case)
            .selectinload(Case.case_products)
            .joinedload(CaseProduct.product)
            .joinedload(Product.dosage_form_ref),
            selectinload(PSURPlan.psur_cases)
            .joinedload(PSURCase.case)
            .selectinload(Case.reactions),
            selectinload(PSURPlan.partner_requests).joinedload(PSURPartnerRequest.partner),
            selectinload(PSURPlan.partner_requests).joinedload(PSURPartnerRequest.contact_person),
            selectinload(PSURPlan.partner_requests).joinedload(PSURPartnerRequest.document),
            selectinload(PSURPlan.sections).joinedload(PSURSection.assignee),
            selectinload(PSURPlan.sections).joinedload(PSURSection.reviewer),
            selectinload(PSURPlan.sections).joinedload(PSURSection.confirmer),
            selectinload(PSURPlan.documents).joinedload(PSURDocument.uploader),
        )
        .filter(PSURPlan.id == psur_plan_id, PSURPlan.is_deleted.is_(False))
        .first()
    )


def create_psur_default_sections(db: Session, psur_plan: PSURPlan) -> None:
    existing_codes = {
        section.section_code
        for section in psur_plan.sections
        if not section.is_deleted
    }
    for section_code, section_title in PSUR_DEFAULT_SECTIONS:
        if section_code in existing_codes:
            continue
        db.add(
            PSURSection(
                psur_plan_id=psur_plan.id,
                section_code=section_code,
                section_title=section_title,
            )
        )


def create_psur_plan(
    db: Session,
    payload: schemas.PSURPlanCreate,
    *,
    created_by_user_id: str | None = None,
) -> PSURPlan:
    data = clean_form_data(model_data(payload))
    data["created_by"] = created_by_user_id or audit_user_id(db)
    plan = PSURPlan(**data)
    db.add(plan)
    db.flush()

    if plan.product_id:
        product = get_product(db, plan.product_id)
        db.add(
            PSURProduct(
                psur_plan_id=plan.id,
                product_id=plan.product_id,
                country=product.authorization_country_code if product else None,
                marketing_authorisation_number=(
                    product.authorization_number if product else None
                ),
                included_in_report=True,
            )
        )

    create_psur_default_sections(db, plan)
    log_create_event(
        db,
        entity_type="PSURPlan",
        entity_id=plan.id,
        data=data,
        user_id=data["created_by"],
    )
    db.commit()
    db.refresh(plan)
    return plan


def update_psur_plan(
    db: Session,
    plan: PSURPlan,
    payload: schemas.PSURPlanUpdate,
    *,
    changed_by_user_id: str | None = None,
) -> PSURPlan:
    data = clean_form_data(model_data(payload))
    old_values = {field: getattr(plan, field, None) for field in data}
    for field, value in data.items():
        setattr(plan, field, value)
    plan.version += 1
    log_field_changes(
        db,
        entity_type="PSURPlan",
        entity_id=plan.id,
        old_values=old_values,
        new_values=data,
        user_id=changed_by_user_id,
    )
    db.commit()
    db.refresh(plan)
    return plan


def update_psur_status(
    db: Session,
    plan: PSURPlan,
    payload: schemas.PSURStatusUpdate,
    *,
    changed_by_user_id: str | None = None,
) -> PSURPlan:
    old_status = plan.status
    plan.status = payload.status
    plan.version += 1
    log_audit(
        db,
        entity_type="PSURPlan",
        entity_id=plan.id,
        action="status_change",
        field_name="status",
        old_value=old_status,
        new_value=plan.status,
        change_reason=payload.change_reason,
        user_id=changed_by_user_id or audit_user_id(db),
    )
    db.commit()
    db.refresh(plan)
    return plan


def delete_psur_plan(
    db: Session,
    plan: PSURPlan,
    *,
    deleted_by_user_id: str | None = None,
    delete_reason: str | None = None,
) -> PSURPlan:
    plan.is_deleted = True
    plan.is_active = False
    plan.deleted_at = utcnow()
    plan.deleted_by = deleted_by_user_id
    plan.delete_reason = delete_reason
    plan.version += 1
    log_delete_event(
        db,
        entity_type="PSURPlan",
        entity_id=plan.id,
        user_id=deleted_by_user_id,
        delete_reason=delete_reason,
    )
    db.commit()
    db.refresh(plan)
    return plan


def touch_psur_plan(db: Session, plan_id: str) -> PSURPlan:
    """Keep child writes in conflict with changes or deletion of their parent."""
    plan = db.get(PSURPlan, plan_id)
    if not plan or plan.is_deleted:
        raise ValueError("PSUR plan not found.")
    plan.version += 1
    return plan


def add_psur_product(
    db: Session,
    plan: PSURPlan,
    payload: schemas.PSURProductCreate,
) -> PSURProduct:
    touch_psur_plan(db, plan.id)
    data = clean_form_data(model_data(payload))
    product = PSURProduct(psur_plan_id=plan.id, **data)
    db.add(product)
    db.flush()
    log_create_event(
        db,
        entity_type="PSURProduct",
        entity_id=product.id,
        data=data,
        comment=f"PSUR plan {plan.id}",
    )
    db.commit()
    db.refresh(product)
    return product


def add_psur_document(
    db: Session,
    plan: PSURPlan,
    payload: schemas.PSURDocumentCreate,
    *,
    uploaded_by_user_id: str | None = None,
) -> PSURDocument:
    touch_psur_plan(db, plan.id)
    data = clean_form_data(model_data(payload))
    document = PSURDocument(
        psur_plan_id=plan.id,
        uploaded_by=uploaded_by_user_id,
        uploaded_at=utcnow(),
        **data,
    )
    db.add(document)
    db.flush()
    log_audit(
        db,
        entity_type="PSURDocument",
        entity_id=document.id,
        action="document_upload",
        field_name="file_name",
        new_value=document.file_name,
        user_id=uploaded_by_user_id or audit_user_id(db),
        comment=document.comment,
    )
    db.commit()
    db.refresh(document)
    return document


def create_task(
    db: Session,
    payload: schemas.TaskCreate,
    *,
    created_by_user_id: str | None = None,
    commit: bool = True,
) -> Task:
    data = clean_form_data(model_data(payload))
    actor_id = created_by_user_id or audit_user_id(db)
    task = Task(**data, created_by=actor_id, updated_by=actor_id)
    db.add(task)
    db.flush()
    log_create_event(
        db,
        entity_type="Task",
        entity_id=task.id,
        data=data,
        user_id=task.created_by,
    )
    if commit:
        db.commit()
        db.refresh(task)
    else:
        db.flush()
    return task


def list_tasks(db: Session) -> list[Task]:
    return (
        db.query(Task)
        .options(joinedload(Task.assigned_to), joinedload(Task.creator), joinedload(Task.updater))
        .filter(Task.is_deleted.is_(False))
        .order_by(Task.due_date.is_(None), Task.due_date.asc(), Task.created_at.desc())
        .all()
    )


def get_task(db: Session, task_id: str) -> Task | None:
    return (
        db.query(Task)
        .options(joinedload(Task.assigned_to), joinedload(Task.creator), joinedload(Task.updater))
        .filter(Task.id == task_id, Task.is_deleted.is_(False))
        .first()
    )


def update_task_status(
    db: Session,
    task: Task,
    *,
    status: str,
    changed_by_user_id: str | None = None,
) -> Task:
    old_values = {"status": task.status}
    task.status = status
    task.updated_by = changed_by_user_id or audit_user_id(db)
    if status in {"completed", "Completed"} and not task.completed_at:
        task.completed_at = utcnow()
    elif status not in {"completed", "Completed"}:
        task.completed_at = None
    task.version += 1
    log_field_changes(
        db,
        entity_type="Task",
        entity_id=task.id,
        old_values=old_values,
        new_values={"status": task.status},
        action="status_change",
        user_id=task.updated_by,
    )
    db.commit()
    db.refresh(task)
    return task


INCOMING_CASE_LINK_TYPES = {
    "initial",
    "follow_up",
    "duplicate",
    "related",
    "created_from_message",
    "manual_link",
}
INCOMING_CASE_CREATION_LINK_TYPES = {"initial", "created_from_message"}
INCOMING_REQUIRED_CRITERIA = {
    "patient_missing": "has_identifiable_patient",
    "reporter_missing": "has_identifiable_reporter",
    "product_missing": "has_suspect_product",
    "event_missing": "has_adverse_event",
}


def incoming_request_missing_criteria(row_or_data) -> list[str]:
    missing = []
    for missing_code, field_name in INCOMING_REQUIRED_CRITERIA.items():
        value = (
            row_or_data.get(field_name)
            if isinstance(row_or_data, dict)
            else getattr(row_or_data, field_name, False)
        )
        if not value:
            missing.append(missing_code)
    return missing


def incoming_request_has_valid_criteria(row_or_data) -> bool:
    return not incoming_request_missing_criteria(row_or_data)


def incoming_request_status_from_triage(row: IncomingRequest) -> str:
    if row.triage_status == "completed":
        if row.triage_result == "create_case" and row.validity_status == "valid_icsr":
            return "triage_completed"
        if row.triage_result == "request_follow_up":
            return "awaiting_information"
        if row.triage_result in {"no_case_required", "medical_information_only", "quality_complaint_only"}:
            return "closed_no_case"
        if row.triage_result == "duplicate":
            return "duplicate"
        return "triage_completed"
    if row.triage_status == "in_progress":
        return "triage_in_progress"
    if row.triage_status == "not_required":
        return "closed_no_case"
    return row.status or "new"


def incoming_request_case_creation_error(row: IncomingRequest) -> str | None:
    existing_link = incoming_request_primary_case_link(row)
    if existing_link:
        return "Safety message is already linked to an initial case."
    if row.triage_status != "completed":
        return "Complete triage before creating a case."
    if row.potential_icsr != "yes":
        return "Safety message triage does not mark this as a potential ICSR."
    if row.validity_status != "valid_icsr":
        return "Safety message must have valid ICSR status before case creation."
    if row.triage_result != "create_case":
        return "Triage result must be create_case before case creation."
    return None


def incoming_request_primary_case_link(row: IncomingRequest) -> IncomingRequestCaseLink | None:
    for link in row.case_links:
        if link.is_deleted:
            continue
        if link.link_type in INCOMING_CASE_CREATION_LINK_TYPES:
            return link
    return None


def list_incoming_requests(db: Session) -> list[IncomingRequest]:
    return (
        db.query(IncomingRequest)
        .options(
            joinedload(IncomingRequest.partner),
            joinedload(IncomingRequest.product).joinedload(Product.dosage_form_ref),
            selectinload(IncomingRequest.product_links)
            .joinedload(IncomingRequestProduct.product)
            .joinedload(Product.dosage_form_ref),
            joinedload(IncomingRequest.case),
            selectinload(IncomingRequest.case_links).joinedload(IncomingRequestCaseLink.case),
            joinedload(IncomingRequest.creator),
            joinedload(IncomingRequest.updater),
            joinedload(IncomingRequest.triage_user),
        )
        .filter(IncomingRequest.is_deleted.is_(False))
        .order_by(IncomingRequest.created_at.desc())
        .all()
    )


def get_incoming_request(db: Session, request_id: str) -> IncomingRequest | None:
    return (
        db.query(IncomingRequest)
        .options(
            joinedload(IncomingRequest.partner),
            joinedload(IncomingRequest.product).joinedload(Product.dosage_form_ref),
            selectinload(IncomingRequest.product_links)
            .joinedload(IncomingRequestProduct.product)
            .joinedload(Product.dosage_form_ref),
            joinedload(IncomingRequest.case),
            selectinload(IncomingRequest.case_links).joinedload(IncomingRequestCaseLink.case),
            joinedload(IncomingRequest.creator),
            joinedload(IncomingRequest.updater),
            joinedload(IncomingRequest.triage_user),
        )
        .filter(IncomingRequest.id == request_id, IncomingRequest.is_deleted.is_(False))
        .first()
    )


def create_incoming_request(
    db: Session,
    payload: schemas.IncomingRequestCreate,
    *,
    created_by_user_id: str | None = None,
) -> IncomingRequest:
    data = clean_form_data(model_data(payload))
    product_ids = list(data.pop("product_ids", []) or [])
    if data.get("product_id") and data["product_id"] not in product_ids:
        product_ids.insert(0, data["product_id"])
    product_ids = validate_message_product_ids(db, product_ids)
    data["product_id"] = product_ids[0] if product_ids else None
    status_value = data.pop("status", None) or "confirmed"
    data["message_number"] = data.get("message_number") or generate_number(
        db,
        IncomingRequest,
        "message_number",
        "MSG",
    )
    data["received_date"] = data.get("received_date") or date.today()
    data["potential_icsr"] = data.get("potential_icsr") or data.get("possible_icsr") or "no"
    data["triage_status"] = data.get("triage_status") or "not_started"
    if product_ids:
        data["has_suspect_product"] = True
    actor_id = created_by_user_id or audit_user_id(db)
    row = IncomingRequest(
        **data,
        human_confirmed=True,
        status=status_value,
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(row)
    db.flush()
    sync_message_product_links(db, row, IncomingRequestProduct, product_ids)
    log_create_event(
        db,
        entity_type="IncomingRequest",
        entity_id=row.id,
        data={
            "request_type": row.request_type,
            "partner_id": row.partner_id,
            "product_id": row.product_id,
            "product_ids": ", ".join(product_ids),
            "possible_icsr": row.possible_icsr,
            "status": row.status,
            "message_number": row.message_number,
            "triage_status": row.triage_status,
            "triage_result": row.triage_result,
        },
        user_id=actor_id,
        comment="Human confirmed automated analysis draft.",
    )
    db.commit()
    db.refresh(row)
    return row


def update_incoming_request_products(
    db: Session,
    row: IncomingRequest,
    product_ids: list[str] | None,
    *,
    changed_by_user_id: str | None = None,
) -> IncomingRequest:
    old_product_ids = row.product_ids
    new_product_ids = sync_message_product_links(
        db,
        row,
        IncomingRequestProduct,
        product_ids,
    )
    row.product_id = new_product_ids[0] if new_product_ids else None
    row.has_suspect_product = bool(row.product_names)
    row.updated_by = changed_by_user_id or audit_user_id(db)
    row.version += 1
    log_audit(
        db,
        entity_type="IncomingRequest",
        entity_id=row.id,
        action="edit",
        field_name="product_ids",
        old_value=", ".join(old_product_ids),
        new_value=", ".join(new_product_ids),
        user_id=row.updated_by,
    )
    db.commit()
    db.refresh(row)
    return row


def update_incoming_request_status(
    db: Session,
    row: IncomingRequest,
    *,
    status: str,
    changed_by_user_id: str | None = None,
) -> IncomingRequest:
    old_values = {"status": row.status}
    row.status = status
    row.updated_by = changed_by_user_id or audit_user_id(db)
    row.version += 1
    log_field_changes(
        db,
        entity_type="IncomingRequest",
        entity_id=row.id,
        old_values=old_values,
        new_values={"status": row.status},
        action="status_change",
        user_id=row.updated_by,
    )
    db.commit()
    db.refresh(row)
    return row


def save_incoming_request_triage(
    db: Session,
    row: IncomingRequest,
    payload: schemas.IncomingRequestTriageUpdate,
    *,
    changed_by_user_id: str | None = None,
) -> IncomingRequest:
    data = clean_form_data(model_data(payload))
    data.pop("expected_version", None)
    manual_override = bool(data.pop("manual_override", False))
    criteria_complete = incoming_request_has_valid_criteria(data)

    if data.get("safety_relevance") == "not_safety_related":
        data["potential_icsr"] = data.get("potential_icsr") or "no"
        data["validity_status"] = data.get("validity_status") or "not_applicable"
        data["triage_result"] = data.get("triage_result") or "no_case_required"
    elif criteria_complete:
        data["potential_icsr"] = data.get("potential_icsr") or "yes"
        data["validity_status"] = data.get("validity_status") or "valid_icsr"
        data["triage_result"] = data.get("triage_result") or "create_case"
    else:
        data["potential_icsr"] = data.get("potential_icsr") or "unclear"
        data["validity_status"] = data.get("validity_status") or "pending_information"
        data["triage_result"] = data.get("triage_result") or "request_follow_up"

    if data.get("validity_status") == "valid_icsr" and not criteria_complete:
        if not manual_override:
            raise ValueError(
                "Valid ICSR requires patient, reporter, product, and adverse event criteria."
            )
        if not data.get("triage_comment"):
            raise ValueError("Manual validity override requires a triage comment.")

    data["missing_criteria"] = data.get("missing_criteria") or (
        "; ".join(incoming_request_missing_criteria(data)) or None
    )
    if data.get("triage_status") == "completed":
        data["triaged_at"] = utcnow()
    data["triaged_by"] = changed_by_user_id or audit_user_id(db)

    old_values = {field: getattr(row, field, None) for field in data}
    for field, value in data.items():
        setattr(row, field, value)
    old_status = row.status
    row.status = incoming_request_status_from_triage(row)
    row.possible_icsr = "yes" if row.potential_icsr == "yes" else "no"
    row.updated_by = data["triaged_by"]
    row.version += 1

    log_field_changes(
        db,
        entity_type="IncomingRequest",
        entity_id=row.id,
        old_values={**old_values, "status": old_status},
        new_values={**data, "status": row.status},
        action="triage",
        user_id=row.updated_by,
        comment=row.triage_comment,
    )
    db.commit()
    db.refresh(row)
    return row


def incoming_request_case_narrative(row: IncomingRequest) -> str:
    parts = [
        "Created from safety message.",
        "",
        "Source text:",
        row.source_text or "",
    ]
    assessment = []
    if row.patient_information:
        assessment.append(f"Patient: {row.patient_information}")
    if row.adverse_event:
        assessment.append(f"Adverse event: {row.adverse_event}")
    if row.missing_information:
        assessment.append(f"Missing information: {row.missing_information}")
    if row.validity_assessment:
        assessment.append(f"Validity assessment: {row.validity_assessment}")
    if assessment:
        parts.extend(["", "Triage assessment:", *assessment])
    return "\n".join(parts)


def create_incoming_request_case_link(
    db: Session,
    row: IncomingRequest,
    case: Case,
    *,
    link_type: str = "manual_link",
    comment: str | None = None,
    created_by_user_id: str | None = None,
    update_message_status: bool = True,
) -> IncomingRequestCaseLink:
    if link_type not in INCOMING_CASE_LINK_TYPES:
        raise ValueError("Invalid safety message case link type.")

    existing = (
        db.query(IncomingRequestCaseLink)
        .filter(
            IncomingRequestCaseLink.incoming_request_id == row.id,
            IncomingRequestCaseLink.case_id == case.id,
            IncomingRequestCaseLink.link_type == link_type,
            IncomingRequestCaseLink.is_deleted.is_(False),
        )
        .first()
    )
    if existing:
        return existing

    assert_case_editable(case)
    case.version += 1
    actor_id = created_by_user_id or audit_user_id(db)
    link = IncomingRequestCaseLink(
        incoming_request_id=row.id,
        case_id=case.id,
        link_type=link_type,
        comment=comment,
        created_by=actor_id,
    )
    db.add(link)
    db.flush()

    old_status = row.status
    if not row.case_id or link_type in INCOMING_CASE_CREATION_LINK_TYPES:
        row.case_id = case.id
    if update_message_status:
        if link_type in {"created_from_message", "initial", "manual_link"}:
            row.status = "converted_to_case"
        elif link_type == "duplicate":
            row.status = "duplicate"
        else:
            row.status = "linked_to_case"
    row.updated_by = actor_id
    row.version += 1

    if link_type == "follow_up":
        current_count = db.query(FollowUp).filter(FollowUp.case_id == case.id).count()
        case.case_version += 1
        if row.received_date:
            case.latest_received_date = row.received_date
        db.add(
            FollowUp(
                case_id=case.id,
                follow_up_number=current_count + 1,
                received_date=row.received_date,
                source_type="safety_message",
                message_direction="needs_review",
                description=incoming_request_case_narrative(row),
                significant_new_information=True,
                case_version_after_follow_up=case.case_version,
            )
        )

    log_create_event(
        db,
        entity_type="IncomingRequestCaseLink",
        entity_id=link.id,
        case_id=case.id,
        data={
            "incoming_request_id": row.id,
            "case_id": case.id,
            "link_type": link.link_type,
        },
        user_id=actor_id,
        comment=comment or "Safety message linked to case.",
    )
    if old_status != row.status:
        log_audit(
            db,
            entity_type="IncomingRequest",
            entity_id=row.id,
            action="status_change",
            field_name="status",
            old_value=old_status,
            new_value=row.status,
            case_id=case.id,
            user_id=actor_id,
            comment=comment or f"Linked to case as {link_type}.",
        )
    return link


def link_incoming_request_to_case(
    db: Session,
    row: IncomingRequest,
    case: Case,
    payload: schemas.IncomingRequestCaseLinkCreate,
    *,
    changed_by_user_id: str | None = None,
) -> IncomingRequestCaseLink:
    link = create_incoming_request_case_link(
        db,
        row,
        case,
        link_type=payload.link_type,
        comment=payload.comment,
        created_by_user_id=changed_by_user_id,
    )
    db.commit()
    db.refresh(link)
    return link


def list_incoming_request_case_links(db: Session, request_id: str) -> list[IncomingRequestCaseLink]:
    return (
        db.query(IncomingRequestCaseLink)
        .options(joinedload(IncomingRequestCaseLink.case))
        .filter(
            IncomingRequestCaseLink.incoming_request_id == request_id,
            IncomingRequestCaseLink.is_deleted.is_(False),
        )
        .order_by(IncomingRequestCaseLink.created_at.desc())
        .all()
    )


def list_case_incoming_request_links(db: Session, case_id: str) -> list[IncomingRequestCaseLink]:
    return (
        db.query(IncomingRequestCaseLink)
        .options(joinedload(IncomingRequestCaseLink.incoming_request))
        .filter(
            IncomingRequestCaseLink.case_id == case_id,
            IncomingRequestCaseLink.is_deleted.is_(False),
        )
        .order_by(IncomingRequestCaseLink.created_at.desc())
        .all()
    )


def create_case_from_incoming_request(
    db: Session,
    row: IncomingRequest,
    case_data: dict[str, Any] | None = None,
) -> Case:
    primary_link = incoming_request_primary_case_link(row)
    if primary_link:
        linked_case = get_case(db, primary_link.case_id)
        if linked_case:
            return linked_case
    if row.case_id:
        linked_case = get_case(db, row.case_id)
        if linked_case:
            create_incoming_request_case_link(
                db,
                row,
                linked_case,
                link_type="created_from_message",
                comment="Legacy direct safety message case link normalized.",
                update_message_status=False,
            )
            db.commit()
            return linked_case

    error = incoming_request_case_creation_error(row)
    if error:
        raise ValueError(error)

    actor_id = audit_user_id(db)
    case_data = clean_form_data(case_data or {})
    received_date = row.received_date or (row.created_at.date() if row.created_at else date.today())
    seriousness = "serious" if normalized_status(row.seriousness) == "serious" else "non-serious"
    case = Case(
        case_number="",
        partner_id=row.partner_id,
        case_type=case_data.get("case_type") or "spontaneous",
        report_type=case_data.get("report_type") or row.request_type or "safety_message",
        initial_received_date=case_data.get("initial_received_date") or received_date,
        latest_received_date=case_data.get("latest_received_date") or received_date,
        seriousness=case_data.get("seriousness") or seriousness,
        narrative=case_data.get("narrative") or incoming_request_case_narrative(row),
        workflow_status=case_data.get("workflow_status") or "data_entry",
        created_by=actor_id,
        updated_by=actor_id,
    )
    assign_case_number(
        db,
        case,
        partner_id=row.partner_id,
        supplied_number=case_data.get("case_number"),
    )
    db.add(case)
    db.flush()

    active_product_links = [link for link in row.product_links if not link.is_deleted]
    active_product_links.sort(
        key=lambda link: (
            link.product_id != row.product_id,
            link.created_at,
            link.id,
        )
    )
    if active_product_links:
        for index, link in enumerate(active_product_links):
            db.add(
                CaseProduct(
                    case_id=case.id,
                    product_id=link.product_id,
                    reported_product_name=link.reported_product_name
                    or (link.product.product_name if link.product else None),
                    active_substance_text=row.active_substance if index == 0 else None,
                    drug_role=link.drug_role or "suspect",
                )
            )
    elif row.product_id or row.active_substance:
        db.add(
            CaseProduct(
                case_id=case.id,
                product_id=row.product_id,
                reported_product_name=row.product.product_name if row.product else None,
                active_substance_text=row.active_substance,
                drug_role="suspect",
            )
        )

    if row.adverse_event:
        db.add(
            Reaction(
                case_id=case.id,
                reported_term=row.adverse_event[:255],
                verbatim_term=row.adverse_event[:255],
                is_serious=seriousness == "serious",
            )
        )

    if row.patient_information:
        db.add(
            Patient(
                case_id=case.id,
                patient_identifier=row.patient_information[:100],
                medical_history_text=row.patient_information,
            )
        )

    old_status = row.status
    row.case_id = case.id
    row.status = "converted_to_case"
    row.possible_icsr = "yes"
    row.updated_by = actor_id
    row.version += 1

    create_incoming_request_case_link(
        db,
        row,
        case,
        link_type="created_from_message",
        comment="Case created from safety message.",
        created_by_user_id=actor_id,
        update_message_status=False,
    )

    log_create_event(
        db,
        entity_type="Case",
        entity_id=case.id,
        case_id=case.id,
        data={
            "case_number": case.case_number,
            "incoming_request_id": row.id,
            "workflow_status": case.workflow_status,
        },
        user_id=actor_id,
        comment="Created from safety message",
    )
    log_audit(
        db,
        entity_type="IncomingRequest",
        entity_id=row.id,
        action="status_change",
        field_name="status",
        old_value=old_status,
        new_value=row.status,
        case_id=case.id,
        user_id=actor_id,
        comment="Converted to case",
    )
    db.commit()
    db.refresh(case)
    return case


INCOMING_REQUEST_EXPORT_HEADERS = [
    "message number",
    "received date",
    "created at",
    "status",
    "triage status",
    "validity status",
    "triage result",
    "request type",
    "source type",
    "source name",
    "channel",
    "message category",
    "partner code",
    "partner",
    "products",
    "dosage forms",
    "possible ICSR",
    "patient information",
    "adverse event",
    "seriousness",
    "missing information",
    "recommended next action",
    "case number",
    "case creation status",
    "linked cases",
    "source text",
]


def incoming_request_export_rows(
    db: Session,
    *,
    dosage_form_language: str = "ru",
) -> list[list[Any]]:
    rows = []
    for row in list_incoming_requests(db):
        linked_cases = [
            link.case.case_number
            for link in row.case_links
            if not link.is_deleted and link.case
        ]
        rows.append(
            [
                row.message_number or "",
                row.received_date or "",
                row.created_at,
                row.status,
                row.triage_status or "",
                row.validity_status or "",
                row.triage_result or "",
                row.request_type or "",
                row.source_type or "",
                row.source_name or "",
                row.channel or "",
                row.message_category or "",
                (
                    (row.partner.partner_code or f"PARTNER{row.partner.id}")
                    if row.partner
                    else "UNASSIGNED"
                ),
                row.partner.partner_name if row.partner else "",
                "; ".join(row.product_names),
                "; ".join(
                    dosage_form
                    for product in row.linked_products
                    if (dosage_form := product_dosage_form_name(product, dosage_form_language))
                ),
                row.possible_icsr,
                row.patient_information or "",
                row.adverse_event or "",
                row.seriousness or "",
                row.missing_information or "",
                row.recommended_next_action or "",
                row.case.case_number if row.case else "",
                "Кейс создан" if row.case or linked_cases else "Кейс не создан",
                "; ".join(linked_cases),
                row.source_text,
            ]
        )
    return rows


def export_incoming_requests_csv(db: Session, *, dosage_form_language: str = "ru") -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(INCOMING_REQUEST_EXPORT_HEADERS)
    writer.writerows(
        incoming_request_export_rows(db, dosage_form_language=dosage_form_language)
    )
    return output.getvalue()


def export_incoming_requests_xlsx(db: Session, *, dosage_form_language: str = "ru") -> bytes:
    return build_xlsx(
        [
            (
                "Safety message journal",
                [
                    INCOMING_REQUEST_EXPORT_HEADERS,
                    *incoming_request_export_rows(
                        db,
                        dosage_form_language=dosage_form_language,
                    ),
                ],
            )
        ]
    )


def list_sops(db: Session) -> list[SOP]:
    return (
        db.query(SOP)
        .options(
            joinedload(SOP.creator),
            joinedload(SOP.updater),
            selectinload(SOP.versions).joinedload(SOPVersion.creator),
        )
        .filter(SOP.is_deleted.is_(False))
        .order_by(SOP.sop_code.asc())
        .all()
    )


def get_sop(db: Session, sop_id: str) -> SOP | None:
    return (
        db.query(SOP)
        .options(
            joinedload(SOP.creator),
            joinedload(SOP.updater),
            selectinload(SOP.versions).joinedload(SOPVersion.creator),
        )
        .filter(SOP.id == sop_id, SOP.is_deleted.is_(False))
        .first()
    )


def get_sop_by_code(db: Session, sop_code: str) -> SOP | None:
    return (
        db.query(SOP)
        .filter(
            SOP.sop_code == sop_code,
            SOP.is_deleted.is_(False),
        )
        .first()
    )


def create_sop(
    db: Session,
    payload: schemas.SOPCreate,
    *,
    created_by_user_id: str | None = None,
) -> SOP:
    data = clean_form_data(model_data(payload))
    actor_id = created_by_user_id or audit_user_id(db)
    sop = SOP(**data, created_by=actor_id, updated_by=actor_id)
    db.add(sop)
    db.flush()
    create_sop_version_snapshot(
        db,
        sop,
        actor_id=actor_id,
        revision_reason=sop.revision_reason,
    )
    log_create_event(
        db,
        entity_type="SOP",
        entity_id=sop.id,
        data={
            "sop_code": sop.sop_code,
            "title": sop.title,
            "document_type": sop.document_type,
            "version": sop.version,
            "status": sop.status,
            "process_area": sop.process_area,
            "owner": sop.owner,
        },
        user_id=actor_id,
    )
    db.commit()
    db.refresh(sop)
    return sop


def update_sop(
    db: Session,
    sop: SOP,
    payload: schemas.SOPCreate,
    *,
    changed_by_user_id: str | None = None,
    commit: bool = True,
) -> SOP:
    data = clean_form_data(model_data(payload))
    old_values = {field: getattr(sop, field, None) for field in data}
    for field, value in data.items():
        setattr(sop, field, value)
    sop.updated_by = changed_by_user_id or audit_user_id(db)
    changed_count = log_field_changes(
        db,
        entity_type="SOP",
        entity_id=sop.id,
        old_values=old_values,
        new_values=data,
        user_id=sop.updated_by,
        comment=sop.revision_reason,
    )
    if changed_count:
        create_sop_version_snapshot(
            db,
            sop,
            actor_id=sop.updated_by,
            revision_reason=sop.revision_reason,
        )
    if commit:
        db.commit()
    else:
        db.flush()
    if commit:
        db.refresh(sop)
    return sop


def list_sop_versions(db: Session, sop_id: str) -> list[SOPVersion]:
    return (
        db.query(SOPVersion)
        .options(joinedload(SOPVersion.creator))
        .filter(SOPVersion.sop_id == sop_id, SOPVersion.is_deleted.is_(False))
        .order_by(SOPVersion.created_at.desc())
        .all()
    )


def list_psur_tasks(db: Session, psur_plan_id: str) -> list[Task]:
    return (
        db.query(Task)
        .options(joinedload(Task.assigned_to), joinedload(Task.creator))
        .filter(
            Task.is_deleted.is_(False),
            Task.related_entity_type == "PSUR",
            Task.related_entity_id == psur_plan_id,
        )
        .order_by(Task.due_date.asc(), Task.created_at.desc())
        .all()
    )


def create_psur_partner_request(
    db: Session,
    plan: PSURPlan,
    payload: schemas.PSURPartnerRequestCreate,
    *,
    created_by_user_id: str | None = None,
    create_related_task: bool = True,
    commit: bool = True,
) -> PSURPartnerRequest:
    touch_psur_plan(db, plan.id)
    data = clean_form_data(model_data(payload))
    request = PSURPartnerRequest(
        psur_plan_id=plan.id,
        created_by=created_by_user_id or audit_user_id(db),
        **data,
    )
    db.add(request)
    db.flush()
    log_create_event(
        db,
        entity_type="PSURPartnerRequest",
        entity_id=request.id,
        data=data,
        user_id=request.created_by,
    )
    if create_related_task:
        partner_name = request.partner.partner_name if request.partner else "partner"
        task = Task(
            title=f"PSUR partner request: {partner_name}",
            description=request.request_type,
            status="Open",
            priority="Normal",
            due_date=request.due_date,
            assigned_to_user_id=plan.responsible_user_id,
            responsible_person=plan.responsible_user.full_name if plan.responsible_user else None,
            related_entity_type="PSUR",
            related_entity_id=plan.id,
            created_by=request.created_by,
            updated_by=request.created_by,
        )
        db.add(task)
        db.flush()
        log_create_event(
            db,
            entity_type="Task",
            entity_id=task.id,
            data={
                "title": task.title,
                "related_entity_type": task.related_entity_type,
                "related_entity_id": task.related_entity_id,
            },
            user_id=task.created_by,
        )
    if commit:
        db.commit()
        db.refresh(request)
    else:
        db.flush()
    return request


def update_psur_partner_request(
    db: Session,
    request: PSURPartnerRequest,
    payload: schemas.PSURPartnerRequestCreate,
    *,
    changed_by_user_id: str | None = None,
) -> PSURPartnerRequest:
    touch_psur_plan(db, request.psur_plan_id)
    data = clean_form_data(model_data(payload))
    old_values = {field: getattr(request, field, None) for field in data}
    for field, value in data.items():
        setattr(request, field, value)
    if request.status in {"Received", "Closed"} and not request.response_date:
        request.response_date = date.today()
    request.version += 1
    log_field_changes(
        db,
        entity_type="PSURPartnerRequest",
        entity_id=request.id,
        old_values=old_values,
        new_values=data,
        user_id=changed_by_user_id,
    )
    db.commit()
    db.refresh(request)
    return request


def create_psur_partner_requests(
    db: Session,
    plan: PSURPlan,
    *,
    partner_ids: list[str],
    request_type: str,
    request_date: date | None,
    due_date: date | None,
    created_by_user_id: str | None = None,
) -> list[PSURPartnerRequest]:
    created = []
    for partner_id in dict.fromkeys(partner_ids):
        partner = get_partner(db, partner_id)
        if not partner:
            continue
        contact = (
            db.query(ContractContact)
            .filter(
                ContractContact.partner_id == partner_id,
                ContractContact.is_deleted.is_(False),
                ContractContact.is_current.is_(True),
            )
            .order_by(ContractContact.last_name, ContractContact.first_name)
            .first()
        )
        created.append(
            create_psur_partner_request(
                db,
                plan,
                schemas.PSURPartnerRequestCreate(
                    partner_id=partner_id,
                    contact_person_id=contact.id if contact else None,
                    request_type=request_type,
                    request_date=request_date,
                    due_date=due_date,
                    status="Not Sent",
                ),
                created_by_user_id=created_by_user_id,
                commit=False,
            )
        )
    db.commit()
    return created


def psur_product_ids(plan: PSURPlan) -> set[str]:
    product_ids = {plan.product_id} if plan.product_id else set()
    product_ids.update(
        row.product_id
        for row in plan.psur_products
        if row.product_id and not row.is_deleted and row.included_in_report
    )
    return {product_id for product_id in product_ids if product_id}


def psur_substance_names(plan: PSURPlan) -> set[str]:
    names = set()
    if plan.active_substance:
        names.add(plan.active_substance.substance_name)
        if plan.active_substance.inn_name:
            names.add(plan.active_substance.inn_name)
    return {normalize_text(name) for name in names if normalize_text(name)}


def case_matches_psur_scope(case: Case, plan: PSURPlan) -> bool:
    product_ids = psur_product_ids(plan)
    substance_names = psur_substance_names(plan)
    for case_product in case.case_products:
        if case_product.product_id and case_product.product_id in product_ids:
            return True
        if case_product.product:
            if normalize_text(case_product.product.product_name) in substance_names:
                return True
            for link in case_product.product.substance_links:
                if link.substance_id == plan.active_substance_id:
                    return True
        active_substance = normalize_text(case_product.active_substance_text)
        reported_product = normalize_text(case_product.reported_product_name)
        if active_substance and any(name in active_substance for name in substance_names):
            return True
        if plan.product and reported_product == normalize_text(plan.product.product_name):
            return True
    return False


def case_is_valid_for_psur(case: Case) -> bool:
    if case.workflow_status in {"invalid", "non_safety", "cancelled"}:
        return False
    if case.safety_report:
        return case.safety_report.is_valid_icsr or case.safety_report.triage_status in {
            "valid_icsr",
            "converted_to_case",
        }
    return True


def find_psur_case_candidates(db: Session, plan: PSURPlan) -> list[Case]:
    candidates = []
    for case in list_cases(db):
        case_date = case.latest_received_date or case.initial_received_date
        if not case_date:
            continue
        if case_date < plan.reporting_period_start or case_date > plan.reporting_period_end:
            continue
        if not case_is_valid_for_psur(case):
            continue
        if case_matches_psur_scope(case, plan):
            candidates.append(case)
    return candidates


def find_and_link_psur_cases(db: Session, plan: PSURPlan) -> list[PSURCase]:
    touch_psur_plan(db, plan.id)
    existing_case_ids = {
        row.case_id
        for row in db.query(PSURCase)
        .filter(PSURCase.psur_plan_id == plan.id, PSURCase.is_deleted.is_(False))
        .all()
    }
    created = []
    for case in find_psur_case_candidates(db, plan):
        if case.id in existing_case_ids:
            continue
        row = PSURCase(
            psur_plan_id=plan.id,
            case_id=case.id,
            case_included=True,
            seriousness=case.seriousness,
            listedness=case.listedness,
            case_origin=case.partner.partner_name if case.partner else case.report_type,
            assessment_comment="Auto-selected by PSUR scope and reporting period.",
        )
        db.add(row)
        db.flush()
        created.append(row)
        log_create_event(
            db,
            entity_type="PSURCase",
            entity_id=row.id,
            case_id=case.id,
            data={
                "psur_plan_id": plan.id,
                "case_id": case.id,
                "case_included": row.case_included,
            },
        )
    if created:
        log_audit(
            db,
            entity_type="PSURPlan",
            entity_id=plan.id,
            action="case_selection",
            field_name="psur_cases",
            new_value=f"{len(created)} ICSR cases linked",
            user_id=audit_user_id(db),
        )
    db.commit()
    return created


def update_psur_case(
    db: Session,
    psur_case: PSURCase,
    payload: schemas.PSURCaseUpdate,
    *,
    changed_by_user_id: str | None = None,
) -> PSURCase:
    touch_psur_plan(db, psur_case.psur_plan_id)
    data = clean_form_data(model_data(payload))
    old_values = {field: getattr(psur_case, field, None) for field in data}
    for field, value in data.items():
        setattr(psur_case, field, value)
    psur_case.version += 1
    log_field_changes(
        db,
        entity_type="PSURCase",
        entity_id=psur_case.id,
        old_values=old_values,
        new_values=data,
        case_id=psur_case.case_id,
        user_id=changed_by_user_id,
    )
    db.commit()
    db.refresh(psur_case)
    return psur_case


def get_psur_case(db: Session, psur_case_id: str) -> PSURCase | None:
    return (
        db.query(PSURCase)
        .filter(PSURCase.id == psur_case_id, PSURCase.is_deleted.is_(False))
        .first()
    )


def get_psur_section(db: Session, section_id: str) -> PSURSection | None:
    return (
        db.query(PSURSection)
        .filter(PSURSection.id == section_id, PSURSection.is_deleted.is_(False))
        .first()
    )


def update_psur_section(
    db: Session,
    section: PSURSection,
    payload: schemas.PSURSectionUpdate,
    *,
    changed_by_user_id: str | None = None,
) -> PSURSection:
    touch_psur_plan(db, section.psur_plan_id)
    data = clean_form_data(model_data(payload))
    old_values = {field: getattr(section, field, None) for field in data}
    for field, value in data.items():
        setattr(section, field, value)
    section.last_updated_at = utcnow()
    if section.human_confirmed:
        section.confirmed_by = changed_by_user_id or audit_user_id(db)
        section.confirmed_at = section.confirmed_at or utcnow()
    else:
        section.confirmed_by = None
        section.confirmed_at = None
    section.version += 1
    action = "approve" if section.section_status == "Approved" else "review"
    log_field_changes(
        db,
        entity_type="PSURSection",
        entity_id=section.id,
        old_values=old_values,
        new_values=data,
        action=action,
        user_id=changed_by_user_id,
        comment=section.comment,
    )
    db.commit()
    db.refresh(section)
    return section


def generate_psur_section_draft(
    db: Session,
    plan: PSURPlan,
    section: PSURSection,
    *,
    changed_by_user_id: str | None = None,
) -> PSURSection:
    touch_psur_plan(db, plan.id)
    included_cases = [row for row in plan.psur_cases
                      if not row.is_deleted and row.case_included and row.case and not row.case.is_deleted]
    serious_cases = [row for row in included_cases if row.seriousness == "serious"]
    products = [
        row.product.product_name
        for row in plan.psur_products
        if not row.is_deleted and row.included_in_report and row.product and not row.product.is_deleted
    ]
    if plan.product and plan.product.product_name not in products:
        products.insert(0, plan.product.product_name)
    prompt = (
        f"Draft PSUR section {section.section_code} '{section.section_title}' "
        f"for {plan.active_substance.substance_name if plan.active_substance else 'substance'} "
        f"covering {plan.reporting_period_start} to {plan.reporting_period_end}. "
        "Use structured database data only. Require human confirmation."
    )
    draft_lines = [
        f"Section: {section.section_title}",
        f"Reporting period: {plan.reporting_period_start} to {plan.reporting_period_end}",
        f"Data lock point: {plan.data_lock_point}",
        f"Products covered: {', '.join(products) if products else 'No products linked'}",
        f"Included ICSR cases: {len(included_cases)}",
        f"Serious cases: {len(serious_cases)}",
        "Human review is required before this text can be treated as final.",
    ]
    output = {
        "draft_text": "\n".join(draft_lines),
        "included_case_numbers": [
            row.case.case_number for row in included_cases if row.case
        ],
        "missing_information": [
            "Confirm medical assessment and listedness.",
            "Confirm partner responses and regulatory action status.",
        ],
        "review_checklist": [
            "Verify reporting period and DLP.",
            "Confirm included and excluded ICSR cases.",
            "Confirm that GPT-assisted text was reviewed by a human.",
        ],
    }
    old_values = {
        "section_text": section.section_text,
        "gpt_generated": section.gpt_generated,
        "gpt_prompt": section.gpt_prompt,
        "gpt_output_json": section.gpt_output_json,
        "human_confirmed": section.human_confirmed,
    }
    section.section_text = output["draft_text"]
    section.section_status = "Draft"
    section.gpt_generated = True
    section.gpt_prompt = prompt
    section.gpt_output_json = json.dumps(output, ensure_ascii=False)
    section.human_confirmed = False
    section.confirmed_by = None
    section.confirmed_at = None
    section.last_updated_at = utcnow()
    section.version += 1
    log_field_changes(
        db,
        entity_type="PSURSection",
        entity_id=section.id,
        old_values=old_values,
        new_values={
            "section_text": section.section_text,
            "gpt_generated": section.gpt_generated,
            "gpt_prompt": section.gpt_prompt,
            "gpt_output_json": section.gpt_output_json,
            "human_confirmed": section.human_confirmed,
        },
        action="gpt_draft",
        user_id=changed_by_user_id,
        comment="GPT-assisted draft requires human review.",
    )
    db.commit()
    db.refresh(section)
    return section


def collect_psur_audit_entity_ids(plan: PSURPlan) -> set[str]:
    entity_ids = {plan.id}
    entity_ids.update(row.id for row in plan.psur_products)
    entity_ids.update(row.id for row in plan.psur_cases)
    entity_ids.update(row.id for row in plan.partner_requests)
    entity_ids.update(row.id for row in plan.sections)
    entity_ids.update(row.id for row in plan.documents)
    return entity_ids


def list_psur_audit_entries(db: Session, plan: PSURPlan) -> list[AuditTrail]:
    task_ids = {task.id for task in list_psur_tasks(db, plan.id)}
    entity_ids = collect_psur_audit_entity_ids(plan) | task_ids
    return (
        db.query(AuditTrail)
        .options(joinedload(AuditTrail.user), joinedload(AuditTrail.case))
        .filter(
            AuditTrail.is_deleted.is_(False),
            AuditTrail.entity_type.in_(PSUR_ENTITY_TYPES),
            AuditTrail.entity_id.in_(entity_ids),
        )
        .order_by(func.coalesce(AuditTrail.changed_at, AuditTrail.timestamp).desc())
        .all()
    )


def psur_dashboard_stats(db: Session) -> dict[str, Any]:
    today = date.today()
    upcoming_window = today + timedelta(days=30)
    plans = list_psur_plans(db)
    active_statuses = {"Data Collection", "Case Selection", "Drafting"}
    review_statuses = {"Under Review", "QA Review"}
    closed_statuses = {"Submitted", "Archived"}
    overdue_plans = [
        plan
        for plan in plans
        if plan.due_date_submission
        and plan.due_date_submission < today
        and plan.status not in closed_statuses
    ]
    upcoming_dlp = [
        plan
        for plan in plans
        if today <= plan.data_lock_point <= upcoming_window
        and plan.status not in closed_statuses
    ]
    upcoming_submission = [
        plan
        for plan in plans
        if plan.due_date_submission
        and today <= plan.due_date_submission <= upcoming_window
        and plan.status not in closed_statuses
    ]
    overdue_partner_requests = (
        db.query(PSURPartnerRequest)
        .filter(
            PSURPartnerRequest.is_deleted.is_(False),
            PSURPartnerRequest.due_date.is_not(None),
            PSURPartnerRequest.due_date < today,
            PSURPartnerRequest.status.notin_(["Received", "Closed"]),
        )
        .count()
    )
    return {
        "total_planned_reports": len(plans),
        "reports_in_progress": sum(1 for plan in plans if plan.status in active_statuses),
        "reports_under_review": sum(1 for plan in plans if plan.status in review_statuses),
        "approved_reports": sum(1 for plan in plans if plan.status == "Approved"),
        "submitted_reports": sum(1 for plan in plans if plan.status == "Submitted"),
        "overdue_reports": len(overdue_plans),
        "upcoming_dlp": len(upcoming_dlp),
        "upcoming_submission_deadline": len(upcoming_submission),
        "overdue_partner_requests": overdue_partner_requests,
    }


def export_psur_cases_csv(plan: PSURPlan, *, dosage_form_language: str = "ru") -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "case_number",
            "received_date",
            "seriousness",
            "products",
            "dosage_forms",
            "listedness",
            "case_origin",
            "included",
            "reason_excluded",
            "assessment_comment",
        ]
    )
    for row in plan.psur_cases:
        case = row.case
        case_products = case.case_products if case else []
        product_names = "; ".join(
            product_row.reported_product_name
            or (product_row.product.product_name if product_row.product else "")
            for product_row in case_products
            if product_row.reported_product_name or product_row.product
        )
        dosage_forms = "; ".join(
            product_dosage_form_name(product_row.product, dosage_form_language)
            for product_row in case_products
            if product_row.product
        )
        writer.writerow(
            [
                case.case_number if case else row.case_id,
                (case.latest_received_date or case.initial_received_date) if case else "",
                row.seriousness or (case.seriousness if case else ""),
                product_names,
                dosage_forms,
                row.listedness or (case.listedness if case else ""),
                row.case_origin or "",
                row.case_included,
                row.reason_excluded or "",
                row.assessment_comment or "",
            ]
        )
    return output.getvalue()


def export_psur_partner_requests_csv(plan: PSURPlan) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "partner",
            "contact",
            "request_type",
            "request_date",
            "due_date",
            "response_date",
            "status",
            "response_summary",
        ]
    )
    for request in plan.partner_requests:
        writer.writerow(
            [
                request.partner.partner_name if request.partner else request.partner_id,
                request.contact_person.email if request.contact_person else "",
                request.request_type,
                request.request_date or "",
                request.due_date or "",
                request.response_date or "",
                request.status,
                request.response_summary or "",
            ]
        )
    return output.getvalue()


def export_psur_audit_csv(db: Session, plan: PSURPlan) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "changed_at",
            "user",
            "entity_type",
            "entity_id",
            "action",
            "field_name",
            "old_value",
            "new_value",
            "comment",
        ]
    )
    for entry in list_psur_audit_entries(db, plan):
        writer.writerow(
            [
                entry.changed_at or entry.timestamp,
                entry.user.email if entry.user else entry.changed_by or "",
                entry.entity_type,
                entry.entity_id,
                entry.action,
                entry.field_name or "",
                entry.old_value or "",
                entry.new_value or "",
                entry.comment or "",
            ]
        )
    return output.getvalue()


def rtf_escape(value: object) -> str:
    text = str(value or "")
    escaped = []
    for char in text:
        if char == "\\":
            escaped.append("\\\\")
        elif char == "{":
            escaped.append("\\{")
        elif char == "}":
            escaped.append("\\}")
        elif char == "\n":
            escaped.append("\\par ")
        elif ord(char) > 127:
            code = ord(char)
            if code > 32767:
                code -= 65536
            escaped.append(f"\\u{code}?")
        else:
            escaped.append(char)
    return "".join(escaped)


def export_psur_summary_rtf(plan: PSURPlan, *, dosage_form_language: str = "ru") -> str:
    included_cases = [row for row in plan.psur_cases if row.case_included]
    approved_sections = [
        section for section in plan.sections if section.section_status == "Approved"
    ]
    lines = [
        r"{\rtf1\ansi\deff0",
        r"\b PSUR / PBRER Plan Summary\b0\par",
        f"Type: {rtf_escape(plan.psur_type)}\\par",
        f"Status: {rtf_escape(plan.status)}\\par",
        f"Active substance: {rtf_escape(plan.active_substance.substance_name if plan.active_substance else '')}\\par",
        f"Product: {rtf_escape(plan.product.product_name if plan.product else '')}\\par",
        f"Product dosage form: {rtf_escape(product_dosage_form_name(plan.product, dosage_form_language))}\\par",
        f"Reporting period: {plan.reporting_period_start} to {plan.reporting_period_end}\\par",
        f"Data lock point: {plan.data_lock_point}\\par",
        f"Internal due date: {plan.due_date_internal or ''}\\par",
        f"Submission due date: {plan.due_date_submission or ''}\\par",
        f"Included ICSR cases: {len(included_cases)}\\par",
        f"Partner requests: {len(plan.partner_requests)}\\par",
        f"Approved sections: {len(approved_sections)} of {len(plan.sections)}\\par",
        r"\par\b Sections\b0\par",
    ]
    for section in sorted(plan.sections, key=lambda item: item.section_code):
        lines.append(
            f"{rtf_escape(section.section_code)}. {rtf_escape(section.section_title)} "
            f"- {rtf_escape(section.section_status)}\\par"
        )
    lines.append("}")
    return "\n".join(lines)


LITERATURE_SOURCE_MODULE = "Literature Monitoring"
LITERATURE_ACTION_DECISIONS = {
    "create_icsr",
    "add_to_rmp",
    "add_to_psur",
    "add_to_psmf",
    "discuss_required",
}


def normalize_id_list(values: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for value in values or []:
        value = (value or "").strip()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


def literature_product_ids(parent) -> list[str]:
    return [
        link.product_id
        for link in getattr(parent, "products", [])
        if link.product_id and not link.is_deleted
    ]


def validate_product_ids(db: Session, product_ids: list[str]) -> list[str]:
    normalized = normalize_id_list(product_ids)
    for product_id in normalized:
        if not get_product(db, product_id):
            raise ValueError("Selected product was not found.")
    return normalized


def sync_literature_products(
    db: Session,
    parent,
    link_model: type,
    parent_field: str,
    product_ids: list[str],
) -> tuple[list[str], list[str]]:
    old_product_ids = sorted(literature_product_ids(parent))
    product_ids = validate_product_ids(db, product_ids)
    getattr(parent, "products").clear()
    db.flush()
    for product_id in product_ids:
        getattr(parent, "products").append(
            link_model(**{parent_field: parent.id, "product_id": product_id})
        )
    db.flush()
    return old_product_ids, sorted(product_ids)


def literature_plan_options(query):
    return query.options(
        joinedload(LiteratureMonitoringPlan.partner),
        joinedload(LiteratureMonitoringPlan.active_substance),
        joinedload(LiteratureMonitoringPlan.responsible_user),
        selectinload(LiteratureMonitoringPlan.products).joinedload(
            LiteratureMonitoringPlanProduct.product
        ),
        selectinload(LiteratureMonitoringPlan.search_logs),
        selectinload(LiteratureMonitoringPlan.results),
    )


def literature_log_options(query):
    return query.options(
        joinedload(LiteratureSearchLog.plan).joinedload(LiteratureMonitoringPlan.partner),
        joinedload(LiteratureSearchLog.plan)
        .selectinload(LiteratureMonitoringPlan.products)
        .joinedload(LiteratureMonitoringPlanProduct.product),
        joinedload(LiteratureSearchLog.partner),
        joinedload(LiteratureSearchLog.active_substance),
        joinedload(LiteratureSearchLog.searched_by),
        selectinload(LiteratureSearchLog.products).joinedload(LiteratureSearchLogProduct.product),
        selectinload(LiteratureSearchLog.results),
    )


def literature_result_options(query):
    return query.options(
        joinedload(LiteratureResult.plan).joinedload(LiteratureMonitoringPlan.partner),
        joinedload(LiteratureResult.search_log),
        joinedload(LiteratureResult.partner),
        joinedload(LiteratureResult.active_substance),
        selectinload(LiteratureResult.products).joinedload(LiteratureResultProduct.product),
        joinedload(LiteratureResult.article_pdf_document),
        joinedload(LiteratureResult.screenshot_document),
        joinedload(LiteratureResult.linked_case),
        joinedload(LiteratureResult.linked_psur_plan),
        joinedload(LiteratureResult.linked_psmf_component),
        joinedload(LiteratureResult.creator),
        joinedload(LiteratureResult.updater),
    )


def list_literature_plans(db: Session) -> list[LiteratureMonitoringPlan]:
    return (
        literature_plan_options(db.query(LiteratureMonitoringPlan))
        .filter(LiteratureMonitoringPlan.is_deleted.is_(False))
        .order_by(
            LiteratureMonitoringPlan.status.asc(),
            LiteratureMonitoringPlan.start_date.desc(),
            LiteratureMonitoringPlan.created_at.desc(),
        )
        .all()
    )


def get_literature_plan(db: Session, plan_id: str) -> LiteratureMonitoringPlan | None:
    return (
        literature_plan_options(db.query(LiteratureMonitoringPlan))
        .filter(
            LiteratureMonitoringPlan.id == plan_id,
            LiteratureMonitoringPlan.is_deleted.is_(False),
        )
        .first()
    )


def create_literature_plan(
    db: Session,
    payload: schemas.LiteratureMonitoringPlanCreate,
    *,
    created_by_user_id: str | None = None,
) -> LiteratureMonitoringPlan:
    data = clean_form_data(model_data(payload))
    product_ids = data.pop("product_ids", [])
    if not get_partner(db, data["partner_id"]):
        raise ValueError("Partner is required.")
    if data.get("active_substance_id") and not get_substance(db, data["active_substance_id"]):
        raise ValueError("Selected active substance was not found.")
    if data.get("responsible_user_id") and not get_user(db, data["responsible_user_id"]):
        raise ValueError("Selected responsible user was not found.")
    actor_id = created_by_user_id or audit_user_id(db)
    plan = LiteratureMonitoringPlan(**data, created_by=actor_id, updated_by=actor_id)
    db.add(plan)
    db.flush()
    _, synced_product_ids = sync_literature_products(
        db,
        plan,
        LiteratureMonitoringPlanProduct,
        "plan_id",
        product_ids,
    )
    log_create_event(
        db,
        entity_type="LiteratureMonitoringPlan",
        entity_id=plan.id,
        data={**data, "product_ids": ", ".join(synced_product_ids)},
        user_id=actor_id,
    )
    db.commit()
    db.refresh(plan)
    return plan


def update_literature_plan(
    db: Session,
    plan: LiteratureMonitoringPlan,
    payload: schemas.LiteratureMonitoringPlanCreate,
    *,
    changed_by_user_id: str | None = None,
) -> LiteratureMonitoringPlan:
    data = clean_form_data(model_data(payload))
    product_ids = data.pop("product_ids", [])
    if not get_partner(db, data["partner_id"]):
        raise ValueError("Partner is required.")
    if data.get("active_substance_id") and not get_substance(db, data["active_substance_id"]):
        raise ValueError("Selected active substance was not found.")
    if data.get("responsible_user_id") and not get_user(db, data["responsible_user_id"]):
        raise ValueError("Selected responsible user was not found.")
    old_values = {field: getattr(plan, field, None) for field in data}
    for field, value in data.items():
        setattr(plan, field, value)
    actor_id = changed_by_user_id or audit_user_id(db)
    plan.updated_by = actor_id
    plan.version += 1
    old_product_ids, new_product_ids = sync_literature_products(
        db,
        plan,
        LiteratureMonitoringPlanProduct,
        "plan_id",
        product_ids,
    )
    log_field_changes(
        db,
        entity_type="LiteratureMonitoringPlan",
        entity_id=plan.id,
        old_values=old_values,
        new_values=data,
        user_id=actor_id,
    )
    if old_product_ids != new_product_ids:
        log_audit(
            db,
            entity_type="LiteratureMonitoringPlan",
            entity_id=plan.id,
            action="edit",
            field_name="product_ids",
            old_value=", ".join(old_product_ids),
            new_value=", ".join(new_product_ids),
            user_id=actor_id,
            source_module=LITERATURE_SOURCE_MODULE,
        )
    db.commit()
    db.refresh(plan)
    return plan


def archive_literature_plan(
    db: Session,
    plan: LiteratureMonitoringPlan,
    *,
    changed_by_user_id: str | None = None,
    comment: str | None = None,
) -> LiteratureMonitoringPlan:
    old_status = plan.status
    plan.status = "archived"
    plan.is_active = False
    plan.updated_by = changed_by_user_id or audit_user_id(db)
    plan.version += 1
    log_audit(
        db,
        entity_type="LiteratureMonitoringPlan",
        entity_id=plan.id,
        action="archive",
        field_name="status",
        old_value=old_status,
        new_value=plan.status,
        comment=comment,
        user_id=plan.updated_by,
        source_module=LITERATURE_SOURCE_MODULE,
    )
    db.commit()
    db.refresh(plan)
    return plan


def list_literature_search_logs(db: Session) -> list[LiteratureSearchLog]:
    return (
        literature_log_options(db.query(LiteratureSearchLog))
        .filter(LiteratureSearchLog.is_deleted.is_(False))
        .order_by(LiteratureSearchLog.search_date.desc(), LiteratureSearchLog.created_at.desc())
        .all()
    )


def get_literature_search_log(db: Session, log_id: str) -> LiteratureSearchLog | None:
    return (
        literature_log_options(db.query(LiteratureSearchLog))
        .filter(
            LiteratureSearchLog.id == log_id,
            LiteratureSearchLog.is_deleted.is_(False),
        )
        .first()
    )


def create_literature_search_log(
    db: Session,
    payload: schemas.LiteratureSearchLogCreate,
    *,
    created_by_user_id: str | None = None,
) -> LiteratureSearchLog:
    data = clean_form_data(model_data(payload))
    product_ids = data.pop("product_ids", [])
    plan = get_literature_plan(db, data["plan_id"])
    if not plan:
        raise ValueError("Monitoring plan was not found.")
    data["partner_id"] = data.get("partner_id") or plan.partner_id
    data["active_substance_id"] = data.get("active_substance_id") or plan.active_substance_id
    if not product_ids:
        product_ids = literature_product_ids(plan)
    actor_id = created_by_user_id or audit_user_id(db)
    search_log = LiteratureSearchLog(**data, created_by=actor_id, updated_by=actor_id)
    db.add(search_log)
    db.flush()
    _, synced_product_ids = sync_literature_products(
        db,
        search_log,
        LiteratureSearchLogProduct,
        "search_log_id",
        product_ids,
    )
    log_create_event(
        db,
        entity_type="LiteratureSearchLog",
        entity_id=search_log.id,
        data={**data, "product_ids": ", ".join(synced_product_ids)},
        user_id=actor_id,
    )
    db.commit()
    db.refresh(search_log)
    return search_log


def update_literature_search_log(
    db: Session,
    search_log: LiteratureSearchLog,
    payload: schemas.LiteratureSearchLogCreate,
    *,
    changed_by_user_id: str | None = None,
) -> LiteratureSearchLog:
    data = clean_form_data(model_data(payload))
    product_ids = data.pop("product_ids", [])
    plan = get_literature_plan(db, data["plan_id"])
    if not plan:
        raise ValueError("Monitoring plan was not found.")
    data["partner_id"] = data.get("partner_id") or plan.partner_id
    data["active_substance_id"] = data.get("active_substance_id") or plan.active_substance_id
    if not product_ids:
        product_ids = literature_product_ids(plan)
    old_values = {field: getattr(search_log, field, None) for field in data}
    for field, value in data.items():
        setattr(search_log, field, value)
    actor_id = changed_by_user_id or audit_user_id(db)
    search_log.updated_by = actor_id
    search_log.version += 1
    old_product_ids, new_product_ids = sync_literature_products(
        db,
        search_log,
        LiteratureSearchLogProduct,
        "search_log_id",
        product_ids,
    )
    log_field_changes(
        db,
        entity_type="LiteratureSearchLog",
        entity_id=search_log.id,
        old_values=old_values,
        new_values=data,
        user_id=actor_id,
    )
    if old_product_ids != new_product_ids:
        log_audit(
            db,
            entity_type="LiteratureSearchLog",
            entity_id=search_log.id,
            action="edit",
            field_name="product_ids",
            old_value=", ".join(old_product_ids),
            new_value=", ".join(new_product_ids),
            user_id=actor_id,
            source_module=LITERATURE_SOURCE_MODULE,
        )
    db.commit()
    db.refresh(search_log)
    return search_log


def list_literature_results(db: Session) -> list[LiteratureResult]:
    return (
        literature_result_options(db.query(LiteratureResult))
        .filter(LiteratureResult.is_deleted.is_(False))
        .order_by(LiteratureResult.created_at.desc())
        .all()
    )


def get_literature_result(db: Session, result_id: str) -> LiteratureResult | None:
    return (
        literature_result_options(db.query(LiteratureResult))
        .filter(LiteratureResult.id == result_id, LiteratureResult.is_deleted.is_(False))
        .first()
    )


def create_literature_result(
    db: Session,
    payload: schemas.LiteratureResultCreate,
    *,
    created_by_user_id: str | None = None,
) -> LiteratureResult:
    data = clean_form_data(model_data(payload))
    product_ids = data.pop("product_ids", [])
    search_log = None
    if data.get("search_log_id"):
        search_log = get_literature_search_log(db, data["search_log_id"])
        if not search_log:
            raise ValueError("Search log record was not found.")
        data["plan_id"] = search_log.plan_id
    plan = get_literature_plan(db, data["plan_id"])
    if not plan:
        raise ValueError("Monitoring plan was not found.")
    data["partner_id"] = data.get("partner_id") or (
        search_log.partner_id if search_log else None
    ) or plan.partner_id
    data["active_substance_id"] = data.get("active_substance_id") or (
        search_log.active_substance_id if search_log else None
    ) or plan.active_substance_id
    if not product_ids:
        product_ids = literature_product_ids(search_log) if search_log else literature_product_ids(plan)
    actor_id = created_by_user_id or audit_user_id(db)
    result = LiteratureResult(**data, created_by=actor_id, updated_by=actor_id)
    db.add(result)
    db.flush()
    _, synced_product_ids = sync_literature_products(
        db,
        result,
        LiteratureResultProduct,
        "result_id",
        product_ids,
    )
    log_create_event(
        db,
        entity_type="LiteratureResult",
        entity_id=result.id,
        data={**data, "product_ids": ", ".join(synced_product_ids)},
        user_id=actor_id,
    )
    db.commit()
    db.refresh(result)
    return result


def update_literature_result(
    db: Session,
    result: LiteratureResult,
    payload: schemas.LiteratureResultCreate,
    *,
    changed_by_user_id: str | None = None,
) -> LiteratureResult:
    data = clean_form_data(model_data(payload))
    product_ids = data.pop("product_ids", [])
    search_log = None
    if data.get("search_log_id"):
        search_log = get_literature_search_log(db, data["search_log_id"])
        if not search_log:
            raise ValueError("Search log record was not found.")
        data["plan_id"] = search_log.plan_id
    plan = get_literature_plan(db, data["plan_id"])
    if not plan:
        raise ValueError("Monitoring plan was not found.")
    data["partner_id"] = data.get("partner_id") or (
        search_log.partner_id if search_log else None
    ) or plan.partner_id
    data["active_substance_id"] = data.get("active_substance_id") or (
        search_log.active_substance_id if search_log else None
    ) or plan.active_substance_id
    if not product_ids:
        product_ids = literature_product_ids(search_log) if search_log else literature_product_ids(plan)
    old_values = {field: getattr(result, field, None) for field in data}
    for field, value in data.items():
        setattr(result, field, value)
    actor_id = changed_by_user_id or audit_user_id(db)
    result.updated_by = actor_id
    result.version += 1
    old_product_ids, new_product_ids = sync_literature_products(
        db,
        result,
        LiteratureResultProduct,
        "result_id",
        product_ids,
    )
    log_field_changes(
        db,
        entity_type="LiteratureResult",
        entity_id=result.id,
        old_values=old_values,
        new_values=data,
        action="decision_update" if old_values.get("pv_decision") != data.get("pv_decision") else "edit",
        user_id=actor_id,
    )
    if old_product_ids != new_product_ids:
        log_audit(
            db,
            entity_type="LiteratureResult",
            entity_id=result.id,
            action="edit",
            field_name="product_ids",
            old_value=", ".join(old_product_ids),
            new_value=", ".join(new_product_ids),
            user_id=actor_id,
            source_module=LITERATURE_SOURCE_MODULE,
        )
    db.commit()
    db.refresh(result)
    return result


def set_literature_result_documents(
    db: Session,
    result: LiteratureResult,
    *,
    article_pdf_document_id: str | None = None,
    screenshot_document_id: str | None = None,
    changed_by_user_id: str | None = None,
) -> LiteratureResult:
    changes = {}
    if article_pdf_document_id and result.article_pdf_document_id != article_pdf_document_id:
        changes["article_pdf_document_id"] = article_pdf_document_id
    if screenshot_document_id and result.screenshot_document_id != screenshot_document_id:
        changes["screenshot_document_id"] = screenshot_document_id
    if not changes:
        return result
    old_values = {field: getattr(result, field, None) for field in changes}
    for field, value in changes.items():
        setattr(result, field, value)
    result.updated_by = changed_by_user_id or audit_user_id(db)
    result.version += 1
    log_field_changes(
        db,
        entity_type="LiteratureResult",
        entity_id=result.id,
        old_values=old_values,
        new_values=changes,
        action="file_upload",
        user_id=result.updated_by,
    )
    db.commit()
    db.refresh(result)
    return result


def create_case_from_literature_result(
    db: Session,
    result: LiteratureResult,
    *,
    created_by_user_id: str | None = None,
) -> Case:
    if result.linked_case_id:
        linked_case = get_case(db, result.linked_case_id)
        if linked_case:
            return linked_case
    actor_id = created_by_user_id or audit_user_id(db)
    reference = result.doi or result.url or ""
    narrative_parts = [
        f"Literature monitoring publication: {result.publication_title}",
        f"Authors: {result.authors or ''}",
        f"Source: {result.journal_source or ''}",
        f"Reference: {reference}",
        "",
        result.abstract or result.specialist_comment or "",
    ]
    case = Case(
        case_number="",
        partner_id=result.partner_id,
        case_type="literature",
        report_type="literature",
        initial_received_date=result.publication_date or date.today(),
        latest_received_date=result.publication_date or date.today(),
        seriousness="non-serious",
        narrative="\n".join(part for part in narrative_parts if part is not None),
        company_comment=(
            "Created from literature monitoring result. Missing information "
            "requires PV review before workflow progression."
        ),
        workflow_status="new",
        created_by=actor_id,
        updated_by=actor_id,
    )
    assign_case_number(db, case, partner_id=result.partner_id)
    db.add(case)
    db.flush()
    for product_link in result.products:
        if product_link.is_deleted or (product_link.product and product_link.product.is_deleted):
            continue
        product = product_link.product or get_product(db, product_link.product_id)
        db.add(
            CaseProduct(
                case_id=case.id,
                product_id=product_link.product_id,
                reported_product_name=product.product_name if product else None,
                active_substance_text=(
                    result.active_substance.substance_name
                    if result.active_substance
                    else None
                ),
                drug_role="suspect",
            )
        )
    result.linked_case_id = case.id
    result.pv_decision = "create_icsr"
    result.processing_status = "under_review"
    result.updated_by = actor_id
    result.version += 1
    log_create_event(
        db,
        entity_type="Case",
        entity_id=case.id,
        case_id=case.id,
        data={
            "case_number": case.case_number,
            "report_type": "literature",
            "literature_result_id": result.id,
        },
        user_id=actor_id,
        comment="Created from literature monitoring publication.",
    )
    log_audit(
        db,
        entity_type="LiteratureResult",
        entity_id=result.id,
        action="create_icsr",
        field_name="linked_case_id",
        old_value=None,
        new_value=case.id,
        user_id=actor_id,
        source_module=LITERATURE_SOURCE_MODULE,
        comment=f"Created ICSR draft {case.case_number}.",
    )
    db.commit()
    db.refresh(case)
    return case


def link_literature_result_to_module(
    db: Session,
    result: LiteratureResult,
    *,
    target: str,
    target_id: str | None = None,
    changed_by_user_id: str | None = None,
) -> LiteratureResult:
    actor_id = changed_by_user_id or audit_user_id(db)
    field_name = ""
    new_value = target_id
    action = f"link_{target}"
    if target == "psur":
        if not target_id or not get_psur_plan(db, target_id):
            raise ValueError("Select a PSUR/PBRER plan.")
        field_name = "linked_psur_plan_id"
        old_value = result.linked_psur_plan_id
        result.linked_psur_plan_id = target_id
        result.pv_decision = "add_to_psur"
    elif target == "psmf":
        component = (
            db.query(PSMFComponent)
            .filter(PSMFComponent.id == target_id, PSMFComponent.is_deleted.is_(False))
            .first()
        )
        if not component:
            raise ValueError("Select a PSMF component.")
        field_name = "linked_psmf_component_id"
        old_value = result.linked_psmf_component_id
        result.linked_psmf_component_id = target_id
        result.pv_decision = "add_to_psmf"
    elif target == "rmp":
        field_name = "rmp_reference"
        old_value = result.rmp_reference
        result.rmp_reference = (target_id or "RMP source").strip() or "RMP source"
        new_value = result.rmp_reference
        result.pv_decision = "add_to_rmp"
    else:
        raise ValueError("Unsupported literature link target.")
    result.processing_status = "processed"
    result.updated_by = actor_id
    result.version += 1
    log_audit(
        db,
        entity_type="LiteratureResult",
        entity_id=result.id,
        action=action,
        field_name=field_name,
        old_value=old_value,
        new_value=new_value,
        user_id=actor_id,
        source_module=LITERATURE_SOURCE_MODULE,
    )
    db.commit()
    db.refresh(result)
    return result


def close_literature_result(
    db: Session,
    result: LiteratureResult,
    *,
    changed_by_user_id: str | None = None,
    comment: str | None = None,
) -> LiteratureResult:
    old_status = result.processing_status
    result.processing_status = "closed"
    if comment:
        result.specialist_comment = (
            f"{result.specialist_comment}\n{comment}"
            if result.specialist_comment
            else comment
        )
    result.updated_by = changed_by_user_id or audit_user_id(db)
    result.version += 1
    log_audit(
        db,
        entity_type="LiteratureResult",
        entity_id=result.id,
        action="close",
        field_name="processing_status",
        old_value=old_status,
        new_value=result.processing_status,
        comment=comment,
        user_id=result.updated_by,
        source_module=LITERATURE_SOURCE_MODULE,
    )
    db.commit()
    db.refresh(result)
    return result


def literature_dashboard_stats(db: Session) -> dict[str, int]:
    plans = list_literature_plans(db)
    logs = list_literature_search_logs(db)
    results = list_literature_results(db)
    return {
        "active_plans": sum(1 for plan in plans if plan.status == "active"),
        "completed_searches": sum(1 for log in logs if log.status in {"completed", "reviewed", "closed"}),
        "open_results": sum(1 for result in results if result.processing_status not in {"closed", "processed"}),
        "action_required": sum(1 for result in results if result.pv_decision in LITERATURE_ACTION_DECISIONS),
    }


def export_literature_results_csv(results: list[LiteratureResult]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "period",
            "partner",
            "products",
            "source",
            "search_date",
            "search_strategy",
            "publications_found",
            "relevant_publications",
            "publication_title",
            "doi",
            "url",
            "result_type",
            "pv_decision",
            "processing_status",
            "comment",
            "responsible",
            "created_at",
        ]
    )
    for result in results:
        search_log = result.search_log
        plan = result.plan
        writer.writerow(
            [
                (
                    f"{search_log.period_start} - {search_log.period_end}"
                    if search_log
                    else ""
                ),
                result.partner.partner_name if result.partner else "",
                ", ".join(
                    link.product.product_name if link.product else link.product_id
                    for link in result.products
                ),
                search_log.search_source if search_log else result.journal_source or "",
                search_log.search_date if search_log else "",
                search_log.search_strategy if search_log else plan.search_strategy if plan else "",
                search_log.publications_found if search_log else "",
                search_log.relevant_publications if search_log else "",
                result.publication_title,
                result.doi or "",
                result.url or "",
                result.result_type,
                result.pv_decision,
                result.processing_status,
                result.specialist_comment or "",
                (
                    plan.responsible_user.full_name
                    if plan and plan.responsible_user
                    else ""
                ),
                result.created_at,
            ]
        )
    return output.getvalue()


def dashboard_stats(db: Session) -> schemas.DashboardStats:
    today = date.today()
    review_window_end = today + timedelta(days=60)
    task_closed_statuses = {"completed", "cancelled", "Completed", "Cancelled", "closed", "Closed"}
    reconciliation_work_statuses = {
        "draft",
        "sent",
        "response_received",
        "response received",
        "discrepancy_found",
        "discrepancy found",
    }
    incoming_review_statuses = {"new", "analyzed", "needs_review", "needs review"}
    total_safety_reports = db.query(SafetyReport).filter(SafetyReport.is_deleted.is_(False)).count()
    reports_awaiting_triage = (
        db.query(SafetyReport)
        .filter(
            SafetyReport.is_deleted.is_(False),
            SafetyReport.triage_status.in_(["new", "in_triage"]),
        )
        .count()
    )
    total_cases = db.query(Case).filter(Case.is_deleted.is_(False)).count()
    open_cases = (
        db.query(Case)
        .filter(
            Case.is_deleted.is_(False),
            Case.workflow_status.notin_(["submitted", "closed"]),
        )
        .count()
    )
    serious_cases = (
        db.query(Case)
        .filter(Case.is_deleted.is_(False), Case.seriousness == "serious")
        .count()
    )
    submissions_due = (
        db.query(Submission)
        .filter(
            Submission.is_deleted.is_(False),
            Submission.submission_status.in_(["planned", "ready"]),
            Submission.due_date.is_not(None),
        )
        .count()
    )
    overdue_submissions = (
        db.query(Submission)
        .filter(
            Submission.is_deleted.is_(False),
            Submission.submission_status.in_(["planned", "ready"]),
            Submission.due_date < today,
        )
        .count()
    )
    total_partners = db.query(Partner).filter(Partner.is_deleted.is_(False)).count()
    total_products = db.query(Product).filter(Product.is_deleted.is_(False)).count()
    active_pv_agreements = (
        db.query(Contract)
        .filter(
            Contract.is_deleted.is_(False),
            Contract.contract_type == "pharmacovigilance_agreement",
            Contract.contract_date <= today,
            Contract.valid_until >= today,
        )
        .count()
    )
    open_tasks = (
        db.query(Task)
        .filter(Task.is_deleted.is_(False), Task.status.notin_(list(task_closed_statuses)))
        .count()
    )
    overdue_tasks = (
        db.query(Task)
        .filter(
            Task.is_deleted.is_(False),
            Task.status.notin_(list(task_closed_statuses)),
            Task.due_date.is_not(None),
            Task.due_date < today,
        )
        .count()
    )
    reconciliations_in_progress = (
        db.query(PartnerReconciliation)
        .filter(
            PartnerReconciliation.is_deleted.is_(False),
            PartnerReconciliation.reconciliation_status.in_(list(reconciliation_work_statuses)),
        )
        .count()
    )
    incoming_requests_needing_review = (
        db.query(IncomingRequest)
        .filter(
            IncomingRequest.is_deleted.is_(False),
            IncomingRequest.status.in_(list(incoming_review_statuses)),
        )
        .count()
    )
    effective_sops = (
        db.query(SOP)
        .filter(SOP.is_deleted.is_(False), SOP.status == "Effective")
        .count()
    )
    sops_requiring_review = (
        db.query(SOP)
        .filter(SOP.is_deleted.is_(False), SOP.status == "Requires Review")
        .count()
    )
    sops_under_approval = (
        db.query(SOP)
        .filter(SOP.is_deleted.is_(False), SOP.status.in_(["Under Approval", "Under Review"]))
        .count()
    )
    sops_review_due_60_days = (
        db.query(SOP)
        .filter(
            SOP.is_deleted.is_(False),
            SOP.next_review_date.is_not(None),
            SOP.next_review_date >= today,
            SOP.next_review_date <= review_window_end,
            SOP.status.notin_(["Archived", "Cancelled"]),
        )
        .count()
    )
    return schemas.DashboardStats(
        total_safety_reports=total_safety_reports,
        reports_awaiting_triage=reports_awaiting_triage,
        total_cases=total_cases,
        open_cases=open_cases,
        serious_cases=serious_cases,
        submissions_due=submissions_due,
        overdue_submissions=overdue_submissions,
        total_partners=total_partners,
        total_products=total_products,
        active_pv_agreements=active_pv_agreements,
        open_tasks=open_tasks,
        overdue_tasks=overdue_tasks,
        reconciliations_in_progress=reconciliations_in_progress,
        incoming_requests_needing_review=incoming_requests_needing_review,
        effective_sops=effective_sops,
        sops_requiring_review=sops_requiring_review,
        sops_under_approval=sops_under_approval,
        sops_review_due_60_days=sops_review_due_60_days,
    )


def dashboard_upcoming_deadlines(db: Session) -> list[dict[str, Any]]:
    today = date.today()
    task_closed_statuses = {"completed", "cancelled", "Completed", "Cancelled", "closed", "Closed"}
    rows: list[dict[str, Any]] = []
    tasks = (
        db.query(Task)
        .options(joinedload(Task.assigned_to))
        .filter(
            Task.is_deleted.is_(False),
            Task.status.notin_(list(task_closed_statuses)),
            Task.due_date.is_not(None),
            Task.due_date >= today,
        )
        .order_by(Task.due_date.asc())
        .limit(6)
        .all()
    )
    for task in tasks:
        rows.append(
            {
                "date": task.due_date,
                "title": task.title,
                "type": "Task",
                "status": task.status,
                "owner": task.responsible_person
                or (task.assigned_to.full_name if task.assigned_to else ""),
                "url": "/tasks",
            }
        )

    submissions = (
        db.query(Submission)
        .filter(
            Submission.is_deleted.is_(False),
            Submission.submission_status.in_(["planned", "ready"]),
            Submission.due_date.is_not(None),
            Submission.due_date >= today,
        )
        .order_by(Submission.due_date.asc())
        .limit(4)
        .all()
    )
    for submission in submissions:
        rows.append(
            {
                "date": submission.due_date,
                "title": submission.submission_number,
                "type": "Submission",
                "status": submission.submission_status,
                "owner": "",
                "url": "/submissions",
            }
        )
    return sorted(rows, key=lambda row: row["date"])[:8]


def dashboard_overdue_tasks(db: Session) -> list[Task]:
    today = date.today()
    task_closed_statuses = {"completed", "cancelled", "Completed", "Cancelled", "closed", "Closed"}
    return (
        db.query(Task)
        .options(joinedload(Task.assigned_to))
        .filter(
            Task.is_deleted.is_(False),
            Task.status.notin_(list(task_closed_statuses)),
            Task.due_date.is_not(None),
            Task.due_date < today,
        )
        .order_by(Task.due_date.asc())
        .limit(8)
        .all()
    )


def dashboard_recent_changes(db: Session) -> list[AuditTrail]:
    return (
        db.query(AuditTrail)
        .options(joinedload(AuditTrail.user))
        .filter(AuditTrail.is_deleted.is_(False))
        .order_by(func.coalesce(AuditTrail.changed_at, AuditTrail.timestamp).desc())
        .limit(8)
        .all()
    )


def case_overview(case: Case) -> schemas.CaseOverview:
    return schemas.CaseOverview(
        case=case,
        patients=case.patients,
        products=case.case_products,
        reactions=case.reactions,
        followups=case.followups,
        submissions=case.submissions,
    )


CASE_DETAIL_EXPORT_HEADERS = [
    "received date",
    "country",
    "seriousness",
    "product",
    "dosage form",
    "active substance",
    "reaction",
    "MedDRA PT",
    "outcome",
    "workflow status",
]

GENERAL_CASE_EXPORT_HEADERS = [
    "full case code",
    "case number",
    "partner code",
    "partner name",
    "partner unassigned",
    "technical case id",
    "external partner case number",
    "additional case numbers",
    "C.1.1 sender case safety report identifier",
    "C.1.8.1 worldwide unique case identification number",
    "warning",
    *CASE_DETAIL_EXPORT_HEADERS,
]

PARTNER_CASE_EXPORT_HEADERS = [
    "case number",
    "external partner case number",
    "additional case numbers",
    *CASE_DETAIL_EXPORT_HEADERS,
]


def case_export_rows(
    db: Session,
    *,
    dosage_form_language: str = "ru",
    partner_id: str | None = None,
) -> list[list[Any]]:
    query = (
        db.query(Case)
        .options(
            joinedload(Case.numbering_partner),
            selectinload(Case.identifiers),
            selectinload(Case.case_products)
            .joinedload(CaseProduct.product)
            .joinedload(Product.dosage_form_ref),
            selectinload(Case.reactions),
        )
        .filter(Case.is_deleted.is_(False))
    )
    if partner_id:
        query = query.filter(Case.numbering_partner_id == partner_id)
    cases = query.order_by(Case.created_at.desc()).all()
    rows = []
    for case in cases:
        products = case.case_products or [None]
        reactions = case.reactions or [None]
        for product_row in products:
            for reaction in reactions:
                product_name = ""
                dosage_form = ""
                active_substance = ""
                if product_row:
                    product_name = product_row.reported_product_name or (
                        product_row.product.product_name if product_row.product else ""
                    )
                    dosage_form = product_dosage_form_name(
                        product_row.product,
                        dosage_form_language,
                    )
                    active_substance = product_row.active_substance_text or ""
                detail_values = [
                    case.initial_received_date,
                    case.country_of_occurrence,
                    case.seriousness,
                    product_name,
                    dosage_form,
                    active_substance,
                    reaction.reported_term if reaction else "",
                    reaction.meddra_pt_name or reaction.meddra_pt_code if reaction else "",
                    reaction.outcome if reaction else "",
                    case.workflow_status,
                ]
                if partner_id:
                    rows.append(
                        [
                            case.case_number,
                            case.primary_external_identifier or "",
                            case.additional_case_numbers,
                            *detail_values,
                        ]
                    )
                else:
                    rows.append(
                        [
                            case.full_case_code,
                            case.case_number,
                            case.numbering_partner_code,
                            case.numbering_partner.partner_name if case.numbering_partner else "",
                            "Да" if case.number_is_provisional else "Нет",
                            case.id,
                            case.primary_external_identifier or "",
                            case.additional_case_numbers,
                            case.local_sender_case_id or "",
                            case.worldwide_case_id or "",
                            case.numbering_warning or "",
                            *detail_values,
                        ]
                    )
    return rows


def export_cases_csv(
    db: Session,
    *,
    dosage_form_language: str = "ru",
    partner_id: str | None = None,
) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(PARTNER_CASE_EXPORT_HEADERS if partner_id else GENERAL_CASE_EXPORT_HEADERS)
    writer.writerows(
        case_export_rows(
            db,
            dosage_form_language=dosage_form_language,
            partner_id=partner_id,
        )
    )
    return output.getvalue()


def export_cases_xlsx(
    db: Session,
    *,
    dosage_form_language: str = "ru",
    partner_id: str | None = None,
) -> bytes:
    return build_xlsx(
        [
            (
                "ICSR cases",
                [
                    PARTNER_CASE_EXPORT_HEADERS if partner_id else GENERAL_CASE_EXPORT_HEADERS,
                    *case_export_rows(
                        db,
                        dosage_form_language=dosage_form_language,
                        partner_id=partner_id,
                    ),
                ],
            )
        ]
    )
