import csv
import io
import json
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app import schemas
from app.audit import log_audit
from app.auth import get_request_user_id
from app.models import (
    AuditTrail,
    Attachment,
    Case,
    CaseProduct,
    Contract,
    ContractContact,
    FollowUp,
    IncomingRequest,
    Partner,
    PartnerReconciliation,
    PartnerReconciliationItem,
    Patient,
    Permission,
    Product,
    ProductSubstance,
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
    SOP,
    Submission,
    Substance,
    Task,
    User,
    UserRole,
    utcnow,
)
from app.rbac import LEGACY_ROLE_MAP


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


def create_user(db: Session, email: str, full_name: str, role: str = "viewer") -> User:
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        if not existing.user_roles:
            role_code = LEGACY_ROLE_MAP.get(role, "readonly_auditor")
            role_record = get_role_by_code(db, role_code)
            if role_record:
                db.add(UserRole(user_id=existing.id, role_id=role_record.id))
                db.commit()
                db.refresh(existing)
        return existing
    user = User(email=email, full_name=full_name, role=role)
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
        joinedload(User.user_roles).joinedload(UserRole.role)
    )
    if not include_deleted:
        query = query.filter(User.is_deleted.is_(False))
    return query.order_by(User.full_name, User.email).all()


def get_user(db: Session, user_id: str) -> User | None:
    return (
        db.query(User)
        .options(joinedload(User.user_roles).joinedload(UserRole.role))
        .filter(User.id == user_id, User.is_deleted.is_(False))
        .first()
    )


def create_app_user(
    db: Session,
    *,
    email: str,
    full_name: str | None,
    role_ids: list[str],
    created_by_user_id: str | None = None,
) -> User:
    user = User(email=email, full_name=full_name, role="viewer")
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
        data={"email": email, "full_name": full_name, "roles": ", ".join(role_ids)},
        user_id=created_by_user_id,
    )
    db.commit()
    db.refresh(user)
    return user


def update_app_user(
    db: Session,
    user: User,
    *,
    email: str,
    full_name: str | None,
    changed_by_user_id: str | None = None,
) -> User:
    old_values = {"email": user.email, "full_name": user.full_name}
    user.email = email
    user.full_name = full_name
    user.version += 1
    log_field_changes(
        db,
        entity_type="User",
        entity_id=user.id,
        old_values=old_values,
        new_values={"email": user.email, "full_name": user.full_name},
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
            joinedload(Role.role_permissions).joinedload(RolePermission.permission),
            joinedload(Role.user_roles),
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
    return db.query(Partner).filter(Partner.is_deleted.is_(False)).order_by(Partner.partner_name).all()


def get_partner(db: Session, partner_id: str) -> Partner | None:
    return db.query(Partner).filter(Partner.id == partner_id, Partner.is_deleted.is_(False)).first()


def create_partner(db: Session, payload: schemas.PartnerCreate) -> Partner:
    data = clean_form_data(model_data(payload))
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
    return db.query(Substance).filter(Substance.id == substance_id).first()


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
        .filter(Substance.substance_name_normalized == normalized)
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


def list_products(db: Session) -> list[Product]:
    return (
        db.query(Product)
        .options(
            joinedload(Product.mah_partner),
            joinedload(Product.substance_links).joinedload(ProductSubstance.substance),
        )
        .filter(Product.is_deleted.is_(False))
        .order_by(Product.product_name)
        .all()
    )


def get_product(db: Session, product_id: str) -> Product | None:
    return db.query(Product).filter(Product.id == product_id).first()


def create_product(db: Session, payload: schemas.ProductCreate) -> Product:
    data = clean_form_data(model_data(payload))
    active_substance = data.pop("active_substance", None)
    data["product_name_normalized"] = data.get("product_name_normalized") or normalize_text(
        data.get("product_name")
    )
    actor_id = audit_user_id(db)
    product = Product(**data, created_by=actor_id, updated_by=actor_id)
    db.add(product)
    db.flush()

    if active_substance:
        substance = get_or_create_substance(db, active_substance)
        link = ProductSubstance(
            product_id=product.id,
            substance_id=substance.id,
            substance_role="active",
            is_primary=True,
        )
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
    if active_substance:
        audit_data["active_substance"] = active_substance
    log_create_event(
        db,
        entity_type="Product",
        entity_id=product.id,
        data=audit_data,
    )
    db.commit()
    db.refresh(product)
    return product


def update_product(db: Session, product: Product, payload: schemas.ProductCreate) -> Product:
    data = clean_form_data(model_data(payload))
    data.pop("active_substance", None)
    data["product_name_normalized"] = data.get("product_name_normalized") or normalize_text(
        data.get("product_name")
    )
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
    db.commit()
    db.refresh(product)
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


def list_contracts(db: Session) -> list[Contract]:
    return (
        db.query(Contract)
        .options(joinedload(Contract.partner), joinedload(Contract.product))
        .filter(Contract.is_deleted.is_(False))
        .order_by(Contract.valid_until.desc(), Contract.contract_date.desc())
        .all()
    )


def get_contract(db: Session, contract_id: str) -> Contract | None:
    return (
        db.query(Contract)
        .options(joinedload(Contract.partner), joinedload(Contract.product))
        .filter(Contract.id == contract_id, Contract.is_deleted.is_(False))
        .first()
    )


def create_contract(db: Session, payload: schemas.ContractCreate) -> Contract:
    data = clean_form_data(model_data(payload))
    existing = (
        db.query(Contract)
        .filter(
            Contract.partner_id == data.get("partner_id"),
            Contract.product_id == data.get("product_id"),
            Contract.is_deleted.is_(False),
        )
        .first()
    )
    if existing:
        raise ValueError("Contract already exists for this partner and product.")
    actor_id = audit_user_id(db)
    contract = Contract(**data, created_by=actor_id, updated_by=actor_id)
    db.add(contract)
    db.flush()
    log_create_event(
        db,
        entity_type="Contract",
        entity_id=contract.id,
        data=data,
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
    existing = (
        db.query(ContractContact)
        .filter(
            ContractContact.partner_id == data.get("partner_id"),
            func.lower(ContractContact.email) == (data.get("email") or "").lower(),
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
    existing = (
        db.query(ContractContact)
        .filter(
            ContractContact.id != contact.id,
            ContractContact.partner_id == data.get("partner_id"),
            func.lower(ContractContact.email) == (data.get("email") or "").lower(),
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
            joinedload(PartnerReconciliation.items).joinedload(PartnerReconciliationItem.internal_case),
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
) -> PartnerReconciliation:
    data = clean_form_data(model_data(payload))
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
    db.commit()
    db.refresh(reconciliation)
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
    reconciliation.reconciliation_status = "closed"
    reconciliation.confirmed_by_user = confirmed_by_user
    reconciliation.confirmed_at = utcnow()
    reconciliation.updated_by = audit_user_id(db)
    reconciliation.version += 1
    for item in reconciliation.items:
        if item.reconciliation_status == "matched":
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
    changed_by_user_id: str | None = None,
) -> PartnerReconciliation:
    old_values = {
        "reconciliation_status": reconciliation.reconciliation_status,
        "sent_date": reconciliation.sent_date,
        "response_date": reconciliation.response_date,
        "discrepancy_description": reconciliation.discrepancy_description,
        "document_id": reconciliation.document_id,
        "products": reconciliation.products,
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
        },
        action="status_change",
        user_id=reconciliation.updated_by,
        comment=reconciliation.discrepancy_description,
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


def list_safety_reports(db: Session) -> list[SafetyReport]:
    return (
        db.query(SafetyReport)
        .options(joinedload(SafetyReport.partner))
        .filter(SafetyReport.is_deleted.is_(False))
        .order_by(SafetyReport.received_at.desc())
        .all()
    )


def get_safety_report(db: Session, report_id: str) -> SafetyReport | None:
    return (
        db.query(SafetyReport)
        .options(
            joinedload(SafetyReport.partner),
            joinedload(SafetyReport.case),
        )
        .filter(SafetyReport.id == report_id)
        .first()
    )


def create_safety_report(db: Session, payload: schemas.SafetyReportCreate) -> SafetyReport:
    data = clean_form_data(model_data(payload))
    data["safety_report_number"] = data.get("safety_report_number") or generate_number(
        db,
        SafetyReport,
        "safety_report_number",
        "SR",
    )
    data["received_at"] = data.get("received_at") or utcnow()
    data["received_date"] = data.get("received_date") or date.today()
    report = SafetyReport(**data)
    db.add(report)
    db.flush()
    log_create_event(
        db,
        entity_type="SafetyReport",
        entity_id=report.id,
        data=data,
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


def list_cases(db: Session) -> list[Case]:
    return (
        db.query(Case)
        .options(
            joinedload(Case.partner),
            joinedload(Case.case_products).joinedload(CaseProduct.product),
            joinedload(Case.reactions),
        )
        .filter(Case.is_deleted.is_(False))
        .order_by(Case.created_at.desc())
        .all()
    )


def get_case(db: Session, case_id: str) -> Case | None:
    return (
        db.query(Case)
        .options(
            joinedload(Case.partner),
            joinedload(Case.safety_report),
            joinedload(Case.patients),
            joinedload(Case.case_products).joinedload(CaseProduct.product),
            joinedload(Case.reactions),
            joinedload(Case.followups),
            joinedload(Case.submissions).joinedload(Submission.recipient_partner),
            joinedload(Case.audit_entries).joinedload(AuditTrail.user),
        )
        .filter(Case.id == case_id)
        .first()
    )


def create_case(db: Session, payload: schemas.CaseCreate) -> Case:
    data = clean_form_data(model_data(payload))
    data["case_number"] = data.get("case_number") or generate_number(db, Case, "case_number", "CASE")
    case = Case(**data)
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
    )
    db.commit()
    db.refresh(case)
    return case


def create_case_from_report(db: Session, report: SafetyReport) -> Case:
    if report.case:
        return report.case

    case = Case(
        case_number=generate_number(db, Case, "case_number", "CASE"),
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
    db.add(case)
    db.flush()
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
    next_status = payload.workflow_status
    next_status_code = normalized_status(next_status)
    if case_is_controlled(case) and next_status_code != "reopened":
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
    case.workflow_status = next_status
    apply_case_lock_state(case, next_status, actor_id)
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


def list_attachments(db: Session) -> list[Attachment]:
    return (
        db.query(Attachment)
        .options(
            joinedload(Attachment.case).joinedload(Case.partner),
            joinedload(Attachment.case).joinedload(Case.case_products).joinedload(CaseProduct.product),
            joinedload(Attachment.safety_report).joinedload(SafetyReport.partner),
            joinedload(Attachment.partner),
            joinedload(Attachment.product),
            joinedload(Attachment.uploaded_by),
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
            joinedload(Attachment.case).joinedload(Case.case_products).joinedload(CaseProduct.product),
            joinedload(Attachment.safety_report).joinedload(SafetyReport.partner),
            joinedload(Attachment.partner),
            joinedload(Attachment.product),
            joinedload(Attachment.uploaded_by),
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
    status: str | None = None,
    comment: str | None = None,
    checksum_sha256: str | None = None,
    uploaded_by_user_id: str | None = None,
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
        status=status or "draft",
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
            "status": status or "draft",
            "comment": comment,
            "checksum_sha256": checksum_sha256,
        },
        user_id=actor_id,
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
    return db.query(Submission).filter(Submission.id == submission_id).first()


def create_submission(db: Session, payload: schemas.SubmissionCreate) -> Submission:
    data = clean_form_data(model_data(payload))
    validate_submission_references(data)
    if data.get("case_id"):
        case = get_case(db, data["case_id"])
        if not case:
            raise ValueError("Selected case was not found.")
        assert_case_editable(case)
    data["submission_number"] = data.get("submission_number") or generate_number(
        db,
        Submission,
        "submission_number",
        "SUB",
    )
    if data.get("recipient_partner_id") and not data.get("recipient_country_code"):
        partner = get_partner(db, data["recipient_partner_id"])
        data["recipient_country_code"] = partner.country_code if partner else None
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
    return create_submission(db, schemas.SubmissionCreate(**data))


def update_submission_status(
    db: Session,
    submission: Submission,
    payload: schemas.SubmissionStatusUpdate,
) -> Submission:
    old_status = submission.submission_status
    submission.submission_status = payload.submission_status
    submission.error_message = payload.error_message
    if submission.submission_status == "submitted" and not submission.submitted_at:
        submission.submitted_at = utcnow()
    log_audit(
        db,
        entity_type="Submission",
        entity_id=submission.id,
        action="status_change",
        case_id=submission.case_id,
        field_name="submission_status",
        old_value=old_status,
        new_value=submission.submission_status,
        comment=payload.error_message,
        user_id=audit_user_id(db),
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
            joinedload(PSURPlan.product),
            joinedload(PSURPlan.responsible_user),
            joinedload(PSURPlan.reviewer_user),
            joinedload(PSURPlan.psur_products).joinedload(PSURProduct.product),
            joinedload(PSURPlan.psur_cases).joinedload(PSURCase.case),
            joinedload(PSURPlan.partner_requests).joinedload(PSURPartnerRequest.partner),
            joinedload(PSURPlan.sections),
            joinedload(PSURPlan.documents),
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
            joinedload(PSURPlan.product),
            joinedload(PSURPlan.responsible_user),
            joinedload(PSURPlan.reviewer_user),
            joinedload(PSURPlan.psur_products).joinedload(PSURProduct.product),
            joinedload(PSURPlan.psur_cases).joinedload(PSURCase.case).joinedload(Case.partner),
            joinedload(PSURPlan.psur_cases)
            .joinedload(PSURCase.case)
            .joinedload(Case.case_products)
            .joinedload(CaseProduct.product),
            joinedload(PSURPlan.psur_cases)
            .joinedload(PSURCase.case)
            .joinedload(Case.reactions),
            joinedload(PSURPlan.partner_requests).joinedload(PSURPartnerRequest.partner),
            joinedload(PSURPlan.partner_requests).joinedload(PSURPartnerRequest.contact_person),
            joinedload(PSURPlan.partner_requests).joinedload(PSURPartnerRequest.document),
            joinedload(PSURPlan.sections).joinedload(PSURSection.assignee),
            joinedload(PSURPlan.sections).joinedload(PSURSection.reviewer),
            joinedload(PSURPlan.sections).joinedload(PSURSection.confirmer),
            joinedload(PSURPlan.documents).joinedload(PSURDocument.uploader),
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


def add_psur_product(
    db: Session,
    plan: PSURPlan,
    payload: schemas.PSURProductCreate,
) -> PSURProduct:
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
    db.commit()
    db.refresh(task)
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


def list_incoming_requests(db: Session) -> list[IncomingRequest]:
    return (
        db.query(IncomingRequest)
        .options(
            joinedload(IncomingRequest.partner),
            joinedload(IncomingRequest.product),
            joinedload(IncomingRequest.creator),
            joinedload(IncomingRequest.updater),
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
            joinedload(IncomingRequest.product),
            joinedload(IncomingRequest.creator),
            joinedload(IncomingRequest.updater),
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
    status_value = data.pop("status", None) or "confirmed"
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
    log_create_event(
        db,
        entity_type="IncomingRequest",
        entity_id=row.id,
        data={
            "request_type": row.request_type,
            "partner_id": row.partner_id,
            "product_id": row.product_id,
            "possible_icsr": row.possible_icsr,
            "status": row.status,
        },
        user_id=actor_id,
        comment="Human confirmed GPT/mock analysis.",
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


def list_sops(db: Session) -> list[SOP]:
    return (
        db.query(SOP)
        .options(joinedload(SOP.creator), joinedload(SOP.updater))
        .filter(SOP.is_deleted.is_(False))
        .order_by(SOP.sop_code.asc())
        .all()
    )


def get_sop(db: Session, sop_id: str) -> SOP | None:
    return (
        db.query(SOP)
        .options(joinedload(SOP.creator), joinedload(SOP.updater))
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
) -> SOP:
    data = clean_form_data(model_data(payload))
    old_values = {field: getattr(sop, field, None) for field in data}
    for field, value in data.items():
        setattr(sop, field, value)
    sop.updated_by = changed_by_user_id or audit_user_id(db)
    log_field_changes(
        db,
        entity_type="SOP",
        entity_id=sop.id,
        old_values=old_values,
        new_values=data,
        user_id=sop.updated_by,
        comment=sop.revision_reason,
    )
    db.commit()
    db.refresh(sop)
    return sop


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
) -> PSURPartnerRequest:
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
    db.commit()
    db.refresh(request)
    return request


def update_psur_partner_request(
    db: Session,
    request: PSURPartnerRequest,
    payload: schemas.PSURPartnerRequestCreate,
    *,
    changed_by_user_id: str | None = None,
) -> PSURPartnerRequest:
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
    for partner_id in partner_ids:
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
            )
        )
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
    included_cases = [row for row in plan.psur_cases if row.case_included and row.case]
    serious_cases = [row for row in included_cases if row.seriousness == "serious"]
    products = [
        row.product.product_name
        for row in plan.psur_products
        if row.included_in_report and row.product
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


def export_psur_cases_csv(plan: PSURPlan) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "case_number",
            "received_date",
            "seriousness",
            "listedness",
            "case_origin",
            "included",
            "reason_excluded",
            "assessment_comment",
        ]
    )
    for row in plan.psur_cases:
        case = row.case
        writer.writerow(
            [
                case.case_number if case else row.case_id,
                (case.latest_received_date or case.initial_received_date) if case else "",
                row.seriousness or (case.seriousness if case else ""),
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


def export_psur_summary_rtf(plan: PSURPlan) -> str:
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


def export_cases_csv(db: Session) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "case number",
            "received date",
            "country",
            "seriousness",
            "product",
            "active substance",
            "reaction",
            "MedDRA PT",
            "outcome",
            "workflow status",
        ]
    )

    cases = (
        db.query(Case)
        .options(
            joinedload(Case.case_products).joinedload(CaseProduct.product),
            joinedload(Case.reactions),
        )
        .filter(Case.is_deleted.is_(False))
        .order_by(Case.created_at.desc())
        .all()
    )
    for case in cases:
        products = case.case_products or [None]
        reactions = case.reactions or [None]
        for product_row in products:
            for reaction in reactions:
                product_name = ""
                active_substance = ""
                if product_row:
                    product_name = product_row.reported_product_name or (
                        product_row.product.product_name if product_row.product else ""
                    )
                    active_substance = product_row.active_substance_text or ""
                writer.writerow(
                    [
                        case.case_number,
                        case.initial_received_date,
                        case.country_of_occurrence,
                        case.seriousness,
                        product_name,
                        active_substance,
                        reaction.reported_term if reaction else "",
                        reaction.meddra_pt_name or reaction.meddra_pt_code if reaction else "",
                        reaction.outcome if reaction else "",
                        case.workflow_status,
                    ]
                )
    return output.getvalue()
