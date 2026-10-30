import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
from sqlalchemy.orm import declared_attr, relationship

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

    @declared_attr.directive
    def __mapper_args__(cls):
        # All ORM updates participate, including writes that do not manually
        # increment version. Bulk SQL writes must use an explicit predicate.
        return {"version_id_col": getattr(cls, "row_version", cls.version)}


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
    # ``email`` is retained as a legacy storage field for existing databases.
    # Authentication and all new user-facing flows use ``login`` instead.
    login = Column(String(100), unique=True, index=True, nullable=True)
    password_hash = Column(String(255), nullable=True)
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


class AuthSession(CommonMixin, Base):
    __tablename__ = "tblAuthSessions"
    __table_args__ = (
        Index("ix_auth_session_user", "user_id"),
        Index("ix_auth_session_expires", "expires_at"),
    )

    user_id = Column(String(36), ForeignKey("tblUsers.id"), nullable=False)
    token_hash = Column(String(64), unique=True, index=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)


class OutlookOAuthRequest(Base):
    __tablename__ = "outlook_oauth_requests"
    state_hash = Column(String(64), primary_key=True)
    session_id = Column(String(36), ForeignKey("tblAuthSessions.id"), nullable=False)
    user_id = Column(String(36), ForeignKey("tblUsers.id"), nullable=False)
    verifier = Column(String(128), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    reconciliation_id = Column(String(36), nullable=True)


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
    address = Column(Text, nullable=True)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    updated_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    safety_reports = relationship("SafetyReport", back_populates="partner")
    cases = relationship(
        "Case",
        foreign_keys="Case.partner_id",
        back_populates="partner",
    )
    numbered_cases = relationship(
        "Case",
        foreign_keys="Case.numbering_partner_id",
        back_populates="numbering_partner",
    )
    case_links = relationship("CasePartnerLink", back_populates="partner")
    case_identifiers = relationship("CaseIdentifier", back_populates="partner")
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
    accounting_periods = relationship("AccountingDataPeriod", back_populates="partner")
    accounting_info = relationship(
        "PartnerAccountingInfo",
        back_populates="partner",
        uselist=False,
        cascade="all, delete-orphan",
    )
    onboarding_wizards = relationship(
        "PartnerOnboardingWizard",
        back_populates="partner",
        cascade="all, delete-orphan",
    )
    product_registration_links = relationship(
        "PartnerProductRegistrationLink",
        back_populates="partner",
        cascade="all, delete-orphan",
    )
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


class DosageForm(Base):
    __tablename__ = "dosage_forms"

    id = Column(Integer, primary_key=True)
    name_ru = Column(Text, nullable=False)
    name_en = Column(Text, nullable=True)
    group_ru = Column(Text, nullable=True)
    group_en = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=True)
    sort_order = Column(Integer, nullable=True)
    row_version = Column(Integer, default=1, nullable=False)

    __mapper_args__ = {"version_id_col": row_version}

    products = relationship("Product", back_populates="dosage_form_ref")


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
    dosage_form_id = Column(Integer, ForeignKey("dosage_forms.id"), nullable=True)
    strength = Column(String(100), nullable=True)
    route = Column(String(100), nullable=True)
    mah_partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=True)
    authorization_number = Column(String(100), nullable=True)
    authorization_country_code = Column(String(2), nullable=True)
    authorization_status = Column(String(50), nullable=True)
    comment = Column(Text, nullable=True)
    is_company_product = Column(Boolean, default=True, nullable=False)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    updated_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    mah_partner = relationship("Partner", back_populates="products")
    contracts = relationship("Contract", back_populates="product")
    registration_certificates = relationship(
        "ProductRegistrationCertificate",
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductRegistrationCertificate.registration_date.desc(), ProductRegistrationCertificate.created_at.desc()",
    )
    partner_registration_links = relationship(
        "PartnerProductRegistrationLink",
        back_populates="product",
        cascade="all, delete-orphan",
    )
    contract_links = relationship(
        "ContractProduct",
        back_populates="product",
        cascade="all, delete-orphan",
    )
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
    dosage_form_ref = relationship("DosageForm", back_populates="products")

    def dosage_form_name(self, language: str = "ru") -> str:
        if self.dosage_form_ref:
            if language == "en":
                return self.dosage_form_ref.name_en or self.dosage_form_ref.name_ru or ""
            return self.dosage_form_ref.name_ru or self.dosage_form_ref.name_en or ""
        return self.dosage_form or ""

    @property
    def dosage_form_name_ru(self) -> str:
        return self.dosage_form_name("ru")

    @property
    def dosage_form_name_en(self) -> str:
        return self.dosage_form_name("en")


class ProductRegistrationCertificate(CommonMixin, Base):
    __tablename__ = "tblProductRegistrationCertificates"
    __table_args__ = (
        UniqueConstraint("product_id", "registration_number", name="uq_product_registration_number"),
        Index("ix_product_registration_product", "product_id"),
        Index("ix_product_registration_number", "registration_number"),
        Index("ix_product_registration_status", "status"),
    )

    product_id = Column(String(36), ForeignKey("tblProducts.id"), nullable=False)
    registration_number = Column(String(100), nullable=False)
    registration_date = Column(Date, nullable=True)
    valid_until = Column(Date, nullable=True)
    status = Column(String(50), default="active", nullable=False)
    holder = Column(String(255), nullable=True)
    country_code = Column(String(2), nullable=True)
    dosage_form = Column(String(100), nullable=True)
    strength = Column(String(100), nullable=True)
    comment = Column(Text, nullable=True)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    updated_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    product = relationship("Product", back_populates="registration_certificates")
    partner_links = relationship(
        "PartnerProductRegistrationLink",
        back_populates="registration_certificate",
    )
    documents = relationship(
        "Attachment",
        foreign_keys="Attachment.registration_certificate_id",
        back_populates="registration_certificate",
    )
    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])


class Contract(CommonMixin, Base):
    __tablename__ = "tblContracts"
    __table_args__ = (
        Index("ix_contract_partner_product", "partner_id", "product_id"),
        Index("ix_contract_parent_contract", "parent_contract_id"),
        Index("ix_contract_valid_until", "valid_until"),
    )

    partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=False, index=True)
    product_id = Column(String(36), ForeignKey("tblProducts.id"), nullable=True, index=True)
    parent_contract_id = Column(String(36), ForeignKey("tblContracts.id"), nullable=True)
    contract_type = Column(String(100), nullable=False, index=True)
    contract_number = Column(String(100), nullable=False, index=True)
    contract_date = Column(Date, nullable=False)
    valid_until = Column(Date, nullable=False)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    updated_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    partner = relationship("Partner", back_populates="contracts")
    product = relationship("Product", back_populates="contracts")
    product_links = relationship(
        "ContractProduct",
        back_populates="contract",
        cascade="all, delete-orphan",
    )
    parent_contract = relationship(
        "Contract",
        remote_side="Contract.id",
        back_populates="additional_agreements",
        foreign_keys=[parent_contract_id],
    )
    additional_agreements = relationship(
        "Contract",
        back_populates="parent_contract",
        foreign_keys="Contract.parent_contract_id",
    )
    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])
    accounting_periods = relationship("AccountingDataPeriod", back_populates="contract")
    accounting_tariffs = relationship(
        "ContractAccountingTariff",
        back_populates="contract",
        cascade="all, delete-orphan",
    )
    partner_product_registration_links = relationship(
        "PartnerProductRegistrationLink",
        back_populates="contract",
    )

    @property
    def is_current(self) -> bool:
        today = date.today()
        return self.is_active and self.contract_date <= today <= self.valid_until

    @property
    def linked_products(self) -> list["Product"]:
        products = [
            link.product
            for link in self.product_links
            if not link.is_deleted and link.product
        ]
        if not products and self.product:
            return [self.product]
        return products

    @property
    def product_ids(self) -> list[str]:
        product_ids = [self.product_id] if self.product_id else []
        for link in self.product_links:
            if not link.is_deleted and link.product_id and link.product_id not in product_ids:
                product_ids.append(link.product_id)
        return product_ids


class ContractProduct(CommonMixin, Base):
    __tablename__ = "tblContractProducts"
    __table_args__ = (
        UniqueConstraint("contract_id", "product_id", name="uq_contract_product"),
        Index("ix_contract_product_contract", "contract_id"),
        Index("ix_contract_product_product", "product_id"),
    )

    contract_id = Column(String(36), ForeignKey("tblContracts.id"), nullable=False)
    product_id = Column(String(36), ForeignKey("tblProducts.id"), nullable=False)

    contract = relationship("Contract", back_populates="product_links")
    product = relationship("Product", back_populates="contract_links")


class PartnerProductRegistrationLink(CommonMixin, Base):
    __tablename__ = "tblPartnerProductRegistrationLinks"
    __table_args__ = (
        UniqueConstraint(
            "partner_id",
            "product_id",
            "registration_certificate_id",
            "contract_id",
            "partner_role",
            name="uq_partner_product_registration_role",
        ),
        Index("ix_partner_product_registration_partner", "partner_id"),
        Index("ix_partner_product_registration_product", "product_id"),
        Index("ix_partner_product_registration_certificate", "registration_certificate_id"),
        Index("ix_partner_product_registration_contract", "contract_id"),
    )

    partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=False)
    product_id = Column(String(36), ForeignKey("tblProducts.id"), nullable=False)
    registration_certificate_id = Column(
        String(36),
        ForeignKey("tblProductRegistrationCertificates.id"),
        nullable=True,
    )
    contract_id = Column(String(36), ForeignKey("tblContracts.id"), nullable=True)
    partner_role = Column(String(100), default="mah", nullable=False)
    comment = Column(Text, nullable=True)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    updated_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    partner = relationship("Partner", back_populates="product_registration_links")
    product = relationship("Product", back_populates="partner_registration_links")
    registration_certificate = relationship(
        "ProductRegistrationCertificate",
        back_populates="partner_links",
    )
    contract = relationship(
        "Contract",
        back_populates="partner_product_registration_links",
    )
    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])


class PartnerOnboardingWizard(CommonMixin, Base):
    __tablename__ = "tblPartnerOnboardingWizards"
    __table_args__ = (
        Index("ix_partner_onboarding_partner", "partner_id"),
        Index("ix_partner_onboarding_status", "status"),
        Index("ix_partner_onboarding_user", "user_id"),
    )

    partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=True)
    current_step = Column(Integer, default=1, nullable=False)
    completed_steps = Column(Text, default="", nullable=False)
    status = Column(String(50), default="draft", nullable=False)
    user_id = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    partner = relationship("Partner", back_populates="onboarding_wizards")
    user = relationship("User")


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
    email = Column(String(255), nullable=True)
    position = Column(String(255), nullable=False)
    mobile_phone = Column(String(100), nullable=True)
    work_phone = Column(String(100), nullable=True)
    extension_phone = Column(String(50), nullable=True)
    company_website = Column(String(255), nullable=True)
    address = Column(Text, nullable=True)
    is_current = Column(Boolean, default=True, nullable=False)
    is_pv_contact = Column(Boolean, default=True, nullable=False)
    is_reconciliation_recipient = Column(Boolean, default=True, nullable=False)
    cc_reconciliation = Column(Boolean, default=False, nullable=False)
    is_primary = Column(Boolean, default=False, nullable=False)
    is_billing_contact = Column(Boolean, default=False, nullable=False)
    contact_type = Column(String(100), default="pv", nullable=True)
    comments = Column(Text, nullable=True)

    partner = relationship("Partner", back_populates="contract_contacts")
    psur_partner_requests = relationship("PSURPartnerRequest", back_populates="contact_person")


class AccountingService(Base):
    __tablename__ = "tblAccountingServices"

    id = Column(String(36), primary_key=True, default=new_uuid)
    name = Column(String(500), nullable=False, unique=True, index=True)
    default_unit = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)

    lines = relationship("AccountingDataLine", back_populates="service")
    tariffs = relationship("ContractAccountingTariff", back_populates="service")


class AccountingDataPeriod(CommonMixin, Base):
    __tablename__ = "tblAccountingDataPeriods"
    __table_args__ = (
        UniqueConstraint("partner_id", "contract_id", "year", "quarter", name="uq_accounting_period_contract_quarter"),
        Index("ix_accounting_period_partner", "partner_id"),
        Index("ix_accounting_period_contract", "contract_id"),
        Index("ix_accounting_period_status", "status"),
        Index("ix_accounting_period_year_quarter", "year", "quarter"),
    )

    partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=False)
    contract_id = Column(String(36), ForeignKey("tblContracts.id"), nullable=False)
    year = Column(Integer, nullable=False)
    quarter = Column(Integer, nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    status = Column(String(50), default="draft", nullable=False, index=True)
    responsible_user_id = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    prepared_at = Column(DateTime(timezone=True), nullable=True)
    sent_to_accounting_at = Column(DateTime(timezone=True), nullable=True)
    total_contract_amount = Column(Float, default=0, nullable=False)
    comment = Column(Text, nullable=True)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    updated_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    partner = relationship("Partner", back_populates="accounting_periods")
    contract = relationship("Contract", back_populates="accounting_periods")
    responsible_user = relationship("User", foreign_keys=[responsible_user_id])
    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])
    lines = relationship(
        "AccountingDataLine",
        back_populates="accounting_period",
        cascade="all, delete-orphan",
    )


class AccountingDataLine(CommonMixin, Base):
    __tablename__ = "tblAccountingDataLines"
    __table_args__ = (
        Index("ix_accounting_line_period", "accounting_period_id"),
        Index("ix_accounting_line_service", "service_id"),
        Index("ix_accounting_line_object", "object_type", "object_id"),
    )

    accounting_period_id = Column(String(36), ForeignKey("tblAccountingDataPeriods.id"), nullable=False)
    service_id = Column(String(36), ForeignKey("tblAccountingServices.id"), nullable=False)
    object_type = Column(String(100), nullable=True)
    object_id = Column(String(36), nullable=True)
    product_id = Column(String(36), ForeignKey("tblProducts.id"), nullable=True)
    active_substance_id = Column(String(36), ForeignKey("tblSubstances.id"), nullable=True)
    product_or_inn = Column(String(500), nullable=True)
    quantity = Column(Float, nullable=False)
    unit = Column(String(100), nullable=False)
    contract_price = Column(Float, nullable=False)
    contract_amount = Column(Float, nullable=False)
    source = Column(Text, nullable=True)
    comment = Column(Text, nullable=True)

    accounting_period = relationship("AccountingDataPeriod", back_populates="lines")
    service = relationship("AccountingService", back_populates="lines")
    product = relationship("Product")
    active_substance = relationship("Substance")


class ContractAccountingTariff(CommonMixin, Base):
    __tablename__ = "tblContractAccountingTariffs"
    __table_args__ = (
        UniqueConstraint("contract_id", "service_id", name="uq_contract_accounting_tariff_service"),
        Index("ix_accounting_tariff_partner", "partner_id"),
        Index("ix_accounting_tariff_contract", "contract_id"),
        Index("ix_accounting_tariff_service", "service_id"),
    )

    partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=False)
    contract_id = Column(String(36), ForeignKey("tblContracts.id"), nullable=False)
    service_id = Column(String(36), ForeignKey("tblAccountingServices.id"), nullable=False)
    unit = Column(String(100), nullable=False)
    price = Column(Float, nullable=False)
    comment = Column(Text, nullable=True)

    partner = relationship("Partner")
    contract = relationship("Contract", back_populates="accounting_tariffs")
    service = relationship("AccountingService", back_populates="tariffs")


class PartnerAccountingInfo(CommonMixin, Base):
    __tablename__ = "tblPartnerAccountingInfo"
    __table_args__ = (
        UniqueConstraint("partner_id", name="uq_partner_accounting_info_partner"),
        Index("ix_partner_accounting_info_partner", "partner_id"),
    )

    partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=False)
    legal_entity_name = Column(String(500), nullable=True)
    inn = Column(String(20), nullable=True)
    kpp = Column(String(20), nullable=True)
    billing_contact_name = Column(String(255), nullable=True)
    billing_email = Column(String(255), nullable=True)
    comment = Column(Text, nullable=True)

    partner = relationship("Partner", back_populates="accounting_info")


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
    reconciliation_type = Column(String(100), default="periodic", nullable=False)
    language = Column(String(2), default="ru", nullable=False)
    reconciliation_status = Column(String(50), default="draft", index=True, nullable=False)
    prepared_by = Column(String(255), nullable=True)
    contact_name = Column(String(255), nullable=True)
    contact_email = Column(String(255), nullable=True)
    products = Column(Text, nullable=True)
    document_path = Column(String(500), nullable=True)
    document_filename = Column(String(255), nullable=True)
    document_format = Column(String(20), default="xlsx", nullable=True)
    email_subject = Column(String(500), nullable=True)
    email_body = Column(Text, nullable=True)
    email_to = Column(Text, nullable=True)
    email_cc = Column(Text, nullable=True)
    outlook_message_id = Column(String(255), nullable=True)
    outlook_draft_web_link = Column(String(1000), nullable=True)
    outlook_status = Column(String(50), default="not_created", nullable=False)
    outlook_error = Column(Text, nullable=True)
    sent_date = Column(Date, nullable=True)
    response_date = Column(Date, nullable=True)
    discrepancy_description = Column(Text, nullable=True)
    document_id = Column(String(36), ForeignKey("tblAttachments.id"), nullable=True)
    generated_at = Column(DateTime(timezone=True), nullable=True)
    draft_created_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    our_case_count = Column(Integer, default=0, nullable=False)
    partner_case_count = Column(Integer, default=0, nullable=False)
    matched_count = Column(Integer, default=0, nullable=False)
    discrepancy_count = Column(Integer, default=0, nullable=False)
    confirmed_by_user = Column(String(255), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    comments = Column(Text, nullable=True)
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
    discrepancy_flag = Column(Boolean, default=False, nullable=False)
    discrepancy_comment = Column(Text, nullable=True)
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
    message_direction = Column(
        String(50),
        default="needs_review",
        nullable=False,
        index=True,
    )
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
    import_batch_id = Column(String(36), ForeignKey("tblE2BImportBatches.id"), nullable=True, index=True)
    import_item_id = Column(String(36), ForeignKey("tblE2BImportItems.id"), nullable=True, index=True)
    e2b_message_identifier = Column(String(255), nullable=True, index=True)
    e2b_message_version = Column(String(50), nullable=True)
    e2b_followup_type = Column(String(100), nullable=True)
    source_checksum_sha256 = Column(String(64), nullable=True, index=True)

    partner = relationship("Partner", back_populates="safety_reports")
    triaged_by = relationship("User", foreign_keys=[triaged_by_user_id])
    case = relationship(
        "Case",
        back_populates="safety_report",
        foreign_keys="Case.safety_report_id",
        uselist=False,
    )
    attachments = relationship("Attachment", back_populates="safety_report")
    product_links = relationship(
        "SafetyReportProduct",
        back_populates="safety_report",
        cascade="all, delete-orphan",
        order_by="SafetyReportProduct.created_at, SafetyReportProduct.id",
    )
    e2b_import_batch = relationship(
        "E2BImportBatch",
        foreign_keys=[import_batch_id],
        back_populates="safety_reports",
    )
    e2b_import_item = relationship("E2BImportItem", foreign_keys=[import_item_id])

    @property
    def product_ids(self) -> list[str]:
        product_ids: list[str] = []
        for link in self.product_links:
            if not link.is_deleted and link.product_id and link.product_id not in product_ids:
                product_ids.append(link.product_id)
        return product_ids

    @property
    def linked_products(self) -> list["Product"]:
        return [
            link.product
            for link in self.product_links
            if not link.is_deleted and link.product
        ]

    @property
    def product_names(self) -> list[str]:
        names: list[str] = []
        for link in self.product_links:
            if link.is_deleted:
                continue
            name = link.reported_product_name or (
                link.product.product_name if link.product else None
            )
            if name and name not in names:
                names.append(name)
        return names


class SafetyReportProduct(CommonMixin, Base):
    __tablename__ = "tblSafetyReportProducts"
    __table_args__ = (
        CheckConstraint(
            "product_id IS NOT NULL OR reported_product_name IS NOT NULL",
            name="ck_safety_report_product_reference",
        ),
        UniqueConstraint(
            "safety_report_id",
            "product_id",
            name="uq_safety_report_product",
        ),
        Index("ix_safety_report_product_report", "safety_report_id"),
        Index("ix_safety_report_product_product", "product_id"),
    )

    safety_report_id = Column(
        String(36),
        ForeignKey("tblSafetyReports.id"),
        nullable=False,
    )
    product_id = Column(String(36), ForeignKey("tblProducts.id"), nullable=True)
    reported_product_name = Column(String(255), nullable=True)
    drug_role = Column(String(50), default="suspect", nullable=False)

    safety_report = relationship("SafetyReport", back_populates="product_links")
    product = relationship("Product")


class CaseNumberCounter(CommonMixin, Base):
    __tablename__ = "tblCaseNumberCounters"
    __table_args__ = (
        UniqueConstraint(
            "scope_key",
            "numbering_year",
            name="uq_case_number_counter_scope_year",
        ),
    )

    scope_key = Column(String(64), nullable=False, index=True)
    numbering_year = Column(Integer, nullable=False, index=True)
    last_value = Column(Integer, nullable=False, default=0)


class Case(CommonMixin, Base):
    __tablename__ = "tblCases"
    __table_args__ = (
        UniqueConstraint(
            "numbering_scope",
            "numbering_year",
            "numbering_sequence",
            name="uq_case_number_scope_year_sequence",
        ),
        Index("ix_case_status_due", "workflow_status", "due_date"),
        Index("ix_case_seriousness_received", "seriousness", "initial_received_date"),
    )

    case_number = Column(String(50), index=True, nullable=False)
    worldwide_case_id = Column(String(100), unique=True, index=True, nullable=True)
    safety_report_id = Column(
        String(36),
        ForeignKey("tblSafetyReports.id"),
        unique=True,
        nullable=True,
    )
    partner_id = Column(String(36), ForeignKey("tblPartners.id"), index=True, nullable=True)
    numbering_partner_id = Column(
        String(36),
        ForeignKey("tblPartners.id"),
        index=True,
        nullable=True,
    )
    numbering_scope = Column(String(64), index=True, nullable=True)
    numbering_year = Column(Integer, index=True, nullable=True)
    numbering_sequence = Column(Integer, nullable=True)
    number_is_provisional = Column(Boolean, default=False, nullable=False, index=True)
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
    local_sender_case_id = Column(String(255), unique=True, nullable=True, index=True)
    source_country_code = Column(String(2), nullable=True)
    data_review_status = Column(String(100), nullable=True)
    e2b_import_batch_id = Column(String(36), ForeignKey("tblE2BImportBatches.id"), nullable=True, index=True)
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
    partner = relationship("Partner", foreign_keys=[partner_id], back_populates="cases")
    numbering_partner = relationship(
        "Partner",
        foreign_keys=[numbering_partner_id],
        back_populates="numbered_cases",
    )
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
    incoming_requests = relationship("IncomingRequest", back_populates="case")
    incoming_request_links = relationship(
        "IncomingRequestCaseLink",
        back_populates="case",
    )
    e2b_import_batch = relationship(
        "E2BImportBatch",
        foreign_keys=[e2b_import_batch_id],
        back_populates="cases",
    )
    e2b_transmissions = relationship("E2BTransmission", back_populates="case")
    literature_references = relationship(
        "CaseLiteratureReference",
        back_populates="case",
        cascade="all, delete-orphan",
    )
    narratives = relationship(
        "CaseNarrative",
        back_populates="case",
        cascade="all, delete-orphan",
    )
    partner_links = relationship(
        "CasePartnerLink",
        back_populates="case",
        cascade="all, delete-orphan",
    )
    identifiers = relationship(
        "CaseIdentifier",
        back_populates="case",
        cascade="all, delete-orphan",
        order_by="CaseIdentifier.received_date, CaseIdentifier.created_at",
    )

    @property
    def numbering_partner_code(self) -> str:
        if self.number_is_provisional or not self.numbering_partner_id:
            return "UNASSIGNED"
        code = (self.numbering_partner.partner_code if self.numbering_partner else "") or ""
        return code.strip() or f"PARTNER{self.numbering_partner_id}"

    @property
    def full_case_code(self) -> str:
        number = (self.case_number or "").strip()
        if self.number_is_provisional or not self.numbering_partner_id:
            return number if number.startswith("UNASSIGNED-") else f"UNASSIGNED-{number}"
        return f"{self.numbering_partner_code}-{number}"

    @property
    def numbering_warning(self) -> str | None:
        if self.number_is_provisional or not self.numbering_partner_id:
            return "Партнёр не выбран"
        if not self.numbering_partner or not (self.numbering_partner.partner_code or "").strip():
            return "Код партнёра не заполнен"
        return None

    @property
    def primary_external_identifier(self) -> str | None:
        primary = next(
            (
                row.identifier_value
                for row in self.identifiers
                if not row.is_deleted
                and row.is_primary
                and row.identifier_type != "previous_internal_number"
            ),
            None,
        )
        if primary:
            return primary
        return next(
            (
                row.identifier_value
                for row in self.identifiers
                if not row.is_deleted and row.identifier_type != "previous_internal_number"
            ),
            None,
        )

    @property
    def additional_case_numbers(self) -> str:
        return "; ".join(
            row.identifier_value
            for row in self.identifiers
            if not row.is_deleted and row.identifier_type != "previous_internal_number"
        )

    @property
    def version_label(self) -> str:
        return "Initial" if self.case_version <= 1 else f"F{self.case_version - 1:02d}"


class CasePartnerLink(CommonMixin, Base):
    __tablename__ = "tblCasePartnerLinks"
    __table_args__ = (
        UniqueConstraint("case_id", "partner_id", name="uq_case_partner_link"),
        Index("ix_case_partner_link_case", "case_id"),
        Index("ix_case_partner_link_partner", "partner_id"),
    )

    case_id = Column(String(36), ForeignKey("tblCases.id"), nullable=False)
    partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=False)
    relationship_type = Column(String(50), default="related", nullable=False)

    case = relationship("Case", back_populates="partner_links")
    partner = relationship("Partner", back_populates="case_links")


class CaseIdentifier(CommonMixin, Base):
    __tablename__ = "tblCaseIdentifiers"
    __table_args__ = (
        Index("ix_case_identifier_case", "case_id"),
        Index(
            "ix_case_identifier_partner_value",
            "partner_id",
            "identifier_value",
        ),
    )

    case_id = Column(String(36), ForeignKey("tblCases.id"), nullable=False)
    identifier_value = Column(String(255), nullable=False, index=True)
    partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=True)
    organization_name = Column(String(255), nullable=True)
    identifier_type = Column(String(100), default="partner_case_number", nullable=False)
    received_date = Column(Date, nullable=True)
    is_primary = Column(Boolean, default=False, nullable=False)

    case = relationship("Case", back_populates="identifiers")
    partner = relationship("Partner", back_populates="case_identifiers")


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
    country_code = Column(String(2), nullable=True)
    age_group = Column(String(100), nullable=True)
    date_of_birth_precision = Column(String(20), nullable=True)
    death_date = Column(Date, nullable=True)
    death_date_raw = Column(String(100), nullable=True)
    death_date_precision = Column(String(20), nullable=True)
    death_cause = Column(Text, nullable=True)
    import_batch_id = Column(String(36), ForeignKey("tblE2BImportBatches.id"), nullable=True, index=True)
    pregnancy_status = Column(String(50), nullable=True)
    medical_history_text = Column(Text, nullable=True)

    case = relationship("Case", back_populates="patients")
    import_batch = relationship("E2BImportBatch")


class CaseProduct(CommonMixin, Base):
    __tablename__ = "tblCaseProducts"

    case_id = Column(String(36), ForeignKey("tblCases.id"), nullable=False, index=True)
    product_id = Column(String(36), ForeignKey("tblProducts.id"), nullable=True)
    reported_product_name = Column(String(255), nullable=True)
    normalized_product_name = Column(String(255), nullable=True)
    active_substance_text = Column(String(255), nullable=True)
    drug_role = Column(String(50), default="suspect", nullable=False)
    product_match_status = Column(String(50), nullable=True)
    substance_match_status = Column(String(50), nullable=True)
    indication_text = Column(Text, nullable=True)
    indication_meddra_pt_code = Column(String(50), nullable=True)
    dose_value = Column(String(50), nullable=True)
    dose_unit = Column(String(50), nullable=True)
    route = Column(String(100), nullable=True)
    frequency = Column(String(100), nullable=True)
    dosage_form = Column(String(100), nullable=True)
    therapy_start_date = Column(Date, nullable=True)
    therapy_start_date_raw = Column(String(100), nullable=True)
    therapy_start_date_precision = Column(String(20), nullable=True)
    therapy_end_date = Column(Date, nullable=True)
    therapy_end_date_raw = Column(String(100), nullable=True)
    therapy_end_date_precision = Column(String(20), nullable=True)
    duration_text = Column(String(255), nullable=True)
    expiry_date = Column(Date, nullable=True)
    expiry_date_raw = Column(String(100), nullable=True)
    authorization_number = Column(String(100), nullable=True)
    authorization_country_code = Column(String(2), nullable=True)
    authorization_holder = Column(String(255), nullable=True)
    action_taken = Column(String(100), nullable=True)
    dechallenge_result = Column(String(100), nullable=True)
    rechallenge_result = Column(String(100), nullable=True)
    causality_text = Column(Text, nullable=True)
    null_flavor = Column(String(50), nullable=True)
    batch_lot_number = Column(String(100), nullable=True)
    import_batch_id = Column(String(36), ForeignKey("tblE2BImportBatches.id"), nullable=True, index=True)

    case = relationship("Case", back_populates="case_products")
    product = relationship("Product", back_populates="case_products")
    import_batch = relationship("E2BImportBatch")
    drug_substances = relationship(
        "CaseDrugSubstance",
        back_populates="case_product",
        cascade="all, delete-orphan",
    )
    assessments = relationship(
        "CaseProductReactionAssessment",
        back_populates="case_product",
        cascade="all, delete-orphan",
    )


class CaseDrugSubstance(CommonMixin, Base):
    __tablename__ = "tblCaseDrugSubstances"
    __table_args__ = (
        Index("ix_case_drug_substance_case_product", "case_product_id"),
        Index("ix_case_drug_substance_substance", "substance_id"),
    )

    case_product_id = Column(String(36), ForeignKey("tblCaseProducts.id"), nullable=False)
    substance_id = Column(String(36), ForeignKey("tblSubstances.id"), nullable=True)
    reported_substance_name = Column(String(255), nullable=True)
    normalized_substance_name = Column(String(255), nullable=True)
    substance_role = Column(String(50), default="active", nullable=False)
    match_status = Column(String(50), nullable=True)
    strength_value = Column(String(100), nullable=True)
    strength_unit = Column(String(100), nullable=True)
    import_batch_id = Column(String(36), ForeignKey("tblE2BImportBatches.id"), nullable=True, index=True)

    case_product = relationship("CaseProduct", back_populates="drug_substances")
    substance = relationship("Substance")
    import_batch = relationship("E2BImportBatch")


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
    meddra_version = Column(String(50), nullable=True)
    term_language = Column(String(20), nullable=True)
    term_country_code = Column(String(2), nullable=True)
    onset_date = Column(Date, nullable=True)
    onset_date_raw = Column(String(100), nullable=True)
    onset_date_precision = Column(String(20), nullable=True)
    end_date = Column(Date, nullable=True)
    end_date_raw = Column(String(100), nullable=True)
    end_date_precision = Column(String(20), nullable=True)
    duration_text = Column(String(255), nullable=True)
    outcome = Column(String(100), nullable=True)
    is_serious = Column(Boolean, default=False, index=True, nullable=False)
    seriousness_death = Column(Boolean, default=False, nullable=False)
    seriousness_life_threatening = Column(Boolean, default=False, nullable=False)
    seriousness_hospitalization = Column(Boolean, default=False, nullable=False)
    seriousness_disability = Column(Boolean, default=False, nullable=False)
    seriousness_congenital_anomaly = Column(Boolean, default=False, nullable=False)
    seriousness_other_medically_important = Column(Boolean, default=False, nullable=False)
    medically_confirmed = Column(Boolean, nullable=True)
    reporter_comment = Column(Text, nullable=True)
    null_flavor = Column(String(50), nullable=True)
    import_batch_id = Column(String(36), ForeignKey("tblE2BImportBatches.id"), nullable=True, index=True)

    case = relationship("Case", back_populates="reactions")
    import_batch = relationship("E2BImportBatch")
    assessments = relationship(
        "CaseProductReactionAssessment",
        back_populates="reaction",
        cascade="all, delete-orphan",
    )


class CaseProductReactionAssessment(CommonMixin, Base):
    __tablename__ = "tblCaseProductReactionAssessments"
    __table_args__ = (
        Index("ix_case_assessment_method_result", "causality_method", "relatedness"),
        Index("ix_case_assessment_role_date", "assessor_role", "assessment_date"),
    )

    case_product_id = Column(String(36), ForeignKey("tblCaseProducts.id"), nullable=False)
    reaction_id = Column(String(36), ForeignKey("tblReactions.id"), nullable=False)
    causality_reporter = Column(String(100), nullable=True)
    causality_company = Column(String(100), nullable=True)
    causality_method = Column(String(100), nullable=True)
    relatedness = Column(String(100), nullable=True)
    expectedness = Column(String(100), nullable=True)
    listedness = Column(String(100), nullable=True)
    assessment_comment = Column(Text, nullable=True)
    naranjo_score = Column(Integer, nullable=True)
    naranjo_answers_json = Column(Text, nullable=True)
    assessor_role = Column(String(50), nullable=True)
    assessor_name = Column(String(255), nullable=True)
    assessor_organization = Column(String(255), nullable=True)
    assessment_date = Column(Date, nullable=True)
    created_by_user_id = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    case_product = relationship("CaseProduct", back_populates="assessments")
    reaction = relationship("Reaction", back_populates="assessments")
    creator = relationship("User", foreign_keys=[created_by_user_id])


class FollowUp(CommonMixin, Base):
    __tablename__ = "tblFollowUps"

    case_id = Column(String(36), ForeignKey("tblCases.id"), nullable=False, index=True)
    follow_up_number = Column(Integer, nullable=False)
    received_date = Column(Date, nullable=True)
    source_type = Column(String(50), nullable=True)
    message_direction = Column(
        String(50),
        default="needs_review",
        nullable=False,
        index=True,
    )
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
    registration_certificate_id = Column(
        String(36),
        ForeignKey("tblProductRegistrationCertificates.id"),
        nullable=True,
        index=True,
    )
    mime_type = Column(String(100), nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    storage_path = Column(String(500), nullable=True)
    file_url = Column(String(500), nullable=True)
    document_version = Column(String(50), nullable=True)
    document_date = Column(Date, nullable=True)
    valid_until = Column(Date, nullable=True, index=True)
    status = Column(String(50), default="draft", nullable=True)
    is_signed = Column(Boolean, default=False, nullable=False)
    page_count = Column(Integer, nullable=True)
    scope = Column(String(50), default="GLOBAL", nullable=False, index=True)
    psmf_annex_code = Column(String(50), nullable=True, index=True)
    include_in_psmf = Column(Boolean, default=False, nullable=False, index=True)
    inclusion_order = Column(Integer, default=0, nullable=False)
    comment = Column(Text, nullable=True)
    checksum_sha256 = Column(String(64), nullable=True)
    import_batch_id = Column(String(36), ForeignKey("tblE2BImportBatches.id"), nullable=True, index=True)
    e2b_source = Column(String(50), nullable=True)
    duplicate_of_attachment_id = Column(String(36), ForeignKey("tblAttachments.id"), nullable=True)
    uploaded_by_user_id = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    uploaded_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    updated_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    case = relationship("Case", back_populates="attachments")
    safety_report = relationship("SafetyReport", back_populates="attachments")
    partner = relationship("Partner", foreign_keys=[partner_id], back_populates="documents")
    product = relationship("Product", foreign_keys=[product_id], back_populates="documents")
    registration_certificate = relationship(
        "ProductRegistrationCertificate",
        foreign_keys=[registration_certificate_id],
        back_populates="documents",
    )
    uploaded_by = relationship("User", foreign_keys=[uploaded_by_user_id])
    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])
    versions = relationship(
        "DocumentVersion",
        back_populates="attachment",
        cascade="all, delete-orphan",
        order_by="DocumentVersion.created_at.desc()",
    )
    psmf_links = relationship(
        "PSMFDocumentLink",
        back_populates="attachment",
        cascade="all, delete-orphan",
        order_by="PSMFDocumentLink.inclusion_order.asc()",
    )
    build_inclusions = relationship(
        "PSMFBuildDocument",
        back_populates="attachment",
        order_by="PSMFBuildDocument.created_at.desc()",
    )
    import_batch = relationship("E2BImportBatch", back_populates="attachments")
    duplicate_of_attachment = relationship("Attachment", remote_side="Attachment.id")


class DocumentVersion(CommonMixin, Base):
    __tablename__ = "tblDocumentVersions"
    __table_args__ = (
        UniqueConstraint("attachment_id", "version_label", name="uq_document_version_label"),
        Index("ix_document_version_attachment", "attachment_id"),
    )

    attachment_id = Column(String(36), ForeignKey("tblAttachments.id"), nullable=False, index=True)
    version_label = Column(String(50), nullable=False)
    file_name = Column(String(255), nullable=False)
    document_title = Column(String(255), nullable=True)
    document_type = Column(String(100), nullable=True)
    status = Column(String(50), nullable=True)
    storage_path = Column(String(500), nullable=True)
    file_url = Column(String(500), nullable=True)
    checksum_sha256 = Column(String(64), nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    document_date = Column(Date, nullable=True)
    valid_until = Column(Date, nullable=True)
    is_signed = Column(Boolean, default=False, nullable=False)
    page_count = Column(Integer, nullable=True)
    change_reason = Column(Text, nullable=True)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    attachment = relationship("Attachment", back_populates="versions")
    creator = relationship("User", foreign_keys=[created_by])
    psmf_links = relationship("PSMFDocumentLink", back_populates="document_version")
    build_inclusions = relationship("PSMFBuildDocument", back_populates="document_version")


class PSMFDocumentLink(CommonMixin, Base):
    """One registry record linked to one PSMF annex and a selected immutable version."""

    __tablename__ = "psmf_document_links"
    __table_args__ = (
        UniqueConstraint("attachment_id", "annex_code", "partner_id", name="uq_psmf_document_link"),
        Index("ix_psmf_document_link_attachment", "attachment_id"),
        Index("ix_psmf_document_link_version", "document_version_id"),
        Index("ix_psmf_document_link_annex", "annex_code"),
        Index("ix_psmf_document_link_partner", "partner_id"),
    )

    attachment_id = Column(String(36), ForeignKey("tblAttachments.id"), nullable=False)
    document_version_id = Column(String(36), ForeignKey("tblDocumentVersions.id"), nullable=True)
    annex_code = Column(String(50), nullable=False)
    partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=True)
    scope = Column(String(50), default="GLOBAL", nullable=False)
    is_included = Column(Boolean, default=True, nullable=False)
    inclusion_order = Column(Integer, default=0, nullable=False)

    attachment = relationship("Attachment", back_populates="psmf_links")
    document_version = relationship("DocumentVersion", back_populates="psmf_links")
    partner = relationship("Partner")


class SOP(CommonMixin, Base):
    row_version = Column(Integer, default=1, nullable=False)
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
    versions = relationship(
        "SOPVersion",
        back_populates="sop",
        cascade="all, delete-orphan",
        order_by="SOPVersion.created_at.desc()",
    )


class SOPVersion(CommonMixin, Base):
    __tablename__ = "tblSOPVersions"
    __table_args__ = (
        UniqueConstraint("sop_id", "version_label", name="uq_sop_version_label"),
        Index("ix_sop_version_sop", "sop_id"),
    )

    sop_id = Column(String(36), ForeignKey("tblSOPs.id"), nullable=False, index=True)
    version_label = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False)
    effective_date = Column(Date, nullable=True)
    next_review_date = Column(Date, nullable=True)
    file_path = Column(String(500), nullable=True)
    file_url = Column(String(500), nullable=True)
    revision_reason = Column(Text, nullable=True)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    sop = relationship("SOP", back_populates="versions")
    creator = relationship("User", foreign_keys=[created_by])


class IncomingRequest(CommonMixin, Base):
    __tablename__ = "tblIncomingRequests"
    __table_args__ = (
        Index("ix_incoming_request_status", "status"),
        Index("ix_incoming_request_partner", "partner_id"),
        Index("ix_incoming_request_product", "product_id"),
        Index("ix_incoming_request_message_number", "message_number"),
        Index("ix_incoming_request_triage", "triage_status", "validity_status"),
    )

    message_number = Column(String(50), unique=True, index=True, nullable=True)
    received_date = Column(Date, nullable=True, index=True)
    received_time = Column(String(20), nullable=True)
    source_text = Column(Text, nullable=False)
    source_type = Column(String(100), nullable=True)
    source_name = Column(String(255), nullable=True)
    channel = Column(String(100), nullable=True)
    request_type = Column(String(100), nullable=True)
    partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=True, index=True)
    product_id = Column(String(36), ForeignKey("tblProducts.id"), nullable=True, index=True)
    active_substance_id = Column(String(36), ForeignKey("tblSubstances.id"), nullable=True, index=True)
    active_substance = Column(String(255), nullable=True)
    reporter_name = Column(String(255), nullable=True)
    reporter_contact = Column(String(255), nullable=True)
    message_category = Column(String(100), nullable=True)
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
    case_id = Column(String(36), ForeignKey("tblCases.id"), nullable=True, index=True)
    triage_status = Column(String(50), default="not_started", nullable=False, index=True)
    safety_relevance = Column(String(50), nullable=True, index=True)
    potential_icsr = Column(String(50), nullable=True, index=True)
    validity_status = Column(String(50), nullable=True, index=True)
    has_identifiable_patient = Column(Boolean, default=False, nullable=False)
    has_identifiable_reporter = Column(Boolean, default=False, nullable=False)
    has_suspect_product = Column(Boolean, default=False, nullable=False)
    has_adverse_event = Column(Boolean, default=False, nullable=False)
    missing_criteria = Column(Text, nullable=True)
    triage_result = Column(String(100), nullable=True, index=True)
    triage_comment = Column(Text, nullable=True)
    triaged_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    triaged_at = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    updated_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    partner = relationship("Partner", back_populates="incoming_requests")
    product = relationship("Product", back_populates="incoming_requests")
    active_substance_ref = relationship("Substance", foreign_keys=[active_substance_id])
    case = relationship("Case", back_populates="incoming_requests")
    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])
    triage_user = relationship("User", foreign_keys=[triaged_by])
    case_links = relationship(
        "IncomingRequestCaseLink",
        back_populates="incoming_request",
    )
    product_links = relationship(
        "IncomingRequestProduct",
        back_populates="incoming_request",
        cascade="all, delete-orphan",
        order_by="IncomingRequestProduct.created_at, IncomingRequestProduct.id",
    )

    @property
    def product_ids(self) -> list[str]:
        product_ids = [self.product_id] if self.product_id else []
        for link in self.product_links:
            if not link.is_deleted and link.product_id and link.product_id not in product_ids:
                product_ids.append(link.product_id)
        return product_ids

    @property
    def linked_products(self) -> list["Product"]:
        products: list["Product"] = []
        if self.product:
            products.append(self.product)
        for link in self.product_links:
            if not link.is_deleted and link.product and link.product not in products:
                products.append(link.product)
        return products

    @property
    def product_names(self) -> list[str]:
        names: list[str] = []
        for link in self.product_links:
            if link.is_deleted:
                continue
            name = link.reported_product_name or (
                link.product.product_name if link.product else None
            )
            if name and name not in names:
                names.append(name)
        if self.product:
            if self.product.product_name in names:
                names.remove(self.product.product_name)
            names.insert(0, self.product.product_name)
        return names


class IncomingRequestProduct(CommonMixin, Base):
    __tablename__ = "tblIncomingRequestProducts"
    __table_args__ = (
        CheckConstraint(
            "product_id IS NOT NULL OR reported_product_name IS NOT NULL",
            name="ck_incoming_request_product_reference",
        ),
        UniqueConstraint(
            "incoming_request_id",
            "product_id",
            name="uq_incoming_request_product",
        ),
        Index("ix_incoming_request_product_request", "incoming_request_id"),
        Index("ix_incoming_request_product_product", "product_id"),
    )

    incoming_request_id = Column(
        String(36),
        ForeignKey("tblIncomingRequests.id"),
        nullable=False,
    )
    product_id = Column(String(36), ForeignKey("tblProducts.id"), nullable=True)
    reported_product_name = Column(String(255), nullable=True)
    drug_role = Column(String(50), default="suspect", nullable=False)

    incoming_request = relationship("IncomingRequest", back_populates="product_links")
    product = relationship("Product")


class IncomingRequestCaseLink(CommonMixin, Base):
    __tablename__ = "tblIncomingRequestCaseLinks"
    __table_args__ = (
        UniqueConstraint(
            "incoming_request_id",
            "case_id",
            "link_type",
            name="uq_incoming_request_case_link_type",
        ),
        Index("ix_incoming_request_case_link_request", "incoming_request_id"),
        Index("ix_incoming_request_case_link_case", "case_id"),
        Index("ix_incoming_request_case_link_type", "link_type"),
    )

    incoming_request_id = Column(
        String(36),
        ForeignKey("tblIncomingRequests.id"),
        nullable=False,
        index=True,
    )
    case_id = Column(String(36), ForeignKey("tblCases.id"), nullable=False, index=True)
    link_type = Column(String(50), default="created_from_message", nullable=False)
    comment = Column(Text, nullable=True)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    incoming_request = relationship("IncomingRequest", back_populates="case_links")
    case = relationship("Case", back_populates="incoming_request_links")
    creator = relationship("User", foreign_keys=[created_by])


class E2BImportBatch(CommonMixin, Base):
    __tablename__ = "tblE2BImportBatches"
    __table_args__ = (
        Index("ix_e2b_batch_status_uploaded", "status", "uploaded_at"),
        Index("ix_e2b_batch_checksum", "zip_checksum_sha256"),
    )

    source_filename = Column(String(255), nullable=False)
    source_size_bytes = Column(Integer, nullable=False, default=0)
    zip_checksum_sha256 = Column(String(64), nullable=False, index=True)
    uploaded_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    uploaded_by_user_id = Column(String(36), ForeignKey("tblUsers.id"), nullable=True, index=True)
    status = Column(String(50), default="uploaded", nullable=False, index=True)
    importer_version = Column(String(50), default="e2b-importer-1", nullable=False)
    storage_dir = Column(String(500), nullable=True)
    source_zip_path = Column(String(500), nullable=True)
    file_manifest_json = Column(Text, nullable=True)
    normalized_json = Column(Text, nullable=True)
    detected_xml_count = Column(Integer, default=0, nullable=False)
    detected_pdf_count = Column(Integer, default=0, nullable=False)
    detected_other_count = Column(Integer, default=0, nullable=False)
    detected_case_count = Column(Integer, default=0, nullable=False)
    detected_message_count = Column(Integer, default=0, nullable=False)
    error_count = Column(Integer, default=0, nullable=False)
    warning_count = Column(Integer, default=0, nullable=False)
    duplicate_batch_id = Column(String(36), ForeignKey("tblE2BImportBatches.id"), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    confirmed_by_user_id = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    imported_at = Column(DateTime(timezone=True), nullable=True)
    import_summary_json = Column(Text, nullable=True)
    created_safety_report_id = Column(String(36), ForeignKey("tblSafetyReports.id"), nullable=True)
    created_incoming_request_id = Column(String(36), ForeignKey("tblIncomingRequests.id"), nullable=True)
    created_case_id = Column(String(36), ForeignKey("tblCases.id"), nullable=True)

    uploaded_by = relationship("User", foreign_keys=[uploaded_by_user_id])
    confirmed_by = relationship("User", foreign_keys=[confirmed_by_user_id])
    duplicate_batch = relationship("E2BImportBatch", remote_side="E2BImportBatch.id")
    items = relationship(
        "E2BImportItem",
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="E2BImportItem.created_at.asc()",
    )
    issues = relationship(
        "E2BImportIssue",
        back_populates="batch",
        cascade="all, delete-orphan",
    )
    transmissions = relationship(
        "E2BTransmission",
        back_populates="batch",
        cascade="all, delete-orphan",
    )
    safety_reports = relationship(
        "SafetyReport",
        foreign_keys="SafetyReport.import_batch_id",
        back_populates="e2b_import_batch",
    )
    cases = relationship(
        "Case",
        foreign_keys="Case.e2b_import_batch_id",
        back_populates="e2b_import_batch",
    )
    attachments = relationship("Attachment", back_populates="import_batch")
    created_safety_report = relationship("SafetyReport", foreign_keys=[created_safety_report_id])
    created_incoming_request = relationship("IncomingRequest", foreign_keys=[created_incoming_request_id])
    created_case = relationship("Case", foreign_keys=[created_case_id])


class E2BImportItem(CommonMixin, Base):
    __tablename__ = "tblE2BImportItems"
    __table_args__ = (
        Index("ix_e2b_item_batch_role", "batch_id", "file_role"),
        Index("ix_e2b_item_checksum", "checksum_sha256"),
    )

    batch_id = Column(String(36), ForeignKey("tblE2BImportBatches.id"), nullable=False, index=True)
    original_path = Column(String(1000), nullable=False)
    stored_path = Column(String(1000), nullable=True)
    file_name = Column(String(255), nullable=False)
    file_role = Column(String(50), nullable=False)
    mime_type = Column(String(100), nullable=True)
    size_bytes = Column(Integer, default=0, nullable=False)
    checksum_sha256 = Column(String(64), nullable=True)
    status = Column(String(50), default="detected", nullable=False)
    e2b_version = Column(String(50), nullable=True)
    message_identifier = Column(String(255), nullable=True, index=True)
    case_identifier = Column(String(255), nullable=True, index=True)
    recipient = Column(String(255), nullable=True)
    validation_json = Column(Text, nullable=True)
    normalized_json = Column(Text, nullable=True)
    duplicate_of_item_id = Column(String(36), ForeignKey("tblE2BImportItems.id"), nullable=True)

    batch = relationship("E2BImportBatch", back_populates="items")
    duplicate_of_item = relationship("E2BImportItem", remote_side="E2BImportItem.id")
    issues = relationship(
        "E2BImportIssue",
        back_populates="item",
        cascade="all, delete-orphan",
    )
    transmissions = relationship("E2BTransmission", back_populates="item")


class E2BImportIssue(CommonMixin, Base):
    __tablename__ = "tblE2BImportIssues"
    __table_args__ = (
        Index("ix_e2b_issue_batch_severity", "batch_id", "severity"),
        Index("ix_e2b_issue_item", "item_id"),
    )

    batch_id = Column(String(36), ForeignKey("tblE2BImportBatches.id"), nullable=False, index=True)
    item_id = Column(String(36), ForeignKey("tblE2BImportItems.id"), nullable=True, index=True)
    severity = Column(String(20), default="warning", nullable=False)
    issue_type = Column(String(100), nullable=False)
    message = Column(Text, nullable=False)
    technical_path = Column(String(1000), nullable=True)
    is_blocking = Column(Boolean, default=False, nullable=False)

    batch = relationship("E2BImportBatch", back_populates="issues")
    item = relationship("E2BImportItem", back_populates="issues")


class E2BTransmission(CommonMixin, Base):
    __tablename__ = "tblE2BTransmissions"
    __table_args__ = (
        Index("ix_e2b_transmission_case", "case_id"),
        Index("ix_e2b_transmission_message", "message_identifier", "message_version"),
        UniqueConstraint("import_item_id", name="uq_e2b_transmission_import_item"),
    )

    batch_id = Column(String(36), ForeignKey("tblE2BImportBatches.id"), nullable=False, index=True)
    import_item_id = Column(String(36), ForeignKey("tblE2BImportItems.id"), nullable=False, index=True)
    case_id = Column(String(36), ForeignKey("tblCases.id"), nullable=True, index=True)
    safety_report_id = Column(String(36), ForeignKey("tblSafetyReports.id"), nullable=True, index=True)
    incoming_request_id = Column(String(36), ForeignKey("tblIncomingRequests.id"), nullable=True, index=True)
    message_identifier = Column(String(255), nullable=True, index=True)
    message_version = Column(String(50), nullable=True)
    worldwide_case_id = Column(String(255), nullable=True, index=True)
    local_sender_case_id = Column(String(255), nullable=True, index=True)
    receiver = Column(String(255), nullable=True)
    sender = Column(String(255), nullable=True)
    message_type = Column(String(100), nullable=True)
    followup_type = Column(String(100), nullable=True)
    xml_checksum_sha256 = Column(String(64), nullable=True, index=True)
    normalized_json = Column(Text, nullable=True)

    batch = relationship("E2BImportBatch", back_populates="transmissions")
    item = relationship("E2BImportItem", back_populates="transmissions")
    case = relationship("Case", back_populates="e2b_transmissions")
    safety_report = relationship("SafetyReport")
    incoming_request = relationship("IncomingRequest")


class CaseLiteratureReference(CommonMixin, Base):
    __tablename__ = "tblCaseLiteratureReferences"
    __table_args__ = (
        Index("ix_case_literature_case", "case_id"),
        Index("ix_case_literature_pmid", "pmid"),
        Index("ix_case_literature_doi", "doi"),
    )

    case_id = Column(String(36), ForeignKey("tblCases.id"), nullable=False, index=True)
    title = Column(String(500), nullable=True)
    authors = Column(Text, nullable=True)
    journal = Column(String(255), nullable=True)
    publication_year = Column(Integer, nullable=True)
    volume = Column(String(100), nullable=True)
    issue = Column(String(100), nullable=True)
    pages = Column(String(100), nullable=True)
    doi = Column(String(255), nullable=True, index=True)
    pmid = Column(String(100), nullable=True, index=True)
    citation = Column(Text, nullable=True)
    linked_attachment_id = Column(String(36), ForeignKey("tblAttachments.id"), nullable=True)
    additional_source_text = Column(Text, nullable=True)
    import_batch_id = Column(String(36), ForeignKey("tblE2BImportBatches.id"), nullable=True, index=True)

    case = relationship("Case", back_populates="literature_references")
    linked_attachment = relationship("Attachment")
    import_batch = relationship("E2BImportBatch")


class CaseNarrative(CommonMixin, Base):
    __tablename__ = "tblCaseNarratives"
    __table_args__ = (
        Index("ix_case_narrative_case", "case_id"),
        Index("ix_case_narrative_type", "narrative_type"),
    )

    case_id = Column(String(36), ForeignKey("tblCases.id"), nullable=False, index=True)
    narrative_type = Column(String(100), default="medical_narrative", nullable=False)
    narrative_text = Column(Text, nullable=True)
    language = Column(String(20), nullable=True)
    source = Column(String(100), nullable=True)
    import_batch_id = Column(String(36), ForeignKey("tblE2BImportBatches.id"), nullable=True, index=True)

    case = relationship("Case", back_populates="narratives")
    import_batch = relationship("E2BImportBatch")


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


class LiteratureMonitoringPlan(CommonMixin, Base):
    __tablename__ = "tblLiteratureMonitoringPlans"
    __table_args__ = (
        Index("ix_literature_plan_partner_status", "partner_id", "status"),
        Index("ix_literature_plan_substance", "active_substance_id"),
        Index("ix_literature_plan_responsible", "responsible_user_id"),
        Index("ix_literature_plan_dates", "start_date", "end_date"),
    )

    partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=False, index=True)
    active_substance_id = Column(String(36), ForeignKey("tblSubstances.id"), nullable=True, index=True)
    monitoring_sources = Column(Text, nullable=False)
    frequency = Column(String(50), default="monthly", nullable=False, index=True)
    search_strategy = Column(Text, nullable=True)
    keywords = Column(Text, nullable=True)
    territory = Column(String(255), nullable=True)
    responsible_user_id = Column(String(36), ForeignKey("tblUsers.id"), nullable=True, index=True)
    start_date = Column(Date, nullable=False, index=True)
    end_date = Column(Date, nullable=True, index=True)
    status = Column(String(50), default="active", nullable=False, index=True)
    comment = Column(Text, nullable=True)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    updated_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    partner = relationship("Partner")
    active_substance = relationship("Substance")
    responsible_user = relationship("User", foreign_keys=[responsible_user_id])
    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])
    products = relationship(
        "LiteratureMonitoringPlanProduct",
        back_populates="plan",
        cascade="all, delete-orphan",
    )
    search_logs = relationship(
        "LiteratureSearchLog",
        back_populates="plan",
        cascade="all, delete-orphan",
    )
    results = relationship("LiteratureResult", back_populates="plan")


class LiteratureMonitoringPlanProduct(CommonMixin, Base):
    __tablename__ = "tblLiteratureMonitoringPlanProducts"
    __table_args__ = (
        UniqueConstraint("plan_id", "product_id", name="uq_literature_plan_product"),
        Index("ix_literature_plan_product_plan", "plan_id"),
        Index("ix_literature_plan_product_product", "product_id"),
    )

    plan_id = Column(String(36), ForeignKey("tblLiteratureMonitoringPlans.id"), nullable=False)
    product_id = Column(String(36), ForeignKey("tblProducts.id"), nullable=False)

    plan = relationship("LiteratureMonitoringPlan", back_populates="products")
    product = relationship("Product")


class LiteratureSearchLog(CommonMixin, Base):
    __tablename__ = "tblLiteratureSearchLogs"
    __table_args__ = (
        Index("ix_literature_log_plan", "plan_id"),
        Index("ix_literature_log_partner_result", "partner_id", "result"),
        Index("ix_literature_log_search_date", "search_date"),
        Index("ix_literature_log_status", "status"),
    )

    plan_id = Column(String(36), ForeignKey("tblLiteratureMonitoringPlans.id"), nullable=False, index=True)
    partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=True, index=True)
    active_substance_id = Column(String(36), ForeignKey("tblSubstances.id"), nullable=True, index=True)
    search_date = Column(Date, nullable=False, index=True)
    searched_by_user_id = Column(String(36), ForeignKey("tblUsers.id"), nullable=True, index=True)
    period_start = Column(Date, nullable=False, index=True)
    period_end = Column(Date, nullable=False, index=True)
    search_source = Column(String(255), nullable=False, index=True)
    search_strategy = Column(Text, nullable=True)
    publications_found = Column(Integer, default=0, nullable=False)
    relevant_publications = Column(Integer, default=0, nullable=False)
    result = Column(String(50), default="nothing_found", nullable=False, index=True)
    status = Column(String(50), default="completed", nullable=False, index=True)
    summary = Column(Text, nullable=True)
    comment = Column(Text, nullable=True)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    updated_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    plan = relationship("LiteratureMonitoringPlan", back_populates="search_logs")
    partner = relationship("Partner")
    active_substance = relationship("Substance")
    searched_by = relationship("User", foreign_keys=[searched_by_user_id])
    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])
    products = relationship(
        "LiteratureSearchLogProduct",
        back_populates="search_log",
        cascade="all, delete-orphan",
    )
    results = relationship(
        "LiteratureResult",
        back_populates="search_log",
        cascade="all, delete-orphan",
    )


class LiteratureSearchLogProduct(CommonMixin, Base):
    __tablename__ = "tblLiteratureSearchLogProducts"
    __table_args__ = (
        UniqueConstraint("search_log_id", "product_id", name="uq_literature_log_product"),
        Index("ix_literature_log_product_log", "search_log_id"),
        Index("ix_literature_log_product_product", "product_id"),
    )

    search_log_id = Column(String(36), ForeignKey("tblLiteratureSearchLogs.id"), nullable=False)
    product_id = Column(String(36), ForeignKey("tblProducts.id"), nullable=False)

    search_log = relationship("LiteratureSearchLog", back_populates="products")
    product = relationship("Product")


class LiteratureResult(CommonMixin, Base):
    __tablename__ = "tblLiteratureResults"
    __table_args__ = (
        Index("ix_literature_result_plan_status", "plan_id", "processing_status"),
        Index("ix_literature_result_partner", "partner_id"),
        Index("ix_literature_result_type_decision", "result_type", "pv_decision"),
        Index("ix_literature_result_publication_date", "publication_date"),
    )

    search_log_id = Column(String(36), ForeignKey("tblLiteratureSearchLogs.id"), nullable=True, index=True)
    plan_id = Column(String(36), ForeignKey("tblLiteratureMonitoringPlans.id"), nullable=False, index=True)
    partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=True, index=True)
    active_substance_id = Column(String(36), ForeignKey("tblSubstances.id"), nullable=True, index=True)
    publication_title = Column(String(500), nullable=False, index=True)
    authors = Column(Text, nullable=True)
    journal_source = Column(String(255), nullable=True, index=True)
    publication_year = Column(Integer, nullable=True, index=True)
    publication_date = Column(Date, nullable=True, index=True)
    doi = Column(String(255), nullable=True, index=True)
    url = Column(String(1000), nullable=True)
    abstract = Column(Text, nullable=True)
    relevance = Column(String(50), default="needs_assessment", nullable=False, index=True)
    result_type = Column(String(50), default="other", nullable=False, index=True)
    pv_decision = Column(String(50), default="no_action", nullable=False, index=True)
    processing_status = Column(String(50), default="new", nullable=False, index=True)
    specialist_comment = Column(Text, nullable=True)
    article_pdf_document_id = Column(String(36), ForeignKey("tblAttachments.id"), nullable=True)
    screenshot_document_id = Column(String(36), ForeignKey("tblAttachments.id"), nullable=True)
    linked_case_id = Column(String(36), ForeignKey("tblCases.id"), nullable=True, index=True)
    linked_psur_plan_id = Column(String(36), ForeignKey("tblPSURPlans.psur_plan_id"), nullable=True, index=True)
    linked_psmf_component_id = Column(String(36), ForeignKey("psmf_components.id"), nullable=True, index=True)
    rmp_reference = Column(String(255), nullable=True)
    created_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    updated_by = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)

    search_log = relationship("LiteratureSearchLog", back_populates="results")
    plan = relationship("LiteratureMonitoringPlan", back_populates="results")
    partner = relationship("Partner")
    active_substance = relationship("Substance")
    article_pdf_document = relationship("Attachment", foreign_keys=[article_pdf_document_id])
    screenshot_document = relationship("Attachment", foreign_keys=[screenshot_document_id])
    linked_case = relationship("Case")
    linked_psur_plan = relationship("PSURPlan")
    linked_psmf_component = relationship("PSMFComponent")
    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])
    products = relationship(
        "LiteratureResultProduct",
        back_populates="result",
        cascade="all, delete-orphan",
    )


class LiteratureResultProduct(CommonMixin, Base):
    __tablename__ = "tblLiteratureResultProducts"
    __table_args__ = (
        UniqueConstraint("result_id", "product_id", name="uq_literature_result_product"),
        Index("ix_literature_result_product_result", "result_id"),
        Index("ix_literature_result_product_product", "product_id"),
    )

    result_id = Column(String(36), ForeignKey("tblLiteratureResults.id"), nullable=False)
    product_id = Column(String(36), ForeignKey("tblProducts.id"), nullable=False)

    result = relationship("LiteratureResult", back_populates="products")
    product = relationship("Product")


class PSMFComponent(CommonMixin, Base):
    __tablename__ = "psmf_components"
    __table_args__ = (
        UniqueConstraint("code", name="uq_psmf_component_code"),
        Index("ix_psmf_component_type_scope", "component_type", "scope"),
        Index("ix_psmf_component_status", "status"),
        Index("ix_psmf_component_partner", "partner_id"),
        Index("ix_psmf_component_parent", "parent_component_id"),
    )

    original_code = Column(String(100), nullable=True)
    original_title = Column(String(255), nullable=True)
    code = Column(String(100), nullable=False)
    title = Column(String(255), nullable=False)
    title_ru = Column(String(255), nullable=True)
    title_en = Column(String(255), nullable=True)
    component_type = Column(String(50), default="MAIN_SECTION", nullable=False)
    parent_component_id = Column(String(36), ForeignKey("psmf_components.id"), nullable=True)
    component_order = Column(Integer, default=0, nullable=False)
    scope = Column(String(50), default="GLOBAL", nullable=False)
    partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=True)
    description = Column(Text, nullable=True)
    is_required = Column(Boolean, default=True, nullable=False)
    is_optional = Column(Boolean, default=False, nullable=False)
    include_if_has_content = Column(Boolean, default=False, nullable=False)
    status = Column(String(50), default="draft", nullable=False, index=True)
    current_version = Column(String(50), default="0.1", nullable=False)
    current_version_id = Column(String(36), nullable=True)

    partner = relationship("Partner", back_populates="psmf_components")
    parent = relationship(
        "PSMFComponent",
        remote_side="PSMFComponent.id",
        back_populates="children",
    )
    children = relationship("PSMFComponent", back_populates="parent")
    blocks = relationship(
        "PSMFBlock",
        back_populates="component",
        cascade="all, delete-orphan",
        order_by="PSMFBlock.block_order.asc()",
    )
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
    row_version = Column(Integer, default=1, nullable=False)
    __mapper_args__ = {"version_id_col": row_version}
    content = Column(Text, nullable=False)
    status = Column(String(50), default="draft", nullable=False)
    change_summary = Column(Text, nullable=True)
    change_reason = Column(Text, nullable=True)
    created_by = Column(String(255), nullable=True)
    created_by_text = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )
    approved_by = Column(String(255), nullable=True)
    approved_by_text = Column(String(255), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approval_comment = Column(Text, nullable=True)
    is_locked = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(String(36), nullable=True)
    delete_reason = Column(Text, nullable=True)

    component = relationship("PSMFComponent", back_populates="versions")


class PSMFBlock(CommonMixin, Base):
    __tablename__ = "psmf_blocks"
    __table_args__ = (
        UniqueConstraint("component_id", "code", "block_type", name="uq_psmf_block_code_type"),
        Index("ix_psmf_block_component", "component_id"),
        Index("ix_psmf_block_type", "block_type"),
        Index("ix_psmf_block_status", "status"),
        Index("ix_psmf_block_partner", "partner_id"),
        Index("ix_psmf_block_source", "source_module"),
    )

    component_id = Column(String(36), ForeignKey("psmf_components.id"), nullable=False)
    original_code = Column(String(100), nullable=True)
    original_title = Column(String(255), nullable=True)
    code = Column(String(100), nullable=False)
    title_ru = Column(String(255), nullable=False)
    title_en = Column(String(255), nullable=True)
    block_type = Column(String(50), nullable=False)
    block_order = Column(Integer, default=0, nullable=False)
    scope = Column(String(50), default="GLOBAL", nullable=False)
    partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=True)
    source_module = Column(String(100), nullable=True)
    source_filter_json = Column(Text, nullable=True)
    is_required = Column(Boolean, default=True, nullable=False)
    include_if_has_content = Column(Boolean, default=False, nullable=False)
    status = Column(String(50), default="draft", nullable=False, index=True)
    current_version_id = Column(String(36), nullable=True)

    component = relationship("PSMFComponent", back_populates="blocks")
    partner = relationship("Partner")
    versions = relationship(
        "PSMFBlockVersion",
        back_populates="block",
        cascade="all, delete-orphan",
        order_by="PSMFBlockVersion.created_at.desc()",
    )


class PSMFBlockVersion(CommonMixin, Base):
    row_version = Column(Integer, default=1, nullable=False)
    __tablename__ = "psmf_block_versions"
    __table_args__ = (
        UniqueConstraint("block_id", "version", name="uq_psmf_block_version"),
        Index("ix_psmf_block_version_block", "block_id"),
        Index("ix_psmf_block_version_status", "status"),
        Index("ix_psmf_block_partner_review", "partner_review_status"),
    )

    block_id = Column(String(36), ForeignKey("psmf_blocks.id"), nullable=False)
    version = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    status = Column(String(50), default="draft", nullable=False)
    source_data_snapshot_json = Column(Text, nullable=True)
    change_summary = Column(Text, nullable=True)
    change_reason = Column(Text, nullable=True)
    created_by_text = Column(String(255), nullable=True)
    approved_by_text = Column(String(255), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approval_comment = Column(Text, nullable=True)
    partner_review_status = Column(String(50), nullable=True)
    partner_reviewed_by_text = Column(String(255), nullable=True)
    partner_reviewed_at = Column(DateTime(timezone=True), nullable=True)
    partner_review_comment = Column(Text, nullable=True)
    is_locked = Column(Boolean, default=False, nullable=False)

    block = relationship("PSMFBlock", back_populates="versions")


class PSMFComment(CommonMixin, Base):
    __tablename__ = "psmf_comments"
    __table_args__ = (
        Index("ix_psmf_comment_object", "object_type", "object_id"),
        Index("ix_psmf_comment_status", "comment_status"),
    )

    object_type = Column(String(50), nullable=False)
    object_id = Column(String(36), nullable=False)
    comment_text = Column(Text, nullable=False)
    comment_status = Column(String(50), default="open", nullable=False)
    created_by_text = Column(String(255), nullable=True)
    resolved_by_text = Column(String(255), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)


class PSMFPartnerBuild(CommonMixin, Base):
    row_version = Column(Integer, default=1, nullable=False)
    __tablename__ = "psmf_partner_builds"
    __table_args__ = (
        Index("ix_psmf_build_partner", "partner_id"),
        Index("ix_psmf_build_type_status", "build_type", "build_status"),
    )

    partner_id = Column(String(36), ForeignKey("tblPartners.id"), nullable=False)
    build_type = Column(String(50), default="DRAFT", nullable=False)
    build_status = Column(String(50), default="generated", nullable=False)
    version = Column(String(50), default="1.0", nullable=False)
    generated_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    generated_by_text = Column(String(255), nullable=True)
    approved_by_text = Column(String(255), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approval_comment = Column(Text, nullable=True)
    partner_review_status = Column(String(50), nullable=True)
    partner_reviewed_by_text = Column(String(255), nullable=True)
    partner_reviewed_at = Column(DateTime(timezone=True), nullable=True)
    partner_review_comment = Column(Text, nullable=True)
    warnings_json = Column(Text, nullable=True)
    docx_file_path = Column(String(500), nullable=True)
    pdf_file_path = Column(String(500), nullable=True)
    package_file_path = Column(String(500), nullable=True)

    partner = relationship("Partner")
    items = relationship(
        "PSMFPartnerBuildItem",
        back_populates="build",
        cascade="all, delete-orphan",
        order_by="PSMFPartnerBuildItem.created_at.asc()",
    )
    documents = relationship(
        "PSMFBuildDocument",
        back_populates="build",
        cascade="all, delete-orphan",
        order_by="PSMFBuildDocument.annex_code.asc(), PSMFBuildDocument.inclusion_order.asc()",
    )


class PSMFPartnerBuildItem(CommonMixin, Base):
    __tablename__ = "psmf_partner_build_items"
    __table_args__ = (
        Index("ix_psmf_build_item_build", "build_id"),
        Index("ix_psmf_build_item_component", "component_id"),
        Index("ix_psmf_build_item_block", "block_id"),
    )

    build_id = Column(String(36), ForeignKey("psmf_partner_builds.id"), nullable=False)
    component_id = Column(String(36), ForeignKey("psmf_components.id"), nullable=False)
    component_version_id = Column(String(36), ForeignKey("psmf_component_versions.id"), nullable=True)
    block_id = Column(String(36), ForeignKey("psmf_blocks.id"), nullable=True)
    block_version_id = Column(String(36), ForeignKey("psmf_block_versions.id"), nullable=True)
    code = Column(String(100), nullable=False)
    title_ru = Column(String(255), nullable=False)
    block_type = Column(String(50), nullable=True)
    status = Column(String(50), nullable=True)
    included_content_snapshot = Column(Text, nullable=True)
    source_data_snapshot_json = Column(Text, nullable=True)

    build = relationship("PSMFPartnerBuild", back_populates="items")
    component = relationship("PSMFComponent")
    component_version = relationship("PSMFComponentVersion")
    block = relationship("PSMFBlock")
    block_version = relationship("PSMFBlockVersion")


class PSMFBuildDocument(CommonMixin, Base):
    """Immutable document-version snapshot included in a concrete PSMF build."""

    __tablename__ = "psmf_build_documents"
    __table_args__ = (
        UniqueConstraint("build_id", "attachment_id", "document_version_id", "annex_code", name="uq_psmf_build_document"),
        Index("ix_psmf_build_document_build", "build_id"),
        Index("ix_psmf_build_document_attachment", "attachment_id"),
        Index("ix_psmf_build_document_version", "document_version_id"),
        Index("ix_psmf_build_document_annex", "annex_code"),
    )

    build_id = Column(String(36), ForeignKey("psmf_partner_builds.id"), nullable=False)
    attachment_id = Column(String(36), ForeignKey("tblAttachments.id"), nullable=False)
    document_version_id = Column(String(36), ForeignKey("tblDocumentVersions.id"), nullable=False)
    annex_code = Column(String(50), nullable=False)
    inclusion_order = Column(Integer, default=0, nullable=False)
    document_title_snapshot = Column(String(255), nullable=False)
    version_label_snapshot = Column(String(50), nullable=False)
    file_name_snapshot = Column(String(255), nullable=False)
    checksum_sha256_snapshot = Column(String(64), nullable=True)
    page_count_snapshot = Column(Integer, nullable=True)

    build = relationship("PSMFPartnerBuild", back_populates="documents")
    attachment = relationship("Attachment", back_populates="build_inclusions")
    document_version = relationship("DocumentVersion", back_populates="build_inclusions")


class PSMFSettings(CommonMixin, Base):
    __tablename__ = "psmf_settings"

    company_name = Column(String(255), default="ООО «АРС»", nullable=False)
    default_language = Column(String(10), default="ru", nullable=False)
    psmf_template_version = Column(String(50), default="RU-PSMF-2026.1", nullable=False)
    title_en_enabled = Column(Boolean, default=False, nullable=False)
    enable_docx_export = Column(Boolean, default=True, nullable=False)
    enable_pdf_export = Column(Boolean, default=True, nullable=False)
    default_empty_state_text = Column(
        Text,
        default="На дату формирования МФСФ сведения отсутствуют / не применимо.",
        nullable=False,
    )
    include_optional_annex_k_if_has_content = Column(Boolean, default=True, nullable=False)
    skip_empty_partner_specific_blocks = Column(Boolean, default=False, nullable=False)
    final_build_requires_all_required_components_approved = Column(Boolean, default=True, nullable=False)
    allow_final_build_with_warnings_after_confirmation = Column(Boolean, default=True, nullable=False)


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
