from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PermissionRead(ORMModel):
    id: str
    permission_code: str
    permission_name: str
    description: str | None = None


class RoleRead(ORMModel):
    id: str
    role_code: str
    role_name: str
    description: str | None = None
    is_system: bool


class UserCreate(BaseModel):
    email: str
    full_name: str | None = None
    role_ids: list[str] = Field(default_factory=list)


class UserRead(ORMModel):
    id: str
    email: str
    full_name: str | None = None
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserRoleAssign(BaseModel):
    role_id: str


class RolePermissionsUpdate(BaseModel):
    permission_ids: list[str] = Field(default_factory=list)


class PartnerBase(BaseModel):
    partner_code: str
    partner_name: str
    partner_type: Literal["archive", "fn", "registration_in_progress"] = "fn"
    reconciliation_frequency: Literal["monthly", "quarterly", "not_conducted"] = (
        "not_conducted"
    )


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


class ContractBase(BaseModel):
    partner_id: str
    product_id: str
    contract_type: Literal[
        "pharmacovigilance_agreement",
        "additional_agreement",
    ] = "pharmacovigilance_agreement"
    contract_number: str
    contract_date: date
    valid_until: date


class ContractCreate(ContractBase):
    pass


class ContractRead(ContractBase, ORMModel):
    id: str
    is_current: bool
    created_at: datetime
    updated_at: datetime


class ContractContactBase(BaseModel):
    partner_id: str
    last_name: str
    first_name: str
    patronymic: str | None = None
    email: str
    position: str
    is_current: bool = True


class ContractContactCreate(ContractContactBase):
    pass


class ContractContactRead(ContractContactBase, ORMModel):
    id: str
    created_at: datetime
    updated_at: datetime


class PartnerReconciliationBase(BaseModel):
    partner_id: str
    contact_id: str | None = None
    reconciliation_date: date | None = None
    period_start: date
    period_end: date
    language: Literal["ru", "en"] = "ru"
    reconciliation_status: str = "draft"
    prepared_by: str | None = None
    products: str | None = None
    sent_date: date | None = None
    response_date: date | None = None
    discrepancy_description: str | None = None
    document_id: str | None = None


class PartnerReconciliationCreate(PartnerReconciliationBase):
    pass


class PartnerReconciliationRead(PartnerReconciliationBase, ORMModel):
    id: str
    contact_name: str | None = None
    contact_email: str | None = None
    our_case_count: int
    partner_case_count: int
    matched_count: int
    discrepancy_count: int
    confirmed_by_user: str | None = None
    confirmed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PartnerReconciliationItemBase(BaseModel):
    internal_case_id: str | None = None
    partner_case_id: str | None = None
    product_id: str | None = None
    source_side: str
    case_type: str = "initial"
    receipt_date_our_company: date | None = None
    receipt_date_partner: date | None = None
    transfer_date_our_company: date | None = None
    transfer_date_partner: date | None = None
    adverse_event: str | None = None
    seriousness: str | None = None
    reconciliation_status: str = "requires_review"
    match_confidence: float | None = None
    match_method: str | None = None
    reviewer_comment: str | None = None
    confirmed_by_user: str | None = None
    internal_case_number: str | None = None
    partner_case_number: str | None = None
    partner_name: str | None = None
    product_name: str | None = None
    active_substance: str | None = None
    patient: str | None = None
    country: str | None = None
    source_type: str | None = None
    short_description: str | None = None
    linked_item_id: str | None = None


class PartnerReconciliationItemCreate(PartnerReconciliationItemBase):
    pass


class PartnerReconciliationItemRead(PartnerReconciliationItemBase, ORMModel):
    id: str
    reconciliation_id: str
    created_at: datetime
    updated_at: datetime


class PartnerReconciliationItemUpdate(BaseModel):
    internal_case_id: str | None = None
    reconciliation_status: str
    reviewer_comment: str | None = None
    confirmed_by_user: str | None = None


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


class PSURPlanBase(BaseModel):
    active_substance_id: str
    product_id: str | None = None
    psur_type: Literal["PSUR", "PBRER", "Local Safety Report"] = "PSUR"
    reporting_period_start: date
    reporting_period_end: date
    data_lock_point: date
    due_date_internal: date | None = None
    due_date_submission: date | None = None
    frequency: str | None = None
    status: str = "Planned"
    responsible_user_id: str | None = None
    reviewer_user_id: str | None = None


class PSURPlanCreate(PSURPlanBase):
    pass


class PSURPlanRead(PSURPlanBase, ORMModel):
    id: str
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    is_deleted: bool


class PSURPlanUpdate(BaseModel):
    product_id: str | None = None
    psur_type: str
    reporting_period_start: date
    reporting_period_end: date
    data_lock_point: date
    due_date_internal: date | None = None
    due_date_submission: date | None = None
    frequency: str | None = None
    responsible_user_id: str | None = None
    reviewer_user_id: str | None = None


class PSURStatusUpdate(BaseModel):
    status: str
    change_reason: str | None = None


class PSURProductCreate(BaseModel):
    product_id: str
    country: str | None = None
    marketing_authorisation_number: str | None = None
    included_in_report: bool = True
    comment: str | None = None


class PSURProductRead(PSURProductCreate, ORMModel):
    id: str
    psur_plan_id: str
    created_at: datetime
    updated_at: datetime


class PSURCaseUpdate(BaseModel):
    case_included: bool = True
    reason_excluded: str | None = None
    seriousness: str | None = None
    listedness: str | None = None
    case_origin: str | None = None
    assessment_comment: str | None = None


class PSURCaseRead(PSURCaseUpdate, ORMModel):
    id: str
    psur_plan_id: str
    case_id: str
    created_at: datetime
    updated_at: datetime


class PSURPartnerRequestCreate(BaseModel):
    partner_id: str
    contact_person_id: str | None = None
    request_type: str = "Cases"
    request_date: date | None = None
    due_date: date | None = None
    status: str = "Not Sent"
    response_summary: str | None = None
    document_id: str | None = None


class PSURPartnerRequestRead(PSURPartnerRequestCreate, ORMModel):
    id: str
    psur_plan_id: str
    created_by: str | None = None
    response_date: date | None = None
    created_at: datetime
    updated_at: datetime


class PSURSectionUpdate(BaseModel):
    section_status: str
    assigned_to: str | None = None
    reviewed_by: str | None = None
    section_text: str | None = None
    comment: str | None = None
    human_confirmed: bool = False


class PSURSectionRead(PSURSectionUpdate, ORMModel):
    id: str
    psur_plan_id: str
    section_code: str
    section_title: str
    gpt_generated: bool
    gpt_prompt: str | None = None
    gpt_output_json: str | None = None
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    last_updated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PSURDocumentCreate(BaseModel):
    document_type: str = "Draft"
    file_name: str
    file_path: str | None = None
    version_number: str | None = None
    is_final: bool = False
    comment: str | None = None


class PSURDocumentRead(PSURDocumentCreate, ORMModel):
    id: str
    psur_plan_id: str
    uploaded_by: str | None = None
    uploaded_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TaskCreate(BaseModel):
    title: str
    description: str | None = None
    status: str = "new"
    priority: str = "normal"
    due_date: date | None = None
    assigned_to_user_id: str | None = None
    responsible_person: str | None = None
    related_entity_type: str | None = None
    related_entity_id: str | None = None
    comment: str | None = None


class TaskRead(TaskCreate, ORMModel):
    id: str
    created_by: str | None = None
    updated_by: str | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class IncomingRequestCreate(BaseModel):
    source_text: str
    request_type: str | None = None
    partner_id: str | None = None
    product_id: str | None = None
    active_substance: str | None = None
    possible_icsr: str = "no"
    patient_information: str | None = None
    adverse_event: str | None = None
    seriousness: str | None = None
    seriousness_criteria: str | None = None
    missing_information: str | None = None
    recommended_next_action: str | None = None
    validity_assessment: str | None = None
    gpt_json_output: str | None = None
    status: str = "confirmed"


class IncomingRequestRead(IncomingRequestCreate, ORMModel):
    id: str
    human_confirmed: bool
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime
    updated_at: datetime


class SOPCreate(BaseModel):
    sop_code: str
    title: str
    document_type: str = "SOP"
    version: str = "1.0"
    status: str = "Draft"
    process_area: str = "Other"
    owner: str
    reviewer: str | None = None
    approver: str | None = None
    approval_date: date | None = None
    effective_date: date
    next_review_date: date
    revision_reason: str | None = None
    file_path: str | None = None
    file_url: str | None = None
    description: str | None = None
    training_required: bool = False


class SOPRead(SOPCreate, ORMModel):
    id: str
    created_by: str | None = None
    updated_by: str | None = None
    created_at: datetime
    updated_at: datetime


class PSMFComponentCreate(BaseModel):
    code: str
    title: str
    component_type: Literal["MAIN_SECTION", "ANNEX"] = "MAIN_SECTION"
    scope: Literal["GLOBAL", "PARTNER_SPECIFIC"] = "GLOBAL"
    partner_id: str | None = None
    description: str | None = None
    status: Literal["draft", "under_review", "approved"] = "draft"
    current_version: str = "0.1"


class PSMFComponentRead(PSMFComponentCreate, ORMModel):
    id: str
    created_at: datetime
    updated_at: datetime


class PSMFComponentVersionCreate(BaseModel):
    component_id: str
    version: str
    content: str
    status: Literal["draft", "under_review", "approved"] = "draft"
    change_summary: str | None = None
    created_by: str | None = None


class PSMFComponentVersionRead(PSMFComponentVersionCreate, ORMModel):
    id: str
    approved_by: str | None = None
    approved_at: datetime | None = None
    is_locked: bool
    created_at: datetime
    updated_at: datetime


class PSMFContentUpdate(BaseModel):
    content: str
    change_summary: str | None = None


class DashboardStats(BaseModel):
    total_safety_reports: int
    reports_awaiting_triage: int
    total_cases: int
    open_cases: int
    serious_cases: int
    submissions_due: int
    overdue_submissions: int
    total_partners: int
    total_products: int
    active_pv_agreements: int
    open_tasks: int
    overdue_tasks: int
    reconciliations_in_progress: int
    incoming_requests_needing_review: int
    effective_sops: int
    sops_requiring_review: int
    sops_under_approval: int
    sops_review_due_60_days: int


class CaseOverview(BaseModel):
    case: CaseRead
    patients: list[PatientRead] = Field(default_factory=list)
    products: list[CaseProductRead] = Field(default_factory=list)
    reactions: list[ReactionRead] = Field(default_factory=list)
    followups: list[FollowUpRead] = Field(default_factory=list)
    submissions: list[SubmissionRead] = Field(default_factory=list)
