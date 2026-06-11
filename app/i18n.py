from collections.abc import Callable

from fastapi import Request
from jinja2 import pass_context


DEFAULT_LANGUAGE = "ru"
LANGUAGE_COOKIE = "pv_lang"
SUPPORTED_LANGUAGES = ("ru", "en")


TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "acknowledged": "Acknowledged",
        "cancelled": "Cancelled",
        "case": "Case",
        "Case": "Case",
        "CaseProduct": "Case product",
        "concomitant": "Concomitant",
        "converted_to_case": "Converted to case",
        "create": "Create",
        "cro": "CRO",
        "data_entry": "Data entry",
        "distributor": "Distributor",
        "duplicate": "Duplicate",
        "email": "Email",
        "failed": "Failed",
        "female": "Female",
        "FollowUp": "Follow-up",
        "in_triage": "In triage",
        "interacting": "Interacting",
        "invalid": "Invalid",
        "icsr": "ICSR",
        "license_partner": "License partner",
        "literature": "Literature",
        "local_affiliate": "Local affiliate",
        "mah": "MAH",
        "male": "Male",
        "medical_review": "Medical review",
        "new": "New",
        "non-serious": "Non-serious",
        "non_safety": "Non-safety",
        "other": "Other",
        "partner": "Partner",
        "Partner": "Partner",
        "phone": "Phone",
        "planned": "Planned",
        "Patient": "Patient",
        "Product": "Product",
        "ProductSubstance": "Product substance",
        "qc": "QC",
        "ready": "Ready",
        "ready_for_submission": "Ready for submission",
        "Reaction": "Reaction",
        "reopened": "Reopened",
        "SafetyReport": "Safety report",
        "serious": "Serious",
        "source": "Source",
        "spontaneous": "Spontaneous",
        "status_change": "Status change",
        "Submission": "Submission",
        "Substance": "Substance",
        "submitted": "Submitted",
        "suspect": "Suspect",
        "treatment": "Treatment",
        "triage": "Triage",
        "triage_status": "Triage status",
        "unknown": "Unknown",
        "valid_icsr": "Valid ICSR",
        "web": "Web",
        "workflow_status": "Workflow status",
        "submission_status": "Submission status",
    },
    "ru": {
        "ARS PharmRussia": "ARS PharmRussia",
        "API": "API",
        "API docs": "Документация API",
        "ATC": "ATC",
        "Action": "Действие",
        "Actions": "Действия",
        "Active substance": "Действующее вещество",
        "Add": "Добавить",
        "Add Case Product": "Добавить препарат в кейс",
        "Add Follow-up": "Добавить follow-up",
        "Add Partner": "Добавить партнёра",
        "Add Patient": "Добавить пациента",
        "Add Product": "Добавить препарат",
        "Add Reaction": "Добавить реакцию",
        "Add Substance": "Добавить вещество",
        "Add substance": "Добавить вещество",
        "Address": "Адрес",
        "Age": "Возраст",
        "Audit": "Аудит",
        "Audit Trail": "Журнал аудита",
        "Auth country": "Страна регистрации",
        "Authorization no.": "Номер регистрации",
        "Auto if blank": "Авто, если пусто",
        "Back": "Назад",
        "CAS": "CAS",
        "CSV": "CSV",
        "Case": "Кейс",
        "Case Metadata": "Метаданные кейса",
        "Case no.": "Номер кейса",
        "Case number": "Номер кейса",
        "Case type": "Тип кейса",
        "Cases": "Кейсы",
        "Change Workflow Status": "Изменить статус процесса",
        "Code": "Код",
        "Comment": "Комментарий",
        "Company": "Компания",
        "Congenital anomaly": "Врождённая аномалия",
        "Country": "Страна",
        "Create": "Создать",
        "Create Case": "Создать кейс",
        "Create Submission": "Создать отправку",
        "Create case": "Создать кейс",
        "Create report": "Создать сообщение",
        "Criteria": "Критерии",
        "Dashboard": "Дашборд",
        "Data Entry": "Ввод данных",
        "Death": "Смерть",
        "Description": "Описание",
        "Disability": "Инвалидизация",
        "Dosage form": "Лекарственная форма",
        "Dose": "Доза",
        "Due": "Срок",
        "Due date": "Срок",
        "Email": "Email",
        "Entity": "Сущность",
        "Event": "Событие",
        "Field": "Поле",
        "Follow-up": "Follow-up",
        "Follow-ups": "Follow-up",
        "Format": "Формат",
        "Free text": "Свободный текст",
        "Frequency": "Частота",
        "Height cm": "Рост, см",
        "Hospitalization": "Госпитализация",
        "ICSR": "ICSR",
        "INN": "МНН",
        "Identifier": "Идентификатор",
        "Initial received": "Первично получено",
        "Initials": "Инициалы",
        "Latest received": "Последнее получение",
        "Life threatening": "Угроза жизни",
        "MAH partner": "Партнёр MAH",
        "Manual ICSR entry": "Ручной ввод ICSR",
        "Mark invalid": "Отметить как невалидное",
        "Mark valid ICSR": "Отметить как валидный ICSR",
        "MedDRA PT code": "Код MedDRA PT",
        "MedDRA PT name": "Название MedDRA PT",
        "Medical history": "Медицинский анамнез",
        "Minimum Criteria": "Минимальные критерии",
        "Name": "Название",
        "Narrative": "Описание случая",
        "New Case": "Новый кейс",
        "New Safety Report": "Новое сообщение по безопасности",
        "New case": "Новый кейс",
        "New": "Новое",
        "No": "Нет",
        "No audit entries.": "Записей аудита пока нет.",
        "No cases yet.": "Кейсов пока нет.",
        "No follow-ups.": "Follow-up пока нет.",
        "No partners yet.": "Партнёров пока нет.",
        "No patients.": "Пациентов пока нет.",
        "No products yet.": "Препаратов пока нет.",
        "No products.": "Препаратов пока нет.",
        "No reactions.": "Реакций пока нет.",
        "No safety reports yet.": "Сообщений по безопасности пока нет.",
        "No substances yet.": "Веществ пока нет.",
        "No submissions.": "Отправок пока нет.",
        "No submissions yet.": "Отправок пока нет.",
        "No.": "№",
        "Non-serious": "Несерьёзная",
        "None": "Нет",
        "Object": "Объект",
        "Old": "Старое",
        "Open case": "Открыть кейс",
        "Open cases": "Открытые кейсы",
        "Operational overview": "Операционная сводка",
        "Other medically important": "Иное медицински значимое",
        "Outcome": "Исход",
        "Overview": "Обзор",
        "Overdue submissions": "Просроченные отправки",
        "PV MVP": "PV MVP",
        "PV responsible": "Ответственный за ФН",
        "Partner": "Партнёр",
        "Partners": "Партнёры",
        "Patient": "Пациент",
        "Patients": "Пациенты",
        "Phone": "Телефон",
        "Pregnancy": "Беременность",
        "Product": "Препарат",
        "Product List": "Список препаратов",
        "Products": "Препараты",
        "Raw text": "Исходный текст",
        "Reaction": "Реакция",
        "Reactions": "Реакции",
        "Ready": "Готово",
        "Reason": "Причина",
        "Received": "Получено",
        "Received date": "Дата получения",
        "Recipient": "Получатель",
        "Recipient partner": "Партнёр-получатель",
        "Region": "Регион",
        "Report no.": "Номер сообщения",
        "Report type": "Тип сообщения",
        "Reported name": "Сообщённое название",
        "Reported term": "Сообщённый термин",
        "Reporter": "Репортёр",
        "Reports": "Сообщения",
        "Reports awaiting triage": "Сообщения на triage",
        "Route": "Путь",
        "Role": "Роль",
        "SDEA": "SDEA",
        "SDEA required": "Требуется SDEA",
        "Safety Reports": "Сообщения по безопасности",
        "Safety report": "Сообщение по безопасности",
        "Save status": "Сохранить статус",
        "Save triage": "Сохранить triage",
        "Serious": "Серьёзная",
        "Serious cases": "Серьёзные кейсы",
        "Seriousness": "Серьёзность",
        "Sex": "Пол",
        "Significant new information": "Значимая новая информация",
        "Source": "Источник",
        "Source Text": "Исходный текст",
        "Status": "Статус",
        "Strength": "Дозировка",
        "Subject": "Тема",
        "Submission": "Отправка",
        "Submissions": "Отправки",
        "Submissions due": "Отправки к сроку",
        "Submitted": "Отправлено",
        "Substance": "Вещество",
        "Substances": "Вещества",
        "Timestamp": "Время",
        "Total cases": "Всего кейсов",
        "Total safety reports": "Всего сообщений",
        "Triage": "Triage",
        "Type": "Тип",
        "Unit": "Ед.",
        "Update": "Обновить",
        "User": "Пользователь",
        "Valid ICSR": "Валидный ICSR",
        "Verbatim": "Дословно",
        "Version": "Версия",
        "View": "Открыть",
        "Weight kg": "Вес, кг",
        "Workflow status": "Статус процесса",
        "Worldwide ID": "Глобальный ID",
        "Yes": "Да",
        "acknowledged": "Подтверждена",
        "cancelled": "Отменена",
        "case": "Кейс",
        "Case": "Кейс",
        "CaseProduct": "Препарат в кейсе",
        "concomitant": "Сопутствующий",
        "converted_to_case": "Преобразовано в кейс",
        "create": "Создание",
        "cro": "CRO",
        "data_entry": "Ввод данных",
        "distributor": "Дистрибьютор",
        "duplicate": "Дубликат",
        "email": "Email",
        "failed": "Ошибка",
        "female": "Женский",
        "FollowUp": "Follow-up",
        "in_triage": "На triage",
        "interacting": "Взаимодействующий",
        "invalid": "Невалидное",
        "icsr": "ICSR",
        "license_partner": "Лицензионный партнёр",
        "literature": "Литература",
        "local_affiliate": "Локальный филиал",
        "mah": "MAH",
        "male": "Мужской",
        "medical_review": "Медицинская оценка",
        "new": "Новое",
        "non-serious": "Несерьёзная",
        "non_safety": "Не safety",
        "other": "Другое",
        "partner": "Партнёр",
        "Partner": "Партнёр",
        "phone": "Телефон",
        "planned": "Запланирована",
        "Patient": "Пациент",
        "Product": "Препарат",
        "ProductSubstance": "Вещество препарата",
        "products": "препаратов",
        "qc": "QC",
        "ready": "Готова",
        "ready_for_submission": "Готово к отправке",
        "Reaction": "Реакция",
        "records": "записей",
        "reopened": "Переоткрыт",
        "SafetyReport": "Сообщение по безопасности",
        "serious": "Серьёзная",
        "source": "источник",
        "spontaneous": "Спонтанный",
        "status_change": "Изменение статуса",
        "Submission": "Отправка",
        "Substance": "Вещество",
        "submitted": "Отправлена",
        "substances": "веществ",
        "suspect": "Подозреваемый",
        "treatment": "Лечение",
        "triage": "Triage",
        "triage_status": "Статус triage",
        "unknown": "Неизвестно",
        "valid_icsr": "Валидный ICSR",
        "web": "Веб",
        "workflow_status": "Статус процесса",
        "submission_status": "Статус отправки",
    },
}


def get_language(request: Request) -> str:
    query_language = request.query_params.get("lang")
    if query_language in SUPPORTED_LANGUAGES:
        return query_language

    cookie_language = request.cookies.get(LANGUAGE_COOKIE)
    if cookie_language in SUPPORTED_LANGUAGES:
        return cookie_language

    return DEFAULT_LANGUAGE


async def language_middleware(request: Request, call_next: Callable):
    query_language = request.query_params.get("lang")
    request.state.lang = get_language(request)
    response = await call_next(request)

    if query_language in SUPPORTED_LANGUAGES:
        response.set_cookie(
            LANGUAGE_COOKIE,
            query_language,
            max_age=60 * 60 * 24 * 365,
            samesite="lax",
        )

    return response


def translate(text: object, language: str) -> str:
    if text is None:
        return ""

    key = str(text)
    if not key:
        return ""

    translated = TRANSLATIONS.get(language, {}).get(key)
    if translated:
        return translated

    english = TRANSLATIONS["en"].get(key)
    if english:
        return english

    if "_" in key:
        return key.replace("_", " ").capitalize()

    return key


@pass_context
def template_translate(context, text: object, **kwargs) -> str:
    request = context.get("request")
    language = getattr(getattr(request, "state", None), "lang", DEFAULT_LANGUAGE)
    value = translate(text, language)
    if kwargs:
        return value.format(**kwargs)
    return value


@pass_context
def template_lang_url(context, language: str) -> str:
    request = context.get("request")
    if request is None:
        return f"?lang={language}"
    return str(request.url.include_query_params(lang=language))
