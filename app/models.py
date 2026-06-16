import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid.uuid4())


class CommonMixin:
    id = Column(String(36), primary_key=True, default=new_uuid)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )
    is_active = Column(Boolean, default=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(String(36), nullable=True)
    delete_reason = Column(Text, nullable=True)
    version = Column(Integer, default=1, nullable=False)


class Permission(CommonMixin, Base):
    __tablename__ = "tblPermissions"

    permission_code = Column(String(100), unique=True, index=True, nullable=False)
    permission_name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    role_permissions = relationship(
        "RolePermission",
        back_populates="permission",
        cascade="all, delete-orphan",
    )


class Role(CommonMixin, Base):
    __tablename__ = "tblRoles"

    role_code = Column(String(100), unique=True, index=True, nullable=False)
    role_name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    is_system = Column(Boolean, default=True, nullable=False)

    user_roles = relationship(
        "UserRole",
        back_populates="role",
        cascade="all, delete-orphan",
    )
    role_permissions = relationship(
        "RolePermission",
        back_populates="role",
        cascade="all, delete-orphan",
    )


class User(CommonMixin, Base):
    __tablename__ = "tblUsers"

    email = Column(String(255), unique=True, index=True, nullable=False)
    full_name = Column(String(255), nullable=True)
    role = Column(String(50), default="viewer", nullable=False, index=True)

    assigned_cases = relationship(
        "Case",
        foreign_keys="Case.assigned_to_user_id",
        back_populates="assigned_to",
    )
    audit_entries = relationship("AuditTrail", back_populates="user")
    user_roles = relationship(
        "UserRole",
        foreign_keys="UserRole.user_id",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    assigned_user_roles = relationship(
        "UserRole",
        foreign_keys="UserRole.assigned_by_user_id",
        back_populates="assigned_by",
    )
    responsible_psur_plans = relationship(
        "PSURPlan",
        foreign_keys="PSURPlan.responsible_user_id",
        back_populates="responsible_user",
    )
    reviewer_psur_plans = relationship(
        "PSURPlan",
        foreign_keys="PSURPlan.reviewer_user_id",
        back_populates="reviewer_user",
    )
    assigned_tasks = relationship(
        "Task",
        foreign_keys="Task.assigned_to_user_id",
        back_populates="assigned_to",
    )


class UserRole(CommonMixin, Base):
    __tablename__ = "tblUserRoles"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_user_role"),
        Index("ix_user_role_user", "user_id"),
        Index("ix_user_role_role", "role_id"),
    )

    user_id = Column(String(36), ForeignKey("tblUsers.id"), nullable=False)
    role_id = Column(String(36), ForeignKey("tblRoles.id"), nullable=False)
    assigned_by_user_id = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    assigned_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    user = relationship("User", foreign_keys=[user_id], back_populates="user_roles")
    role = relationship("Role", back_populates="user_roles")
    assigned_by = relationship(
        "User",
        foreign_keys=[assigned_by_user_id],
        back_populates="assigned_user_roles",
    )


class RolePermission(CommonMixin, Base):
    __tablename__ = "tblRolePermissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),
        Index("ix_role_permission_role", "role_id"),
        Index("ix_role_permission_permission", "permission_id"),
    )

    role_id = Column(String(36), ForeignKey("tblRoles.id"), nullable=False)
    permission_id = Column(String(36), ForeignKey("tblPermissions.id"), nullable=False)

    role = relationship("Role", back_populates="role_permissions")
    permission = relationship("Permission", back_populates="role_permissions")


class Partner(CommonMixin, Base):
    __tablename__ = "tblPartners"

    partner_code = Column(String(50), unique=True, index=True, nullable=False)
    partner_name = Column(String(255), index=True, nullable=False)
    partner_type = Column(String(50), default="fn", index=True, nullable=False)
    reconciliation_frequency = Column(
        String(50),
        default="not_conducted",
        nullable=False,
    )
    country_code = Column(String(2), index=True, nullable=True)
    region = Column(String(100), nullable=True)
    email = Column(String(255), nullable=True)
    phone = Column(String(100), nullable=True)
    address = Column(Text, nullable=True)
    pv_responsible_person = Column(String(255), nullable=True)
    sdea_required = Column(Boolean, default=False, nullable=False)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    updated_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    safety_reports = relationship("SafetyReport", back_populates="partner")
    cases = relationship("Case", back_populates="partner")
    products = relationship("Product", back_populates="mah_partner")
    contracts = relationship("Contract", back_populates="partner")
    contract_contacts = relationship("ContractContact", back_populates="partner")
    reconciliations = relationship("PartnerReconciliation", back_populates="partner")
    submissions = relationship("Submission", back_populates="recipient_partner")
    psur_partner_requests = relationship("PSURPartnerRequest", back_populates="partner")
    psmf_components = relationship("PSMFComponent", back_populates="partner")
    documents = relationship(
        "Attachment",
        foreign_keys="Attachment.partner_id",
        back_populates="partner",
    )
    incoming_requests = relationship("IncomingRequest", back_populates="partner")
    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])


class Substance(CommonMixin, Base):
    __tablename__ = "tblSubstances"
    __table_args__ = (
        Index("ix_substance_normalized", "substance_name_normalized"),
        Index("ix_substance_inn", "inn_name"),
        Index("ix_substance_atc", "atc_code"),
        Index("ix_substance_cas", "cas_number"),
    )

    substance_name = Column(String(255), index=True, nullable=False)
    substance_name_normalized = Column(String(255), nullable=True)
    inn_name = Column(String(255), nullable=True)
    cas_number = Column(String(100), nullable=True)
    atc_code = Column(String(50), nullable=True)
    substance_type = Column(String(50), nullable=True)

    product_links = relationship("ProductSubstance", back_populates="substance")
    psur_plans = relationship("PSURPlan", back_populates="active_substance")


class Product(CommonMixin, Base):
    __tablename__ = "tblProducts"
    __table_args__ = (
        Index("ix_product_name_normalized", "product_name_normalized"),
        Index("ix_product_authorization", "authorization_number"),
        Index("ix_product_auth_country", "authorization_country_code"),
        Index("ix_product_company", "is_company_product"),
    )

    product_code = Column(String(50), unique=True, index=True, nullable=False)
    product_name = Column(String(255), index=True, nullable=False)
    product_name_normalized = Column(String(255), nullable=True)
    dosage_form = Column(String(100), nullable=True)
    strength = Column(String(100), nullable=True)
    route = Column(String(100), nullable=True)
    mah_partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=True)
    authorization_number = Column(String(100), nullable=True)
    authorization_country_code = Column(String(2), nullable=True)
    authorization_status = Column(String(50), nullable=True)
    is_company_product = Column(Boolean, default=True, nullable=False)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    updated_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    mah_partner = relationship("Partner", back_populates="products")
    contracts = relationship("Contract", back_populates="product")
    substance_links = relationship(
        "ProductSubstance",
        back_populates="product",
        cascade="all, delete-orphan",
    )
    case_products = relationship("CaseProduct", back_populates="product")
    psur_plans = relationship("PSURPlan", back_populates="product")
    psur_products = relationship("PSURProduct", back_populates="product")
    documents = relationship(
        "Attachment",
        foreign_keys="Attachment.product_id",
        back_populates="product",
    )
    incoming_requests = relationship("IncomingRequest", back_populates="product")
    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])


class Contract(CommonMixin, Base):
    __tablename__ = "tblContracts"
    __table_args__ = (
        UniqueConstraint("partner_id", "product_id", name="uq_contract_partner_product"),
        Index("ix_contract_partner_product", "partner_id", "product_id"),
        Index("ix_contract_valid_until", "valid_until"),
    )

    partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=False, index=True)
    product_id = Column(String(36), ForeignKey("tblProducts.id"), nullable=False, index=True)
    contract_type = Column(String(100), nullable=False, index=True)
    contract_number = Column(String(100), nullable=False, index=True)
    contract_date = Column(Date, nullable=False)
    valid_until = Column(Date, nullable=False)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    updated_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    partner = relationship("Partner", back_populates="contracts")
    product = relationship("Product", back_populates="contracts")
    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])

    @property
    def is_current(self) -> bool:
        today = date.today()
        return self.contract_date <= today <= self.valid_until


class ContractContact(CommonMixin, Base):
    __tablename__ = "tblContractContacts"
    __table_args__ = (
        UniqueConstraint("partner_id", "email", name="uq_contract_contact_partner_email"),
        Index("ix_contract_contact_partner", "partner_id"),
        Index("ix_contract_contact_current", "is_current"),
    )

    partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=False, index=True)
    last_name = Column(String(100), nullable=False, index=True)
    first_name = Column(String(100), nullable=False)
    patronymic = Column(String(100), nullable=True)
    email = Column(String(255), nullable=False)
    position = Column(String(255), nullable=False)
    is_current = Column(Boolean, default=True, nullable=False)

    partner = relationship("Partner", back_populates="contract_contacts")
    psur_partner_requests = relationship("PSURPartnerRequest", back_populates="contact_person")


class PartnerReconciliation(CommonMixin, Base):
    __tablename__ = "tblPartnerReconciliations"
    __table_args__ = (
        Index("ix_partner_reconciliation_period", "partner_id", "period_start", "period_end"),
        Index("ix_partner_reconciliation_status", "reconciliation_status"),
    )

    partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=False, index=True)
    contact_id = Column(String(36), ForeignKey("tblContractContacts.id"), nullable=True)
    reconciliation_date = Column(Date, default=date.today, nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    language = Column(String(2), default="ru", nullable=False)
    reconciliation_status = Column(String(50), default="draft", index=True, nullable=False)
    prepared_by = Column(String(255), nullable=True)
    contact_name = Column(String(255), nullable=True)
    contact_email = Column(String(255), nullable=True)
    products = Column(Text, nullable=True)
    sent_date = Column(Date, nullable=True)
    response_date = Column(Date, nullable=True)
    discrepancy_description = Column(Text, nullable=True)
    document_id = Column(String(36), ForeignKey("tblAttachments.id"), nullable=True)
    our_case_count = Column(Integer, default=0, nullable=False)
    partner_case_count = Column(Integer, default=0, nullable=False)
    matched_count = Column(Integer, default=0, nullable=False)
    discrepancy_count = Column(Integer, default=0, nullable=False)
    confirmed_by_user = Column(String(255), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    updated_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    partner = relationship("Partner", back_populates="reconciliations")
    contact = relationship("ContractContact")
    response_document = relationship("Attachment", foreign_keys=[document_id])
    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])
    items = relationship(
        "PartnerReconciliationItem",
        back_populates="reconciliation",
        cascade="all, delete-orphan",
    )


class PartnerReconciliationItem(CommonMixin, Base):
    __tablename__ = "tblPartnerReconciliationItems"
    __table_args__ = (
        Index("ix_reconciliation_item_reconciliation", "reconciliation_id"),
        Index("ix_reconciliation_item_status", "reconciliation_status"),
        Index("ix_reconciliation_item_source", "source_side"),
    )

    reconciliation_id = Column(
        String(36),
        ForeignKey("tblPartnerReconciliations.id"),
        nullable=False,
        index=True,
    )
    internal_case_id = Column(String(36), ForeignKey("tblCases.id"), nullable=True)
    partner_case_id = Column(String(100), nullable=True, index=True)
    product_id = Column(String(36), ForeignKey("tblProducts.id"), nullable=True)
    source_side = Column(String(50), nullable=False, index=True)
    case_type = Column(String(50), default="initial", nullable=False)
    receipt_date_our_company = Column(Date, nullable=True)
    receipt_date_partner = Column(Date, nullable=True)
    transfer_date_our_company = Column(Date, nullable=True)
    transfer_date_partner = Column(Date, nullable=True)
    adverse_event = Column(Text, nullable=True)
    seriousness = Column(String(50), nullable=True)
    reconciliation_status = Column(String(100), default="requires_review", nullable=False)
    match_confidence = Column(Float, nullable=True)
    match_method = Column(String(100), nullable=True)
    reviewer_comment = Column(Text, nullable=True)
    confirmed_by_user = Column(String(255), nullable=True)
    internal_case_number = Column(String(100), nullable=True)
    partner_case_number = Column(String(100), nullable=True)
    partner_name = Column(String(255), nullable=True)
    product_name = Column(String(255), nullable=True)
    active_substance = Column(String(255), nullable=True)
    patient = Column(String(255), nullable=True)
    country = Column(String(2), nullable=True)
    source_type = Column(String(100), nullable=True)
    short_description = Column(Text, nullable=True)
    linked_item_id = Column(String(36), nullable=True)

    reconciliation = relationship("PartnerReconciliation", back_populates="items")
    internal_case = relationship("Case")
    product = relationship("Product")


class ProductSubstance(CommonMixin, Base):
    __tablename__ = "tblProductSubstances"
    __table_args__ = (
        UniqueConstraint("product_id", "substance_id", name="uq_product_substance"),
    )

    product_id = Column(String(36), ForeignKey("tblProducts.id"), nullable=False)
    substance_id = Column(String(36), ForeignKey("tblSubstances.id"), nullable=False)
    substance_role = Column(String(50), default="active", nullable=False)
    strength_value = Column(String(50), nullable=True)
    strength_unit = Column(String(50), nullable=True)
    is_primary = Column(Boolean, default=True, nullable=False)

    product = relationship("Product", back_populates="substance_links")
    substance = relationship("Substance", back_populates="product_links")


class SafetyReport(CommonMixin, Base):
    __tablename__ = "tblSafetyReports"
    __table_args__ = (
        Index("ix_safety_report_triage_date", "triage_status", "received_date"),
    )

    safety_report_number = Column(String(50), unique=True, index=True, nullable=False)
    received_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    received_date = Column(Date, index=True, nullable=True)
    source_type = Column(String(50), index=True, nullable=True)
    partner_id = Column(String(36), ForeignKey("tblPartners.id"), index=True, nullable=True)
    reporter_name = Column(String(255), nullable=True)
    reporter_email = Column(String(255), nullable=True)
    reporter_country_code = Column(String(2), nullable=True)
    raw_subject = Column(String(500), nullable=True)
    raw_text = Column(Text, nullable=True)
    triage_status = Column(String(50), default="new", index=True, nullable=False)
    triage_comment = Column(Text, nullable=True)
    triaged_by_user_id = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    triaged_at = Column(DateTime(timezone=True), nullable=True)
    case_id = Column(String(36), index=True, nullable=True)
    is_valid_icsr = Column(Boolean, default=False, nullable=False)
    minimum_criteria_patient = Column(Boolean, default=False, nullable=False)
    minimum_criteria_reporter = Column(Boolean, default=False, nullable=False)
    minimum_criteria_product = Column(Boolean, default=False, nullable=False)
    minimum_criteria_event = Column(Boolean, default=False, nullable=False)

    partner = relationship("Partner", back_populates="safety_reports")
    triaged_by = relationship("User", foreign_keys=[triaged_by_user_id])
    case = relationship(
        "Case",
        back_populates="safety_report",
        foreign_keys="Case.safety_report_id",
        uselist=False,
    )
    attachments = relationship("Attachment", back_populates="safety_report")


class Case(CommonMixin, Base):
    __tablename__ = "tblCases"
    __table_args__ = (
        Index("ix_case_status_due", "workflow_status", "due_date"),
        Index("ix_case_seriousness_received", "seriousness", "initial_received_date"),
    )

    case_number = Column(String(50), unique=True, index=True, nullable=False)
    worldwide_case_id = Column(String(100), index=True, nullable=True)
    safety_report_id = Column(
        String(36),
        ForeignKey("tblSafetyReports.id"),
        unique=True,
        nullable=True,
    )
    partner_id = Column(String(36), ForeignKey("tblPartners.id"), index=True, nullable=True)
    case_version = Column(Integer, default=1, nullable=False)
    case_type = Column(String(50), nullable=True)
    report_type = Column(String(50), nullable=True)
    initial_received_date = Column(Date, index=True, nullable=True)
    latest_received_date = Column(Date, index=True, nullable=True)
    country_of_occurrence = Column(String(2), index=True, nullable=True)
    seriousness = Column(String(50), index=True, nullable=True)
    listedness = Column(String(50), nullable=True)
    expectedness = Column(String(50), nullable=True)
    case_outcome = Column(String(100), nullable=True)
    narrative = Column(Text, nullable=True)
    company_comment = Column(Text, nullable=True)
    medical_review_comment = Column(Text, nullable=True)
    workflow_status = Column(String(50), default="new", index=True, nullable=False)
    assigned_to_user_id = Column(String(36), ForeignKey("tblUsers.id"), index=True, nullable=True)
    due_date = Column(Date, nullable=True)
    is_locked = Column(Boolean, default=False, nullable=False)
    locked_by_user_id = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    updated_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    safety_report = relationship(
        "SafetyReport",
        back_populates="case",
        foreign_keys=[safety_report_id],
    )
    partner = relationship("Partner", back_populates="cases")
    assigned_to = relationship(
        "User",
        foreign_keys=[assigned_to_user_id],
        back_populates="assigned_cases",
    )
    locked_by = relationship("User", foreign_keys=[locked_by_user_id])
    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])
    patients = relationship("Patient", back_populates="case", cascade="all, delete-orphan")
    case_products = relationship(
        "CaseProduct",
        back_populates="case",
        cascade="all, delete-orphan",
    )
    reactions = relationship("Reaction", back_populates="case", cascade="all, delete-orphan")
    followups = relationship("FollowUp", back_populates="case", cascade="all, delete-orphan")
    attachments = relationship("Attachment", back_populates="case")
    submissions = relationship("Submission", back_populates="case")
    audit_entries = relationship("AuditTrail", back_populates="case")
    psur_cases = relationship("PSURCase", back_populates="case")


class PSURPlan(CommonMixin, Base):
    __tablename__ = "tblPSURPlans"
    __table_args__ = (
        Index("ix_psur_plan_substance_status", "active_substance_id", "status"),
        Index("ix_psur_plan_product_period", "product_id", "reporting_period_start", "reporting_period_end"),
        Index("ix_psur_plan_due", "due_date_submission"),
    )

    id = Column("psur_plan_id", String(36), primary_key=True, default=new_uuid)
    active_substance_id = Column(
        String(36),
        ForeignKey("tblSubstances.id"),
        nullable=False,
        index=True,
    )
    product_id = Column(String(36), ForeignKey("tblProducts.id"), nullable=True, index=True)
    psur_type = Column(String(50), default="PSUR", nullable=False, index=True)
    reporting_period_start = Column(Date, nullable=False, index=True)
    reporting_period_end = Column(Date, nullable=False, index=True)
    data_lock_point = Column(Date, nullable=False, index=True)
    due_date_internal = Column(Date, nullable=True, index=True)
    due_date_submission = Column(Date, nullable=True, index=True)
    frequency = Column(String(50), nullable=True)
    status = Column(String(50), default="Planned", nullable=False, index=True)
    responsible_user_id = Column(String(36), ForeignKey("tblUsers.id"), nullable=True, index=True)
    reviewer_user_id = Column(String(36), ForeignKey("tblUsers.id"), nullable=True, index=True)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    active_substance = relationship("Substance", back_populates="psur_plans")
    product = relationship("Product", back_populates="psur_plans")
    responsible_user = relationship(
        "User",
        foreign_keys=[responsible_user_id],
        back_populates="responsible_psur_plans",
    )
    reviewer_user = relationship(
        "User",
        foreign_keys=[reviewer_user_id],
        back_populates="reviewer_psur_plans",
    )
    creator = relationship("User", foreign_keys=[created_by])
    psur_products = relationship(
        "PSURProduct",
        back_populates="psur_plan",
        cascade="all, delete-orphan",
    )
    psur_cases = relationship(
        "PSURCase",
        back_populates="psur_plan",
        cascade="all, delete-orphan",
    )
    partner_requests = relationship(
        "PSURPartnerRequest",
        back_populates="psur_plan",
        cascade="all, delete-orphan",
    )
    sections = relationship(
        "PSURSection",
        back_populates="psur_plan",
        cascade="all, delete-orphan",
    )
    documents = relationship(
        "PSURDocument",
        back_populates="psur_plan",
        cascade="all, delete-orphan",
    )
    tasks = relationship(
        "Task",
        primaryjoin="and_(Task.related_entity_type=='PSUR', foreign(Task.related_entity_id)==PSURPlan.id)",
        viewonly=True,
    )


class PSURProduct(CommonMixin, Base):
    __tablename__ = "tblPSURProducts"
    __table_args__ = (
        UniqueConstraint("psur_plan_id", "product_id", "country", name="uq_psur_product_scope"),
        Index("ix_psur_product_plan", "psur_plan_id"),
    )

    id = Column("psur_product_id", String(36), primary_key=True, default=new_uuid)
    psur_plan_id = Column(
        String(36),
        ForeignKey("tblPSURPlans.psur_plan_id"),
        nullable=False,
        index=True,
    )
    product_id = Column(String(36), ForeignKey("tblProducts.id"), nullable=False, index=True)
    country = Column(String(100), nullable=True)
    marketing_authorisation_number = Column(String(100), nullable=True)
    included_in_report = Column(Boolean, default=True, nullable=False)
    comment = Column(Text, nullable=True)

    psur_plan = relationship("PSURPlan", back_populates="psur_products")
    product = relationship("Product", back_populates="psur_products")


class PSURCase(CommonMixin, Base):
    __tablename__ = "tblPSURCases"
    __table_args__ = (
        UniqueConstraint("psur_plan_id", "case_id", name="uq_psur_case"),
        Index("ix_psur_case_plan", "psur_plan_id"),
        Index("ix_psur_case_included", "case_included"),
    )

    id = Column("psur_case_id", String(36), primary_key=True, default=new_uuid)
    psur_plan_id = Column(
        String(36),
        ForeignKey("tblPSURPlans.psur_plan_id"),
        nullable=False,
        index=True,
    )
    case_id = Column(String(36), ForeignKey("tblCases.id"), nullable=False, index=True)
    case_included = Column(Boolean, default=True, nullable=False)
    reason_excluded = Column(Text, nullable=True)
    seriousness = Column(String(50), nullable=True)
    listedness = Column(String(50), nullable=True)
    case_origin = Column(String(100), nullable=True)
    assessment_comment = Column(Text, nullable=True)

    psur_plan = relationship("PSURPlan", back_populates="psur_cases")
    case = relationship("Case", back_populates="psur_cases")


class PSURPartnerRequest(CommonMixin, Base):
    __tablename__ = "tblPSURPartnerRequests"
    __table_args__ = (
        Index("ix_psur_partner_request_plan", "psur_plan_id"),
        Index("ix_psur_partner_request_status", "status"),
        Index("ix_psur_partner_request_due", "due_date"),
    )

    id = Column("psur_partner_request_id", String(36), primary_key=True, default=new_uuid)
    psur_plan_id = Column(
        String(36),
        ForeignKey("tblPSURPlans.psur_plan_id"),
        nullable=False,
        index=True,
    )
    partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=False, index=True)
    contact_person_id = Column(
        String(36),
        ForeignKey("tblContractContacts.id"),
        nullable=True,
        index=True,
    )
    request_type = Column(String(100), default="Cases", nullable=False, index=True)
    request_date = Column(Date, nullable=True)
    due_date = Column(Date, nullable=True, index=True)
    response_date = Column(Date, nullable=True)
    status = Column(String(50), default="Not Sent", nullable=False, index=True)
    response_summary = Column(Text, nullable=True)
    document_id = Column(
        String(36),
        ForeignKey("tblPSURDocuments.psur_document_id"),
        nullable=True,
    )
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    psur_plan = relationship("PSURPlan", back_populates="partner_requests")
    partner = relationship("Partner", back_populates="psur_partner_requests")
    contact_person = relationship("ContractContact", back_populates="psur_partner_requests")
    document = relationship(
        "PSURDocument",
        foreign_keys=[document_id],
        back_populates="partner_requests",
    )
    creator = relationship("User", foreign_keys=[created_by])


class PSURSection(CommonMixin, Base):
    __tablename__ = "tblPSURSections"
    __table_args__ = (
        UniqueConstraint("psur_plan_id", "section_code", name="uq_psur_section"),
        Index("ix_psur_section_plan_status", "psur_plan_id", "section_status"),
    )

    id = Column("psur_section_id", String(36), primary_key=True, default=new_uuid)
    psur_plan_id = Column(
        String(36),
        ForeignKey("tblPSURPlans.psur_plan_id"),
        nullable=False,
        index=True,
    )
    section_code = Column(String(50), nullable=False)
    section_title = Column(String(255), nullable=False)
    section_status = Column(String(50), default="Not Started", nullable=False, index=True)
    assigned_to = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    reviewed_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    section_text = Column(Text, nullable=True)
    comment = Column(Text, nullable=True)
    gpt_generated = Column(Boolean, default=False, nullable=False)
    gpt_prompt = Column(Text, nullable=True)
    gpt_output_json = Column(Text, nullable=True)
    human_confirmed = Column(Boolean, default=False, nullable=False)
    confirmed_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    last_updated_at = Column(DateTime(timezone=True), nullable=True)

    psur_plan = relationship("PSURPlan", back_populates="sections")
    assignee = relationship("User", foreign_keys=[assigned_to])
    reviewer = relationship("User", foreign_keys=[reviewed_by])
    confirmer = relationship("User", foreign_keys=[confirmed_by])


class PSURDocument(CommonMixin, Base):
    __tablename__ = "tblPSURDocuments"
    __table_args__ = (
        Index("ix_psur_document_plan", "psur_plan_id"),
        Index("ix_psur_document_type", "document_type"),
    )

    id = Column("psur_document_id", String(36), primary_key=True, default=new_uuid)
    psur_plan_id = Column(
        String(36),
        ForeignKey("tblPSURPlans.psur_plan_id"),
        nullable=False,
        index=True,
    )
    document_type = Column(String(100), default="Draft", nullable=False, index=True)
    file_name = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=True)
    version_number = Column(String(50), nullable=True)
    uploaded_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    uploaded_at = Column(DateTime(timezone=True), nullable=True)
    is_final = Column(Boolean, default=False, nullable=False)
    comment = Column(Text, nullable=True)

    psur_plan = relationship("PSURPlan", back_populates="documents")
    uploader = relationship("User", foreign_keys=[uploaded_by])
    partner_requests = relationship(
        "PSURPartnerRequest",
        foreign_keys="PSURPartnerRequest.document_id",
        back_populates="document",
    )


class Task(CommonMixin, Base):
    __tablename__ = "tblTasks"
    __table_args__ = (
        Index("ix_task_related_entity", "related_entity_type", "related_entity_id"),
        Index("ix_task_status_due", "status", "due_date"),
        Index("ix_task_assignee", "assigned_to_user_id"),
    )

    id = Column("task_id", String(36), primary_key=True, default=new_uuid)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(50), default="Open", nullable=False, index=True)
    priority = Column(String(50), default="Normal", nullable=False)
    due_date = Column(Date, nullable=True, index=True)
    assigned_to_user_id = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    responsible_person = Column(String(255), nullable=True)
    related_entity_type = Column(String(100), nullable=True, index=True)
    related_entity_id = Column(String(36), nullable=True, index=True)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    updated_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    comment = Column(Text, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    assigned_to = relationship(
        "User",
        foreign_keys=[assigned_to_user_id],
        back_populates="assigned_tasks",
    )
    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])


class Patient(CommonMixin, Base):
    __tablename__ = "tblPatients"

    case_id = Column(String(36), ForeignKey("tblCases.id"), nullable=False, index=True)
    patient_role = Column(String(50), default="patient", nullable=False)
    patient_initials = Column(String(50), nullable=True)
    patient_identifier = Column(String(100), nullable=True)
    sex = Column(String(50), nullable=True)
    date_of_birth = Column(Date, nullable=True)
    birth_date_raw = Column(String(100), nullable=True)
    age_value = Column(Float, nullable=True)
    age_unit = Column(String(50), nullable=True)
    weight_kg = Column(Float, nullable=True)
    height_cm = Column(Float, nullable=True)
    pregnancy_status = Column(String(50), nullable=True)
    medical_history_text = Column(Text, nullable=True)

    case = relationship("Case", back_populates="patients")


class CaseProduct(CommonMixin, Base):
    __tablename__ = "tblCaseProducts"

    case_id = Column(String(36), ForeignKey("tblCases.id"), nullable=False, index=True)
    product_id = Column(String(36), ForeignKey("tblProducts.id"), nullable=True)
    reported_product_name = Column(String(255), nullable=True)
    active_substance_text = Column(String(255), nullable=True)
    drug_role = Column(String(50), default="suspect", nullable=False)
    indication_text = Column(Text, nullable=True)
    indication_meddra_pt_code = Column(String(50), nullable=True)
    dose_value = Column(String(50), nullable=True)
    dose_unit = Column(String(50), nullable=True)
    route = Column(String(100), nullable=True)
    frequency = Column(String(100), nullable=True)
    therapy_start_date = Column(Date, nullable=True)
    therapy_start_date_raw = Column(String(100), nullable=True)
    therapy_end_date = Column(Date, nullable=True)
    therapy_end_date_raw = Column(String(100), nullable=True)
    action_taken = Column(String(100), nullable=True)
    dechallenge_result = Column(String(100), nullable=True)
    rechallenge_result = Column(String(100), nullable=True)
    batch_lot_number = Column(String(100), nullable=True)

    case = relationship("Case", back_populates="case_products")
    product = relationship("Product", back_populates="case_products")
    assessments = relationship(
        "CaseProductReactionAssessment",
        back_populates="case_product",
        cascade="all, delete-orphan",
    )


class Reaction(CommonMixin, Base):
    __tablename__ = "tblReactions"
    __table_args__ = (
        Index("ix_reaction_meddra_serious", "meddra_pt_code", "is_serious"),
        Index("ix_reaction_case_meddra", "case_id", "meddra_pt_code"),
    )

    case_id = Column(String(36), ForeignKey("tblCases.id"), nullable=False, index=True)
    reported_term = Column(String(255), index=True, nullable=False)
    verbatim_term = Column(String(255), nullable=True)
    meddra_llt_code = Column(String(50), nullable=True)
    meddra_llt_name = Column(String(255), nullable=True)
    meddra_pt_code = Column(String(50), index=True, nullable=True)
    meddra_pt_name = Column(String(255), nullable=True)
    meddra_soc_code = Column(String(50), index=True, nullable=True)
    meddra_soc_name = Column(String(255), nullable=True)
    onset_date = Column(Date, nullable=True)
    onset_date_raw = Column(String(100), nullable=True)
    end_date = Column(Date, nullable=True)
    end_date_raw = Column(String(100), nullable=True)
    outcome = Column(String(100), nullable=True)
    is_serious = Column(Boolean, default=False, index=True, nullable=False)
    seriousness_death = Column(Boolean, default=False, nullable=False)
    seriousness_life_threatening = Column(Boolean, default=False, nullable=False)
    seriousness_hospitalization = Column(Boolean, default=False, nullable=False)
    seriousness_disability = Column(Boolean, default=False, nullable=False)
    seriousness_congenital_anomaly = Column(Boolean, default=False, nullable=False)
    seriousness_other_medically_important = Column(Boolean, default=False, nullable=False)

    case = relationship("Case", back_populates="reactions")
    assessments = relationship(
        "CaseProductReactionAssessment",
        back_populates="reaction",
        cascade="all, delete-orphan",
    )


class CaseProductReactionAssessment(CommonMixin, Base):
    __tablename__ = "tblCaseProductReactionAssessments"

    case_product_id = Column(String(36), ForeignKey("tblCaseProducts.id"), nullable=False)
    reaction_id = Column(String(36), ForeignKey("tblReactions.id"), nullable=False)
    causality_reporter = Column(String(100), nullable=True)
    causality_company = Column(String(100), nullable=True)
    causality_method = Column(String(100), nullable=True)
    relatedness = Column(String(100), nullable=True)
    expectedness = Column(String(100), nullable=True)
    listedness = Column(String(100), nullable=True)
    assessment_comment = Column(Text, nullable=True)

    case_product = relationship("CaseProduct", back_populates="assessments")
    reaction = relationship("Reaction", back_populates="assessments")


class FollowUp(CommonMixin, Base):
    __tablename__ = "tblFollowUps"

    case_id = Column(String(36), ForeignKey("tblCases.id"), nullable=False, index=True)
    follow_up_number = Column(Integer, nullable=False)
    received_date = Column(Date, nullable=True)
    source_type = Column(String(50), nullable=True)
    description = Column(Text, nullable=True)
    significant_new_information = Column(Boolean, default=False, nullable=False)
    processed_by_user_id = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    case_version_after_follow_up = Column(Integer, nullable=True)

    case = relationship("Case", back_populates="followups")
    processed_by = relationship("User")


class Attachment(CommonMixin, Base):
    __tablename__ = "tblAttachments"

    case_id = Column(String(36), ForeignKey("tblCases.id"), nullable=True, index=True)
    safety_report_id = Column(
        String(36),
        ForeignKey("tblSafetyReports.id"),
        nullable=True,
        index=True,
    )
    attachment_type = Column(String(100), nullable=True)
    file_name = Column(String(255), nullable=False)
    document_title = Column(String(255), nullable=True)
    document_type = Column(String(100), nullable=True)
    related_object_type = Column(String(100), nullable=True)
    related_object_id = Column(String(36), nullable=True)
    partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=True, index=True)
    product_id = Column(String(36), ForeignKey("tblProducts.id"), nullable=True, index=True)
    mime_type = Column(String(100), nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    storage_path = Column(String(500), nullable=True)
    file_url = Column(String(500), nullable=True)
    document_version = Column(String(50), nullable=True)
    document_date = Column(Date, nullable=True)
    status = Column(String(50), default="draft", nullable=True)
    comment = Column(Text, nullable=True)
    checksum_sha256 = Column(String(64), nullable=True)
    uploaded_by_user_id = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    uploaded_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    updated_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    case = relationship("Case", back_populates="attachments")
    safety_report = relationship("SafetyReport", back_populates="attachments")
    partner = relationship("Partner", foreign_keys=[partner_id], back_populates="documents")
    product = relationship("Product", foreign_keys=[product_id], back_populates="documents")
    uploaded_by = relationship("User", foreign_keys=[uploaded_by_user_id])
    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])


class SOP(CommonMixin, Base):
    __tablename__ = "tblSOPs"
    __table_args__ = (
        UniqueConstraint("sop_code", name="uq_sop_code"),
        Index("ix_sop_status", "status"),
        Index("ix_sop_document_type", "document_type"),
        Index("ix_sop_process_area", "process_area"),
        Index("ix_sop_next_review", "next_review_date"),
    )

    sop_code = Column(String(100), nullable=False, index=True)
    title = Column(String(255), nullable=False, index=True)
    document_type = Column(String(100), default="SOP", nullable=False, index=True)
    version = Column(String(50), default="1.0", nullable=False)
    status = Column(String(50), default="Draft", nullable=False, index=True)
    process_area = Column(String(100), default="Other", nullable=False, index=True)
    owner = Column(String(255), nullable=False, index=True)
    reviewer = Column(String(255), nullable=True)
    approver = Column(String(255), nullable=True)
    approval_date = Column(Date, nullable=True)
    effective_date = Column(Date, nullable=False)
    next_review_date = Column(Date, nullable=False, index=True)
    revision_reason = Column(Text, nullable=True)
    file_path = Column(String(500), nullable=True)
    file_url = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)
    training_required = Column(Boolean, default=False, nullable=False)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    updated_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])


class IncomingRequest(CommonMixin, Base):
    __tablename__ = "tblIncomingRequests"
    __table_args__ = (
        Index("ix_incoming_request_status", "status"),
        Index("ix_incoming_request_partner", "partner_id"),
        Index("ix_incoming_request_product", "product_id"),
    )

    source_text = Column(Text, nullable=False)
    request_type = Column(String(100), nullable=True)
    partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=True, index=True)
    product_id = Column(String(36), ForeignKey("tblProducts.id"), nullable=True, index=True)
    active_substance = Column(String(255), nullable=True)
    possible_icsr = Column(String(10), default="no", nullable=False)
    patient_information = Column(Text, nullable=True)
    adverse_event = Column(Text, nullable=True)
    seriousness = Column(String(100), nullable=True)
    seriousness_criteria = Column(Text, nullable=True)
    missing_information = Column(Text, nullable=True)
    recommended_next_action = Column(Text, nullable=True)
    validity_assessment = Column(Text, nullable=True)
    gpt_json_output = Column(Text, nullable=True)
    human_confirmed = Column(Boolean, default=False, nullable=False)
    status = Column(String(50), default="new", nullable=False, index=True)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    updated_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    partner = relationship("Partner", back_populates="incoming_requests")
    product = relationship("Product", back_populates="incoming_requests")
    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])


class Submission(CommonMixin, Base):
    __tablename__ = "tblSubmissions"

    submission_number = Column(String(50), unique=True, index=True, nullable=False)
    submission_object_type = Column(String(50), default="case", nullable=False)
    case_id = Column(String(36), ForeignKey("tblCases.id"), nullable=True, index=True)
    pbrer_id = Column(String(36), nullable=True)
    rmp_id = Column(String(36), nullable=True)
    recipient_partner_id = Column(
        String(36),
        ForeignKey("tblPartners.id"),
        nullable=True,
        index=True,
    )
    recipient_type = Column(String(50), default="partner", nullable=False)
    recipient_country_code = Column(String(2), nullable=True)
    submission_type = Column(String(100), nullable=True)
    submission_format = Column(String(100), nullable=True)
    submission_status = Column(String(50), default="planned", index=True, nullable=False)
    due_date = Column(Date, index=True, nullable=True)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    acknowledgement_code = Column(String(100), nullable=True)
    error_message = Column(Text, nullable=True)
    submitted_by_user_id = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    case = relationship("Case", back_populates="submissions")
    recipient_partner = relationship("Partner", back_populates="submissions")
    submitted_by = relationship("User")


class PSMFComponent(CommonMixin, Base):
    __tablename__ = "psmf_components"
    __table_args__ = (
        UniqueConstraint("code", name="uq_psmf_component_code"),
        Index("ix_psmf_component_type_scope", "component_type", "scope"),
        Index("ix_psmf_component_status", "status"),
        Index("ix_psmf_component_partner", "partner_id"),
    )

    code = Column(String(100), nullable=False)
    title = Column(String(255), nullable=False)
    component_type = Column(String(50), default="MAIN_SECTION", nullable=False)
    scope = Column(String(50), default="GLOBAL", nullable=False)
    partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=True)
    description = Column(Text, nullable=True)
    status = Column(String(50), default="draft", nullable=False, index=True)
    current_version = Column(String(50), default="0.1", nullable=False)

    partner = relationship("Partner", back_populates="psmf_components")
    versions = relationship(
        "PSMFComponentVersion",
        back_populates="component",
        cascade="all, delete-orphan",
        order_by="PSMFComponentVersion.created_at.desc()",
    )


class PSMFComponentVersion(Base):
    __tablename__ = "psmf_component_versions"
    __table_args__ = (
        UniqueConstraint("component_id", "version", name="uq_psmf_component_version"),
        Index("ix_psmf_version_component", "component_id"),
        Index("ix_psmf_version_status", "status"),
    )

    id = Column(String(36), primary_key=True, default=new_uuid)
    component_id = Column(
        String(36),
        ForeignKey("psmf_components.id"),
        nullable=False,
        index=True,
    )
    version = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    status = Column(String(50), default="draft", nullable=False)
    change_summary = Column(Text, nullable=True)
    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )
    approved_by = Column(String(255), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    is_locked = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(String(36), nullable=True)
    delete_reason = Column(Text, nullable=True)

    component = relationship("PSMFComponent", back_populates="versions")


class AuditTrail(CommonMixin, Base):
    __tablename__ = "tblAuditTrail"
    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_case_time", "case_id", "timestamp"),
        Index("ix_audit_module_time", "source_module", "changed_at"),
    )

    entity_type = Column(String(100), nullable=False)
    entity_id = Column(String(36), nullable=False)
    case_id = Column(String(36), ForeignKey("tblCases.id"), nullable=True, index=True)
    action = Column(String(50), index=True, nullable=False)
    field_name = Column(String(100), nullable=True)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    change_reason = Column(Text, nullable=True)
    user_id = Column(String(36), ForeignKey("tblUsers.id"), nullable=True, index=True)
    timestamp = Column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)
    changed_by = Column(String(36), nullable=True, index=True)
    changed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    source_module = Column(String(100), nullable=True, index=True)
    comment = Column(Text, nullable=True)
    ip_address = Column(String(100), nullable=True)
    correlation_id = Column(String(100), nullable=True)

    case = relationship("Case", back_populates="audit_entries")
    user = relationship("User", back_populates="audit_entries")
