from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditTrail, utcnow


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
    ip_address: str | None = None,
    correlation_id: str | None = None,
) -> AuditTrail:
    entry = AuditTrail(
        entity_type=entity_type,
        entity_id=entity_id,
        case_id=case_id,
        action=action,
        field_name=field_name,
        old_value=stringify(old_value),
        new_value=stringify(new_value),
        change_reason=change_reason,
        user_id=user_id,
        timestamp=utcnow(),
        ip_address=ip_address,
        correlation_id=correlation_id,
    )
    db.add(entry)
    return entry
