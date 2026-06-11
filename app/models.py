import uuid
from datetime import datetime, timezone

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
    version = Column(Integer, default=1, nullable=False)


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


class Partner(CommonMixin, Base):
    __tablename__ = "tblPartners"

    partner_code = Column(String(50), unique=True, index=True, nullable=False)
    partner_name = Column(String(255), index=True, nullable=False)
    partner_type = Column(String(50), default="partner", index=True, nullable=False)
    country_code = Column(String(2), index=True, nullable=True)
    region = Column(String(100), nullable=True)
    email = Column(String(255), nullable=True)
    phone = Column(String(100), nullable=True)
    address = Column(Text, nullable=True)
    pv_responsible_person = Column(String(255), nullable=True)
    sdea_required = Column(Boolean, default=False, nullable=False)

    safety_reports = relationship("SafetyReport", back_populates="partner")
    cases = relationship("Case", back_populates="partner")
    products = relationship("Product", back_populates="mah_partner")
    submissions = relationship("Submission", back_populates="recipient_partner")


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

    mah_partner = relationship("Partner", back_populates="products")
    substance_links = relationship(
        "ProductSubstance",
        back_populates="product",
        cascade="all, delete-orphan",
    )
    case_products = relationship("CaseProduct", back_populates="product")


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
    mime_type = Column(String(100), nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    storage_path = Column(String(500), nullable=True)
    checksum_sha256 = Column(String(64), nullable=True)
    uploaded_by_user_id = Column(String(36), ForeignKey("tblUsers.id"), nullable=True)
    uploaded_at = Column(DateTime(timezone=True), nullable=True)

    case = relationship("Case", back_populates="attachments")
    safety_report = relationship("SafetyReport", back_populates="attachments")
    uploaded_by = relationship("User")


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


class AuditTrail(CommonMixin, Base):
    __tablename__ = "tblAuditTrail"
    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_case_time", "case_id", "timestamp"),
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
    ip_address = Column(String(100), nullable=True)
    correlation_id = Column(String(100), nullable=True)

    case = relationship("Case", back_populates="audit_entries")
    user = relationship("User", back_populates="audit_entries")
