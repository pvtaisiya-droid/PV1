from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PartnerBase(BaseModel):
    partner_code: str
    partner_name: str
    partner_type: str = "partner"
    country_code: str | None = None
    region: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    pv_responsible_person: str | None = None
    sdea_required: bool = False
    is_active: bool = True


class PartnerCreate(PartnerBase):
    pass


class PartnerRead(PartnerBase, ORMModel):
    id: str
    created_at: datetime
    updated_at: datetime


class SubstanceBase(BaseModel):
    substance_name: str
    substance_name_normalized: str | None = None
    inn_name: str | None = None
    cas_number: str | None = None
    atc_code: str | None = None
    substance_type: str | None = None
    is_active: bool = True


class SubstanceCreate(SubstanceBase):
    pass


class SubstanceRead(SubstanceBase, ORMModel):
    id: str
    created_at: datetime
    updated_at: datetime


class ProductBase(BaseModel):
    product_code: str
    product_name: str
    product_name_normalized: str | None = None
    dosage_form: str | None = None
    strength: str | None = None
    route: str | None = None
    mah_partner_id: str | None = None
    authorization_number: str | None = None
    authorization_country_code: str | None = None
    authorization_status: str | None = None
    is_company_product: bool = True
    is_active: bool = True


class ProductCreate(ProductBase):
    active_substance: str | None = None


class ProductRead(ProductBase, ORMModel):
    id: str
    created_at: datetime
    updated_at: datetime


class ProductSubstanceCreate(BaseModel):
    product_id: str
    substance_id: str
    substance_role: str = "active"
    strength_value: str | None = None
    strength_unit: str | None = None
    is_primary: bool = True


class ProductSubstanceRead(ProductSubstanceCreate, ORMModel):
    id: str
    created_at: datetime
    updated_at: datetime


class SafetyReportBase(BaseModel):
    safety_report_number: str | None = None
    received_at: datetime | None = None
    received_date: date | None = None
    source_type: str | None = "email"
    partner_id: str | None = None
    reporter_name: str | None = None
    reporter_email: str | None = None
    reporter_country_code: str | None = None
    raw_subject: str | None = None
    raw_text: str | None = None
    triage_status: str = "new"
    triage_comment: str | None = None
    is_valid_icsr: bool = False
    minimum_criteria_patient: bool = False
    minimum_criteria_reporter: bool = False
    minimum_criteria_product: bool = False
    minimum_criteria_event: bool = False


class SafetyReportCreate(SafetyReportBase):
    pass


class SafetyReportRead(SafetyReportBase, ORMModel):
    id: str
    triaged_by_user_id: str | None = None
    triaged_at: datetime | None = None
    case_id: str | None = None
    created_at: datetime
    updated_at: datetime


class TriageUpdate(BaseModel):
    triage_status: str
    triage_comment: str | None = None
    is_valid_icsr: bool = False
    minimum_criteria_patient: bool = False
    minimum_criteria_reporter: bool = False
    minimum_criteria_product: bool = False
    minimum_criteria_event: bool = False
    change_reason: str | None = None


class CaseBase(BaseModel):
    case_number: str | None = None
    worldwide_case_id: str | None = None
    safety_report_id: str | None = None
    partner_id: str | None = None
    case_version: int = 1
    case_type: str | None = "spontaneous"
    report_type: str | None = None
    initial_received_date: date | None = None
    latest_received_date: date | None = None
    country_of_occurrence: str | None = None
    seriousness: str | None = "non-serious"
    listedness: str | None = None
    expectedness: str | None = None
    case_outcome: str | None = None
    narrative: str | None = None
    company_comment: str | None = None
    medical_review_comment: str | None = None
    workflow_status: str = "new"
    assigned_to_user_id: str | None = None
    due_date: date | None = None


class CaseCreate(CaseBase):
    pass


class CaseRead(CaseBase, ORMModel):
    id: str
    is_locked: bool
    locked_by_user_id: str | None = None
    locked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CaseStatusUpdate(BaseModel):
    workflow_status: str
    change_reason: str | None = None


class PatientCreate(BaseModel):
    patient_role: str = "patient"
    patient_initials: str | None = None
    patient_identifier: str | None = None
    sex: str | None = None
    date_of_birth: date | None = None
    birth_date_raw: str | None = None
    age_value: float | None = None
    age_unit: str | None = "years"
    weight_kg: float | None = None
    height_cm: float | None = None
    pregnancy_status: str | None = None
    medical_history_text: str | None = None


class PatientRead(PatientCreate, ORMModel):
    id: str
    case_id: str
    created_at: datetime
    updated_at: datetime


class CaseProductCreate(BaseModel):
    product_id: str | None = None
    reported_product_name: str | None = None
    active_substance_text: str | None = None
    drug_role: str = "suspect"
    indication_text: str | None = None
    indication_meddra_pt_code: str | None = None
    dose_value: str | None = None
    dose_unit: str | None = None
    route: str | None = None
    frequency: str | None = None
    therapy_start_date: date | None = None
    therapy_start_date_raw: str | None = None
    therapy_end_date: date | None = None
    therapy_end_date_raw: str | None = None
    action_taken: str | None = None
    dechallenge_result: str | None = None
    rechallenge_result: str | None = None
    batch_lot_number: str | None = None


class CaseProductRead(CaseProductCreate, ORMModel):
    id: str
    case_id: str
    created_at: datetime
    updated_at: datetime


class ReactionCreate(BaseModel):
    reported_term: str
    verbatim_term: str | None = None
    meddra_llt_code: str | None = None
    meddra_llt_name: str | None = None
    meddra_pt_code: str | None = None
    meddra_pt_name: str | None = None
    meddra_soc_code: str | None = None
    meddra_soc_name: str | None = None
    onset_date: date | None = None
    onset_date_raw: str | None = None
    end_date: date | None = None
    end_date_raw: str | None = None
    outcome: str | None = None
    is_serious: bool = False
    seriousness_death: bool = False
    seriousness_life_threatening: bool = False
    seriousness_hospitalization: bool = False
    seriousness_disability: bool = False
    seriousness_congenital_anomaly: bool = False
    seriousness_other_medically_important: bool = False


class ReactionRead(ReactionCreate, ORMModel):
    id: str
    case_id: str
    created_at: datetime
    updated_at: datetime


class FollowUpCreate(BaseModel):
    follow_up_number: int | None = None
    received_date: date | None = None
    source_type: str | None = None
    description: str | None = None
    significant_new_information: bool = False
    processed_by_user_id: str | None = None
    processed_at: datetime | None = None
    case_version_after_follow_up: int | None = None


class FollowUpRead(FollowUpCreate, ORMModel):
    id: str
    case_id: str
    created_at: datetime
    updated_at: datetime


class SubmissionCreate(BaseModel):
    submission_number: str | None = None
    submission_object_type: str = "case"
    case_id: str | None = None
    pbrer_id: str | None = None
    rmp_id: str | None = None
    recipient_partner_id: str | None = None
    recipient_type: str = "partner"
    recipient_country_code: str | None = None
    submission_type: str | None = "icsr"
    submission_format: str | None = "email"
    submission_status: str = "planned"
    due_date: date | None = None
    submitted_at: datetime | None = None
    acknowledged_at: datetime | None = None
    acknowledgement_code: str | None = None
    error_message: str | None = None
    submitted_by_user_id: str | None = None


class SubmissionRead(SubmissionCreate, ORMModel):
    id: str
    created_at: datetime
    updated_at: datetime


class SubmissionStatusUpdate(BaseModel):
    submission_status: str
    error_message: str | None = None


class DashboardStats(BaseModel):
    total_safety_reports: int
    reports_awaiting_triage: int
    total_cases: int
    open_cases: int
    serious_cases: int
    submissions_due: int
    overdue_submissions: int


class CaseOverview(BaseModel):
    case: CaseRead
    patients: list[PatientRead] = Field(default_factory=list)
    products: list[CaseProductRead] = Field(default_factory=list)
    reactions: list[ReactionRead] = Field(default_factory=list)
    followups: list[FollowUpRead] = Field(default_factory=list)
    submissions: list[SubmissionRead] = Field(default_factory=list)
