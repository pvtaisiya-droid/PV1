from dataclasses import dataclass


@dataclass(frozen=True)
class StatusDefinition:
    code: str
    label: str
    terminal: bool = False
    locked: bool = False


CASE_STATUSES = [
    StatusDefinition("new", "New"),
    StatusDefinition("triage", "Triage"),
    StatusDefinition("data_entry", "Data entry"),
    StatusDefinition("medical_review", "Medical review"),
    StatusDefinition("qc", "QC"),
    StatusDefinition("ready_for_submission", "Ready for submission"),
    StatusDefinition("submitted", "Submitted", terminal=True, locked=True),
    StatusDefinition("closed", "Closed", terminal=True, locked=True),
    StatusDefinition("reopened", "Reopened"),
]

TRIAGE_STATUSES = [
    StatusDefinition("new", "New"),
    StatusDefinition("in_triage", "In triage"),
    StatusDefinition("valid_icsr", "Valid ICSR"),
    StatusDefinition("invalid", "Invalid", terminal=True),
    StatusDefinition("non_safety", "Non-safety", terminal=True),
    StatusDefinition("converted_to_case", "Converted to case", terminal=True, locked=True),
]

DOCUMENT_STATUSES = [
    StatusDefinition("draft", "Draft"),
    StatusDefinition("under_review", "Under review"),
    StatusDefinition("active", "Active"),
    StatusDefinition("archived", "Archived", terminal=True, locked=True),
]

SOP_STATUSES = [
    StatusDefinition("Draft", "Draft"),
    StatusDefinition("Under Review", "Under Review"),
    StatusDefinition("Under Approval", "Under Approval"),
    StatusDefinition("Effective", "Effective", locked=True),
    StatusDefinition("Requires Review", "Requires Review"),
    StatusDefinition("Archived", "Archived", terminal=True, locked=True),
    StatusDefinition("Cancelled", "Cancelled", terminal=True, locked=True),
]

TASK_STATUSES = [
    StatusDefinition("new", "New"),
    StatusDefinition("in_progress", "In progress"),
    StatusDefinition("waiting_for_response", "Waiting for response"),
    StatusDefinition("completed", "Completed", terminal=True),
    StatusDefinition("cancelled", "Cancelled", terminal=True),
]

SUBMISSION_STATUSES = [
    StatusDefinition("planned", "Planned"),
    StatusDefinition("ready", "Ready"),
    StatusDefinition("submitted", "Submitted", terminal=True, locked=True),
    StatusDefinition("acknowledged", "Acknowledged", terminal=True, locked=True),
    StatusDefinition("failed", "Failed"),
    StatusDefinition("cancelled", "Cancelled", terminal=True),
]

PSUR_STATUSES = [
    StatusDefinition("Planned", "Planned"),
    StatusDefinition("Data Collection", "Data Collection"),
    StatusDefinition("Case Selection", "Case Selection"),
    StatusDefinition("Drafting", "Drafting"),
    StatusDefinition("Under Review", "Under Review"),
    StatusDefinition("QA Review", "QA Review"),
    StatusDefinition("Approved", "Approved", locked=True),
    StatusDefinition("Submitted", "Submitted", terminal=True, locked=True),
    StatusDefinition("Archived", "Archived", terminal=True, locked=True),
]

STATUS_REGISTRY = {
    "case": CASE_STATUSES,
    "triage": TRIAGE_STATUSES,
    "document": DOCUMENT_STATUSES,
    "sop": SOP_STATUSES,
    "task": TASK_STATUSES,
    "submission": SUBMISSION_STATUSES,
    "psur": PSUR_STATUSES,
}


def status_codes(domain: str) -> list[str]:
    return [status.code for status in STATUS_REGISTRY[domain]]


def status_definition(domain: str, code: str) -> StatusDefinition | None:
    return next((status for status in STATUS_REGISTRY[domain] if status.code == code), None)


def is_locked_status(domain: str, code: str | None) -> bool:
    definition = status_definition(domain, code or "")
    return bool(definition and definition.locked)


def is_terminal_status(domain: str, code: str | None) -> bool:
    definition = status_definition(domain, code or "")
    return bool(definition and definition.terminal)
