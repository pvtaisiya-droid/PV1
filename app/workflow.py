from app.statuses import status_codes


ICSR_ALLOWED_TRANSITIONS = {
    "new": {"triage", "data_entry", "closed"},
    "triage": {"data_entry", "medical_review", "closed"},
    "data_entry": {"medical_review", "qc", "closed"},
    "medical_review": {"data_entry", "qc", "closed"},
    "qc": {"data_entry", "medical_review", "ready_for_submission", "closed"},
    "ready_for_submission": {"qc", "submitted", "closed"},
    "submitted": {"reopened", "closed"},
    "closed": {"reopened"},
    "reopened": {"data_entry", "medical_review", "qc", "ready_for_submission", "closed"},
}


def normalize_workflow_status(status: str | None) -> str:
    return (status or "").strip().lower()


def validate_case_status(status: str | None) -> str:
    status_code = normalize_workflow_status(status)
    if status_code not in status_codes("case"):
        raise ValueError(f"Unknown ICSR workflow status: {status}.")
    return status_code


def validate_icsr_transition(current_status: str | None, next_status: str | None) -> str:
    current_code = normalize_workflow_status(current_status) or "new"
    next_code = validate_case_status(next_status)
    if current_code == next_code:
        return next_code
    allowed_next = ICSR_ALLOWED_TRANSITIONS.get(current_code, set())
    if next_code not in allowed_next:
        raise ValueError(
            f"Invalid ICSR workflow transition: {current_code} -> {next_code}."
        )
    return next_code
