from typing import Any
from contextvars import ContextVar

from sqlalchemy.orm import Session

from app.models import AuditTrail, utcnow


ENTITY_SOURCE_MODULES = {
    "Partner": "Partners",
    "Product": "Products",
    "Substance": "Substances",
    "ProductSubstance": "Product-substance links",
    "Contract": "Contracts",
    "ContractContact": "Contacts",
    "PartnerReconciliation": "Partner Reconciliation",
    "PartnerReconciliationItem": "Partner Reconciliation",
    "SafetyReport": "PV Intake",
    "Case": "ICSRs",
    "Patient": "ICSRs",
    "CaseProduct": "ICSRs",
    "Reaction": "ICSRs",
    "FollowUp": "ICSRs",
    "Attachment": "Documents",
    "DocumentVersion": "Documents",
    "IncomingRequest": "Incoming Requests",
    "SOP": "SOPs and Instructions",
    "SOPVersion": "SOPs and Instructions",
    "Submission": "Submissions",
    "PSURPlan": "PSUR / PBRER",
    "PSURProduct": "PSUR / PBRER",
    "PSURCase": "PSUR / PBRER",
    "PSURPartnerRequest": "PSUR / PBRER",
    "PSURSection": "PSUR / PBRER",
    "PSURDocument": "PSUR / PBRER",
    "PSMF_COMPONENT": "PSMF",
    "PSMF_VERSION": "PSMF",
    "PARTNER_PSMF_PREVIEW": "PSMF",
    "PARTNER_PSMF_EXPORT": "PSMF",
    "Task": "Tasks",
    "User": "Users & Roles",
    "Role": "Users & Roles",
    "Permission": "Users & Roles",
}

CURRENT_IP_ADDRESS: ContextVar[str | None] = ContextVar("pv_ip_address", default=None)
CURRENT_CORRELATION_ID: ContextVar[str | None] = ContextVar(
    "pv_correlation_id",
    default=None,
)


def set_audit_context(
    *,
    ip_address: str | None = None,
    correlation_id: str | None = None,
) -> tuple:
    return (
        CURRENT_IP_ADDRESS.set(ip_address),
        CURRENT_CORRELATION_ID.set(correlation_id),
    )


def reset_audit_context(tokens: tuple | None) -> None:
    if not tokens:
        return
    ip_token, correlation_token = tokens
    CURRENT_IP_ADDRESS.reset(ip_token)
    CURRENT_CORRELATION_ID.reset(correlation_token)


def stringify(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def log_audit(
    db: Session,
    *,
    entity_type: str,
    entity_id: str,
    action: str,
    case_id: str | None = None,
    field_name: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    change_reason: str | None = None,
    user_id: str | None = None,
    changed_by: str | None = None,
    source_module: str | None = None,
    comment: str | None = None,
    ip_address: str | None = None,
    correlation_id: str | None = None,
) -> AuditTrail:
    changed_at = utcnow()
    actor_id = changed_by or user_id
    audit_comment = comment or change_reason
    request_ip = ip_address or CURRENT_IP_ADDRESS.get()
    request_correlation_id = correlation_id or CURRENT_CORRELATION_ID.get()
    entry = AuditTrail(
        entity_type=entity_type,
        entity_id=entity_id,
        case_id=case_id,
        action=action,
        field_name=field_name,
        old_value=stringify(old_value),
        new_value=stringify(new_value),
        change_reason=change_reason,
        user_id=actor_id,
        timestamp=changed_at,
        changed_by=actor_id,
        changed_at=changed_at,
        source_module=source_module or ENTITY_SOURCE_MODULES.get(entity_type),
        comment=audit_comment,
        ip_address=request_ip,
        correlation_id=request_correlation_id,
    )
    db.add(entry)
    return entry
