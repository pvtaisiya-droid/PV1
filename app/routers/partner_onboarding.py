from app.concurrency import snapshot_token, require_snapshot, ONBOARDING_INPUTS
from app import models
from app.concurrency import protect_record
from app.unit_of_work import atomic_write
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud, schemas
from app.auth import require_any_permission
from app.database import get_db
from app.routers.contract_contacts import (
    CONTACT_TYPE_OPTIONS,
    RECONCILIATION_PARTICIPATION_OPTIONS,
    build_contract_contact_payload,
)
from app.routers.placeholders import DOCUMENT_STATUSES, DOCUMENT_TYPES, store_document_file
from app.routers.products import REGISTRATION_STATUS_OPTIONS, normalize_product_status
from app.templating import templates
from app.security import require_csrf
from app.ui_helpers import redirect_with_message


router = APIRouter()

STEP_DEFINITIONS = [
    (1, "Партнёр"),
    (2, "Контактные лица"),
    (3, "Договоры и дополнительные соглашения"),
    (4, "Препараты и регистрационные удостоверения"),
    (5, "Услуги и цены"),
    (6, "Документы"),
    (7, "Проверка"),
]
STEP_IDS = {step for step, _label in STEP_DEFINITIONS}
OPTIONAL_STEPS = {2, 3, 4, 5, 6}
PARTNER_ROLE_OPTIONS = [
    ("mah", "Держатель РУ"),
    ("local_representative", "Локальный представитель"),
    ("distributor", "Дистрибьютор"),
    ("pv_service_provider", "Поставщик услуг ФН"),
    ("manufacturer", "Производитель"),
    ("other", "Другое"),
]


@router.get(
    "/partner-onboarding/new",
    response_class=HTMLResponse,
    dependencies=[Depends(require_any_permission("manage_reference_data", "create"))],
)
def partner_onboarding_start_page(request: Request):
    return templates.TemplateResponse(
        request, "partner_onboarding_start.html",
        {"request": request, "active_page": "partners"},
    )


@router.post(
    "/partner-onboarding/new",
    dependencies=[Depends(require_any_permission("manage_reference_data", "create")), Depends(require_csrf)],
)
def start_partner_onboarding(
    request: Request,
    db: Session = Depends(get_db),
):
    current_user = getattr(request.state, "current_user", None)
    wizard = crud.create_partner_onboarding_wizard(
        db,
        user_id=current_user.id if current_user else None,
    )
    return redirect_with_message(
        f"/partner-onboarding/{wizard.id}/step/1",
        message="Сценарий подключения партнёра начат.",
    )


@router.get("/partner-onboarding/{wizard_id}")
def continue_partner_onboarding(wizard_id: str, db: Session = Depends(get_db)):
    wizard = require_wizard(db, wizard_id)
    if wizard.status == "completed" and wizard.partner_id:
        return redirect_with_message(f"/partners/{wizard.partner_id}", message="Сценарий уже завершён.")
    return redirect_with_message(f"/partner-onboarding/{wizard.id}/step/{wizard.current_step}")


@router.get("/partner-onboarding/{wizard_id}/step/{step}", response_class=HTMLResponse)
def partner_onboarding_step(
    wizard_id: str,
    step: int,
    request: Request,
    db: Session = Depends(get_db),
):
    wizard = require_wizard(db, wizard_id)
    if step not in STEP_IDS:
        raise HTTPException(status_code=404, detail="Step not found")
    if step > 1 and not wizard.partner_id:
        return redirect_with_message(
            f"/partner-onboarding/{wizard.id}/step/1",
            validation="Сначала сохраните основные сведения партнёра.",
        )
    return render_step(request, db, wizard, step)


@router.post(
    "/partner-onboarding/{wizard_id}/step/{step}",
    dependencies=[Depends(require_any_permission("manage_reference_data", "create", "edit"))],
)
@atomic_write(serialize=True)
def save_partner_onboarding_step(
    wizard_id: str,
    step: int,
    request: Request,
    submit_action: str = Form("save_continue"),
    expected_version: int = Form(..., gt=0),
    expected_snapshot: str = Form(..., min_length=64, max_length=64),
    db: Session = Depends(get_db),
    partner_code: str | None = Form(None),
    partner_name: str | None = Form(None),
    partner_type: str = Form("registration_in_progress"),
    reconciliation_frequency: str = Form("not_conducted"),
    email: str | None = Form(None),
    address: str | None = Form(None),
    duplicate_ack: str | None = Form(None),
    last_name: str | None = Form(None),
    first_name: str | None = Form(None),
    patronymic: str | None = Form(None),
    position: str | None = Form(None),
    mobile_phone: str | None = Form(None),
    work_phone: str | None = Form(None),
    extension_phone: str | None = Form(None),
    company_website: str | None = Form(None),
    contact_status: str = Form("current"),
    is_current: str | None = Form(None),
    reconciliation_participation: str = Form("primary"),
    contact_type: str | None = Form("pv"),
    comments: str | None = Form(None),
    contract_id: str | None = Form(None),
    contract_type: str = Form("pharmacovigilance_agreement"),
    contract_number: str | None = Form(None),
    contract_date: str | None = Form(None),
    valid_until: str | None = Form(None),
    parent_contract_id: str | None = Form(None),
    additional_number: str | None = Form(None),
    additional_date: str | None = Form(None),
    additional_valid_until: str | None = Form(None),
    contract_file: UploadFile | None = File(None),
    product_mode: str = Form("existing"),
    product_id: str | None = Form(None),
    product_name: str | None = Form(None),
    dosage_form_id: str | None = Form(None),
    strength: str | None = Form(None),
    active_substance_id: str | None = Form(None),
    substance_name_ru: str | None = Form(None),
    substance_name_en: str | None = Form(None),
    substance_atc_code: str | None = Form(None),
    dosage_form_name_ru: str | None = Form(None),
    dosage_form_name_en: str | None = Form(None),
    dosage_form_group_ru: str | None = Form(None),
    dosage_form_group_en: str | None = Form(None),
    authorization_number: str | None = Form(None),
    authorization_status: str | None = Form(None),
    registration_ids: list[str] | None = Form(None),
    registration_number: str | None = Form(None),
    registration_date: str | None = Form(None),
    registration_valid_until: str | None = Form(None),
    registration_holder: str | None = Form(None),
    registration_country_code: str | None = Form(None),
    partner_role: str = Form("mah"),
    link_comment: str | None = Form(None),
    reconciliation_enabled: str | None = Form(None),
    main_contact_id: str | None = Form(None),
    cc_contact_ids: list[str] | None = Form(None),
    service_contract_id: str | None = Form(None),
    service_id: str | None = Form(None),
    service_unit: str | None = Form(None),
    service_price: str | None = Form(None),
    service_comment: str | None = Form(None),
    document_file: UploadFile | None = File(None),
    document_title: str | None = Form(None),
    document_type: str | None = Form(None),
    document_version: str | None = Form(None),
    document_date: str | None = Form(None),
    document_status: str = Form("draft"),
    document_comment: str | None = Form(None),
):
    require_snapshot(db, ONBOARDING_INPUTS, expected_snapshot)
    protect_record(db, models.PartnerOnboardingWizard, wizard_id, expected_version)
    wizard = require_wizard(db, wizard_id)
    if step not in STEP_IDS:
        raise HTTPException(status_code=404, detail="Step not found")
    if submit_action == "skip" and step in OPTIONAL_STEPS:
        crud.update_partner_onboarding_wizard(db, wizard, current_step=next_step(step), status="draft")
        return redirect_after_action(wizard, step, submit_action)
    if step > 1 and not wizard.partner_id:
        return redirect_with_message(
            f"/partner-onboarding/{wizard.id}/step/1",
            validation="Сначала сохраните основные сведения партнёра.",
        )

    try:
        if step == 1:
            duplicate_candidates = duplicate_partner_candidates(
                db,
                partner_code=partner_code,
                partner_name=partner_name,
                partner_id=wizard.partner_id,
            )
            if duplicate_candidates and duplicate_ack != "true":
                return render_step(
                    request,
                    db,
                    wizard,
                    step,
                    duplicate_candidates=duplicate_candidates,
                    form_values={
                        "partner_code": partner_code,
                        "partner_name": partner_name,
                        "partner_type": partner_type,
                        "reconciliation_frequency": reconciliation_frequency,
                    },
                    validation="Найдены похожие партнёры. Проверьте список ниже; сохранение доступно повторным нажатием.",
                )
            save_step_partner(
                db,
                wizard,
                partner_code=partner_code,
                partner_name=partner_name,
                partner_type=partner_type,
                reconciliation_frequency=reconciliation_frequency,
            )
        elif step == 2:
            save_step_contact(
                db,
                wizard,
                last_name=last_name,
                first_name=first_name,
                patronymic=patronymic,
                email=email,
                position=position,
                mobile_phone=mobile_phone,
                work_phone=work_phone,
                extension_phone=extension_phone,
                company_website=company_website,
                address=address,
                contact_status=contact_status,
                is_current=is_current,
                reconciliation_participation=reconciliation_participation,
                contact_type=contact_type,
                comments=comments,
            )
        elif step == 3:
            save_step_contracts(
                request,
                db,
                wizard,
                contract_id=contract_id,
                contract_type=contract_type,
                contract_number=contract_number,
                contract_date=contract_date,
                valid_until=valid_until,
                parent_contract_id=parent_contract_id,
                additional_number=additional_number,
                additional_date=additional_date,
                additional_valid_until=additional_valid_until,
                contract_file=contract_file,
            )
        elif step == 4:
            save_step_products(
                db,
                wizard,
                product_mode=product_mode,
                product_id=product_id,
                product_name=product_name,
                dosage_form_id=dosage_form_id,
                strength=strength,
                active_substance_id=active_substance_id,
                substance_name_ru=substance_name_ru,
                substance_name_en=substance_name_en,
                substance_atc_code=substance_atc_code,
                dosage_form_name_ru=dosage_form_name_ru,
                dosage_form_name_en=dosage_form_name_en,
                dosage_form_group_ru=dosage_form_group_ru,
                dosage_form_group_en=dosage_form_group_en,
                authorization_number=authorization_number,
                authorization_status=authorization_status,
                registration_ids=registration_ids or [],
                registration_number=registration_number,
                registration_date=registration_date,
                registration_valid_until=registration_valid_until,
                registration_holder=registration_holder,
                registration_country_code=registration_country_code,
                contract_id=contract_id,
                partner_role=partner_role,
                link_comment=link_comment,
            )
        elif step == 5:
            save_step_settings(
                db,
                wizard,
                reconciliation_enabled=reconciliation_enabled,
                reconciliation_frequency=reconciliation_frequency,
                main_contact_id=main_contact_id,
                cc_contact_ids=cc_contact_ids or [],
                service_contract_id=service_contract_id,
                service_id=service_id,
                service_unit=service_unit,
                service_price=service_price,
                service_comment=service_comment,
            )
        elif step == 6:
            save_step_document(
                request,
                db,
                wizard,
                document_file=document_file,
                document_title=document_title,
                document_type=document_type,
                document_version=document_version,
                document_date=document_date,
                document_status=document_status,
                document_comment=document_comment,
            )
        elif step == 7:
            summary = onboarding_summary(db, wizard.partner_id)
            blocking = [item for item in summary["attention"] if item["blocking"]]
            if blocking:
                return render_step(
                    request,
                    db,
                    wizard,
                    step,
                    validation="Есть блокирующие замечания. Исправьте их перед завершением.",
                )
            crud.update_partner_onboarding_wizard(
                db,
                wizard,
                current_step=7,
                completed_step=7,
                status="completed",
            )
            return redirect_with_message(
                f"/partners/{wizard.partner_id}",
                message="Подключение нового партнёра завершено.",
            )
    except (ValueError, IntegrityError) as exc:
        db.rollback()
        return render_step(request, db, wizard, step, validation=str(exc))

    crud.update_partner_onboarding_wizard(
        db,
        wizard,
        current_step=next_step(step),
        completed_step=step,
        status="draft",
    )
    return redirect_after_action(wizard, step, submit_action)


def render_step(
    request: Request,
    db: Session,
    wizard,
    step: int,
    *,
    duplicate_candidates: list | None = None,
    form_values: dict | None = None,
    validation: str | None = None,
) -> HTMLResponse:
    partner = crud.get_partner(db, wizard.partner_id) if wizard.partner_id else None
    contacts = partner_contacts(db, wizard.partner_id)
    contracts = partner_contracts(db, wizard.partner_id)
    base_contracts = [contract for contract in contracts if contract.contract_type != "additional_agreement"]
    documents = partner_documents(db, wizard.partner_id)
    product_links = (
        crud.list_partner_product_registration_links(db, partner_id=wizard.partner_id)
        if wizard.partner_id
        else []
    )
    summary = onboarding_summary(db, wizard.partner_id) if wizard.partner_id else None
    return templates.TemplateResponse(
        request,
        "partner_onboarding.html",
        {
            "request": request,
            "expected_snapshot": snapshot_token(db, ONBOARDING_INPUTS),
            "active_page": "partners",
            "wizard": wizard,
            "step": step,
            "steps": STEP_DEFINITIONS,
            "optional_steps": OPTIONAL_STEPS,
            "completed_steps": crud.wizard_completed_steps(wizard),
            "partner": partner,
            "contacts": contacts,
            "contracts": contracts,
            "base_contracts": base_contracts,
            "products": crud.list_products(db),
            "linked_product_rows": product_links,
            "partner_products": partner_products(db, wizard.partner_id),
            "product_registrations": {
                product.id: crud.product_registration_certificates(product)
                for product in crud.list_products(db)
            },
            "partners": crud.list_partners(db),
            "dosage_forms": crud.list_active_dosage_forms(db),
            "substances": crud.list_substances(db),
            "accounting_services": crud.list_accounting_services(db),
            "accounting_tariffs_by_contract": {
                contract.id: crud.list_contract_accounting_tariffs(db, contract.id)
                for contract in contracts
            },
            "documents": documents,
            "duplicate_candidates": duplicate_candidates or [],
            "form_values": form_values or {},
            "summary": summary,
            "contact_type_options": CONTACT_TYPE_OPTIONS,
            "reconciliation_participation_options": RECONCILIATION_PARTICIPATION_OPTIONS,
            "registration_status_options": REGISTRATION_STATUS_OPTIONS,
            "partner_role_options": PARTNER_ROLE_OPTIONS,
            "document_types": DOCUMENT_TYPES,
            "document_statuses": DOCUMENT_STATUSES,
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
            "validation": validation or request.query_params.get("validation"),
        },
    )


def save_step_partner(db: Session, wizard, **values) -> None:
    partner_code = (values.get("partner_code") or "").strip()
    partner_name = (values.get("partner_name") or "").strip()
    if not partner_code or not partner_name:
        raise ValueError("Код и наименование партнёра обязательны.")
    existing_partner = crud.get_partner(db, wizard.partner_id) if wizard.partner_id else None
    payload = schemas.PartnerCreate(
        partner_code=partner_code,
        partner_name=partner_name,
        partner_type=values.get("partner_type") or "registration_in_progress",
        reconciliation_frequency=values.get("reconciliation_frequency") or "not_conducted",
        address=existing_partner.address if existing_partner else None,
    )
    if wizard.partner_id:
        if not existing_partner:
            raise ValueError("Partner not found.")
        crud.update_partner(db, existing_partner, payload)
    else:
        partner = crud.create_partner(db, payload)
        crud.update_partner_onboarding_wizard(db, wizard, partner_id=partner.id)


def save_step_contact(db: Session, wizard, **values) -> None:
    if not any((values.get("last_name"), values.get("first_name"), values.get("email"))):
        return
    crud.create_contract_contact(
        db,
        build_contract_contact_payload(
            partner_id=wizard.partner_id,
            last_name=values.get("last_name") or "",
            first_name=values.get("first_name") or "",
            patronymic=values.get("patronymic"),
            email=values.get("email"),
            position=values.get("position") or "",
            mobile_phone=values.get("mobile_phone"),
            work_phone=values.get("work_phone"),
            extension_phone=values.get("extension_phone"),
            company_website=values.get("company_website"),
            address=values.get("address"),
            contact_status=(values.get("contact_status") or "current") if values.get("is_current") != "no" else "inactive",
            reconciliation_participation=values.get("reconciliation_participation") or "primary",
            contact_type=values.get("contact_type") or "pv",
            is_billing_contact=False,
            comments=values.get("comments"),
        ),
    )


def save_step_contracts(request: Request, db: Session, wizard, **values) -> None:
    selected_contract_id = optional_text(values.get("contract_id"))
    if values.get("contract_number"):
        contract_type = values.get("contract_type") or "pharmacovigilance_agreement"
        parent_contract_id = optional_text(values.get("parent_contract_id"))
        if contract_type == "additional_agreement" and not parent_contract_id:
            raise ValueError("Выберите основной договор для дополнительного соглашения.")
        contract = crud.create_contract(
            db,
            schemas.ContractCreate(
                partner_id=wizard.partner_id,
                contract_type=contract_type,
                parent_contract_id=parent_contract_id if contract_type == "additional_agreement" else None,
                contract_number=values["contract_number"].strip(),
                contract_date=parse_required_date(values.get("contract_date"), "Укажите дату договора."),
                valid_until=parse_required_date(values.get("valid_until"), "Укажите дату окончания договора."),
            ),
        )
        selected_contract_id = contract.id
    if values.get("additional_number"):
        parent_id = optional_text(values.get("parent_contract_id")) or selected_contract_id
        if not parent_id:
            raise ValueError("Выберите договор для дополнительного соглашения.")
        additional = crud.create_contract(
            db,
            schemas.ContractCreate(
                partner_id=wizard.partner_id,
                contract_type="additional_agreement",
                parent_contract_id=parent_id,
                contract_number=values["additional_number"].strip(),
                contract_date=parse_required_date(values.get("additional_date"), "Укажите дату ДС."),
                valid_until=parse_required_date(
                    values.get("additional_valid_until"),
                    "Укажите дату окончания ДС.",
                ),
            ),
        )
        selected_contract_id = additional.id
    contract_file = values.get("contract_file")
    if contract_file and safe_upload_name(contract_file):
        if not selected_contract_id:
            raise ValueError("Для файла договора или ДС выберите связанную запись.")
        stored = store_document_file(contract_file)
        current_user = getattr(request.state, "current_user", None)
        contract = crud.get_contract(db, selected_contract_id)
        crud.create_attachment(
            db,
            file_name=contract_file.filename or "contract-document",
            attachment_type="PV agreement",
            document_title=contract_file.filename or "Contract document",
            document_type="PV agreement",
            related_object_type="Contract",
            related_object_id=selected_contract_id,
            partner_id=wizard.partner_id,
            mime_type=contract_file.content_type or "application/octet-stream",
            file_size_bytes=stored["file_size_bytes"],
            storage_path=stored["storage_path"],
            status="active",
            comment=f"Документ загружен из мастера для договора {contract.contract_number if contract else ''}.",
            checksum_sha256=stored["checksum_sha256"],
            uploaded_by_user_id=current_user.id if current_user else None,
        )


def save_step_products(db: Session, wizard, **values) -> None:
    product_id = optional_text(values.get("product_id"))
    substance_id = optional_text(values.get("active_substance_id"))
    new_substance_name = optional_text(values.get("substance_name_ru"))
    if new_substance_name:
        substance = crud.get_or_create_substance(
            db,
            new_substance_name,
            inn_name=optional_text(values.get("substance_name_en")),
            atc_code=optional_text(values.get("substance_atc_code")),
        )
        substance_id = substance.id

    dosage_form_id = optional_int(values.get("dosage_form_id"))
    new_dosage_form_name = optional_text(values.get("dosage_form_name_ru"))
    if new_dosage_form_name:
        dosage_form = crud.create_dosage_form(
            db,
            schemas.DosageFormCreate(
                name_ru=new_dosage_form_name,
                name_en=optional_text(values.get("dosage_form_name_en")),
                group_ru=optional_text(values.get("dosage_form_group_ru")),
                group_en=optional_text(values.get("dosage_form_group_en")),
                is_active=True,
            ),
        )
        dosage_form_id = dosage_form.id

    product_name = (values.get("product_name") or "").strip()
    if not product_id and product_name and values.get("product_mode") != "new":
        product_id = next(
            (
                product.id
                for product in crud.list_products(db)
                if normalize_for_match(product.product_name) == normalize_for_match(product_name)
            ),
            None,
        )
    if values.get("product_mode") == "new" or not product_id:
        if not product_name:
            raise ValueError("Выберите препарат или укажите название нового препарата.")
        product = crud.create_product(
            db,
            schemas.ProductCreate(
                product_name=product_name,
                dosage_form_id=dosage_form_id,
                strength=optional_text(values.get("strength")),
                active_substance_id=substance_id,
                authorization_number=optional_text(values.get("authorization_number")),
                authorization_status=normalize_product_status(values.get("authorization_status")),
                comment=optional_text(values.get("link_comment")),
            ),
        )
        product_id = product.id
    product = crud.get_product(db, product_id)
    if not product:
        raise ValueError("Product not found.")

    certificate_ids = list(dict.fromkeys(values.get("registration_ids") or []))
    registration_number_value = optional_text(values.get("registration_number"))
    if registration_number_value:
        certificate = crud.create_product_registration_certificate(
            db,
            schemas.ProductRegistrationCertificateCreate(
                product_id=product.id,
                registration_number=registration_number_value,
                registration_date=optional_date(values.get("registration_date")),
                valid_until=optional_date(values.get("registration_valid_until")),
                status=values.get("authorization_status") or "active",
                holder=optional_text(values.get("registration_holder")),
                country_code=optional_text(values.get("registration_country_code")),
                dosage_form=crud.product_dosage_form_name(product),
                strength=product.strength,
                comment=optional_text(values.get("link_comment")),
            ),
        )
        certificate_ids.append(certificate.id)
    if not certificate_ids:
        certificate_ids = [None]
    for certificate_id in certificate_ids:
        crud.upsert_partner_product_registration_link(
            db,
            schemas.PartnerProductRegistrationLinkCreate(
                partner_id=wizard.partner_id,
                product_id=product.id,
                registration_certificate_id=certificate_id,
                contract_id=optional_text(values.get("contract_id")),
                partner_role=values.get("partner_role") or "mah",
                comment=optional_text(values.get("link_comment")),
            ),
        )


def save_step_settings(db: Session, wizard, **values) -> None:
    partner = crud.get_partner(db, wizard.partner_id)
    if not partner:
        raise ValueError("Partner not found.")
    if values.get("reconciliation_enabled") is not None:
        reconciliation_frequency = (
            values.get("reconciliation_frequency")
            if values.get("reconciliation_enabled") == "yes"
            else "not_conducted"
        )
        crud.update_partner(
            db,
            partner,
            schemas.PartnerCreate(
                partner_code=partner.partner_code,
                partner_name=partner.partner_name,
                partner_type=partner.partner_type,
                reconciliation_frequency=reconciliation_frequency or "not_conducted",
                address=partner.address,
            ),
        )
        save_reconciliation_contacts(
            db,
            wizard.partner_id,
            main_contact_id=optional_text(values.get("main_contact_id")),
            cc_contact_ids=values.get("cc_contact_ids") or [],
        )
    if values.get("service_contract_id") and values.get("service_id") and values.get("service_price"):
        service = crud.get_accounting_service(db, values["service_id"])
        crud.upsert_contract_accounting_tariff(
            db,
            partner_id=wizard.partner_id,
            contract_id=values["service_contract_id"],
            service_id=values["service_id"],
            unit=optional_text(values.get("service_unit")) or (service.default_unit if service else "услуга"),
            price=float(str(values.get("service_price")).replace(",", ".")),
            comment=optional_text(values.get("service_comment")),
            is_active=True,
        )


def save_step_document(request: Request, db: Session, wizard, **values) -> None:
    document_file = values.get("document_file")
    if not document_file or not safe_upload_name(document_file):
        return
    stored = store_document_file(document_file)
    current_user = getattr(request.state, "current_user", None)
    title = optional_text(values.get("document_title")) or document_file.filename or "Partner document"
    crud.create_attachment(
        db,
        file_name=document_file.filename or title,
        attachment_type=values.get("document_type") or "other",
        document_title=title,
        document_type=values.get("document_type") or "other",
        related_object_type="Partner",
        related_object_id=wizard.partner_id,
        partner_id=wizard.partner_id,
        mime_type=document_file.content_type or "application/octet-stream",
        file_size_bytes=stored["file_size_bytes"],
        storage_path=stored["storage_path"],
        document_version=optional_text(values.get("document_version")),
        document_date=optional_date(values.get("document_date")),
        status=values.get("document_status") or "draft",
        comment=optional_text(values.get("document_comment")),
        checksum_sha256=stored["checksum_sha256"],
        uploaded_by_user_id=current_user.id if current_user else None,
    )


def save_reconciliation_contacts(
    db: Session,
    partner_id: str,
    *,
    main_contact_id: str | None,
    cc_contact_ids: list[str],
) -> None:
    contacts = partner_contacts(db, partner_id)
    cc_set = {contact_id for contact_id in cc_contact_ids if contact_id}
    for contact in contacts:
        contact.is_reconciliation_recipient = contact.id == main_contact_id
        contact.is_primary = contact.id == main_contact_id
        contact.cc_reconciliation = contact.id in cc_set
        contact.version += 1
    db.commit()


def onboarding_summary(db: Session, partner_id: str | None) -> dict:
    partner = crud.get_partner(db, partner_id) if partner_id else None
    contacts = partner_contacts(db, partner_id)
    contracts = partner_contracts(db, partner_id)
    documents = partner_documents(db, partner_id)
    product_links = (
        crud.list_partner_product_registration_links(db, partner_id=partner_id)
        if partner_id
        else []
    )
    products = partner_products(db, partner_id)
    additional_count = len([contract for contract in contracts if contract.contract_type == "additional_agreement"])
    base_contracts = [contract for contract in contracts if contract.contract_type != "additional_agreement"]
    tariffs = [
        tariff
        for contract in contracts
        for tariff in crud.list_contract_accounting_tariffs(db, contract.id)
        if not tariff.is_deleted and tariff.is_active
    ]
    attention = []
    if not partner or not partner.partner_code or not partner.partner_name:
        attention.append({"text": "Не заполнены обязательные основные сведения.", "blocking": True})
    if not base_contracts:
        attention.append({"text": "Договор не добавлен.", "blocking": False})
    if not any(contact.is_pv_contact and contact.is_current for contact in contacts):
        attention.append({"text": "Не указан основной контакт по фармаконадзору.", "blocking": False})
    if product_links and any(not link.registration_certificate_id for link in product_links):
        attention.append({"text": "Для одного или нескольких препаратов не выбрано РУ.", "blocking": False})
    if partner and partner.reconciliation_frequency == "not_conducted":
        attention.append({"text": "Сверка сообщений не настроена.", "blocking": False})
    if not tariffs:
        attention.append({"text": "Не добавлены договорные услуги и цены.", "blocking": False})
    if not documents:
        attention.append({"text": "Не добавлены документы партнёра.", "blocking": False})
    return {
        "partner": partner,
        "contacts_count": len(contacts),
        "contracts": base_contracts,
        "additional_agreements_count": additional_count,
        "products": products,
        "product_links": product_links,
        "registration_count": len({link.registration_certificate_id for link in product_links if link.registration_certificate_id}),
        "documents_count": len(documents),
        "reconciliation": partner.reconciliation_frequency if partner else "not_conducted",
        "services_count": len(tariffs),
        "attention": attention,
        "blocking_attention": [item for item in attention if item["blocking"]],
        "nonblocking_attention": [item for item in attention if not item["blocking"]],
    }


def onboarding_status_rows(db: Session, partner_id: str) -> list[dict[str, str]]:
    summary = onboarding_summary(db, partner_id)
    rows = [
        ("Основные сведения", "завершено" if summary["partner"] else "не заполнено"),
        ("Контакты", "завершено" if summary["contacts_count"] else "не заполнено"),
        ("Договоры", "завершено" if summary["contracts"] else "требует внимания"),
        ("Препараты и РУ", "завершено" if summary["product_links"] else "не заполнено"),
        ("Документы", f"{summary['documents_count']} документов" if summary["documents_count"] else "не заполнено"),
        ("Сверка", "настроена" if summary["reconciliation"] != "not_conducted" else "не настроена"),
        ("Услуги", "завершено" if summary["services_count"] else "не заполнено"),
    ]
    return [{"label": label, "status": status} for label, status in rows]


def duplicate_partner_candidates(
    db: Session,
    *,
    partner_code: str | None,
    partner_name: str | None,
    partner_id: str | None = None,
) -> list:
    values = [partner_code, partner_name]
    normalized = [normalize_for_match(value) for value in values if normalize_for_match(value)]
    candidates = []
    for partner in crud.list_partners(db):
        if partner.id == partner_id:
            continue
        fields = [
            partner.partner_code,
            partner.partner_name,
            partner.accounting_info.inn if partner.accounting_info else None,
        ]
        haystack = [normalize_for_match(value) for value in fields if normalize_for_match(value)]
        if any(is_close_match(needle, candidate) for needle in normalized for candidate in haystack):
            candidates.append(partner)
    return candidates[:5]


def partner_contacts(db: Session, partner_id: str | None) -> list:
    if not partner_id:
        return []
    return [contact for contact in crud.list_contract_contacts(db) if contact.partner_id == partner_id]


def partner_contracts(db: Session, partner_id: str | None) -> list:
    if not partner_id:
        return []
    return crud.list_contracts(db, partner_id=partner_id)


def partner_documents(db: Session, partner_id: str | None) -> list:
    if not partner_id:
        return []
    return [
        document
        for document in crud.list_attachments(db)
        if document.partner_id == partner_id
        or (
            document.related_object_type in {"Partner", "partner"}
            and document.related_object_id == partner_id
        )
    ]


def partner_products(db: Session, partner_id: str | None) -> list:
    if not partner_id:
        return []
    product_ids = {
        link.product_id
        for link in crud.list_partner_product_registration_links(db, partner_id=partner_id)
    }
    for contract in partner_contracts(db, partner_id):
        product_ids.update(contract.product_ids)
    return [
        product
        for product in crud.list_products(db)
        if product.mah_partner_id == partner_id or product.id in product_ids
    ]


def redirect_after_action(wizard, step: int, submit_action: str):
    if submit_action == "save_close":
        if wizard.partner_id:
            return redirect_with_message(
                f"/partners/{wizard.partner_id}",
                message="Сценарий сохранён. Продолжить можно из карточки партнёра.",
            )
        return redirect_with_message("/", message="Сценарий сохранён.")
    return redirect_with_message(
        f"/partner-onboarding/{wizard.id}/step/{next_step(step)}",
        message="Шаг сохранён.",
    )


def require_wizard(db: Session, wizard_id: str):
    wizard = crud.get_partner_onboarding_wizard(db, wizard_id)
    if not wizard:
        raise HTTPException(status_code=404, detail="Onboarding wizard not found")
    return wizard


def next_step(step: int) -> int:
    return min(7, step + 1)


def optional_text(value: str | None) -> str | None:
    stripped = (value or "").strip()
    return stripped or None


def optional_int(value: str | None) -> int | None:
    stripped = (value or "").strip()
    return int(stripped) if stripped else None


def optional_date(value: str | None) -> date | None:
    stripped = (value or "").strip()
    return date.fromisoformat(stripped) if stripped else None


def parse_required_date(value: str | None, message: str) -> date:
    parsed = optional_date(value)
    if not parsed:
        raise ValueError(message)
    return parsed


def safe_upload_name(upload: UploadFile | None) -> str:
    return (upload.filename if upload else "").strip()


def normalize_for_match(value: str | None) -> str:
    return " ".join((value or "").strip().lower().replace('"', "").split())


def is_close_match(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right or left in right or right in left:
        return True
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return False
    return len(left_tokens & right_tokens) / max(len(left_tokens), len(right_tokens)) >= 0.6
