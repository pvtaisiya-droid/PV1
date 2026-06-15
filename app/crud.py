import csv
import io
from datetime import date
from typing import Any

from sqlalchemy.orm import Session, joinedload

from app import schemas
from app.audit import log_audit
from app.models import (
    AuditTrail,
    Attachment,
    Case,
    CaseProduct,
    Contract,
    ContractContact,
    FollowUp,
    Partner,
    PartnerReconciliation,
    PartnerReconciliationItem,
    Patient,
    Permission,
    Product,
    ProductSubstance,
    Reaction,
    Role,
    RolePermission,
    SafetyReport,
    Submission,
    Substance,
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


def get_current_user(db: Session) -> User | None:
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
    log_audit(
        db,
        entity_type="User",
        entity_id=user.id,
        action="create",
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
    old_value = f"{user.email} / {user.full_name or ''}"
    user.email = email
    user.full_name = full_name
    user.version += 1
    log_audit(
        db,
        entity_type="User",
        entity_id=user.id,
        action="edit",
        field_name="profile",
        old_value=old_value,
        new_value=f"{user.email} / {user.full_name or ''}",
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
    log_audit(
        db,
        entity_type="User",
        entity_id=user.id,
        action="soft_delete",
        field_name="is_deleted",
        old_value=False,
        new_value=True,
        change_reason=delete_reason,
        user_id=deleted_by_user_id,
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
    partner = Partner(**data)
    db.add(partner)
    db.flush()
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="Partner",
        entity_id=partner.id,
        action="create",
        user_id=user.id if user else None,
    )
    db.commit()
    db.refresh(partner)
    return partner


def update_partner(db: Session, partner: Partner, payload: schemas.PartnerCreate) -> Partner:
    data = clean_form_data(model_data(payload))
    for field, value in data.items():
        setattr(partner, field, value)
    partner.version += 1
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="Partner",
        entity_id=partner.id,
        action="edit",
        user_id=user.id if user else None,
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
    log_audit(
        db,
        entity_type="Partner",
        entity_id=partner.id,
        action="soft_delete",
        change_reason=delete_reason,
        user_id=deleted_by_user_id,
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
    log_audit(
        db,
        entity_type="Substance",
        entity_id=substance.id,
        action="create",
        user_id=(get_current_user(db).id if get_current_user(db) else None),
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
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="Substance",
        entity_id=substance.id,
        action="create",
        user_id=user.id if user else None,
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
    product = Product(**data)
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

    user = get_current_user(db)
    log_audit(
        db,
        entity_type="Product",
        entity_id=product.id,
        action="create",
        user_id=user.id if user else None,
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
    for field, value in data.items():
        setattr(product, field, value)
    product.version += 1
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="Product",
        entity_id=product.id,
        action="edit",
        user_id=user.id if user else None,
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
    log_audit(
        db,
        entity_type="Product",
        entity_id=product.id,
        action="soft_delete",
        change_reason=delete_reason,
        user_id=deleted_by_user_id,
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
    contract = Contract(**clean_form_data(model_data(payload)))
    db.add(contract)
    db.flush()
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="Contract",
        entity_id=contract.id,
        action="create",
        user_id=user.id if user else None,
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
    contact = ContractContact(**clean_form_data(model_data(payload)))
    db.add(contact)
    db.flush()
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="ContractContact",
        entity_id=contact.id,
        action="create",
        user_id=user.id if user else None,
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
    for field, value in data.items():
        setattr(contact, field, value)
    contact.version += 1
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="ContractContact",
        entity_id=contact.id,
        action="edit",
        user_id=user.id if user else None,
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
    log_audit(
        db,
        entity_type="ContractContact",
        entity_id=contact.id,
        action="soft_delete",
        change_reason=delete_reason,
        user_id=deleted_by_user_id,
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

    reconciliation = PartnerReconciliation(
        **data,
        reconciliation_date=reconciliation_date,
        reconciliation_status="draft",
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

    user = get_current_user(db)
    log_audit(
        db,
        entity_type="PartnerReconciliation",
        entity_id=reconciliation.id,
        action="create",
        user_id=user.id if user else None,
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
    if "internal_case_id" in data:
        item.internal_case_id = data.get("internal_case_id")
    item.reconciliation_status = data["reconciliation_status"]
    item.reviewer_comment = data.get("reviewer_comment")
    item.confirmed_by_user = data.get("confirmed_by_user")
    item.version += 1
    log_audit(
        db,
        entity_type="PartnerReconciliationItem",
        entity_id=item.id,
        action="status_change",
        field_name="reconciliation_status",
        new_value=item.reconciliation_status,
        user_id=(get_current_user(db).id if get_current_user(db) else None),
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
    reconciliation.reconciliation_status = "confirmed"
    reconciliation.confirmed_by_user = confirmed_by_user
    reconciliation.confirmed_at = utcnow()
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
        user_id=(get_current_user(db).id if get_current_user(db) else None),
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
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="ProductSubstance",
        entity_id=link.id,
        action="create",
        user_id=user.id if user else None,
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
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="SafetyReport",
        entity_id=report.id,
        action="create",
        user_id=user.id if user else None,
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
    user = get_current_user(db)
    report.triaged_by_user_id = user.id if user else None
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
        user_id=user.id if user else None,
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

    user = get_current_user(db)
    log_audit(
        db,
        entity_type="Case",
        entity_id=case.id,
        action="create",
        case_id=case.id,
        user_id=user.id if user else None,
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
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="Case",
        entity_id=case.id,
        action="create",
        case_id=case.id,
        user_id=user.id if user else None,
        change_reason="Created from safety report",
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
        user_id=user.id if user else None,
    )
    db.commit()
    db.refresh(case)
    return case


def update_case_status(db: Session, case: Case, payload: schemas.CaseStatusUpdate) -> Case:
    old_status = case.workflow_status
    case.workflow_status = payload.workflow_status
    case.version += 1
    user = get_current_user(db)
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
        user_id=user.id if user else None,
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
    case.is_deleted = True
    case.is_active = False
    case.deleted_at = utcnow()
    case.deleted_by = deleted_by_user_id
    case.delete_reason = delete_reason
    case.version += 1
    if case.safety_report:
        case.safety_report.case_id = None
    log_audit(
        db,
        entity_type="Case",
        entity_id=case.id,
        action="soft_delete",
        case_id=case.id,
        change_reason=delete_reason,
        user_id=deleted_by_user_id,
    )
    db.commit()
    db.refresh(case)
    return case


def add_patient(db: Session, case: Case, payload: schemas.PatientCreate) -> Patient:
    patient = Patient(case_id=case.id, **clean_form_data(model_data(payload)))
    db.add(patient)
    db.flush()
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="Patient",
        entity_id=patient.id,
        action="create",
        case_id=case.id,
        user_id=user.id if user else None,
    )
    db.commit()
    db.refresh(patient)
    return patient


def add_case_product(db: Session, case: Case, payload: schemas.CaseProductCreate) -> CaseProduct:
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
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="CaseProduct",
        entity_id=case_product.id,
        action="create",
        case_id=case.id,
        user_id=user.id if user else None,
    )
    db.commit()
    db.refresh(case_product)
    return case_product


def add_reaction(db: Session, case: Case, payload: schemas.ReactionCreate) -> Reaction:
    reaction = Reaction(case_id=case.id, **clean_form_data(model_data(payload)))
    db.add(reaction)
    if reaction.is_serious:
        case.seriousness = "serious"
    db.flush()
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="Reaction",
        entity_id=reaction.id,
        action="create",
        case_id=case.id,
        user_id=user.id if user else None,
    )
    db.commit()
    db.refresh(reaction)
    return reaction


def add_followup(db: Session, case: Case, payload: schemas.FollowUpCreate) -> FollowUp:
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
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="FollowUp",
        entity_id=followup.id,
        action="create",
        case_id=case.id,
        user_id=user.id if user else None,
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
    mime_type: str | None = None,
    storage_path: str | None = None,
    uploaded_by_user_id: str | None = None,
) -> Attachment:
    attachment = Attachment(
        file_name=file_name,
        attachment_type=attachment_type,
        case_id=case_id,
        safety_report_id=safety_report_id,
        mime_type=mime_type,
        storage_path=storage_path,
        uploaded_by_user_id=uploaded_by_user_id,
        uploaded_at=utcnow(),
    )
    db.add(attachment)
    db.flush()
    log_audit(
        db,
        entity_type="Attachment",
        entity_id=attachment.id,
        action="create",
        case_id=case_id,
        user_id=uploaded_by_user_id,
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
    log_audit(
        db,
        entity_type="Attachment",
        entity_id=attachment.id,
        action="soft_delete",
        case_id=attachment.case_id,
        change_reason=delete_reason,
        user_id=deleted_by_user_id,
    )
    db.commit()
    db.refresh(attachment)
    return attachment


def list_audit_entries(db: Session) -> list[AuditTrail]:
    return (
        db.query(AuditTrail)
        .options(joinedload(AuditTrail.user), joinedload(AuditTrail.case))
        .filter(AuditTrail.is_deleted.is_(False))
        .order_by(AuditTrail.timestamp.desc())
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
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="Submission",
        entity_id=submission.id,
        action="create",
        case_id=submission.case_id,
        user_id=user.id if user else None,
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
    user = get_current_user(db)
    log_audit(
        db,
        entity_type="Submission",
        entity_id=submission.id,
        action="status_change",
        case_id=submission.case_id,
        field_name="submission_status",
        old_value=old_status,
        new_value=submission.submission_status,
        user_id=user.id if user else None,
    )
    db.commit()
    db.refresh(submission)
    return submission


def dashboard_stats(db: Session) -> schemas.DashboardStats:
    today = date.today()
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
    return schemas.DashboardStats(
        total_safety_reports=total_safety_reports,
        reports_awaiting_triage=reports_awaiting_triage,
        total_cases=total_cases,
        open_cases=open_cases,
        serious_cases=serious_cases,
        submissions_due=submissions_due,
        overdue_submissions=overdue_submissions,
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
