from app import models
from app.concurrency import protect_record
from app.unit_of_work import atomic_write
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from pydantic import PositiveInt, TypeAdapter, ValidationError
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app import crud
from app.accounting_activity_report import (
    SERVICE_OPTIONS,
    build_activity_report,
    build_activity_report_xlsx,
    previous_quarter_bounds,
    quarter_bounds,
)
from app.auth import require_any_permission, require_permission
from app.database import get_db
from app.concurrency import require_version
from app.reconciliation_excel import build_xlsx
from app.templating import templates
from app.ui_helpers import redirect_with_message


router = APIRouter()


@router.get("/accounting-data", response_class=HTMLResponse)
def accounting_data_page(request: Request, db: Session = Depends(get_db)):
    today = date.today()
    previous_quarter_start, previous_quarter_end = previous_quarter_bounds(today)
    current_quarter_start, _ = quarter_bounds(today)
    filters = {
        "partner_id": (request.query_params.get("partner_id") or "").strip(),
        "contract_id": (request.query_params.get("contract_id") or "").strip(),
        "year": (request.query_params.get("year") or "").strip(),
        "quarter": (request.query_params.get("quarter") or "").strip(),
        "status": (request.query_params.get("status") or "").strip(),
        "responsible_user_id": (request.query_params.get("responsible_user_id") or "").strip(),
    }
    periods = crud.list_accounting_periods(
        db,
        partner_id=filters["partner_id"] or None,
        contract_id=filters["contract_id"] or None,
        year=int(filters["year"]) if filters["year"].isdigit() else None,
        quarter=int(filters["quarter"]) if filters["quarter"].isdigit() else None,
        status=filters["status"] or None,
        responsible_user_id=filters["responsible_user_id"] or None,
    )
    return templates.TemplateResponse(
        request,
        "accounting_data.html",
        {
            "request": request,
            "active_page": "accounting_data",
            "periods": periods,
            "partners": crud.list_partners(db),
            "contracts": crud.list_contracts(db),
            "users": crud.list_users(db),
            "statuses": crud.ACCOUNTING_STATUSES,
            "filters": filters,
            "current_year": date.today().year,
            "activity_services": SERVICE_OPTIONS,
            "activity_default_date_from": previous_quarter_start,
            "activity_default_date_to": previous_quarter_end,
            "activity_previous_date_from": previous_quarter_start,
            "activity_previous_date_to": previous_quarter_end,
            "activity_current_date_from": current_quarter_start,
            "activity_current_date_to": today,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
        },
    )


@router.post(
    "/accounting-data",
    dependencies=[Depends(require_any_permission("create", "manage_reference_data"))],
)
@atomic_write
def create_accounting_period_form(
    partner_id: str = Form(...),
    contract_id: str = Form(...),
    year: int = Form(...),
    quarter: int = Form(...),
    responsible_user_id: str | None = Form(None),
    comment: str | None = Form(None),
    expected_contract_version: int = Form(..., gt=0),
    db: Session = Depends(get_db),
):
    protect_record(db, models.Contract, contract_id, expected_contract_version)
    try:
        period = crud.create_accounting_period(
            db,
            partner_id=partner_id,
            contract_id=contract_id,
            year=year,
            quarter=quarter,
            responsible_user_id=(responsible_user_id or "").strip() or None,
            comment=comment,
        )
    except IntegrityError:
        db.rollback()
        return redirect_with_message(
            "/accounting-data",
            validation="Расшифровка для выбранного договора и квартала уже существует.",
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message("/accounting-data", validation=str(exc))
    return redirect_with_message(
        f"/accounting-data/{period.id}",
        message="Расшифровка создана.",
    )


@router.get("/accounting-data/export", dependencies=[Depends(require_permission("export"))])
def export_accounting_periods_register(db: Session = Depends(get_db)):
    rows = [
        [
            "Год",
            "Квартал",
            "Партнер",
            "Договор",
            "Количество услуг",
            "Сумма по договору",
            "Статус",
            "Дата подготовки",
            "Ответственный",
        ]
    ]
    periods = crud.list_accounting_periods(db)
    for period in periods:
        rows.append(
            [
                period.year,
                period.quarter,
                period.partner.partner_name if period.partner else "",
                period.contract.contract_number if period.contract else "",
                len([line for line in period.lines if not line.is_deleted]),
                period.total_contract_amount,
                crud.ACCOUNTING_STATUSES.get(period.status, period.status),
                period.prepared_at or period.created_at,
                period.responsible_user.full_name if period.responsible_user else "",
            ]
        )
    return Response(
        build_xlsx([("Реестр", rows)]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=accounting-data-register.xlsx"},
    )


@router.get("/accounting-data/activity-export", dependencies=[Depends(require_permission("export"))])
def export_accounting_activity(
    date_from: date,
    date_to: date,
    service: list[str] | None = Query(None),
    db: Session = Depends(get_db),
):
    try:
        report = build_activity_report(
            db,
            date_from=date_from,
            date_to=date_to,
            services=service or [],
        )
    except ValueError as exc:
        return redirect_with_message("/accounting-data", validation=str(exc))
    filename = quote(f"accounting-services-{date_from.isoformat()}-{date_to.isoformat()}.xlsx")
    return Response(
        build_activity_report_xlsx(report),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.get("/accounting-data/{period_id}", response_class=HTMLResponse)
def accounting_period_detail(period_id: str, request: Request, db: Session = Depends(get_db)):
    period = crud.get_accounting_period(db, period_id)
    if not period:
        return redirect_with_message("/accounting-data", error="Расшифровка не найдена.")
    return templates.TemplateResponse(
        request,
        "accounting_data_detail.html",
        {
            "request": request,
            "active_page": "accounting_data",
            "period": period,
            "services": crud.list_accounting_services(db),
            "tariffs": crud.list_contract_accounting_tariffs(db, period.contract_id),
            "statuses": crud.ACCOUNTING_STATUSES,
            "object_types": [
                "МНН",
                "Препарат",
                "Сообщение по безопасности",
                "ПООБ",
                "ПУР",
                "Литературный мониторинг",
                "Документ",
                "Другое",
            ],
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": request.query_params.get("validation"),
        },
    )


@router.post(
    "/accounting-data/{period_id}/lines",
    dependencies=[Depends(require_any_permission("create", "edit", "manage_reference_data"))],
)
def add_accounting_line_form(
    period_id: str,
    expected_version: int = Form(..., ge=1),
    service_id: str = Form(...),
    object_type: str | None = Form(None),
    product_or_inn: str | None = Form(None),
    quantity: float = Form(...),
    unit: str | None = Form(None),
    contract_price: float | None = Form(None),
    contract_amount: float | None = Form(None),
    source: str | None = Form(None),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
):
    period = crud.get_accounting_period(db, period_id)
    if not period:
        return redirect_with_message("/accounting-data", error="Расшифровка не найдена.")
    require_version(period, expected_version)
    try:
        crud.add_accounting_line(
            db,
            period,
            service_id=service_id,
            object_type=object_type,
            product_or_inn=product_or_inn,
            quantity=quantity,
            unit=unit,
            contract_price=contract_price,
            contract_amount=contract_amount,
            source=source,
            comment=comment,
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message(f"/accounting-data/{period_id}", validation=str(exc))
    return redirect_with_message(f"/accounting-data/{period_id}", message="Услуга добавлена.")


@router.post(
    "/accounting-data/{period_id}/lines/{line_id}/delete",
    dependencies=[Depends(require_any_permission("soft_delete", "manage_reference_data"))],
)
def delete_accounting_line_form(
    period_id: str,
    line_id: str,
    expected_version: int = Form(..., ge=1),
    expected_line_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    period = crud.get_accounting_period(db, period_id)
    line = db.get(crud.AccountingDataLine, line_id)
    if not period or not line or line.is_deleted or line.accounting_period_id != period_id:
        raise HTTPException(status_code=404, detail="Строка выбранной расшифровки не найдена.")
    require_version(period, expected_version)
    require_version(line, expected_line_version)
    crud.delete_accounting_line(db, line_id, accounting_period_id=period_id)
    return redirect_with_message(f"/accounting-data/{period_id}", message="Строка удалена.")


@router.post(
    "/accounting-data/{period_id}/status",
    dependencies=[Depends(require_any_permission("edit", "manage_reference_data"))],
)
def update_accounting_period_status_form(
    period_id: str,
    expected_version: int = Form(..., ge=1),
    status: str = Form(...),
    db: Session = Depends(get_db),
):
    period = crud.get_accounting_period(db, period_id)
    if not period:
        return redirect_with_message("/accounting-data", error="Расшифровка не найдена.")
    require_version(period, expected_version)
    try:
        crud.update_accounting_period_status(db, period, status)
    except ValueError as exc:
        db.rollback()
        return redirect_with_message(f"/accounting-data/{period_id}", validation=str(exc))
    return redirect_with_message(f"/accounting-data/{period_id}", message="Статус обновлен.")


@router.post(
    "/accounting-data/{period_id}/recalculate",
    dependencies=[Depends(require_any_permission("edit", "manage_reference_data"))],
)
def recalculate_accounting_period_form(
    period_id: str, expected_version: int = Form(..., ge=1), db: Session = Depends(get_db),
):
    period = crud.get_accounting_period(db, period_id)
    if not period:
        return redirect_with_message("/accounting-data", error="Расшифровка не найдена.")
    require_version(period, expected_version)
    crud.recalculate_accounting_period(db, period)
    return redirect_with_message(f"/accounting-data/{period_id}", message="Сумма пересчитана.")


@router.get("/accounting-data/{period_id}/export", dependencies=[Depends(require_permission("export"))])
def export_accounting_period(period_id: str, db: Session = Depends(get_db)):
    period = crud.get_accounting_period(db, period_id)
    if not period:
        return redirect_with_message("/accounting-data", error="Расшифровка не найдена.")
    filename = quote(f"accounting-data-{period.year}-Q{period.quarter}.xlsx")
    return Response(
        crud.export_accounting_period_xlsx(period),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.post(
    "/partners/{partner_id}/accounting-info",
    dependencies=[Depends(require_any_permission("edit", "manage_reference_data"))],
)
def save_partner_accounting_info_form(
    partner_id: str,
    expected_version: int = Form(..., ge=1),
    expected_info_version: int = Form(..., ge=0),
    expected_contact_versions: str = Form(...),
    legal_entity_name: str | None = Form(None),
    inn: str | None = Form(None),
    kpp: str | None = Form(None),
    billing_contact_name: str | None = Form(None),
    billing_email: str | None = Form(None),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
):
    try:
        contact_versions = TypeAdapter(dict[str, PositiveInt]).validate_json(expected_contact_versions)
    except ValidationError:
        raise HTTPException(status_code=422, detail="Некорректные версии контактных лиц.")
    partner = crud.get_partner(db, partner_id)
    if not partner:
        return redirect_with_message("/partners", error="Partner not found.")
    require_version(partner, expected_version)
    info = crud.get_partner_accounting_info(db, partner_id)
    if info:
        require_version(info, expected_info_version)
    elif expected_info_version != 0:
        raise StaleDataError("Accounting info has changed. Reload before saving.")
    contacts = [c for c in crud.list_contract_contacts(db) if c.partner_id == partner_id]
    if {c.id: c.version for c in contacts} != contact_versions:
        raise StaleDataError("Billing contacts have changed. Reload before saving.")
    accounting_data = {
        "legal_entity_name": legal_entity_name,
        "inn": inn,
        "kpp": kpp,
        "billing_contact_name": billing_contact_name,
        "billing_email": billing_email,
        "comment": comment,
    }
    from app.routers.contract_imports import find_partner_contact, sync_billing_contact

    clean_data = {
        field: value.strip() if value and value.strip() else None
        for field, value in accounting_data.items()
    }
    try:
        # Claim the parent before inserting info/contact rows, including first save.
        partner.version += 1
        contact = find_partner_contact(
            db, partner_id, clean_data.get("billing_contact_name"), clean_data.get("billing_email"),
        )
        if contact:
            contact.version += 1
        db.flush()
        crud.upsert_partner_accounting_info(db, partner_id, accounting_data, commit=False)
        sync_billing_contact(db, partner, clean_data)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return redirect_with_message(
        f"/partners/{partner_id}",
        message="Данные для бухгалтерии сохранены.",
    )


@router.post(
    "/contracts/{contract_id}/accounting-tariffs",
    dependencies=[Depends(require_any_permission("edit", "manage_reference_data"))],
)
def save_contract_accounting_tariff_form(
    contract_id: str,
    service_id: str = Form(...),
    unit: str = Form(...),
    price: float = Form(...),
    comment: str | None = Form(None),
    is_active: bool = Form(False),
    expected_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    contract = crud.get_contract(db, contract_id)
    if not contract:
        return redirect_with_message("/contracts", error="Contract not found.")
    require_version(contract, expected_version)
    try:
        crud.upsert_contract_accounting_tariff(
            db,
            partner_id=contract.partner_id,
            contract_id=contract.id,
            service_id=service_id,
            unit=unit,
            price=price,
            comment=comment,
            is_active=is_active,
        )
    except ValueError as exc:
        db.rollback()
        return redirect_with_message("/contracts", validation=str(exc))
    return redirect_with_message("/contracts", message="Тариф сохранен.")


@router.post(
    "/contracts/accounting-tariffs/{tariff_id}/delete",
    dependencies=[Depends(require_any_permission("soft_delete", "manage_reference_data"))],
)
def delete_contract_accounting_tariff_form(
    tariff_id: str,
    expected_version: int = Form(..., ge=1),
    db: Session = Depends(get_db),
):
    tariff = db.get(crud.ContractAccountingTariff, tariff_id)
    if not tariff or tariff.is_deleted:
        return redirect_with_message("/contracts", error="Тариф не найден.")
    require_version(tariff, expected_version)
    crud.delete_contract_accounting_tariff(db, tariff_id)
    return redirect_with_message("/contracts", message="Тариф удален.")
