from sqlalchemy.orm.exc import StaleDataError
from app.unit_of_work import atomic_write
from app.concurrency import snapshot_token, require_snapshot, IMPORT_INPUTS
import json
import zipfile
from io import BytesIO
from copy import deepcopy
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import func, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import crud
from app.auth import require_any_permission
from app.case_numbering import validate_partner_code
from app.database import get_db
from app.models import (
    AccountingService,
    Contract,
    ContractAccountingTariff,
    ContractContact,
    ContractProduct,
    DosageForm,
    Partner,
    PartnerAccountingInfo,
    Product,
    ProductRegistrationCertificate,
    ProductSubstance,
)
from app.templating import templates


router = APIRouter()

ROOT_DIR = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT_DIR / "docs" / "contract_import.schema.json"
REFERENCES_PATH = ROOT_DIR / "examples" / "contract_import_reference_values.json"

ACCOUNTING_FIELD_LABELS = {
    "legal_entity_name": "Юрлицо для счета",
    "inn": "ИНН",
    "kpp": "КПП",
    "billing_contact_name": "Контакт для счетов",
    "billing_email": "E-mail для счетов",
    "comment": "Комментарий о порядке направления счетов",
}

LEGACY_GPT_PROMPT = """Я загружаю PDF договора по фармаконадзору.

Твоя задача: извлечь данные из договора и вернуть один строго валидный JSON для импорта в PV1.

Перед формированием ответа внимательно используй приложенные файлы:
1. contract_import.schema.json — это обязательная JSON Schema. Все имена полей, типы, enum-коды и вложенность должны соответствовать ей.
2. contract_import_reference_values.json — используй только коды из справочников.
3. contract_import_example.json — используй как образец структуры.

Версия формата всегда:
{
  "schema_name": "pv_contract_import",
  "schema_version": "1.0"
}

Критически важно: используй ТОЧНЫЕ имена полей из схемы.

Запрещены alias-поля и альтернативные названия:
- в корне: requested_operation, marketing_authorisations, information_transfer_timelines;
- в partner: full_name, short_name, inn, kpp, ogrn, legal_address;
- в contract: number, date, effective_date, expiration_date, currency, subject, territory, termination_notice_days, cure_period_days;
- в contacts: name, role, phone, party_inn;
- в products: name, trade_name, inn, dosage_form, status_code;
- в services: name, monthly_price, quarterly_price, unit_price, currency как отдельное поле, price как число.

Используй вместо них только правильные поля:
- partner.partner_name;
- partner.accounting.legal_entity_name;
- partner.accounting.inn;
- partner.accounting.kpp;
- partner.address;
- contract.contract_number;
- contract.contract_date;
- contract.valid_until;
- products[].product_name;
- products[].active_substances[];
- products[].registration_certificates[];
- message_transfer_terms[];
- services[].price.amount.value;
- services[].price.currency.

Обязательный корневой каркас JSON:
{
  "schema_name": "pv_contract_import",
  "schema_version": "1.0",
  "source_document": {},
  "import_policy": {
    "allow_create": false,
    "allow_update": false,
    "requested_operations": []
  },
  "partner": {},
  "contract": {},
  "parties": [],
  "contacts": [],
  "products": [],
  "product_links": [],
  "services": [],
  "pv_obligations": [],
  "message_transfer_terms": [],
  "reconciliation_settings": {},
  "additional_agreements": [],
  "documents": [],
  "manual_review": [],
  "warnings": []
}

Не используй внутренние ID базы данных. Для поиска записей используй только бизнес-ключи: ИНН, полное наименование, номер и дату договора, номер РУ, наименование препарата, e-mail контакта.

Извлеки:
1. партнера: наименование, юридическое наименование, ИНН, КПП, адрес и периодичность сверки;
2. договор: номер, дата, тип, срок действия, условия продления и расторжения;
3. стороны договора: наименование, роль, ИНН/регистрационный номер, адрес;
4. контакты: ФИО, должность, e-mail, телефоны, тип контакта, участие в сверке;
5. препараты: торговое наименование, МНН/действующее вещество, лекарственная форма, дозировка, путь введения, статус регистрации;
6. регистрационные удостоверения: номер РУ, дата регистрации, срок действия, держатель, страна, статус;
7. услуги и цены: код услуги из справочника, единица расчета, сумма, валюта, НДС, пункт договора;
8. обязанности по фармаконадзору: тип обязанности, ответственная сторона, описание, срок/правило исполнения;
9. сроки передачи информации: тип сообщения, ответственная сторона, срок передачи, рабочие/календарные дни;
10. настройки сверки: периодичность, способ обмена, контактный e-mail;
11. дополнительные соглашения: номер, дата, родительский договор, срок действия, изменяемые условия;
12. документы и приложения.

Для каждого важного значения используй объект с происхождением:
{
  "value": "...",
  "source": {
    "page": 1,
    "clause": "4.2",
    "quote": "короткая цитата из договора",
    "confidence": 0.95,
    "requires_manual_review": false
  }
}

Обязательные правила:
- partner.partner_name должен быть объектом {"value": "...", "source": {...}}, а не строкой.
- partner.accounting.inn и partner.accounting.kpp должны быть внутри partner.accounting.
- contract.contract_number, contract.contract_date и contract.valid_until должны быть объектами {"value": "...", "source": {...}}.
- contacts[].last_name, contacts[].first_name и contacts[].position обязательны. Если в договоре указано ФИО одной строкой, раздели его на фамилию, имя, отчество.
- products[].import_key обязателен, например "product-1".
- products[].product_name обязателен и должен быть объектом {"value": "...", "source": {...}}.
- Если есть МНН, добавь его в products[].active_substances[].name и inn_name как объекты {"value": "..."}.
- Если есть РУ, добавь его в products[].registration_certificates[].registration_number как объект {"value": "...", "source": {...}}.
- services[].price должен быть объектом, а не числом.

Пример услуги:
{
  "service_code": "post_registration_pv",
  "unit": "month",
  "price": {
    "amount": {
      "value": 17000,
      "source": {
        "page": 16,
        "clause": "Приложение № 4",
        "quote": "17 000,00 за месяц",
        "confidence": 0.99,
        "requires_manual_review": true
      }
    },
    "currency": "RUB",
    "vat_included": false
  },
  "requested_operation": "manual_review"
}

Если значение неоднозначно, отсутствует, конфликтует или не может быть безопасно импортировано, добавь пункт в manual_review:
{
  "category": "other",
  "description": "...",
  "source": {
    "page": 1,
    "clause": "...",
    "quote": "...",
    "confidence": 0.8,
    "requires_manual_review": true
  },
  "possible_values": [],
  "recommended_action": "Проверить вручную."
}

Не придумывай данные, которых нет в договоре.
Если поле не найдено, лучше не добавляй его или добавь проблему в manual_review.
Если есть сомнение, укажи requires_manual_review = true.
Не принимай решение о создании или обновлении записей в базе: используй requested_operation = "manual_review".
Не добавляй внутренние UUID или ID базы.

Перед ответом мысленно проверь:
1. В JSON нет запрещенных alias-полей.
2. В JSON нет Markdown.
3. Все enum-коды взяты из contract_import_reference_values.json.
4. JSON проходит contract_import.schema.json.

Верни только JSON без пояснений, без Markdown и без текста до или после JSON."""


GPT_PROMPT = """Я загружаю договор и архив PV1_contract_import_GPT_package.zip.

Подготовь один строго валидный JSON для импорта в PV1. Используй из архива:
- contract_import.schema.json — обязательная JSON Schema;
- contract_import_reference_values.json — актуальные справочники PV1;
- contract_import_example.json — пример структуры.

Верни только JSON без Markdown и пояснений. Не придумывай отсутствующие значения.
Для каждого найденного значения сохрани страницу, пункт, короткую цитату, confidence и признак ручной проверки в source.

Извлекай только следующие данные:
1. Партнёр: код партнёра, название, статус, частота сверки.
2. Информация для бухгалтерии: юридическое лицо, на которое должны выставляться счета (partner.accounting.legal_entity_name), ИНН (partner.accounting.inn), КПП (partner.accounting.kpp), контактное лицо для получения счетов (partner.accounting.billing_contact_name), специальный e-mail для счетов (partner.accounting.billing_email) и комментарий об установленном договором порядке направления счетов (partner.accounting.comment).
3. Контактные лица партнёра: фамилия, имя, отчество, должность, тип контакта, статус контакта, актуальность, email, мобильный телефон, рабочий телефон, добавочный номер, сайт компании, адрес, участие в сверке сообщений, комментарии.
4. Договоры и дополнительные соглашения: тип договора, основной договор, номер, дата, действителен до.
5. Препараты: торговое наименование; действующее вещество (наименование RU, наименование EN, АТХ/ATC-код); лекарственная форма (ЛФ на русском и английском, группа на русском и английском); дозировка; комментарий.
6. Услуги и цены: договор, услуга, единица, цена, комментарий к услуге.

Правила извлечения информации для бухгалтерии:
- юридическим лицом для счета считай именно лицо, на которое по договору должны выставляться счета; не подменяй его общим наименованием партнёра, если договор устанавливает иное;
- в partner.accounting.billing_email указывай только специальный e-mail, явно предназначенный для счетов, бухгалтерских документов или направления оригиналов/копий счетов;
- не подменяй специальный бухгалтерский e-mail общим адресом организации, обычным контактным e-mail, адресом для сообщений по фармаконадзору или адресом менеджера проекта;
- если специальный e-mail для счетов не указан, оставь billing_email равным null и добавь пункт в manual_review; не угадывай адрес по домену организации;
- если контакт указан в пункте о выставлении, получении или направлении счетов, используй его как billing_contact_name, даже если в другом приложении или разделе он также указан как менеджер проекта;
- в comment кратко и точно зафиксируй установленный договором порядок направления счетов: способ, адресата, канал, срок или иные специальные условия; не добавляй общие рассуждения;
- billing_contact_name, billing_email и comment передавай строкой или null, как требует схема; legal_entity_name, inn и kpp передавай объектами с value и source.

Правила извлечения контактных лиц:
- добавляй в contacts[] только сотрудников и представителей партнёра (Заказчика);
- контакты ООО «АРС», сотрудников Исполнителя и адреса домена pharmrussia.com в contacts[] не добавляй;
- если одно лицо упомянуто и как обычный контакт партнёра, и как контакт для счетов, оставь один объект contacts[] и дополнительно заполни partner.accounting.billing_contact_name и partner.accounting.billing_email.

Не извлекай иные реквизиты сторон, обязанности по фармаконадзору, сроки передачи сообщений, условия продления/расторжения, маршруты введения, НДС, регистрационные статусы и любые иные поля вне списка выше.

Сопоставление со справочниками:
- для действующего вещества сначала ищи совпадение в contract_import_reference_values.json → substances;
- для лекарственной формы сначала ищи совпадение в dosage_forms;
- для услуги используй код из accounting_services;
- если уверенного совпадения нет, передай извлечённые наименования и добавь manual_review с вопросом, нужно ли создать новую запись;
- не используй внутренние ID базы данных.

Обязательные правила разбора таблиц препаратов:
- определяй количество препаратов только по нумерованным позициям таблицы, а не по количеству строк, переносов, ячеек или обозначений «ТН» и «МНН»;
- торговое наименование и МНН внутри одной нумерованной позиции относятся к одному препарату и должны быть объединены в один объект products[];
- один объект products[] должен соответствовать одной нумерованной позиции исходной таблицы; не создавай второй препарат из отдельной строки или атрибута той же позиции;
- действующее вещество извлекай только из явно обозначенного поля «МНН», «действующее вещество» или «active substance»;
- не выводи действующее вещество из дозировки, единиц измерения, лекарственной формы, пути введения или собственных знаний;
- при противоречии отдельных атрибутов сохраняй одну карточку препарата и выноси сомнение в manual_review, а не создавай дополнительный препарат;
- для каждого объекта products[] в source укажи номер нумерованной позиции и цитату, позволяющую проверить границы этой позиции.

Если значение из обязательного списка отсутствует в договоре, оставь value: null (где это допускает схема) и обязательно добавь manual_review с описанием «Не найдено в договоре — заполнить вручную». Все извлечённые значения пользователь сможет исправить перед сохранением.

Проверь перед ответом: JSON соответствует схеме, enum-коды взяты из справочника, лишних полей нет."""


@router.get(
    "/contract-imports/gpt-package",
    dependencies=[Depends(require_any_permission("view", "manage_reference_data"))],
)
def download_gpt_package(db: Session = Depends(get_db)):
    all_references = json.loads(REFERENCES_PATH.read_text(encoding="utf-8"))
    included_reference_groups = (
        "partner_types",
        "contract_types",
        "contact_types",
        "contact_statuses",
        "reconciliation_participation",
        "reconciliation_frequencies",
        "accounting_services",
        "units",
        "operation_types",
    )
    references = {
        "schema_name": "pv_contract_import_references",
        "schema_version": "1.0",
        "notes": "Только справочники, используемые быстрым сценарием импорта договоров.",
        **{
            group: all_references.get(group, [])
            for group in included_reference_groups
        },
    }
    references["substances"] = [
        {
            "name_ru": substance.substance_name,
            "name_en": substance.inn_name,
            "atc_code": substance.atc_code,
            "is_active": substance.is_active,
        }
        for substance in crud.list_substances(db)
        if substance.is_active and not substance.is_deleted
    ]
    references["dosage_forms"] = [
        {
            "code": f"dosage_form_{dosage_form.id}",
            "name_ru": dosage_form.name_ru,
            "name_en": dosage_form.name_en,
            "group_ru": dosage_form.group_ru,
            "group_en": dosage_form.group_en,
            "is_active": dosage_form.is_active,
        }
        for dosage_form in crud.list_active_dosage_forms(db)
    ]

    archive = BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("GPT_PROMPT.txt", GPT_PROMPT)
        package.write(SCHEMA_PATH, "contract_import.schema.json")
        package.writestr(
            "contract_import_reference_values.json",
            json.dumps(references, ensure_ascii=False, indent=2),
        )
        package.write(ROOT_DIR / "examples" / "contract_import_example.json", "contract_import_example.json")
        package.writestr(
            "README.txt",
            "Загрузите этот архив в GPT вместе с договором. Затем вставьте текст из GPT_PROMPT.txt. "
            "Полученный JSON загрузите в раздел PV1 «Импорт договоров» для проверки и ручной корректировки.",
        )
    archive.seek(0)
    return StreamingResponse(
        archive,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="PV1_contract_import_GPT_package.zip"'},
    )


@router.get("/contract-imports", response_class=HTMLResponse)
def contract_imports_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "contract_imports.html",
        {
            "request": request,
            "expected_snapshot": snapshot_token(db, IMPORT_INPUTS),
            "active_page": "contract_imports",
            "prompt_text": GPT_PROMPT,
            "preview": None,
            "validation_errors": [],
            "parse_error": None,
            "uploaded_filename": None,
            "payload_json": None,
            "import_result": None,
            "accounting_result": None,
            "substances": crud.list_substances(db),
            "dosage_forms": crud.list_active_dosage_forms(db),
        },
    )


@router.post(
    "/contract-imports/preview",
    response_class=HTMLResponse,
    dependencies=[Depends(require_any_permission("view", "manage_reference_data"))],
)
async def contract_import_preview(
    request: Request,
    import_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    parse_error = None
    validation_errors: list[str] = []
    preview = None

    raw_content = await import_file.read()
    try:
        payload = json.loads(raw_content.decode("utf-8-sig"))
    except UnicodeDecodeError:
        payload = None
        parse_error = "Файл должен быть в UTF-8."
    except json.JSONDecodeError as exc:
        payload = None
        parse_error = f"Некорректный JSON: строка {exc.lineno}, колонка {exc.colno}: {exc.msg}"

    if payload is not None:
        payload, normalization_warnings = normalize_payload(payload)
        validation_errors = validate_payload(payload)
        preview = build_preview(db, payload, validation_errors)
        if preview:
            preview["warnings"] = unique_strings(normalization_warnings + preview["warnings"])

    return templates.TemplateResponse(
        request,
        "contract_imports.html",
        {
            "request": request,
            "expected_snapshot": snapshot_token(db, IMPORT_INPUTS),
            "active_page": "contract_imports",
            "prompt_text": GPT_PROMPT,
            "preview": preview,
            "validation_errors": validation_errors,
            "parse_error": parse_error,
            "uploaded_filename": import_file.filename,
            "payload_json": json.dumps(payload, ensure_ascii=False),
            "import_result": None,
            "accounting_result": None,
            "substances": crud.list_substances(db),
            "dosage_forms": crud.list_active_dosage_forms(db),
        },
    )


@router.post(
    "/contract-imports/confirm",
    response_class=HTMLResponse,
    dependencies=[Depends(require_any_permission("manage_reference_data", "create"))],
)
@atomic_write(serialize=True)
async def contract_import_confirm(request: Request, expected_snapshot: str = Form(..., min_length=64, max_length=64), db: Session = Depends(get_db)):
    require_snapshot(db, IMPORT_INPUTS, expected_snapshot)
    form = await request.form()
    payload_json = str(form.get("payload_json") or "")
    errors: list[str] = []
    payload: dict[str, Any] | None = None
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        errors.append("Данные предпросмотра повреждены. Загрузите JSON повторно.")

    if payload is not None:
        payload, normalization_warnings = normalize_payload(payload)
        errors.extend(validate_payload(payload))
    else:
        normalization_warnings = []

    if form.get("confirm_review") != "yes":
        errors.append("Подтвердите, что пункты ручной проверки просмотрены.")

    preview = build_preview(db, payload, errors) if payload is not None else None
    result = None
    if payload is not None and not errors:
        try:
            result = persist_confirmed_import(db, payload, form)
        except StaleDataError:
            db.rollback()
            raise
        except (ValueError, TypeError, SQLAlchemyError) as exc:
            db.rollback()
            errors.append(import_error_message(exc))
            preview = build_preview(db, payload, errors)

    if preview:
        preview["warnings"] = unique_strings(normalization_warnings + preview["warnings"])
    return templates.TemplateResponse(
        request,
        "contract_imports.html",
        {
            "request": request,
            "expected_snapshot": snapshot_token(db, IMPORT_INPUTS),
            "active_page": "contract_imports",
            "prompt_text": GPT_PROMPT,
            "preview": preview if not result else None,
            "validation_errors": errors,
            "parse_error": None,
            "uploaded_filename": None,
            "payload_json": payload_json,
            "import_result": result,
            "accounting_result": None,
            "substances": crud.list_substances(db),
            "dosage_forms": crud.list_active_dosage_forms(db),
        },
    )


@router.post(
    "/contract-imports/confirm-accounting",
    response_class=HTMLResponse,
    dependencies=[Depends(require_any_permission("manage_reference_data", "create"))],
)
@atomic_write(serialize=True)
async def contract_import_confirm_accounting(request: Request, expected_snapshot: str = Form(..., min_length=64, max_length=64), db: Session = Depends(get_db)):
    require_snapshot(db, IMPORT_INPUTS, expected_snapshot)
    form = await request.form()
    payload_json = str(form.get("payload_json") or "")
    errors: list[str] = []
    payload: dict[str, Any] | None = None
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError:
        errors.append("Данные предпросмотра повреждены. Загрузите JSON повторно.")

    if payload is not None:
        payload, normalization_warnings = normalize_payload(payload)
        errors.extend(validate_payload(payload))
    else:
        normalization_warnings = []

    result = None
    if payload is not None and not errors:
        try:
            result = persist_accounting_only(db, payload, form)
        except StaleDataError:
            db.rollback()
            raise
        except (ValueError, TypeError, SQLAlchemyError) as exc:
            db.rollback()
            errors.append(import_error_message(exc))

    preview = build_preview(db, payload, errors) if payload is not None and not result else None
    if preview:
        preview["warnings"] = unique_strings(normalization_warnings + preview["warnings"])
    return templates.TemplateResponse(
        request,
        "contract_imports.html",
        {
            "request": request,
            "expected_snapshot": snapshot_token(db, IMPORT_INPUTS),
            "active_page": "contract_imports",
            "prompt_text": GPT_PROMPT,
            "preview": preview,
            "validation_errors": errors,
            "parse_error": None,
            "uploaded_filename": None,
            "payload_json": payload_json,
            "import_result": None,
            "accounting_result": result,
            "substances": crud.list_substances(db),
            "dosage_forms": crud.list_active_dosage_forms(db),
        },
    )


def persist_confirmed_import(db: Session, payload: dict[str, Any], form: Any) -> dict[str, Any]:
    partner_data = payload["partner"]
    accounting_data = partner_data.get("accounting") or {}
    selected_partner_id = str(form.get("partner_id") or "").strip()
    partner = None
    created_partner = False
    duplicate_messages: list[str] = []
    updated_messages: list[str] = []
    if selected_partner_id:
        partner_resolution = resolve_partner(db, partner_data)
        candidate_ids = {
            str(candidate["id"])
            for candidate in partner_resolution["candidates"]
        }
        if selected_partner_id not in candidate_ids:
            raise ValueError("Выбранный партнер не входит в найденные совпадения. Обновите предпросмотр.")
        if form.get("confirm_existing_partner") != "yes":
            raise ValueError("Подтвердите, что выбранный существующий партнер — именно тот, к которому относится договор.")
        partner = db.query(Partner).filter(
            Partner.id == selected_partner_id,
            Partner.is_deleted.is_(False),
        ).first()
        if not partner:
            raise ValueError("Выбранный партнер не найден. Обновите предпросмотр.")
        duplicate_messages.append(f"Партнер уже существует: {partner.partner_code} / {partner.partner_name}.")
    else:
        inn = source_value(accounting_data.get("inn"))
        if inn:
            existing = db.query(Partner).join(PartnerAccountingInfo).filter(
                Partner.is_deleted.is_(False),
                PartnerAccountingInfo.is_deleted.is_(False),
                PartnerAccountingInfo.inn == str(inn),
            ).first()
            if existing:
                raise ValueError("Партнер с таким ИНН уже существует. Выберите его в предпросмотре.")
        partner_code = validate_partner_code(
            str(form.get("partner_code") or "").strip() or available_partner_code(db, inn)
        )
        partner_name = str(form.get("partner_name") or "").strip()
        if not partner_name:
            raise ValueError("Укажите наименование партнера.")
        if db.query(Partner).filter(func.lower(Partner.partner_code) == partner_code.lower()).first():
            raise ValueError(f"Код партнера {partner_code} уже используется.")
        partner = Partner(
            partner_code=partner_code,
            partner_name=partner_name,
            partner_type=str(form.get("partner_type") or partner_data.get("partner_type") or "fn"),
            reconciliation_frequency=str(form.get("reconciliation_frequency") or source_value(partner_data.get("reconciliation_frequency")) or "not_conducted"),
            address=source_value(partner_data.get("address")),
        )
        db.add(partner)
        db.flush()
        created_partner = True

    accounting_form_data = read_accounting_form_data(form)
    validate_accounting_form_data(accounting_form_data)
    accounting_info = crud.get_partner_accounting_info(db, partner.id)
    if accounting_info:
        accounting_changed = False
        for field, value in accounting_form_data.items():
            if value is None:
                continue
            current_value = getattr(accounting_info, field, None)
            label = ACCOUNTING_FIELD_LABELS[field]
            if import_values_equal(current_value, value):
                duplicate_messages.append(f"{label} уже содержится в карточке партнера: {value}.")
                continue
            setattr(accounting_info, field, value)
            accounting_changed = True
            updated_messages.append(f"{label}: значение добавлено или обновлено.")
        if accounting_changed:
            accounting_info.version += 1
    elif any(value is not None for value in accounting_form_data.values()):
        db.add(PartnerAccountingInfo(partner_id=partner.id, **accounting_form_data))
        updated_messages.append("Информация для бухгалтерии добавлена в карточку партнера.")

    product_ids: list[str] = []
    created_products = 0
    linked_products = 0
    for index, product_data in enumerate(payload.get("products") or []):
        if form.get(f"include_product_{index}") != "yes":
            continue
        match = resolve_product(db, product_data)
        if len(match["candidates"]) == 1:
            product_id = match["candidates"][0]["id"]
            linked_products += 1
            duplicate_messages.append(
                f"Препарат уже существует: {match['candidates'][0]['label']}."
            )
        elif match["candidates"]:
            raise ValueError(f"У препарата #{index + 1} несколько совпадений. Снимите флажок и сопоставьте его вручную.")
        else:
            product_name = str(form.get(f"product_name_{index}") or source_value(product_data.get("product_name")) or "").strip()
            if not product_name:
                raise ValueError(f"У препарата #{index + 1} отсутствует наименование.")
            substance_id = str(form.get(f"product_substance_id_{index}") or "").strip() or None
            substance_name_ru = str(form.get(f"product_substance_name_ru_{index}") or "").strip()
            if not substance_id and substance_name_ru:
                substance = crud.get_or_create_substance(
                    db,
                    substance_name_ru,
                    inn_name=str(form.get(f"product_substance_name_en_{index}") or "").strip() or None,
                    atc_code=str(form.get(f"product_substance_atc_{index}") or "").strip() or None,
                )
                substance_id = substance.id

            dosage_form_id_text = str(form.get(f"product_dosage_form_id_{index}") or "").strip()
            dosage_form_id = int(dosage_form_id_text) if dosage_form_id_text.isdigit() else None
            dosage_form_name_ru = str(form.get(f"product_dosage_form_name_ru_{index}") or "").strip()
            if not dosage_form_id and dosage_form_name_ru:
                dosage_form = DosageForm(
                    name_ru=dosage_form_name_ru,
                    name_en=str(form.get(f"product_dosage_form_name_en_{index}") or "").strip() or None,
                    group_ru=str(form.get(f"product_dosage_form_group_ru_{index}") or "").strip() or None,
                    group_en=str(form.get(f"product_dosage_form_group_en_{index}") or "").strip() or None,
                    is_active=True,
                )
                db.add(dosage_form)
                db.flush()
                dosage_form_id = dosage_form.id

            product = Product(
                product_code=crud.generate_product_code(db),
                product_name=str(product_name),
                product_name_normalized=normalize(product_name),
                dosage_form_id=dosage_form_id,
                strength=str(form.get(f"product_strength_{index}") or source_value(product_data.get("strength")) or "").strip() or None,
                comment=str(form.get(f"product_comment_{index}") or product_data.get("comment") or "").strip() or None,
                is_company_product=True,
                is_active=True,
            )
            db.add(product)
            db.flush()
            if substance_id:
                db.add(ProductSubstance(
                    product_id=product.id,
                    substance_id=substance_id,
                    substance_role="active",
                    is_primary=True,
                ))
            product_id = product.id
            created_products += 1
        if product_id not in product_ids:
            product_ids.append(product_id)

    contract_data = payload["contract"]
    contract_number = str(form.get("contract_number") or "").strip()
    contract_date = parse_import_date(form.get("contract_date"), "дату договора")
    valid_until = parse_import_date(form.get("valid_until"), "срок действия договора")
    if not contract_number:
        raise ValueError("Укажите номер договора.")
    contract_type = str(form.get("contract_type") or contract_data.get("contract_type") or "pharmacovigilance_agreement")
    parent_contract = None
    parent_contract_number = str(form.get("parent_contract_number") or source_value((contract_data.get("parent_contract") or {}).get("contract_number")) or "").strip()
    if contract_type == "additional_agreement" and parent_contract_number:
        parent_contract = db.query(Contract).filter(
            Contract.partner_id == partner.id,
            Contract.contract_number == parent_contract_number,
            Contract.is_deleted.is_(False),
        ).first()
    existing_contract = db.query(Contract).filter(
        Contract.partner_id == partner.id,
        Contract.contract_number == contract_number,
        Contract.contract_date == contract_date,
        Contract.contract_type == contract_type,
        Contract.is_deleted.is_(False),
    ).first()
    if existing_contract:
        contract = existing_contract
        contract_action = "linked"
        duplicate_messages.append(
            f"Договор уже существует: {contract.contract_number} от {contract.contract_date}."
        )
        if contract.valid_until != valid_until:
            contract.valid_until = valid_until
            contract.version += 1
            updated_messages.append("Срок действия существующего договора обновлен.")
    else:
        contract = Contract(
            partner_id=partner.id,
            product_id=product_ids[0] if product_ids else None,
            contract_type=contract_type,
            parent_contract_id=parent_contract.id if parent_contract else None,
            contract_number=contract_number,
            contract_date=contract_date,
            valid_until=valid_until,
            is_active=bool(contract_data.get("is_active", True)),
        )
        db.add(contract)
        db.flush()
        contract_action = "created"

    if existing_contract:
        existing_contract_product_ids = set(contract.product_ids)
        for product_id in product_ids:
            if product_id in existing_contract_product_ids:
                duplicate_messages.append("Связь договора с выбранным препаратом уже существует.")
                continue
            db.add(ContractProduct(contract_id=contract.id, product_id=product_id))
            existing_contract_product_ids.add(product_id)
            updated_messages.append("Препарат добавлен в существующий договор.")
    else:
        for product_id in product_ids:
            db.add(ContractProduct(contract_id=contract.id, product_id=product_id))

    created_contacts = 0
    linked_contacts = 0
    skipped_contacts_without_email = 0
    for index, contact_data in enumerate(payload.get("contacts") or []):
        if form.get(f"include_contact_{index}") != "yes":
            continue
        email = str(form.get(f"contact_email_{index}") or "").strip() or None
        if not email:
            skipped_contacts_without_email += 1
            continue
        incoming_type = str(
            form.get(f"contact_type_{index}")
            or contact_data.get("contact_type")
            or "other"
        )
        existing = None
        if email:
            existing = db.query(ContractContact).filter(
                ContractContact.partner_id == partner.id,
                func.lower(ContractContact.email) == str(email).lower(),
                ContractContact.is_deleted.is_(False),
            ).first()
        if existing:
            linked_contacts += 1
            duplicate_messages.append(
                f"Контакт с e-mail {email} уже существует у выбранного партнера."
            )
            contact_changed = False
            if incoming_type == "pv" and not existing.is_pv_contact:
                existing.is_pv_contact = True
                contact_changed = True
                updated_messages.append(
                    f"Существующему контакту {email} добавлено назначение «Фармаконадзор»."
                )
            if existing.contact_type == "accounting" and incoming_type != "accounting":
                existing.contact_type = incoming_type
                contact_changed = True
            if contact_changed:
                existing.version += 1
            continue
        phones = contact_data.get("phones") or {}
        db.add(ContractContact(
            partner_id=partner.id,
            last_name=str(form.get(f"contact_last_name_{index}") or "Не указано").strip(),
            first_name=str(form.get(f"contact_first_name_{index}") or "Не указано").strip(),
            patronymic=str(form.get(f"contact_patronymic_{index}") or "").strip() or None,
            email=email,
            position=str(form.get(f"contact_position_{index}") or "Контактное лицо").strip(),
            mobile_phone=str(form.get(f"contact_phone_{index}") or "").strip() or None,
            work_phone=str(form.get(f"contact_work_phone_{index}") or source_value(phones.get("work")) or "").strip() or None,
            extension_phone=str(form.get(f"contact_extension_{index}") or source_value(phones.get("extension")) or "").strip() or None,
            company_website=str(form.get(f"contact_website_{index}") or contact_data.get("company_website") or "").strip() or None,
            address=str(form.get(f"contact_address_{index}") or source_value(contact_data.get("address")) or "").strip() or None,
            is_current=(
                str(form.get(f"contact_status_{index}") or contact_data.get("contact_status") or "current") == "current"
                and str(form.get(f"contact_is_current_{index}") or ("yes" if contact_data.get("is_current", True) else "no")) == "yes"
            ),
            is_pv_contact=incoming_type == "pv",
            is_reconciliation_recipient=str(form.get(f"contact_reconciliation_{index}") or contact_data.get("reconciliation_participation") or "none") != "none",
            cc_reconciliation=str(form.get(f"contact_reconciliation_{index}") or "") == "cc",
            is_primary=str(form.get(f"contact_reconciliation_{index}") or contact_data.get("reconciliation_participation") or "none") == "primary",
            is_billing_contact=incoming_type == "accounting",
            contact_type=incoming_type,
            comments=str(form.get(f"contact_comments_{index}") or contact_data.get("comments") or "").strip() or None,
        ))
        created_contacts += 1

    billing_duplicates, billing_updates = sync_billing_contact(
        db,
        partner,
        accounting_form_data,
    )
    duplicate_messages.extend(billing_duplicates)
    updated_messages.extend(billing_updates)

    created_tariffs = 0
    skipped_duplicate_tariffs = 0
    selected_service_ids: set[str] = set()
    for index, service_data in enumerate(payload.get("services") or []):
        if form.get(f"include_service_{index}") != "yes":
            continue
        match = resolve_service(db, service_data)
        if len(match["candidates"]) != 1:
            raise ValueError(f"Услуга #{index + 1} не сопоставлена со справочником.")
        service_id = match["candidates"][0]["id"]
        if service_id in selected_service_ids:
            skipped_duplicate_tariffs += 1
            duplicate_messages.append(
                f"Услуга {match['candidates'][0]['label']} повторяется в импортируемых данных."
            )
            continue
        selected_service_ids.add(service_id)
        amount = str(form.get(f"service_amount_{index}") or source_value((service_data.get("price") or {}).get("amount")) or "").replace(",", ".").strip()
        if not amount:
            raise ValueError(f"У услуги #{index + 1} не указана цена.")
        existing = db.query(ContractAccountingTariff).filter(
            ContractAccountingTariff.contract_id == contract.id,
            ContractAccountingTariff.service_id == service_id,
            ContractAccountingTariff.is_deleted.is_(False),
        ).first()
        if not existing:
            db.add(ContractAccountingTariff(
                partner_id=partner.id,
                contract_id=contract.id,
                service_id=service_id,
                unit=str(form.get(f"service_unit_{index}") or service_data.get("unit") or "service"),
                price=float(amount),
                comment=str(form.get(f"service_comment_{index}") or source_value(service_data.get("comment")) or "").strip() or None,
            ))
            created_tariffs += 1
        else:
            duplicate_messages.append(
                f"Тариф по услуге {match['candidates'][0]['label']} уже существует для договора."
            )

    db.commit()
    return {
        "partner_id": partner.id,
        "partner_name": partner.partner_name,
        "partner_action": "created" if created_partner else "linked",
        "contract_id": contract.id,
        "contract_number": contract.contract_number,
        "contract_action": contract_action,
        "created_contacts": created_contacts,
        "linked_contacts": linked_contacts,
        "skipped_contacts_without_email": skipped_contacts_without_email,
        "created_products": created_products,
        "linked_products": linked_products,
        "created_tariffs": created_tariffs,
        "skipped_duplicate_tariffs": skipped_duplicate_tariffs,
        "duplicate_messages": unique_strings(duplicate_messages),
        "updated_messages": unique_strings(updated_messages),
    }


def persist_accounting_only(db: Session, payload: dict[str, Any], form: Any) -> dict[str, Any]:
    partner_data = payload["partner"]
    selected_partner_id = str(form.get("partner_id") or "").strip()
    if not selected_partner_id:
        raise ValueError("Для отдельного сохранения бухгалтерии выберите существующего партнера.")

    partner_resolution = resolve_partner(db, partner_data)
    candidate_ids = {
        str(candidate["id"])
        for candidate in partner_resolution["candidates"]
    }
    if selected_partner_id not in candidate_ids:
        raise ValueError("Выбранный партнер не входит в найденные совпадения. Обновите предпросмотр.")
    if form.get("confirm_existing_partner") != "yes":
        raise ValueError("Подтвердите, что выбранный существующий партнер — именно тот, к которому относится договор.")

    partner = db.query(Partner).filter(
        Partner.id == selected_partner_id,
        Partner.is_deleted.is_(False),
    ).first()
    if not partner:
        raise ValueError("Выбранный партнер не найден. Обновите предпросмотр.")

    data = read_accounting_form_data(form)
    validate_accounting_form_data(data)
    if not any(value is not None for value in data.values()):
        raise ValueError("Заполните хотя бы одно поле информации для бухгалтерии.")

    duplicate_messages: list[str] = []
    updated_messages: list[str] = []
    accounting_info = crud.get_partner_accounting_info(db, partner.id)
    if accounting_info:
        changed = False
        for field, value in data.items():
            if value is None:
                continue
            label = ACCOUNTING_FIELD_LABELS[field]
            current_value = getattr(accounting_info, field, None)
            if import_values_equal(current_value, value):
                duplicate_messages.append(f"{label} уже содержится в карточке партнера: {value}.")
                continue
            setattr(accounting_info, field, value)
            changed = True
            updated_messages.append(f"{label}: значение добавлено или обновлено.")
        if changed:
            accounting_info.version += 1
    else:
        db.add(PartnerAccountingInfo(partner_id=partner.id, **data))
        updated_messages.append("Информация для бухгалтерии добавлена в карточку партнера.")

    billing_duplicates, billing_updates = sync_billing_contact(db, partner, data)
    duplicate_messages.extend(billing_duplicates)
    updated_messages.extend(billing_updates)

    db.commit()
    return {
        "partner_id": partner.id,
        "partner_name": partner.partner_name,
        "duplicate_messages": unique_strings(duplicate_messages),
        "updated_messages": unique_strings(updated_messages),
    }


def parse_import_date(value: Any, label: str) -> date:
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError as exc:
        raise ValueError(f"Проверьте {label}: требуется формат ГГГГ-ММ-ДД.") from exc


def optional_form_text(form: Any, field_name: str) -> str | None:
    value = form.get(field_name)
    if value is None:
        return None
    return str(value).strip() or None


def read_accounting_form_data(form: Any) -> dict[str, str | None]:
    return {
        "legal_entity_name": optional_form_text(form, "accounting_legal_entity_name"),
        "inn": optional_form_text(form, "accounting_inn"),
        "kpp": optional_form_text(form, "accounting_kpp"),
        "billing_contact_name": optional_form_text(form, "accounting_billing_contact_name"),
        "billing_email": optional_form_text(form, "accounting_billing_email"),
        "comment": optional_form_text(form, "accounting_comment"),
    }


def validate_accounting_form_data(data: dict[str, str | None]) -> None:
    inn = data.get("inn")
    if inn and (not inn.isdigit() or len(inn) not in (10, 12)):
        raise ValueError("Проверьте ИНН: требуется 10 или 12 цифр.")
    kpp = data.get("kpp")
    if kpp and (not kpp.isdigit() or len(kpp) != 9):
        raise ValueError("Проверьте КПП: требуется 9 цифр.")


def import_values_equal(left: Any, right: Any) -> bool:
    return normalize(left) == normalize(right)


def sync_billing_contact(
    db: Session,
    partner: Partner,
    accounting_data: dict[str, str | None],
) -> tuple[list[str], list[str]]:
    full_name = accounting_data.get("billing_contact_name")
    email = accounting_data.get("billing_email")
    comment = accounting_data.get("comment")
    if not full_name and not email:
        return [], []

    existing = find_partner_contact(db, partner.id, full_name, email)

    duplicates: list[str] = []
    updates: list[str] = []
    billing_note = f"Бухгалтерия: {comment}" if comment else None
    if existing:
        changed = False
        existing_label = contact_label(existing)
        if existing.is_billing_contact:
            duplicates.append(
                f"Контакт {existing_label} уже имеет назначение «Бухгалтерия»."
            )
        else:
            existing.is_billing_contact = True
            changed = True
            updates.append(
                f"Существующему контакту {existing_label} добавлено второе назначение «Бухгалтерия»."
            )
        if email and not existing.email:
            existing.email = email
            changed = True
        if billing_note and normalize(billing_note) not in normalize(existing.comments):
            existing.comments = (
                f"{existing.comments}\n{billing_note}"
                if existing.comments
                else billing_note
            )
            changed = True
        if changed:
            existing.version += 1
        return duplicates, updates

    last_name, first_name, patronymic = split_full_name(full_name)
    contact = ContractContact(
        partner_id=partner.id,
        last_name=last_name or "Контакт",
        first_name=first_name or "для счетов",
        patronymic=patronymic,
        email=email,
        position="Контакт для счетов",
        is_current=True,
        is_pv_contact=False,
        is_reconciliation_recipient=False,
        cc_reconciliation=False,
        is_primary=False,
        is_billing_contact=True,
        contact_type="accounting",
        comments=billing_note,
    )
    db.add(contact)
    db.flush()
    updates.append("Контакт для счетов добавлен в таблицу контактных лиц с назначением «Бухгалтерия».")
    return duplicates, updates


def find_partner_contact(
    db: Session,
    partner_id: str,
    full_name: str | None,
    email: str | None,
) -> ContractContact | None:
    if email:
        contact = db.query(ContractContact).filter(
            ContractContact.partner_id == partner_id,
            ContractContact.is_deleted.is_(False),
            func.lower(ContractContact.email) == email.lower(),
        ).first()
        if contact:
            return contact
    if full_name:
        normalized_name = normalize(full_name)
        for contact in db.query(ContractContact).filter(
            ContractContact.partner_id == partner_id,
            ContractContact.is_deleted.is_(False),
        ).all():
            if normalize(contact_label(contact)) == normalized_name:
                return contact
    return None


def contact_label(contact: ContractContact | None) -> str:
    if not contact:
        return ""
    return " ".join(
        part
        for part in [contact.last_name, contact.first_name, contact.patronymic]
        if part
    )


def available_partner_code(db: Session, inn: Any) -> str:
    base = f"FN-{inn}" if inn else "FN-IMPORT"
    candidate = base
    suffix = 2
    while db.query(Partner.id).filter(func.lower(Partner.partner_code) == candidate.lower()).first():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def import_error_message(exc: Exception) -> str:
    if isinstance(exc, SQLAlchemyError):
        return "Не удалось сохранить данные из-за ограничения базы. Изменения отменены; проверьте совпадения и выбранные тарифы."
    return str(exc)


def validate_payload(payload: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(load_schema(), format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
    return [
        f"{json_path(error.path)}: {error.message}"
        for error in errors
    ]


def normalize_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    normalized = deepcopy(payload)
    warnings: list[str] = []

    if normalized.pop("requested_operation", None) is not None:
        warnings.append("Корневое поле requested_operation было перенесено в логику предпросмотра: оно не является частью JSON Schema.")

    normalized["partner"] = normalize_partner(normalized.get("partner") or {}, warnings)
    normalized["contract"] = normalize_contract(normalized.get("contract") or {}, warnings)
    for obsolete_field in (
        "parties",
        "product_links",
        "pv_obligations",
        "message_transfer_terms",
        "reconciliation_settings",
        "information_transfer_timelines",
        "marketing_authorisations",
        "additional_agreements",
        "documents",
    ):
        if normalized.pop(obsolete_field, None):
            warnings.append(f"Лишний раздел {obsolete_field} удалён: новый импорт принимает только данные быстрого сценария.")
    normalized_contacts = []
    for index, contact in enumerate(normalized.get("contacts") or [], start=1):
        if is_ars_contact(contact):
            warnings.append(
                f"Контакт #{index} относится к ООО «АРС» и исключён из импорта партнёра."
            )
            continue
        normalized_contacts.append(normalize_contact(contact, index, warnings))
    normalized["contacts"] = normalized_contacts
    normalized["products"] = [
        normalize_product(product, index, warnings)
        for index, product in enumerate(normalized.get("products") or [], start=1)
    ]
    normalized["services"] = [
        normalize_service(service, warnings)
        for service in normalized.get("services") or []
    ]

    normalized["manual_review"] = normalize_manual_review(normalized.get("manual_review") or [])

    return normalized, warnings


def normalize_partner(partner: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    if not partner:
        return partner
    accounting = partner.get("accounting") or {}
    result = {
        "requested_operation": partner.get("requested_operation") or "manual_review",
        "partner_code": partner.get("partner_code"),
        "partner_name": partner.get("partner_name") or partner.get("short_name") or partner.get("full_name"),
        "partner_type": normalize_code(partner.get("partner_type")) or "fn",
        "address": partner.get("address"),
        "reconciliation_frequency": normalize_code_source(
            partner.get("reconciliation_frequency"),
            {
                "MONTHLY": "monthly",
                "QUARTERLY": "quarterly",
                "NOT_CONDUCTED": "not_conducted",
            },
        ),
        "accounting": {
            "legal_entity_name": accounting.get("legal_entity_name") or partner.get("legal_entity_name") or partner.get("full_name"),
            "inn": accounting.get("inn") or partner.get("inn"),
            "kpp": accounting.get("kpp") or partner.get("kpp"),
            "billing_contact_name": accounting.get("billing_contact_name"),
            "billing_email": accounting.get("billing_email"),
            "comment": accounting.get("comment"),
        },
    }
    if "full_name" in partner or "short_name" in partner:
        warnings.append("Поля partner.full_name/short_name были преобразованы в partner.partner_name.")
    return remove_empty(result)


def normalize_contract(contract: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    if not contract:
        return contract
    result = {
        "requested_operation": contract.get("requested_operation") or "manual_review",
        "contract_type": contract.get("contract_type") or "pharmacovigilance_agreement",
        "contract_number": contract.get("contract_number") or contract.get("number"),
        "contract_date": contract.get("contract_date") or contract.get("date") or contract.get("effective_date"),
        "valid_until": contract.get("valid_until") or contract.get("expiration_date"),
        "parent_contract": contract.get("parent_contract"),
    }
    if "number" in contract or "date" in contract or "expiration_date" in contract:
        warnings.append("Поля contract.number/date/expiration_date были преобразованы в contract_number/contract_date/valid_until.")
    return remove_empty(result)


def normalize_party(party: dict[str, Any], index: int, warnings: list[str]) -> dict[str, Any]:
    role = normalize_role_code(party.get("role_code"))
    if role != party.get("role_code"):
        warnings.append(f"Роль стороны #{index} была нормализована: {party.get('role_code')} -> {role}.")
    return remove_empty(
        {
            "role_code": role,
            "partner_name": ensure_source_object(
                party.get("partner_name") or party.get("short_name") or party.get("full_name")
            ),
            "legal_entity_name": ensure_source_object(party.get("legal_entity_name") or party.get("full_name")),
            "inn": ensure_source_object(party.get("inn")),
            "registration_number": ensure_source_object(party.get("ogrn") or party.get("registration_number")),
            "address": party.get("address"),
            "is_primary_partner": party.get("is_primary_partner", index == 1),
        }
    )


def normalize_contact(contact: dict[str, Any], index: int, warnings: list[str]) -> dict[str, Any]:
    full_name = source_value(contact.get("name"))
    last_name, first_name, patronymic = split_full_name(full_name)
    phones = contact.get("phones") or {}
    if contact.get("name"):
        warnings.append(f"Контакт #{index}: поле name было разложено на last_name/first_name/patronymic.")
    role_text = source_value(contact.get("role")) or source_value(contact.get("position"))
    return remove_empty(
        {
            "requested_operation": contact.get("requested_operation") or "manual_review",
            "contact_type": infer_contact_type(role_text),
            "last_name": contact.get("last_name") or with_value(contact.get("name"), last_name),
            "first_name": contact.get("first_name") or with_value(contact.get("name"), first_name),
            "patronymic": contact.get("patronymic") or with_value(contact.get("name"), patronymic),
            "email": contact.get("email"),
            "position": contact.get("position") or contact.get("role") or {"value": "Контактное лицо"},
            "phones": {
                "mobile": phones.get("mobile") or contact.get("mobile_phone") or contact.get("phone"),
                "work": phones.get("work") or contact.get("work_phone"),
                "extension": phones.get("extension") or contact.get("extension_phone"),
            },
            "company_website": contact.get("company_website"),
            "address": contact.get("address"),
            "contact_status": contact.get("contact_status") or ("current" if contact.get("is_current", True) else "inactive"),
            "is_current": contact.get("is_current", True),
            "reconciliation_participation": contact.get("reconciliation_participation") or "primary",
            "comments": contact.get("comments"),
        }
    )


def is_ars_contact(contact: dict[str, Any]) -> bool:
    text_value = json.dumps(contact, ensure_ascii=False, default=str).casefold()
    normalized_text = (
        text_value
        .replace("«", '"')
        .replace("»", '"')
        .replace("“", '"')
        .replace("”", '"')
        .replace("ё", "е")
    )
    return (
        "@pharmrussia.com" in normalized_text
        or "www.pharmrussia.com" in normalized_text
        or 'ооо "арс"' in normalized_text
        or "ооо арс" in normalized_text
    )


def normalize_product(product: dict[str, Any], index: int, warnings: list[str]) -> dict[str, Any]:
    product_name = (
        product.get("product_name")
        if "product_name" in product
        else first_source_object(product.get("trade_name"), product.get("name"))
    )
    if product.get("name"):
        warnings.append(f"Препарат #{index}: поле name было преобразовано в product_name.")
    active_substances = []
    if source_value(product.get("inn")):
        active_substances.append(
            {
                "name_ru": ensure_source_object(product.get("inn")),
                "name_en": ensure_source_object(product.get("inn")),
                "add_new": True,
            }
        )
    return remove_empty(
        {
            "requested_operation": product.get("requested_operation") or "manual_review",
            "import_key": product.get("import_key") or f"product-{index}",
            "product_name": product_name,
            "dosage_form": normalize_dosage_form(product),
            "strength": product.get("strength"),
            "comment": product.get("comment"),
            "active_substances": active_substances or normalize_substances(product.get("active_substances") or []),
        }
    )


def normalize_dosage_form(product: dict[str, Any]) -> dict[str, Any] | None:
    value = product.get("dosage_form")
    if isinstance(value, dict):
        return value
    code = source_value(product.get("dosage_form_code"))
    name = source_value(value)
    if not code and not name:
        return None
    return remove_empty(
        {
            "reference_code": code,
            "name_ru": ensure_source_object(name),
            "add_new": bool(name and not code),
        }
    )


def normalize_substances(substances: list[Any]) -> list[dict[str, Any]]:
    result = []
    for substance in substances:
        if not isinstance(substance, dict):
            continue
        result.append(
            remove_empty(
                {
                    "reference_name": substance.get("reference_name"),
                    "name_ru": substance.get("name_ru") or substance.get("name"),
                    "name_en": substance.get("name_en") or substance.get("inn_name"),
                    "atc_code": substance.get("atc_code"),
                    "add_new": substance.get("add_new", not bool(substance.get("reference_name"))),
                }
            )
        )
    return result


def normalize_service(service: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    raw_code = service.get("service_code")
    service_code = {
        "PRE_REG_PV": "other",
        "POST_REG_PV_BASE": "post_registration_pv",
        "POST_REG_PV": "post_registration_pv",
        "PSUR_PREPARATION_SUBMISSION": "psur_preparation_submission",
        "RMP_PREPARATION": "rmp_preparation",
        "LITERATURE_SCREENING": "literature_safety_screening",
        "ICSR_RECONCILIATION": "partner_message_reconciliation",
    }.get(raw_code, raw_code)
    if service_code != raw_code:
        warnings.append(f"Код услуги {raw_code} был преобразован в {service_code}.")

    amount = service.get("price")
    unit = service.get("unit")
    if amount is None and service.get("monthly_price") is not None:
        amount = service.get("monthly_price")
        unit = unit or "month"
    if amount is None and service.get("unit_price") is not None:
        amount = service.get("unit_price")
    if unit is None:
        unit = "service"
    unit = normalize_unit(unit)

    price = service.get("price") if isinstance(service.get("price"), dict) else {
        "amount": {
            "value": amount,
            "source": service.get("source"),
        },
    }
    if isinstance(price, dict):
        price = {"amount": price.get("amount")}

    comment_parts = []
    if service.get("name"):
        comment_parts.append(service["name"])
    if service.get("quarterly_price") is not None:
        comment_parts.append(f"Квартальная цена: {service['quarterly_price']}.")

    return remove_empty(
        {
            "requested_operation": service.get("requested_operation") or "manual_review",
            "service_code": service_code,
            "contract_number": ensure_source_object(service.get("contract_number")),
            "unit": unit,
            "price": price,
            "comment": {"value": " ".join(comment_parts), "source": service.get("source")} if comment_parts else service.get("comment"),
        }
    )


def normalize_transfer_term(item: dict[str, Any]) -> dict[str, Any]:
    deadline = item.get("deadline")
    if not deadline:
        value = item.get("timeline_value")
        unit = item.get("timeline_unit")
        deadline = {
            "value": f"{value} {unit}".strip(),
            "source": item.get("source"),
        }
    return remove_empty(
        {
            "message_type": infer_message_type(item.get("message_type") or item.get("type_code")),
            "responsible_party": normalize_responsible_party(item.get("sender_role")),
            "deadline": deadline,
            "business_day_rule": (
                {"value": "Рабочие дни", "source": item.get("source")}
                if item.get("timeline_unit") == "WORKING_DAYS"
                else None
            ),
            "requested_operation": "manual_review",
        }
    )


def normalize_reconciliation_settings(settings: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    if not settings:
        return settings
    frequency = settings.get("frequency") or settings.get("frequency_code")
    if "frequency_code" in settings:
        warnings.append("Поле reconciliation_settings.frequency_code было преобразовано в frequency.value.")
    return remove_empty(
        {
            "frequency": normalize_code_source(
                frequency,
                {
                    "MONTHLY": "monthly",
                    "QUARTERLY": "quarterly",
                    "NOT_CONDUCTED": "not_conducted",
                },
            ),
            "method": settings.get("method") or {"value": settings.get("scope"), "source": settings.get("source")},
            "contact_email": settings.get("contact_email"),
            "requested_operation": settings.get("requested_operation") or "manual_review",
        }
    )


def normalize_documents(documents: list[Any], warnings: list[str]) -> list[dict[str, Any]]:
    result = []
    for index, document in enumerate(documents, start=1):
        if isinstance(document, str):
            result.append(
                {
                    "file_name": document[:255],
                    "document_title": document[:255],
                    "document_type": "contract_attachment",
                    "status": "imported",
                    "related_to": "contract_import",
                    "requested_operation": "manual_review",
                }
            )
            continue
        if isinstance(document, dict):
            result.append(document)
        else:
            warnings.append(f"Документ #{index} пропущен: неподдерживаемый формат.")
    if documents and any(isinstance(document, str) for document in documents):
        warnings.append("Строковые элементы documents были преобразованы в объекты документов.")
    return result


def normalize_manual_review(items: list[Any]) -> list[dict[str, Any]]:
    result = []
    for item in items:
        if not isinstance(item, dict):
            result.append(
                {
                    "category": "other",
                    "description": str(item),
                    "recommended_action": "Проверить вручную.",
                }
            )
            continue
        if {"category", "description", "recommended_action"}.issubset(item):
            result.append(item)
            continue
        result.append(
            remove_empty({
                "category": "other",
                "description": item.get("message") or item.get("description") or item.get("code") or "Требуется ручная проверка.",
                "recommended_action": item.get("recommended_action") or "Проверить вручную.",
                "possible_values": item.get("possible_values") or [],
                "source": item.get("source"),
            })
        )
    return result


def build_preview(
    db: Session,
    payload: dict[str, Any],
    validation_errors: list[str],
) -> dict[str, Any]:
    partner_preview = resolve_partner(db, payload.get("partner") or {})
    contract_preview = resolve_contract(
        db,
        payload.get("contract") or {},
        partner_preview.get("strong_candidate"),
    )
    products_preview = [
        resolve_product(db, product)
        for product in payload.get("products") or []
    ]
    contacts_preview = [
        resolve_contact(db, contact, partner_preview.get("strong_candidate"))
        for contact in payload.get("contacts") or []
    ]
    services_preview = [
        resolve_service(db, service)
        for service in payload.get("services") or []
    ]
    partner_preview["accounting_comparison"] = build_accounting_comparison(
        db,
        partner_preview,
    )
    annotate_service_tariff_statuses(db, contract_preview, services_preview)
    duplicate_messages = preview_duplicate_messages(
        db,
        partner_preview,
        contract_preview,
        contacts_preview,
        products_preview,
        services_preview,
    )
    manual_review = payload.get("manual_review") or []
    warnings = list(payload.get("warnings") or [])
    return {
        "schema_name": payload.get("schema_name"),
        "schema_version": payload.get("schema_version"),
        "source_document": payload.get("source_document") or {},
        "partner": partner_preview,
        "contract": contract_preview,
        "contacts": contacts_preview,
        "products": products_preview,
        "services": services_preview,
        "duplicate_messages": duplicate_messages,
        "manual_review": manual_review,
        "warnings": unique_strings(warnings),
        "blocking_errors": bool(validation_errors),
        "summary": {
            "validation_errors": len(validation_errors),
            "partner_candidates": len(partner_preview["candidates"]),
            "contacts": len(contacts_preview),
            "products": len(products_preview),
            "services": len(services_preview),
            "duplicates": len(duplicate_messages),
            "manual_review": len(manual_review),
        },
    }


def build_accounting_comparison(
    db: Session,
    partner_preview: dict[str, Any],
) -> dict[str, Any]:
    input_data = partner_preview["input"]
    values = {
        "legal_entity_name": input_data.get("legal_entity_name"),
        "inn": input_data.get("inn"),
        "kpp": input_data.get("kpp"),
        "billing_contact_name": input_data.get("billing_contact_name"),
        "billing_email": input_data.get("billing_email"),
        "comment": input_data.get("accounting_comment"),
    }
    candidate = partner_preview.get("strong_candidate")
    accounting_info = (
        crud.get_partner_accounting_info(db, candidate["id"])
        if candidate
        else None
    )
    fields: dict[str, dict[str, Any]] = {}
    for field, value in values.items():
        current_value = getattr(accounting_info, field, None) if accounting_info else None
        if value in (None, ""):
            status = "empty"
        elif current_value in (None, ""):
            status = "new"
        elif import_values_equal(current_value, value):
            status = "duplicate"
        else:
            status = "update"
        fields[field] = {
            "status": status,
            "current_value": current_value,
        }

    contact = (
        find_partner_contact(
            db,
            candidate["id"],
            values["billing_contact_name"],
            values["billing_email"],
        )
        if candidate
        else None
    )
    has_contact_value = bool(values["billing_contact_name"] or values["billing_email"])
    return {
        "fields": fields,
        "billing_contact": {
            "status": "duplicate" if contact else "new" if has_contact_value else "empty",
            "label": contact_label(contact) if contact else None,
            "assignment_status": (
                "duplicate"
                if contact and contact.is_billing_contact
                else "new"
                if has_contact_value
                else "empty"
            ),
        },
    }


def annotate_service_tariff_statuses(
    db: Session,
    contract_preview: dict[str, Any],
    services_preview: list[dict[str, Any]],
) -> None:
    contract_id = (
        contract_preview["candidates"][0]["id"]
        if len(contract_preview["candidates"]) == 1
        else None
    )
    for service in services_preview:
        service["tariff_status"] = "unknown"
        if len(service["candidates"]) != 1:
            continue
        service["tariff_status"] = "new"
        if not contract_id:
            continue
        exists = db.query(ContractAccountingTariff.id).filter(
            ContractAccountingTariff.contract_id == contract_id,
            ContractAccountingTariff.service_id == service["candidates"][0]["id"],
            ContractAccountingTariff.is_deleted.is_(False),
        ).first()
        if exists:
            service["tariff_status"] = "duplicate"


def preview_duplicate_messages(
    db: Session,
    partner_preview: dict[str, Any],
    contract_preview: dict[str, Any],
    contacts_preview: list[dict[str, Any]],
    products_preview: list[dict[str, Any]],
    services_preview: list[dict[str, Any]],
) -> list[str]:
    messages: list[str] = []
    imported_accounting = {
        "legal_entity_name": partner_preview["input"].get("legal_entity_name"),
        "inn": partner_preview["input"].get("inn"),
        "kpp": partner_preview["input"].get("kpp"),
        "billing_contact_name": partner_preview["input"].get("billing_contact_name"),
        "billing_email": partner_preview["input"].get("billing_email"),
        "comment": partner_preview["input"].get("accounting_comment"),
    }
    for candidate in partner_preview["candidates"]:
        messages.append(
            f"Партнер уже есть в PV1: {candidate['label']} (совпадение: {candidate['reason']})."
        )
        accounting_info = crud.get_partner_accounting_info(db, candidate["id"])
        if not accounting_info:
            continue
        for field, imported_value in imported_accounting.items():
            current_value = getattr(accounting_info, field, None)
            if imported_value in (None, "") or current_value in (None, ""):
                continue
            label = ACCOUNTING_FIELD_LABELS[field]
            if import_values_equal(current_value, imported_value):
                messages.append(f"{label} уже совпадает с данными выбранного партнера.")
            else:
                messages.append(
                    f"{label} уже заполнено другим значением; после подтверждения будет обновлено значением из формы."
                )

    for candidate in contract_preview["candidates"]:
        messages.append(f"Договор уже есть в PV1: {candidate['label']}.")
    for contact in contacts_preview:
        for candidate in contact["candidates"]:
            messages.append(f"Контакт уже есть у партнера: {candidate['label']}.")
    for product in products_preview:
        for candidate in product["candidates"]:
            messages.append(f"Препарат уже есть в PV1: {candidate['label']}.")
    if len(contract_preview["candidates"]) == 1:
        contract_candidate = contract_preview["candidates"][0]
        for service in services_preview:
            if len(service["candidates"]) != 1:
                continue
            service_candidate = service["candidates"][0]
            tariff = db.query(ContractAccountingTariff).filter(
                ContractAccountingTariff.contract_id == contract_candidate["id"],
                ContractAccountingTariff.service_id == service_candidate["id"],
                ContractAccountingTariff.is_deleted.is_(False),
            ).first()
            if tariff:
                messages.append(
                    f"Тариф уже есть в договоре: {service_candidate['label']} — {tariff.price:g} за {tariff.unit}."
                )
    return unique_strings(messages)


def resolve_partner(db: Session, partner_data: dict[str, Any]) -> dict[str, Any]:
    name = source_value(partner_data.get("partner_name"))
    code = source_value(partner_data.get("partner_code"))
    accounting = partner_data.get("accounting") or {}
    inn = source_value(accounting.get("inn"))
    legal_name = source_value(accounting.get("legal_entity_name"))

    candidates: list[dict[str, Any]] = []
    if inn:
        query = (
            db.query(Partner)
            .join(PartnerAccountingInfo, PartnerAccountingInfo.partner_id == Partner.id)
            .filter(
                Partner.is_deleted.is_(False),
                PartnerAccountingInfo.is_deleted.is_(False),
                PartnerAccountingInfo.inn == inn,
            )
        )
        candidates.extend(candidate_partner(row, "ИНН") for row in query.all())

    if code:
        query = db.query(Partner).filter(
            Partner.is_deleted.is_(False),
            func.lower(Partner.partner_code) == code.lower(),
        )
        candidates.extend(candidate_partner(row, "код партнера") for row in query.all())

    for lookup_name, reason in ((legal_name, "юридическое наименование"), (name, "наименование")):
        if not lookup_name:
            continue
        normalized = normalize(lookup_name)
        query = db.query(Partner).outerjoin(PartnerAccountingInfo).filter(
            Partner.is_deleted.is_(False),
            or_(
                func.lower(Partner.partner_name) == normalized,
                func.lower(PartnerAccountingInfo.legal_entity_name) == normalized,
            ),
        )
        candidates.extend(candidate_partner(row, reason) for row in query.all())
        # SQLite lower() does not case-fold Cyrillic. Keep the SQL lookup for
        # indexed ASCII values and add a Python Unicode fallback for Russian names.
        unicode_matches = db.query(Partner).filter(
            Partner.is_deleted.is_(False),
        ).all()
        for row in unicode_matches:
            accounting_info = row.accounting_info
            if (
                normalize(row.partner_name) == normalized
                or (
                    accounting_info
                    and not accounting_info.is_deleted
                    and normalize(accounting_info.legal_entity_name) == normalized
                )
            ):
                candidates.append(candidate_partner(row, reason))

    candidates = unique_candidates(candidates)
    return {
        "input": {
            "partner_name": name,
            "partner_code": code,
            "inn": inn,
            "legal_entity_name": legal_name,
            "kpp": source_value(accounting.get("kpp")),
            "billing_contact_name": accounting.get("billing_contact_name"),
            "billing_email": accounting.get("billing_email"),
            "accounting_comment": accounting.get("comment"),
            "partner_type": partner_data.get("partner_type"),
            "reconciliation_frequency": source_value(partner_data.get("reconciliation_frequency")),
        },
        "candidates": candidates,
        "strong_candidate": candidates[0] if len(candidates) == 1 else None,
        "resolved_operation": operation_for_candidates(candidates, create_label="create"),
        "status": status_for_candidates(candidates),
    }


def resolve_contract(
    db: Session,
    contract_data: dict[str, Any],
    partner_candidate: dict[str, Any] | None,
    *,
    is_additional: bool = False,
) -> dict[str, Any]:
    number = source_value(contract_data.get("contract_number"))
    contract_date = source_value(contract_data.get("contract_date"))
    contract_type = contract_data.get("contract_type")
    candidates: list[dict[str, Any]] = []

    if partner_candidate and number and contract_date and contract_type:
        query = db.query(Contract).filter(
            Contract.is_deleted.is_(False),
            Contract.partner_id == partner_candidate["id"],
            func.lower(Contract.contract_number) == str(number).lower(),
            Contract.contract_date == contract_date,
            Contract.contract_type == contract_type,
        )
        candidates = [candidate_contract(row, "партнер + номер + дата + тип") for row in query.all()]

    parent_candidates: list[dict[str, Any]] = []
    parent_contract = contract_data.get("parent_contract") or {}
    parent_number = source_value(parent_contract.get("contract_number"))
    parent_date = source_value(parent_contract.get("contract_date"))
    if is_additional or contract_type == "additional_agreement":
        if partner_candidate and parent_number:
            query = db.query(Contract).filter(
                Contract.is_deleted.is_(False),
                Contract.partner_id == partner_candidate["id"],
                func.lower(Contract.contract_number) == str(parent_number).lower(),
            )
            if parent_date:
                query = query.filter(Contract.contract_date == parent_date)
            parent_candidates = [
                candidate_contract(row, "родительский договор")
                for row in query.all()
            ]

    return {
        "input": {
            "contract_type": contract_type,
            "contract_number": number,
            "contract_date": contract_date,
            "valid_until": source_value(contract_data.get("valid_until")),
            "parent_contract_number": parent_number,
            "parent_contract_date": parent_date,
        },
        "candidates": candidates,
        "parent_candidates": parent_candidates,
        "resolved_operation": operation_for_candidates(candidates, create_label="create"),
        "status": status_for_candidates(candidates),
    }


def resolve_contact(
    db: Session,
    contact_data: dict[str, Any],
    partner_candidate: dict[str, Any] | None,
) -> dict[str, Any]:
    email = source_value(contact_data.get("email"))
    candidates: list[dict[str, Any]] = []
    if partner_candidate and email:
        query = db.query(ContractContact).filter(
            ContractContact.is_deleted.is_(False),
            ContractContact.partner_id == partner_candidate["id"],
            func.lower(ContractContact.email) == str(email).lower(),
        )
        candidates = [candidate_contact(row, "партнер + e-mail") for row in query.all()]

    return {
        "input": {
            "last_name": source_value(contact_data.get("last_name")),
            "first_name": source_value(contact_data.get("first_name")),
            "patronymic": source_value(contact_data.get("patronymic")),
            "full_name": " ".join(
                str(value)
                for value in [
                    source_value(contact_data.get("last_name")),
                    source_value(contact_data.get("first_name")),
                    source_value(contact_data.get("patronymic")),
                ]
                if value
            ),
            "email": email,
            "position": source_value(contact_data.get("position")),
            "mobile_phone": source_value((contact_data.get("phones") or {}).get("mobile")),
            "work_phone": source_value((contact_data.get("phones") or {}).get("work")),
            "extension_phone": source_value((contact_data.get("phones") or {}).get("extension")),
            "company_website": contact_data.get("company_website"),
            "address": source_value(contact_data.get("address")),
            "contact_status": contact_data.get("contact_status") or ("current" if contact_data.get("is_current", True) else "inactive"),
            "is_current": contact_data.get("is_current", True),
            "contact_type": contact_data.get("contact_type"),
            "reconciliation_participation": contact_data.get("reconciliation_participation"),
            "comments": contact_data.get("comments"),
        },
        "candidates": candidates,
        "resolved_operation": operation_for_candidates(candidates, create_label="create"),
        "status": status_for_candidates(candidates),
    }


def resolve_product(db: Session, product_data: dict[str, Any]) -> dict[str, Any]:
    product_name = source_value(product_data.get("product_name"))
    candidates: list[dict[str, Any]] = []

    if product_name:
        normalized = normalize(product_name)
        query = db.query(Product).filter(
            Product.is_deleted.is_(False),
            or_(
                func.lower(Product.product_name) == normalized,
                func.lower(Product.product_name_normalized) == normalized,
            ),
        )
        candidates.extend(candidate_product(row, "наименование") for row in query.all())

    candidates = unique_candidates(candidates)
    dosage_form = product_data.get("dosage_form") or {}
    substance = (product_data.get("active_substances") or [{}])[0]
    return {
        "input": {
            "import_key": product_data.get("import_key"),
            "product_name": product_name,
            "dosage_form_code": dosage_form.get("reference_code"),
            "dosage_form_name_ru": source_value(dosage_form.get("name_ru")),
            "dosage_form_name_en": source_value(dosage_form.get("name_en")),
            "dosage_form_group_ru": source_value(dosage_form.get("group_ru")),
            "dosage_form_group_en": source_value(dosage_form.get("group_en")),
            "dosage_form_add_new": dosage_form.get("add_new", False),
            "strength": source_value(product_data.get("strength")),
            "comment": product_data.get("comment"),
            "substance_reference_name": substance.get("reference_name"),
            "substance_name_ru": source_value(substance.get("name_ru")),
            "substance_name_en": source_value(substance.get("name_en")),
            "substance_atc_code": substance.get("atc_code"),
            "substance_add_new": substance.get("add_new", False),
        },
        "candidates": candidates,
        "resolved_operation": operation_for_candidates(candidates, create_label="manual_review"),
        "status": status_for_candidates(candidates),
    }


def resolve_service(db: Session, service_data: dict[str, Any]) -> dict[str, Any]:
    code = service_data.get("service_code")
    reference = accounting_service_reference(code)
    service = None
    if reference:
        service = db.query(AccountingService).filter(
            AccountingService.name == reference["db_lookup_name"],
            AccountingService.is_active.is_(True),
        ).first()

    price = service_data.get("price") or {}
    amount = source_value(price.get("amount"))
    return {
        "input": {
            "service_code": code,
            "service_name": reference["name_ru"] if reference else None,
            "unit": service_data.get("unit"),
            "amount": amount,
            "contract_number": source_value(service_data.get("contract_number")),
            "comment": source_value(service_data.get("comment")),
        },
        "candidates": [
            {
                "id": service.id,
                "label": service.name,
                "reason": "код услуги",
            }
        ]
        if service
        else [],
        "resolved_operation": "manual_review" if service else "manual_review",
        "status": "unique_match" if service else "not_found",
        "warning": None if service else "Код услуги не найден в справочнике/БД.",
    }


def candidate_partner(partner: Partner, reason: str) -> dict[str, Any]:
    return {
        "id": partner.id,
        "label": f"{partner.partner_code} / {partner.partner_name}",
        "reason": reason,
    }


def candidate_contract(contract: Contract, reason: str) -> dict[str, Any]:
    return {
        "id": contract.id,
        "label": f"{contract.contract_number} от {contract.contract_date} / {contract.contract_type}",
        "reason": reason,
    }


def candidate_contact(contact: ContractContact, reason: str) -> dict[str, Any]:
    name = " ".join(part for part in [contact.last_name, contact.first_name, contact.patronymic] if part)
    return {
        "id": contact.id,
        "label": f"{name} / {contact.email or '-'}",
        "reason": reason,
    }


def candidate_product(product: Product, reason: str) -> dict[str, Any]:
    current_registration = crud.current_product_registration(product)
    registration_number = (
        current_registration.registration_number
        if current_registration
        else product.authorization_number
    )
    return {
        "id": product.id,
        "label": f"{product.product_name} / {registration_number or '-'}",
        "reason": reason,
    }


def source_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def ensure_source_object(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict) and "value" in value:
        return value
    return {"value": value}


def first_source_object(*values: Any) -> dict[str, Any] | None:
    for value in values:
        if source_value(value) not in (None, ""):
            return ensure_source_object(value)
    return None


def with_value(template: Any, value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(template, dict):
        result = deepcopy(template)
        result["value"] = value
        return result
    return {"value": value}


def numeric_term_source(value: Any, template: str) -> dict[str, Any] | None:
    extracted = source_value(value)
    if extracted is None:
        return None
    source = value.get("source") if isinstance(value, dict) else None
    return {"value": template.format(value=extracted), "source": source}


def normalize_code(value: Any) -> str | None:
    extracted = source_value(value)
    if extracted is None:
        return None
    return str(extracted).strip()


def normalize_code_source(value: Any, mapping: dict[str, str]) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        result = deepcopy(value)
        raw = str(result.get("value") or "").strip()
        result["value"] = mapping.get(raw, mapping.get(raw.upper(), raw.lower()))
        return result
    raw = str(value).strip()
    return {"value": mapping.get(raw, mapping.get(raw.upper(), raw.lower()))}


def normalize_role_code(value: Any) -> str:
    raw = normalize_code(value)
    return {
        "CUSTOMER": "mah",
        "CONTRACTOR": "pv_service_provider",
        "MAH": "mah",
        "LOCAL_REPRESENTATIVE": "local_representative",
        "MANUFACTURER": "manufacturer",
        "DISTRIBUTOR": "distributor",
        "APPLICANT": "applicant",
    }.get(raw or "", raw or "other")


def normalize_responsible_party(value: Any) -> str:
    raw = normalize_code(value)
    return {
        "CUSTOMER": "partner",
        "CONTRACTOR": "our_company",
        "MAH": "mah",
        "BOTH": "both",
    }.get(raw or "", "unspecified")


def normalize_unit(value: Any) -> str:
    raw = normalize_code(value)
    return {
        "SERVICE": "service",
        "MESSAGE": "message",
        "SEARCH": "search",
        "REPORT": "report",
        "PLAN": "document",
        "DOCUMENT": "document",
        "RECONCILIATION": "reconciliation",
        "REQUEST": "request",
        "HOUR": "hour",
        "MONTH": "month",
        "QUARTER": "quarter",
        "YEAR": "year",
    }.get(raw or "", (raw or "service").lower())


def infer_contact_type(role_text: Any) -> str:
    text = normalize(role_text)
    if "кач" in text or "quality" in text:
        return "quality"
    if "рег" in text or "regulatory" in text:
        return "regulatory"
    if "договор" in text or "legal" in text:
        return "legal_contracts"
    if "директор" in text or "руковод" in text:
        return "management"
    return "pv"


def infer_message_type(value: Any) -> str:
    raw = normalize_code(value) or ""
    text = raw.lower()
    if "icsr" in text:
        return "serious_icsr"
    if "pregnancy" in text:
        return "pregnancy"
    if "literature" in text:
        return "literature_case"
    if "regulatory" in text:
        return "regulatory_request"
    return "other"


def split_full_name(full_name: Any) -> tuple[str | None, str | None, str | None]:
    if not full_name:
        return None, None, None
    parts = str(full_name).strip().split()
    if not parts:
        return None, None, None
    last_name = parts[0]
    first_name = parts[1] if len(parts) > 1 else "Не указано"
    patronymic = " ".join(parts[2:]) if len(parts) > 2 else None
    return last_name, first_name, patronymic


def remove_empty(value: Any) -> Any:
    if isinstance(value, dict):
        if "value" in value and value.get("value") is None:
            return deepcopy(value)
        result = {}
        for key, item in value.items():
            cleaned = remove_empty(item)
            if cleaned is None:
                continue
            if cleaned == {} and key not in {"phones"}:
                continue
            if cleaned == []:
                continue
            result[key] = cleaned
        return result
    if isinstance(value, list):
        return [remove_empty(item) for item in value if remove_empty(item) is not None]
    return value


def normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def status_for_candidates(candidates: list[dict[str, Any]]) -> str:
    if len(candidates) == 1:
        return "unique_match"
    if len(candidates) > 1:
        return "multiple_candidates"
    return "not_found"


def operation_for_candidates(candidates: list[dict[str, Any]], *, create_label: str) -> str:
    if len(candidates) == 1:
        return "update"
    if len(candidates) > 1:
        return "manual_review"
    return create_label


def unique_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[Any, dict[str, Any]] = {}
    for candidate in candidates:
        existing = by_id.get(candidate["id"])
        if existing:
            reasons = [reason.strip() for reason in str(existing["reason"]).split(",")]
            if candidate["reason"] not in reasons:
                existing["reason"] = f"{existing['reason']}, {candidate['reason']}"
            continue
        by_id[candidate["id"]] = dict(candidate)
    return list(by_id.values())


def unique_strings(values: list[str]) -> list[str]:
    result = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def json_path(path) -> str:
    parts = [str(part) for part in path]
    return "$" if not parts else "$." + ".".join(parts)


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_references() -> dict[str, Any]:
    return json.loads(REFERENCES_PATH.read_text(encoding="utf-8"))


def accounting_service_reference(code: str | None) -> dict[str, Any] | None:
    for row in load_references().get("accounting_services", []):
        if row.get("code") == code:
            return row
    return None
