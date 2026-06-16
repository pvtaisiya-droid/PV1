from typing import Any

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
    "IncomingRequest": "Incoming Requests",
    "SOP": "SOPs and Instructions",
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
        ip_address=ip_address,
        correlation_id=correlation_id,
    )
    db.add(entry)
    return entry
