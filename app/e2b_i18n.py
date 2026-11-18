"""Presentation of E2B diagnostics without changing source or historical records."""

import re


E2B_RU = {
    "Product matching": "Сопоставление препаратов",
    "A company product is matched. Review matching before marking this source outside PV ARS.": "Сопоставлен препарат нашей компании. Пересмотрите сопоставление перед отметкой «не ФН АРС».",
    "Search order: trade name first, then INN if no trade name matches. INN candidates require confirmation.": "Порядок поиска: сначала торговое название, затем МНН, если совпадений по торговому названию нет. Кандидатов по МНН нужно подтвердить.",
    "Previous product matching changed. Review and confirm the candidates again.": "Прежнее сопоставление изменилось. Проверьте и подтвердите кандидатов повторно.",
    "Trade name match": "Совпадение по торговому названию",
    "INN search": "Поиск по МНН",
    "Select product": "Выберите препарат",
    "Leave unmatched": "Оставить без сопоставления",
    "Confirm product matching": "Подтвердить сопоставление препаратов",
    "INN candidates": "Кандидаты по МНН",
    "Confirmed company product": "Подтверждён препарат нашей компании",
    "Confirmed non-company product": "Подтверждён препарат другой компании",
    "Product matching is unavailable for this case. Reload the preview.": "Сопоставление препаратов для этого случая недоступно. Обновите предпросмотр.",
    "Select a product candidate and provide a reason.": "Выберите препарат из кандидатов и укажите основание.",
    "Selected product is no longer a candidate. Reload the preview.": "Выбранный препарат отсутствует среди текущих кандидатов. Обновите предпросмотр.",
    "Product matching saved. Review the case before import.": "Сопоставление препаратов сохранено. Проверьте случай перед импортом.",
    "All documents from the archive": "Все документы архива",
    "This archive contains several cases. All PDF files and other attachments are shown below. Check the allocation and select the documents for the current case.": "В архиве несколько случаев. Ниже — все PDF и другие вложения архива. Проверьте распределение и отметьте документы текущего случая.",
    "All PDF files and other attachments from the archive are shown below. Select the documents for the current case.": "Ниже — все PDF и другие вложения архива. Отметьте документы текущего случая.",
    "Allocation is based on the archive folders. Check it against the document contents.": "Распределение предложено по папкам архива. Сверьте его с содержимым документов.",
    "Suggested for this case": "Отнесён к текущему случаю",
    "Suggested for another case": "Отнесён к другому случаю",
    "Not assigned to a case. Check manually.": "Не распределён — проверьте вручную",
    "No additional documents in this archive.": "В этом архиве нет дополнительных документов.",
    "Selected documents will be attached to this case.": "Отмеченные файлы будут приложены к текущему случаю.",
    "Safety reports": "Входящие сообщения",
    "Package review steps": "Шаги проверки пакета",
    "Initial": "Первичное",
    "Follow-up": "Повторное",
    "Case review pages": "Страницы проверки случая",
    "Cases in package": "Случаи пакета",
    "Ready to import": "Готов к импорту",
    "Select ready cases": "Выбрать готовые",
    "Selection": "Выбор",
    "Select case": "Выбрать случай",
    "Not determined": "Не определён",
    "Case overview": "Обзор случая",
    "Conflicts and remarks": "Противоречия и замечания",
    "Detailed ICSR": "Подробное ИСНР",
    "More details": "Подробнее",
    "Case characteristics": "Характеристика случая",
    "Product from our list": "ЛП из нашего списка",
    "Requires matching": "Требуется сопоставление",
    "Non-serious": "Несерьёзная",
    "Conflicts": "Противоречия",
    "No source conflicts.": "Противоречий между источниками нет.",
    "Import selected cases": "Импортировать выбранные",
    "Package actions": "Действия с пакетом",
    "Next": "Далее",
    "E2B import preview": "Предпросмотр импорта E2B",
    "E2B import summary": "Сводка импорта E2B",
    "Cases in archive": "Случаев в архиве",
    "Messages": "Сообщения",
    "Deaths": "Случаи со смертельным исходом",
    "Expectedness": "Предвиденность",
    "Requires assessment": "Требует оценки",
    "RO due date": "Срок подачи в регуляторный орган",
    "Not calculated": "Не рассчитан",
    "PV ARS scope": "Принадлежность ФН АРС",
    "Not checked": "Не проверено",
    "Company product found": "Найден препарат нашей компании",
    "not PV system ARS LLC / не ФН АРС": "Не ФН АРС",
    "company": "нашей компании",
    "not found": "не найдены",
    "non-company": "других компаний",
    "Files": "Файлы",
    "Errors / warnings": "Ошибки / предупреждения",
    "Source checksum": "Контрольная сумма источника",
    "Repeated E2B source detected.": "Источник E2B уже загружался.",
    "Import": "Импортировать",
    "Mark not PV ARS": "Отметить как «не ФН АРС»",
    "Re-check": "Проверить повторно",
    "Package contents": "Состав пакета",
    "File": "Файл",
    "Message": "Сообщение",
    "No files detected.": "Файлы не обнаружены.",
    "Detected messages and cases": "Обнаруженные сообщения и случаи",
    "Case key": "Идентификатор случая",
    "No E2B messages detected.": "Сообщения E2B не обнаружены.",
    "Message ID": "Идентификатор сообщения",
    "Sender": "Отправитель",
    "Receiver": "Получатель",
    "Dictionary match": "Сопоставление со справочником",
    "Company product": "Препарат нашей компании",
    "Non-company product": "Препарат другой компании",
    "Requires choice": "Требуется выбор",
    "Not found": "Не найдено",
    "Literature": "Литературный источник",
    "Title": "Название",
    "Citation": "Библиографическая ссылка",
    "Errors and warnings": "Ошибки и предупреждения",
    "Severity": "Уровень замечания",
    "Blocking": "Блокирует импорт",
    "No errors or warnings.": "Ошибок и предупреждений нет.",
    "No ARS company product was found in this E2B source.": "В источнике E2B не найден препарат нашей компании.",
    "This package can be marked for partner reconciliation as not PV system ARS LLC / не ФН АРС. Normal case import is blocked until a company product is matched.": "Пакет можно отметить для сверки с партнёром как «не ФН АРС». Импорт случая заблокирован до сопоставления с препаратом нашей компании.",
    "E2B source uploaded. Review before import.": "Источник E2B загружен. Проверьте данные перед импортом.",
    "E2B package re-checked.": "Пакет E2B проверен повторно.",
    "E2B import cancelled.": "Импорт E2B отменён.",
    "Package marked as not PV system ARS LLC / не ФН АРС.": "Пакет отмечен как «не ФН АРС».",
    "Message and processing directions": "Сообщение и направления обработки",
    "E2B import completed. Add other processing directions if needed.": "Импорт завершён. При необходимости добавьте другие направления обработки.",
    "Импорт завершён. При необходимости добавьте другие направления обработки.": "Импорт завершён. При необходимости добавьте другие направления обработки.",
    "Technical details": "Технические подробности",
    "Source value": "Значение в источнике",
    "Source value: {value}": "Значение в источнике: {value}",
    "Unknown value": "Неизвестное значение",
    "Uploaded": "Загружен",
    "Detected": "Обнаружен",
    "Parsed": "Разобран",
    "Imported": "Импортирован",
    "Repeat upload": "Повторная загрузка",
    "Not PV ARS": "Не ФН АРС",
    "Error": "Ошибка",
    "Warning": "Предупреждение",
    "Cancelled": "Отменён",
    "Requires review": "Требует проверки",
    "Duplicate": "Дубликат",
    "Other file": "Другой файл",
    "Literature PDF": "PDF литературного источника",
    "Male": "Мужской",
    "Female": "Женский",
    "Unknown": "Неизвестно",
    "Years": "лет",
    "Months": "месяцев",
    "Weeks": "недель",
    "Days": "дней",
    "Hours": "часов",
    "Minutes": "минут",
    "Decades": "десятилетий",
    "Recovered": "Выздоровление",
    "Recovering": "В процессе выздоровления",
    "Not recovered": "Без выздоровления",
    "Recovered with sequelae": "Выздоровление с последствиями",
    "Fatal": "Смертельный исход",
    "Outcome code: {code}": "Код исхода: {code}",
    "Suspect": "Подозреваемый",
    "Concomitant": "Сопутствующий",
    "Interacting": "Взаимодействующий",
    "Treatment": "Лечение",
    "Oral": "Внутрь",
    "Intravenous": "Внутривенно",
    "Intramuscular": "Внутримышечно",
    "Subcutaneous": "Подкожно",
    "Topical": "Наружно",
    "Inhalation": "Ингаляционно",
    "mg": "мг",
    "g": "г",
    "microgram": "мкг",
    "ml": "мл",
    "Unknown E2B diagnostic": "Другое замечание E2B",
    "Review the diagnostic details before continuing.": "Перед продолжением проверьте технические подробности замечания.",
    "E2B operation could not be completed. See technical details.": "Не удалось выполнить операцию E2B. См. технические подробности.",
    "Company product not matched": "Препарат нашей компании не сопоставлен",
    "No exact company product match was found. Match the product dictionary or confirm that the package is outside PV ARS.": "Точное совпадение с препаратом нашей компании не найдено. Проверьте сопоставление со справочником или подтвердите, что пакет не относится к ФН АРС.",
    "Multiple cases in package": "В пакете несколько случаев",
    "The package contains {count} distinct cases. Import one case per package to avoid merging unrelated data.": "В пакете {count} разных случаев. Импортируйте по одному случаю на пакет, чтобы не объединить несвязанные данные.",
    "Unknown products": "Несопоставленные препараты",
    "{count} product records were not found in the dictionary.": "В справочнике не найдено записей препаратов: {count}.",
    "No XML files": "Нет XML-файлов",
    "Package does not contain XML files.": "Пакет не содержит XML-файлов.",
    "Unsafe ZIP path": "Недопустимый путь в ZIP",
    "Unsafe ZIP member path: {path}": "Недопустимый путь файла в ZIP: {path}",
    "Encrypted file": "Зашифрованный файл",
    "Encrypted ZIP member is not supported: {path}": "Зашифрованный файл в ZIP не поддерживается: {path}",
    "File size limit": "Превышен размер файла",
    "File exceeds size limit: {path}": "Файл превышает допустимый размер: {path}",
    "Package size limit": "Превышен размер пакета",
    "Unpacked package exceeds size limit.": "Распакованный пакет превышает допустимый размер.",
    "Nested ZIP depth limit": "Превышена глубина вложенных ZIP",
    "Nested ZIP depth limit reached: {path}": "Достигнут предел вложенности ZIP: {path}",
    "Invalid nested ZIP": "Повреждённый вложенный ZIP",
    "Nested ZIP cannot be opened: {path}": "Не удалось открыть вложенный ZIP: {path}",
    "XML parsing error": "Ошибка разбора XML",
    "Cannot parse this XML. See technical details.": "Не удалось разобрать XML. См. технические подробности.",
    "Package processing error": "Ошибка обработки пакета",
    "Cannot process this package. See technical details.": "Не удалось обработать пакет. См. технические подробности.",
    "Missing message identifier": "Не указан идентификатор сообщения",
    "Message identifier is required.": "Необходим идентификатор сообщения.",
    "Missing case identifier": "Не указан идентификатор случая",
    "Case identifier was not found.": "Идентификатор случая не найден.",
    "Missing reaction": "Не найдена реакция",
    "No reaction/event block was detected.": "Блок реакции или события не обнаружен.",
    "Missing product": "Не найден препарат",
    "No drug/product block was detected.": "Блок лекарственного препарата не обнаружен.",
}

# Exact notices also cover historical redirect messages. Dynamic details stay
# separate from the translatable message and are never treated as translation keys.
E2B_RU.update({
    "Only E2B XML files and ZIP packages can be imported.": "Можно импортировать только XML E2B и ZIP-пакеты.",
    "E2B source file exceeds configured upload size limit.": "Файл E2B превышает установленный предел загрузки.",
    "XML exceeds configured E2B file size limit.": "XML превышает допустимый размер файла E2B.",
    "XML contains external entity or DOCTYPE declaration.": "XML содержит внешнюю сущность или объявление DOCTYPE.",
    "ZIP cannot be opened.": "Не удалось открыть ZIP.",
    "Finalized batch cannot be re-checked.": "Завершённый пакет нельзя проверить повторно.",
    "Original E2B source file is not available for re-check.": "Исходный файл E2B недоступен для повторной проверки.",
    "Cancelled package must be re-checked before import.": "Перед импортом отменённый пакет нужно проверить повторно.",
    "Cancelled package must be re-checked before marking.": "Перед изменением отметки отменённый пакет нужно проверить повторно.",
    "Package is marked as not PV system ARS LLC and cannot be imported.": "Пакет отмечен как «не ФН АРС» и не может быть импортирован.",
    "Import has blocking errors.": "Есть замечания, блокирующие импорт.",
    "No company product was found in the E2B package.": "В пакете E2B не найден препарат нашей компании.",
    "No parsed XML messages are available for import.": "Нет разобранных XML-сообщений для импорта.",
    "No parsed XML messages are available.": "Нет разобранных XML-сообщений.",
    "Imported batch cannot be marked as not PV system ARS LLC.": "Импортированный пакет нельзя отметить как «не ФН АРС».",
    "Finalized batch cannot be cancelled from preview. Use reversal workflow.": "Завершённый пакет нельзя отменить в предпросмотре. Используйте процедуру отмены результата.",
    "Related case has changed. Reload the E2B preview.": "Связанный случай изменён. Обновите предпросмотр E2B.",
    "E2B import batch not found": "Пакет импорта E2B не найден",
    "Stored package path is outside E2B import storage.": "Путь пакета находится за пределами хранилища импорта E2B.",
    "Transaction cleanup path is outside E2B import storage.": "Путь очистки операции находится за пределами хранилища импорта E2B.",
    "Stored E2B file path is outside import storage.": "Путь файла E2B находится за пределами хранилища импорта.",
    "Embedded attachment path is outside E2B import storage.": "Путь встроенного вложения находится за пределами хранилища импорта E2B.",
    "Embedded E2B document path is outside import storage.": "Путь встроенного документа E2B находится за пределами хранилища импорта.",
})

E2B_EN = {
    "Импорт завершён. При необходимости добавьте другие направления обработки.": "E2B import completed. Add other processing directions if needed.",
}

E2B_RU.update({
    "XML sources": "Источники XML",
    "Clinical version": "Версия клинических данных",
    "Case imported": "Случай импортирован",
    "Selected case imported. Other cases remain separate.": "Выбранный случай импортирован. Остальные случаи обрабатываются отдельно.",
    "Conflicting source values. Resolve this case before import.": "В источниках есть противоречия. Выберите значения перед импортом этого случая.",
    "No company product match for this case. Other cases are assessed separately.": "Для этого случая не сопоставлен препарат нашей компании. Остальные случаи оцениваются отдельно.",
    "This case has blocking source diagnostics.": "В источниках этого случая есть замечания, блокирующие импорт.",
    "Source field": "Поле источника",
    "Source values": "Значения в источниках",
    "Decision": "Решение",
    "Select source value": "Выберите источник значения",
    "Decision reason": "Основание решения",
    "Save source decisions": "Сохранить решения по источникам",
    "Documents for this case": "Документы этого случая",
    "Unchecked documents remain in the source package.": "Неотмеченные документы остаются в исходном пакете.",
    "Import this case": "Импортировать этот случай",
    "Assembled data and source provenance": "Технические данные сборки и происхождение значений",
    "Select a case to import from this package.": "Выберите случай для импорта из пакета.",
    "Case group was not found. Reload the preview.": "Группа случая не найдена. Обновите предпросмотр.",
    "This case requires review before import.": "Перед импортом этого случая требуется проверка.",
    "Selected documents do not belong to this case group.": "Выбранные документы не относятся к этой группе случая.",
    "Finalized case sources cannot be changed.": "Источники завершённого случая нельзя изменить.",
    "Select valid source values and provide a reason.": "Выберите допустимые значения источников и укажите основание.",
    "Conflict resolution refers to an unavailable source.": "Решение по противоречию ссылается на недоступный источник.",
    "Case source decisions saved. Review the assembled case before import.": "Решения по источникам сохранены. Проверьте собранный случай перед импортом.",
    "Use separate case decisions for a package with multiple cases.": "Для пакета с несколькими случаями принимайте решения отдельно по каждому случаю.",
    "Partially imported": "Частично импортирован",
    "Multiple-case import is not enabled for this installation.": "Импорт нескольких случаев в этой установке пока недоступен.",
    "This source was parsed by an earlier importer. Re-check it before import.": "Источник разобран прежней версией импортёра. Выполните повторную проверку перед импортом.",
    "Some source transmissions were already imported. Review the mixed package before import.": "Часть передач из источников уже импортирована. Проверьте смешанный пакет перед импортом.",
    "An imported message has changed source content. Review before import.": "Содержимое ранее импортированного сообщения изменилось. Проверьте источник перед импортом.",
    "Patient": "Пациент", "Products": "Препараты", "Reactions": "Реакции", "Reporter": "Сообщившее лицо",
    "Narrative": "Описание", "Sex": "Пол", "Age": "Возраст", "Weight": "Масса тела", "Height": "Рост",
    "Birth date": "Дата рождения", "Death date": "Дата смерти", "Medical history": "Анамнез",
    "Dose": "Доза", "Dose unit": "Единица дозы", "Route": "Путь введения", "Outcome": "Исход",
    "Version": "Версия", "Medical narrative": "Описание случая", "Product name": "Название препарата",
    "Active substances": "Действующие вещества", "Reported term": "Сообщённый термин",
})

FIELD_KEYS = {"patient": "Patient", "products": "Products", "reactions": "Reactions", "reporter": "Reporter",
              "literature": "Literature", "narrative": "Narrative", "message": "Message", "sex": "Sex",
              "age_value": "Age", "age_unit": "Age", "weight": "Weight", "height": "Height",
              "birth_date": "Birth date", "death_date": "Death date", "medical_history": "Medical history",
              "dose_value": "Dose", "dose_unit": "Dose unit", "route": "Route", "outcome": "Outcome",
              "message_version": "Version", "medical_narrative": "Medical narrative",
              "reported_product_name": "Product name", "active_substances": "Active substances", "reported_term": "Reported term"}

LABELS = {
    "status": {"uploaded": "Uploaded", "detected": "Detected", "parsed": "Parsed", "imported": "Imported", "error": "Error", "warning": "Warning", "cancelled": "Cancelled", "requires_review": "Requires review", "repeat_upload": "Repeat upload", "not_pv_ars": "Not PV ARS", "duplicate": "Duplicate", "partially_imported": "Partially imported"},
    "file_role": {"xml": "XML", "pdf": "PDF", "cioms": "CIOMS", "literature_pdf": "Literature PDF", "other": "Other file"},
    "match": {"not_found": "Not found", "requires_choice": "Requires choice", "exact_company_product": "Company product", "exact_non_company_product": "Non-company product",
              "substance_candidates": "INN candidates", "confirmed_company_product": "Confirmed company product", "confirmed_non_company_product": "Confirmed non-company product"},
    "sex": {"male": "Male", "female": "Female", "unknown": "Unknown"},
    "age_unit": {"years": "Years", "year": "Years", "months": "Months", "month": "Months", "weeks": "Weeks", "week": "Weeks", "days": "Days", "day": "Days", "hours": "Hours", "hour": "Hours", "minutes": "Minutes", "decades": "Decades"},
    "outcome": {"recovered": "Recovered", "recovering": "Recovering", "not recovered": "Not recovered", "not_recovered": "Not recovered", "recovered with sequelae": "Recovered with sequelae", "fatal": "Fatal", "unknown": "Unknown"},
    "drug_role": {"suspect": "Suspect", "concomitant": "Concomitant", "interacting": "Interacting", "treatment": "Treatment", "unknown": "Unknown"},
    "route": {"oral": "Oral", "intravenous": "Intravenous", "intramuscular": "Intramuscular", "subcutaneous": "Subcutaneous", "topical": "Topical", "inhalation": "Inhalation"},
    "dose_unit": {"mg": "mg", "g": "g", "mcg": "microgram", "µg": "microgram", "ml": "ml"},
}

ISSUES = {
    "not_pv_ars_candidate": ("Company product not matched", "No exact company product match was found. Match the product dictionary or confirm that the package is outside PV ARS."),
    "multiple_cases_not_supported": ("Multiple cases in package", "The package contains {count} distinct cases. Import one case per package to avoid merging unrelated data."),
    "unknown_product": ("Unknown products", "{count} product records were not found in the dictionary."),
    "xml_not_found": ("No XML files", "Package does not contain XML files."),
    "unsafe_path": ("Unsafe ZIP path", "Unsafe ZIP member path: {path}"),
    "encrypted_file": ("Encrypted file", "Encrypted ZIP member is not supported: {path}"),
    "file_too_large": ("File size limit", "File exceeds size limit: {path}"),
    "package_too_large": ("Package size limit", "Unpacked package exceeds size limit."),
    "nested_zip_depth_limit": ("Nested ZIP depth limit", "Nested ZIP depth limit reached: {path}"),
    "invalid_nested_zip": ("Invalid nested ZIP", "Nested ZIP cannot be opened: {path}"),
    "xml_parse_error": ("XML parsing error", "Cannot parse this XML. See technical details."),
    "package_error": ("Package processing error", "Cannot process this package. See technical details."),
    "missing_message_identifier": ("Missing message identifier", "Message identifier is required."),
    "missing_case_identifier": ("Missing case identifier", "Case identifier was not found."),
    "missing_reaction": ("Missing reaction", "No reaction/event block was detected."),
    "missing_product": ("Missing product", "No drug/product block was detected."),
}


class E2BDisplay:
    def __init__(self, language):
        self.language = language

    def text(self, key, **parameters):
        text = E2B_RU.get(key, key) if self.language == "ru" else E2B_EN.get(key, key)
        return text.format(**parameters)

    def field_label(self, path):
        parts = path.split(".")
        labels = [self.text(FIELD_KEYS.get(parts[0], "Source field"))]
        if len(parts) > 1 and parts[1].isdigit():
            labels.append(str(int(parts[1]) + 1))
        if len(parts) > 1 and not parts[-1].isdigit():
            labels.append(self.text(FIELD_KEYS.get(parts[-1], "Source field")))
        return " · ".join(labels)

    def value(self, value, category):
        if value is None or value == "":
            return ""
        raw = str(value)
        if not raw:
            return ""
        key = LABELS[category].get(raw.casefold())
        if key:
            return self.text(key)
        if category == "outcome" and raw.isdecimal():
            # Keep the source code explicit rather than assume its coding system.
            return self.text("Outcome code: {code}", code=raw)
        if category in {"outcome", "route", "dose_unit"}:
            return self.text("Source value: {value}", value=raw)
        return self.text("Unknown value")

    def notice(self, raw, fallback="E2B operation could not be completed. See technical details."):
        if not raw:
            return None
        if raw in E2B_RU and not re.search(r"{\w+}", raw):
            return {"message": self.text(raw), "details": None}
        return {"message": self.text(fallback), "details": raw}

    def issue(self, issue):
        title, key = ISSUES.get(issue.issue_type, ("Unknown E2B diagnostic", "Review the diagnostic details before continuing."))
        parameters = {}
        if "{count}" in key:
            # Legacy text is immutable. Extract only the parameter, then translate
            # by stable issue type; a future wording change gets a safe fallback.
            patterns = {
                "multiple_cases_not_supported": r"The package contains (\d+) distinct cases\.",
                "unknown_product": r"(\d+) imported product\(s\) were not found",
            }
            match = re.match(patterns[issue.issue_type], issue.message)
            if match:
                parameters["count"] = int(match[1])
            else:
                key = "Review the diagnostic details before continuing."
        if "{path}" in key:
            parameters["path"] = issue.message.partition(": ")[2] or "—"
        if issue.issue_type in {"xml_parse_error", "package_error"}:
            notice = self.notice(issue.message, fallback=key)
            message = notice["message"]
        else:
            message = self.text(key, **parameters)
        return {"title": self.text(title), "message": message, "details": issue.message, "code": issue.issue_type}
