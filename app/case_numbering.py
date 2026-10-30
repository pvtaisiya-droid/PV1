import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import case as sql_case
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.audit import log_audit
from app.config import get_settings
from app.models import (
    Case,
    CaseIdentifier,
    CaseNumberCounter,
    CasePartnerLink,
    Partner,
    utcnow,
)


UNASSIGNED_SCOPE = "UNASSIGNED"
PARTNER_SCOPE_PREFIX = "PARTNER:"
CASE_NUMBER_PATTERN = re.compile(r"^(?P<year>\d{4})-(?P<sequence>\d{3,})$")
UNASSIGNED_NUMBER_PATTERN = re.compile(
    r"^UNASSIGNED-(?P<year>\d{4})-(?P<sequence>\d{3,})$"
)
PARTNER_CODE_PATTERN = re.compile(r"^[A-Z0-9-]+$")


def business_timezone():
    configured = get_settings().business_timezone
    try:
        return ZoneInfo(configured)
    except ZoneInfoNotFoundError as exc:
        if configured == "Europe/Moscow":
            return timezone(timedelta(hours=3), name="Europe/Moscow")
        if configured in {"UTC", "Etc/UTC"}:
            return timezone.utc
        raise ValueError(f"Unknown business timezone: {configured}") from exc


def business_year(registered_at: datetime | date | None = None) -> int:
    if isinstance(registered_at, date) and not isinstance(registered_at, datetime):
        return registered_at.year
    moment = registered_at or utcnow()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(business_timezone()).year


def scope_key(partner_id: str | None) -> str:
    return f"{PARTNER_SCOPE_PREFIX}{partner_id}" if partner_id else UNASSIGNED_SCOPE


def format_sequence(value: int) -> str:
    return f"{value:03d}"


def format_case_number(year: int, sequence: int, *, partner_id: str | None) -> str:
    ordinary = f"{year}-{format_sequence(sequence)}"
    return ordinary if partner_id else f"UNASSIGNED-{ordinary}"


def normalize_partner_code(value: str) -> str:
    return (value or "").strip().upper()


def validate_partner_code(value: str) -> str:
    normalized = normalize_partner_code(value)
    if not normalized or not PARTNER_CODE_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Код партнёра должен содержать только латинские заглавные буквы, цифры и дефис."
        )
    return normalized


def _atomic_counter_value(
    db: Session,
    *,
    scope: str,
    year: int,
    minimum: int | None = None,
) -> int:
    """Increment or reserve a per-scope counter in one database statement."""
    table = CaseNumberCounter.__table__
    dialect = db.get_bind().dialect.name
    initial_value = max(1, minimum or 1)

    if dialect == "sqlite":
        insert_statement = sqlite_insert(table).values(
            scope_key=scope,
            numbering_year=year,
            last_value=initial_value,
        )
        if minimum is None:
            next_value = table.c.last_value + 1
        else:
            next_value = sql_case(
                (table.c.last_value < minimum, minimum),
                else_=table.c.last_value,
            )
        statement = insert_statement.on_conflict_do_update(
            index_elements=[table.c.scope_key, table.c.numbering_year],
            set_={
                "last_value": next_value,
                "updated_at": utcnow(),
                "version": table.c.version + 1,
            },
        ).returning(table.c.last_value)
        return int(db.execute(statement).scalar_one())

    if dialect == "postgresql":
        insert_statement = postgresql_insert(table).values(
            scope_key=scope,
            numbering_year=year,
            last_value=initial_value,
        )
        if minimum is None:
            next_value = table.c.last_value + 1
        else:
            next_value = sql_case(
                (table.c.last_value < minimum, minimum),
                else_=table.c.last_value,
            )
        statement = insert_statement.on_conflict_do_update(
            index_elements=[table.c.scope_key, table.c.numbering_year],
            set_={
                "last_value": next_value,
                "updated_at": utcnow(),
                "version": table.c.version + 1,
            },
        ).returning(table.c.last_value)
        return int(db.execute(statement).scalar_one())

    counter = (
        db.query(CaseNumberCounter)
        .filter(
            CaseNumberCounter.scope_key == scope,
            CaseNumberCounter.numbering_year == year,
        )
        .with_for_update()
        .one_or_none()
    )
    if counter is None:
        counter = CaseNumberCounter(
            scope_key=scope,
            numbering_year=year,
            last_value=initial_value,
        )
        db.add(counter)
    elif minimum is None:
        counter.last_value += 1
    else:
        counter.last_value = max(counter.last_value, minimum)
    db.flush()
    return int(counter.last_value)


def next_sequence(db: Session, *, partner_id: str | None, year: int) -> int:
    return _atomic_counter_value(
        db,
        scope=scope_key(partner_id),
        year=year,
    )


def reserve_sequence(
    db: Session,
    *,
    partner_id: str | None,
    year: int,
    sequence: int,
) -> int:
    return _atomic_counter_value(
        db,
        scope=scope_key(partner_id),
        year=year,
        minimum=sequence,
    )


def _parsed_number(value: str, partner_id: str | None) -> tuple[int, int] | None:
    pattern = CASE_NUMBER_PATTERN if partner_id else UNASSIGNED_NUMBER_PATTERN
    match = pattern.fullmatch(value)
    if not match:
        return None
    return int(match.group("year")), int(match.group("sequence"))


def assign_case_number(
    db: Session,
    case: Case,
    *,
    partner_id: str | None,
    registered_at: datetime | date | None = None,
    supplied_number: str | None = None,
) -> None:
    """Assign a new number, or adopt an explicitly supplied legacy number."""
    supplied = (supplied_number or "").strip()
    case.numbering_partner_id = partner_id
    case.number_is_provisional = partner_id is None

    if supplied:
        duplicate = (
            db.query(Case.id)
            .filter(
                Case.case_number == supplied,
                Case.numbering_partner_id == partner_id,
                Case.is_deleted.is_(False),
            )
            .first()
        )
        if duplicate:
            raise ValueError("Такой номер уже используется в реестре выбранного партнёра.")
        parsed = _parsed_number(supplied, partner_id)
        case.case_number = supplied
        if parsed:
            year, sequence = parsed
            reserve_sequence(
                db,
                partner_id=partner_id,
                year=year,
                sequence=sequence,
            )
            case.numbering_scope = scope_key(partner_id)
            case.numbering_year = year
            case.numbering_sequence = sequence
        return

    year = business_year(registered_at)
    sequence = next_sequence(db, partner_id=partner_id, year=year)
    case.case_number = format_case_number(year, sequence, partner_id=partner_id)
    case.numbering_scope = scope_key(partner_id)
    case.numbering_year = year
    case.numbering_sequence = sequence


def add_case_partner_link(
    db: Session,
    case: Case,
    partner: Partner,
    *,
    relationship_type: str = "related",
) -> CasePartnerLink:
    existing = (
        db.query(CasePartnerLink)
        .filter(
            CasePartnerLink.case_id == case.id,
            CasePartnerLink.partner_id == partner.id,
        )
        .one_or_none()
    )
    if existing:
        return existing
    link = CasePartnerLink(
        case_id=case.id,
        partner_id=partner.id,
        relationship_type=relationship_type or "related",
    )
    db.add(link)
    return link


def add_case_identifier(
    db: Session,
    case: Case,
    *,
    identifier_value: str,
    identifier_type: str,
    partner_id: str | None = None,
    organization_name: str | None = None,
    received_date: date | None = None,
    is_primary: bool = False,
) -> tuple[CaseIdentifier, bool]:
    value = (identifier_value or "").strip()
    if not value:
        raise ValueError("Значение внешнего идентификатора обязательно.")
    possible_duplicate = bool(
        db.query(CaseIdentifier.id)
        .filter(
            CaseIdentifier.partner_id == partner_id,
            CaseIdentifier.identifier_value == value,
            CaseIdentifier.is_deleted.is_(False),
            CaseIdentifier.case_id != case.id,
        )
        .first()
    )
    if is_primary:
        for row in case.identifiers:
            if row.identifier_type != "previous_internal_number":
                row.is_primary = False
    identifier = CaseIdentifier(
        case_id=case.id,
        identifier_value=value,
        identifier_type=identifier_type or "partner_case_number",
        partner_id=partner_id,
        organization_name=(organization_name or "").strip() or None,
        received_date=received_date,
        is_primary=is_primary,
    )
    case.identifiers.append(identifier)
    return identifier, possible_duplicate


def finalize_provisional_number(
    db: Session,
    case: Case,
    partner: Partner,
    *,
    actor_id: str | None = None,
) -> Case:
    if not case.number_is_provisional or case.numbering_partner_id:
        raise ValueError("Окончательный партнёр нумерации уже назначен.")
    old_code = case.full_case_code
    year = case.numbering_year or business_year(case.created_at)
    sequence = next_sequence(db, partner_id=partner.id, year=year)
    add_case_identifier(
        db,
        case,
        identifier_value=old_code,
        identifier_type="previous_internal_number",
        received_date=date.today(),
    )
    case.case_number = format_case_number(year, sequence, partner_id=partner.id)
    case.numbering_partner_id = partner.id
    case.numbering_partner = partner
    case.numbering_scope = scope_key(partner.id)
    case.numbering_year = year
    case.numbering_sequence = sequence
    case.number_is_provisional = False
    if not case.partner_id:
        case.partner_id = partner.id
    add_case_partner_link(db, case, partner, relationship_type="numbering")
    case.version += 1
    log_audit(
        db,
        entity_type="Case",
        entity_id=case.id,
        case_id=case.id,
        action="number_finalized",
        field_name="case_number",
        old_value=old_code,
        new_value=case.full_case_code,
        user_id=actor_id,
        comment="Предварительный номер заменён окончательным номером партнёра.",
    )
    db.commit()
    db.refresh(case)
    return case


def correct_numbering_partner(
    db: Session,
    case: Case,
    partner: Partner,
    *,
    reason: str,
    actor_id: str | None = None,
) -> Case:
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Для исправления партнёра нумерации необходимо указать причину.")
    if case.number_is_provisional:
        raise ValueError("Для предварительного номера используйте обычное назначение партнёра.")
    if case.numbering_partner_id == partner.id:
        raise ValueError("Выбран текущий партнёр нумерации.")
    old_code = case.full_case_code
    year = case.numbering_year or business_year(case.created_at)
    sequence = next_sequence(db, partner_id=partner.id, year=year)
    add_case_identifier(
        db,
        case,
        identifier_value=old_code,
        identifier_type="previous_internal_number",
        received_date=date.today(),
    )
    case.case_number = format_case_number(year, sequence, partner_id=partner.id)
    case.numbering_partner_id = partner.id
    case.numbering_partner = partner
    case.numbering_scope = scope_key(partner.id)
    case.numbering_year = year
    case.numbering_sequence = sequence
    add_case_partner_link(db, case, partner, relationship_type="numbering")
    case.version += 1
    log_audit(
        db,
        entity_type="Case",
        entity_id=case.id,
        case_id=case.id,
        action="numbering_partner_corrected",
        field_name="numbering_partner_id",
        old_value=old_code,
        new_value=case.full_case_code,
        change_reason=reason,
        user_id=actor_id,
    )
    db.commit()
    db.refresh(case)
    return case
