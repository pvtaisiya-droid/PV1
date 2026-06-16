from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.audit import log_audit
from app.models import (
    AuditTrail,
    Partner,
    PSMFComponent,
    PSMFComponentVersion,
    User,
    utcnow,
)


PSMF_SOURCE_MODULE = "PSMF"

PSMF_COMPONENT_TYPES = ["MAIN_SECTION", "ANNEX"]
PSMF_SCOPES = ["GLOBAL", "PARTNER_SPECIFIC"]
PSMF_STATUSES = ["draft", "under_review", "approved"]

PSMF_TYPE_LABELS = {
    "MAIN_SECTION": "Основной раздел",
    "ANNEX": "Приложение",
}
PSMF_SCOPE_LABELS = {
    "GLOBAL": "Общий",
    "PARTNER_SPECIFIC": "Партнер-специфичный",
}
PSMF_STATUS_LABELS = {
    "draft": "Черновик",
    "under_review": "На проверке",
    "approved": "Утверждено",
}
PSMF_ACTION_LABELS = {
    "created": "Создан компонент МФСФ",
    "content_updated": "Изменен текст компонента",
    "submitted_for_review": "Компонент отправлен на проверку",
    "approved": "Компонент утвержден",
    "new_version_created": "Создана новая версия компонента",
    "preview_generated": "Сформирован предварительный МФСФ",
    "html_downloaded": "Скачан HTML МФСФ",
}


def demo_actor_name() -> str:
    return "Demo User"


def actor_name(user: Any | None = None) -> str:
    if user and getattr(user, "full_name", None):
        return user.full_name
    if user and getattr(user, "email", None):
        return user.email
    return demo_actor_name()


def first_user_id(db: Session) -> str | None:
    user = (
        db.query(User)
        .filter(User.is_deleted.is_(False), User.is_active.is_(True))
        .order_by(User.created_at.asc())
        .first()
    )
    return user.id if user else None


def short_text(value: str | None, limit: int = 500) -> str:
    value = (value or "").strip()
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."


def version_sort_key(value: str | None) -> tuple[int, ...]:
    parts: list[int] = []
    for part in (value or "").split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts or [0])


def increment_version(value: str | None) -> str:
    parts = list(version_sort_key(value))
    if not parts:
        return "0.1"
    if len(parts) == 1:
        parts.append(1)
    else:
        parts[-1] += 1
    return ".".join(str(part) for part in parts)


def current_version(component: PSMFComponent) -> PSMFComponentVersion | None:
    for version in component.versions:
        if version.version == component.current_version and not version.is_deleted:
            return version
    active_versions = [version for version in component.versions if not version.is_deleted]
    return max(active_versions, key=lambda row: version_sort_key(row.version), default=None)


def latest_approved_version(component: PSMFComponent) -> PSMFComponentVersion | None:
    versions = [
        version
        for version in component.versions
        if version.status == "approved" and not version.is_deleted
    ]
    return max(versions, key=lambda row: version_sort_key(row.version), default=None)


def log_psmf_event(
    db: Session,
    *,
    entity_type: str,
    entity_id: str,
    action: str,
    title: str,
    details: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    field_name: str | None = None,
    user_id: str | None = None,
) -> None:
    log_audit(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        field_name=field_name,
        old_value=old_value,
        new_value=new_value,
        user_id=user_id,
        source_module=PSMF_SOURCE_MODULE,
        comment=f"{title}. {details or ''}".strip(),
    )


def list_psmf_components(db: Session) -> list[PSMFComponent]:
    return (
        db.query(PSMFComponent)
        .options(joinedload(PSMFComponent.partner), joinedload(PSMFComponent.versions))
        .filter(PSMFComponent.is_deleted.is_(False))
        .order_by(PSMFComponent.code.asc(), PSMFComponent.title.asc())
        .all()
    )


def get_psmf_component(db: Session, component_id: str) -> PSMFComponent | None:
    return (
        db.query(PSMFComponent)
        .options(joinedload(PSMFComponent.partner), joinedload(PSMFComponent.versions))
        .filter(PSMFComponent.id == component_id, PSMFComponent.is_deleted.is_(False))
        .first()
    )


def psmf_dashboard_stats(db: Session) -> dict[str, int]:
    components = list_psmf_components(db)
    return {
        "total": len(components),
        "global": sum(1 for row in components if row.scope == "GLOBAL"),
        "partner_specific": sum(
            1 for row in components if row.scope == "PARTNER_SPECIFIC"
        ),
        "draft": sum(1 for row in components if row.status == "draft"),
        "under_review": sum(1 for row in components if row.status == "under_review"),
        "approved": sum(1 for row in components if row.status == "approved"),
    }


def save_component_draft(
    db: Session,
    component: PSMFComponent,
    *,
    content: str,
    change_summary: str | None = None,
    user_id: str | None = None,
) -> PSMFComponent:
    version = current_version(component)
    if component.status != "draft" or not version or version.is_locked:
        raise ValueError("Only draft PSMF components can be edited.")

    old_content = version.content
    version.content = content.strip()
    version.change_summary = change_summary
    version.updated_at = utcnow()
    component.updated_at = utcnow()
    component.version += 1

    log_psmf_event(
        db,
        entity_type="PSMF_VERSION",
        entity_id=version.id,
        action="content_updated",
        title=component.title,
        details=change_summary or "Текст компонента обновлен.",
        field_name="content",
        old_value=short_text(old_content),
        new_value=short_text(version.content),
        user_id=user_id,
    )
    db.commit()
    db.refresh(component)
    return component


def submit_component_for_review(
    db: Session,
    component: PSMFComponent,
    *,
    user_id: str | None = None,
) -> PSMFComponent:
    version = current_version(component)
    if component.status != "draft" or not version:
        raise ValueError("Only draft PSMF components can be submitted for review.")

    component.status = "under_review"
    component.updated_at = utcnow()
    component.version += 1
    version.status = "under_review"
    version.is_locked = True
    version.updated_at = utcnow()

    log_psmf_event(
        db,
        entity_type="PSMF_COMPONENT",
        entity_id=component.id,
        action="submitted_for_review",
        title=component.title,
        details=f"Версия {version.version}.",
        field_name="status",
        old_value="draft",
        new_value="under_review",
        user_id=user_id,
    )
    db.commit()
    db.refresh(component)
    return component


def approve_component(
    db: Session,
    component: PSMFComponent,
    *,
    approved_by: str,
    user_id: str | None = None,
) -> PSMFComponent:
    version = current_version(component)
    if component.status != "under_review" or not version:
        raise ValueError("Only PSMF components under review can be approved.")

    now = utcnow()
    component.status = "approved"
    component.updated_at = now
    component.version += 1
    version.status = "approved"
    version.is_locked = True
    version.approved_by = approved_by
    version.approved_at = now
    version.updated_at = now

    log_psmf_event(
        db,
        entity_type="PSMF_COMPONENT",
        entity_id=component.id,
        action="approved",
        title=component.title,
        details=f"Версия {version.version} утверждена пользователем {approved_by}.",
        field_name="status",
        old_value="under_review",
        new_value="approved",
        user_id=user_id,
    )
    db.commit()
    db.refresh(component)
    return component


def create_new_component_version(
    db: Session,
    component: PSMFComponent,
    *,
    created_by: str,
    user_id: str | None = None,
) -> PSMFComponent:
    if component.status != "approved":
        raise ValueError("A new PSMF version can be created only from an approved component.")

    source_version = latest_approved_version(component) or current_version(component)
    if not source_version:
        raise ValueError("Approved source version was not found.")

    new_version_number = increment_version(source_version.version)
    new_version = PSMFComponentVersion(
        component_id=component.id,
        version=new_version_number,
        content=source_version.content,
        status="draft",
        change_summary=f"Создана новая версия из утвержденной версии {source_version.version}.",
        created_by=created_by,
        is_locked=False,
    )
    db.add(new_version)
    db.flush()
    component.current_version = new_version_number
    component.status = "draft"
    component.updated_at = utcnow()
    component.version += 1

    log_psmf_event(
        db,
        entity_type="PSMF_VERSION",
        entity_id=new_version.id,
        action="new_version_created",
        title=component.title,
        details=f"Версия {source_version.version} -> {new_version_number}.",
        old_value=source_version.version,
        new_value=new_version_number,
        field_name="version",
        user_id=user_id,
    )
    db.commit()
    db.refresh(component)
    return component


def ensure_demo_partner(db: Session, *, code: str, name: str) -> Partner:
    partner = (
        db.query(Partner)
        .filter(
            Partner.is_deleted.is_(False),
            or_(Partner.partner_code == code, Partner.partner_name == name),
        )
        .first()
    )
    if partner:
        return partner

    partner = Partner(
        partner_code=code,
        partner_name=name,
        partner_type="fn",
        reconciliation_frequency="not_conducted",
    )
    db.add(partner)
    db.flush()
    return partner


def create_seed_component(
    db: Session,
    *,
    code: str,
    title: str,
    component_type: str,
    scope: str,
    partner_id: str | None,
    status: str,
    version_number: str,
    description: str,
    content: str,
    user_id: str | None,
) -> None:
    existing = (
        db.query(PSMFComponent)
        .filter(PSMFComponent.code == code, PSMFComponent.is_deleted.is_(False))
        .first()
    )
    if existing:
        return

    component = PSMFComponent(
        code=code,
        title=title,
        component_type=component_type,
        scope=scope,
        partner_id=partner_id,
        description=description,
        status=status,
        current_version=version_number,
    )
    db.add(component)
    db.flush()

    version = PSMFComponentVersion(
        component_id=component.id,
        version=version_number,
        content=content,
        status=status,
        change_summary="Начальная тестовая версия для MVP.",
        created_by=demo_actor_name(),
        approved_by=demo_actor_name() if status == "approved" else None,
        approved_at=utcnow() if status == "approved" else None,
        is_locked=status != "draft",
    )
    db.add(version)
    db.flush()

    log_psmf_event(
        db,
        entity_type="PSMF_COMPONENT",
        entity_id=component.id,
        action="created",
        title=component.title,
        details=f"Создан тестовый компонент МФСФ, версия {version_number}.",
        new_value=component.title,
        user_id=user_id,
    )


def ensure_psmf_seed_data(db: Session) -> None:
    user_id = first_user_id(db)
    partner_1 = ensure_demo_partner(
        db,
        code="PSMF-PARTNER-001",
        name="Партнер 1",
    )
    ensure_demo_partner(
        db,
        code="PSMF-PARTNER-002",
        name="Партнер 2",
    )

    create_seed_component(
        db,
        code="1",
        title="Раздел об уполномоченном лице по фармаконадзору",
        component_type="MAIN_SECTION",
        scope="GLOBAL",
        partner_id=None,
        status="approved",
        version_number="1.0",
        description="общий раздел основного МФСФ, применяется ко всем партнерам",
        content=(
            "Настоящий раздел описывает сведения об уполномоченном лице по "
            "фармаконадзору ООО «АРС», его обязанностях, квалификации, "
            "контактной информации и области ответственности. Раздел является "
            "общим для всех партнеров, использующих данную систему фармаконадзора."
        ),
        user_id=user_id,
    )
    create_seed_component(
        db,
        code="Приложение А-1",
        title="Информация об УЛФ",
        component_type="ANNEX",
        scope="GLOBAL",
        partner_id=None,
        status="approved",
        version_number="1.0",
        description="общее приложение к основному МФСФ, применяется ко всем партнерам",
        content=(
            "Настоящее приложение содержит информацию об уполномоченном лице "
            "по фармаконадзору ООО «АРС», включая ФИО, должность, контактные "
            "данные, сведения о квалификации и доступности. Приложение является "
            "общим для всех партнеров."
        ),
        user_id=user_id,
    )
    create_seed_component(
        db,
        code="Приложение Б-1.1",
        title="Организационная структура партнера",
        component_type="ANNEX",
        scope="PARTNER_SPECIFIC",
        partner_id=partner_1.id,
        status="draft",
        version_number="0.1",
        description="специфическое приложение для конкретного партнера",
        content=(
            "Настоящее приложение описывает организационную структуру "
            "{{partner_name}}, включая подразделения и ответственных лиц, "
            "участвующих в деятельности по фармаконадзору. Приложение является "
            "партнер-специфичным и применяется только к выбранному партнеру."
        ),
        user_id=user_id,
    )
    db.commit()


def build_partner_psmf(db: Session, partner_id: str) -> dict[str, Any] | None:
    partner = (
        db.query(Partner)
        .filter(Partner.id == partner_id, Partner.is_deleted.is_(False))
        .first()
    )
    if not partner:
        return None

    global_components = (
        db.query(PSMFComponent)
        .options(joinedload(PSMFComponent.versions))
        .filter(
            PSMFComponent.is_deleted.is_(False),
            PSMFComponent.scope == "GLOBAL",
            PSMFComponent.status == "approved",
        )
        .order_by(PSMFComponent.code.asc())
        .all()
    )
    partner_components = (
        db.query(PSMFComponent)
        .options(joinedload(PSMFComponent.versions), joinedload(PSMFComponent.partner))
        .filter(
            PSMFComponent.is_deleted.is_(False),
            PSMFComponent.scope == "PARTNER_SPECIFIC",
            PSMFComponent.partner_id == partner.id,
        )
        .order_by(PSMFComponent.code.asc())
        .all()
    )

    rows = []
    warnings: list[str] = []
    if not partner_components:
        warnings.append("Для выбранного партнера отсутствуют партнер-специфичные приложения.")

    for component in [*global_components, *partner_components]:
        version = current_version(component)
        if not version:
            warnings.append(f"Для компонента {component.code} отсутствует текст версии.")
            continue
        rendered_content = version.content.replace("{{partner_name}}", partner.partner_name)
        if component.status != "approved":
            warnings.append(
                f"Компонент {component.code} не утвержден: "
                f"{PSMF_STATUS_LABELS.get(component.status, component.status)}."
            )
        if "{{" in rendered_content or "}}" in rendered_content:
            warnings.append(f"В компоненте {component.code} остались placeholders.")
        rows.append(
            {
                "component": component,
                "version": version,
                "content": rendered_content,
            }
        )

    return {
        "partner": partner,
        "rows": rows,
        "warnings": warnings,
        "generated_at": utcnow(),
    }


def log_partner_preview(
    db: Session,
    *,
    partner: Partner,
    row_count: int,
    warning_count: int,
    user_id: str | None = None,
) -> None:
    log_psmf_event(
        db,
        entity_type="PARTNER_PSMF_PREVIEW",
        entity_id=partner.id,
        action="preview_generated",
        title=f"МФСФ для {partner.partner_name}",
        details=f"Компонентов: {row_count}; предупреждений: {warning_count}.",
        new_value=partner.partner_name,
        user_id=user_id,
    )
    db.commit()


def log_partner_export(
    db: Session,
    *,
    partner: Partner,
    row_count: int,
    user_id: str | None = None,
) -> None:
    log_psmf_event(
        db,
        entity_type="PARTNER_PSMF_EXPORT",
        entity_id=partner.id,
        action="html_downloaded",
        title=f"HTML МФСФ для {partner.partner_name}",
        details=f"Экспортировано компонентов: {row_count}.",
        new_value=partner.partner_name,
        user_id=user_id,
    )
    db.commit()


def render_partner_psmf_html(preview: dict[str, Any]) -> str:
    partner = preview["partner"]
    rows = preview["rows"]
    warnings = preview["warnings"]
    generated_at: datetime = preview["generated_at"]

    component_items = "\n".join(
        (
            "<li>"
            f"{escape(row['component'].code)} - {escape(row['component'].title)} "
            f"(версия {escape(row['version'].version)})"
            "</li>"
        )
        for row in rows
    )
    warning_items = "\n".join(f"<li>{escape(warning)}</li>" for warning in warnings)
    section_html = "\n".join(
        (
            "<section>"
            f"<h2>{escape(row['component'].code)}. {escape(row['component'].title)}</h2>"
            f"<p><strong>Версия:</strong> {escape(row['version'].version)}; "
            f"<strong>Статус:</strong> "
            f"{escape(PSMF_STATUS_LABELS.get(row['component'].status, row['component'].status))}</p>"
            f"<div class=\"component-text\">{escape(row['content'])}</div>"
            "</section>"
        )
        for row in rows
    )
    warnings_block = (
        f"<section><h2>Предупреждения</h2><ul>{warning_items}</ul></section>"
        if warnings
        else "<section><h2>Предупреждения</h2><p>Предупреждений нет.</p></section>"
    )
    return f"""<!doctype html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <title>МФСФ для {escape(partner.partner_name)}</title>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.5; color: #24313d; }}
        main {{ max-width: 980px; margin: 32px auto; }}
        h1 {{ color: #008dc6; }}
        section {{ border-top: 1px solid #d8d9db; padding: 18px 0; }}
        .component-text {{ white-space: pre-wrap; }}
    </style>
</head>
<body>
<main>
    <h1>МФСФ для {escape(partner.partner_name)}</h1>
    <p><strong>Дата формирования:</strong> {escape(generated_at.strftime("%Y-%m-%d %H:%M"))}</p>
    <section>
        <h2>Включенные компоненты</h2>
        <ul>{component_items}</ul>
    </section>
    {section_html}
    {warnings_block}
</main>
</body>
</html>
"""


def list_psmf_audit_entries(db: Session) -> list[AuditTrail]:
    return (
        db.query(AuditTrail)
        .options(joinedload(AuditTrail.user), joinedload(AuditTrail.case))
        .filter(
            AuditTrail.is_deleted.is_(False),
            AuditTrail.source_module == PSMF_SOURCE_MODULE,
        )
        .order_by(func.coalesce(AuditTrail.changed_at, AuditTrail.timestamp).desc())
        .all()
    )
