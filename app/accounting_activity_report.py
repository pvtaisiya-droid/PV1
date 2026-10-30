from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from typing import Any, Iterable
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy.orm import Session

from app import crud


SERVICE_OPTIONS = (
    {
        "code": "icsr",
        "label": "Поданные ИСНР",
        "description": "Квитанции и номера первичных отчётов Росздравнадзора.",
    },
    {
        "code": "psur",
        "label": "Поданные ПООБ",
        "description": "Подачи ПООБ/PSUR с отчётным периодом и регистрационными данными.",
    },
    {
        "code": "literature",
        "label": "Результаты литературного мониторинга",
        "description": "Выявленная за период информация по безопасности.",
    },
)
SERVICE_CODES = tuple(option["code"] for option in SERVICE_OPTIONS)
SERVICE_LABELS = {option["code"]: option["label"] for option in SERVICE_OPTIONS}
SUBMITTED_STATUSES = {"submitted", "acknowledged"}

RESULT_TYPE_LABELS = {
    "potential_icsr": "Потенциальная ИСНР по результатам литературного мониторинга",
    "signal": "Сигнал по безопасности по результатам литературного мониторинга",
    "medical_information": "Медицинская информация по результатам литературного мониторинга",
    "safety_information": "Информация по безопасности по результатам литературного мониторинга",
    "other": "Результат литературного мониторинга",
}


@dataclass(frozen=True)
class ActivitySection:
    code: str
    title: str
    headers: list[str]
    rows: list[list[str]]
    empty_message: str


@dataclass(frozen=True)
class ActivityReport:
    date_from: date
    date_to: date
    generated_on: date
    selected_services: tuple[str, ...]
    period_label: str
    preliminary_note: str | None
    sections: list[ActivitySection]

    @property
    def total_rows(self) -> int:
        return sum(len(section.rows) for section in self.sections)


@dataclass(frozen=True)
class WorkbookRow:
    values: list[Any]
    style: int
    height: float | None = None
    merge_to_column: int | None = None


def quarter_bounds(reference: date) -> tuple[date, date]:
    quarter = (reference.month - 1) // 3 + 1
    start_month = (quarter - 1) * 3 + 1
    start = date(reference.year, start_month, 1)
    if quarter == 4:
        next_start = date(reference.year + 1, 1, 1)
    else:
        next_start = date(reference.year, start_month + 3, 1)
    return start, date.fromordinal(next_start.toordinal() - 1)


def previous_quarter_bounds(reference: date) -> tuple[date, date]:
    current_start, _ = quarter_bounds(reference)
    previous_end = date.fromordinal(current_start.toordinal() - 1)
    return quarter_bounds(previous_end)


def format_date(value: date | datetime | None) -> str:
    if isinstance(value, datetime):
        value = value.date()
    return value.strftime("%d.%m.%Y") if value else ""


def submission_activity_date(submission: Any) -> date | None:
    for value in (
        getattr(submission, "submitted_at", None),
        getattr(submission, "acknowledged_at", None),
        getattr(submission, "created_at", None),
    ):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
    return None


def literature_activity_date(result: Any) -> date | None:
    values = [getattr(result, "publication_date", None)]
    search_log = getattr(result, "search_log", None)
    if search_log:
        values.append(getattr(search_log, "search_date", None))
    values.append(getattr(result, "created_at", None))
    for value in values:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
    return None


def in_period(value: date | None, date_from: date, date_to: date) -> bool:
    return value is not None and date_from <= value <= date_to


def normalize_services(services: Iterable[str]) -> tuple[str, ...]:
    selected = []
    for code in services:
        normalized = (code or "").strip().lower()
        if normalized not in SERVICE_CODES:
            raise ValueError(f"Неизвестная услуга: {code}.")
        if normalized not in selected:
            selected.append(normalized)
    if not selected:
        raise ValueError("Выберите хотя бы одну услугу для выгрузки.")
    return tuple(code for code in SERVICE_CODES if code in selected)


def validate_period(date_from: date, date_to: date) -> None:
    if date_from > date_to:
        raise ValueError("Дата начала периода не может быть позже даты окончания.")
    if (date_to - date_from).days > 3660:
        raise ValueError("Период выгрузки не должен превышать 10 лет.")


def describe_period(date_from: date, date_to: date, generated_on: date) -> tuple[str, str | None]:
    quarter_start, quarter_end = quarter_bounds(date_from)
    quarter_number = (date_from.month - 1) // 3 + 1
    if date_from == quarter_start and date_to == quarter_end:
        label = f"Q{quarter_number} {date_from.year} · {format_date(date_from)}–{format_date(date_to)}"
    elif date_from == quarter_start and date_to <= quarter_end:
        label = (
            f"Q{quarter_number} {date_from.year} · по состоянию на {format_date(date_to)} "
            f"({format_date(date_from)}–{format_date(date_to)})"
        )
    else:
        label = f"Период: {format_date(date_from)}–{format_date(date_to)}"

    current_start, current_end = quarter_bounds(generated_on)
    is_current_quarter = date_from <= generated_on <= date_to and generated_on < current_end
    if date_from == current_start and date_to <= current_end and date_to >= generated_on:
        is_current_quarter = True
    note = None
    if is_current_quarter:
        note = (
            f"Предварительный статус: текущий квартал ещё не завершён. "
            f"Отчёт отражает сведения, доступные на {format_date(generated_on)}, "
            "и не является окончательным квартальным расчётом."
        )
    return label, note


def case_display_number(case: Any) -> str:
    number = (getattr(case, "case_number", None) or "").strip()
    external = (getattr(case, "additional_case_numbers", None) or "").strip()
    if external:
        return f"{number}; {external}" if number else external
    return number


def included_submissions(db: Session, date_from: date, date_to: date) -> list[Any]:
    rows = []
    for submission in crud.list_submissions(db):
        if (submission.submission_status or "").lower() not in SUBMITTED_STATUSES:
            continue
        if in_period(submission_activity_date(submission), date_from, date_to):
            rows.append(submission)
    return rows


def icsr_section(submissions: list[Any]) -> ActivitySection:
    matching = [submission for submission in submissions if submission.case_id and submission.case]
    matching.sort(
        key=lambda submission: (
            submission.case.initial_received_date or submission_activity_date(submission) or date.min,
            submission.case.case_number or "",
            submission.submission_number or "",
        )
    )
    rows = [
        [
            format_date(submission.case.initial_received_date or submission_activity_date(submission)),
            case_display_number(submission.case),
            submission.acknowledgement_code or "",
            submission.submission_number or "",
        ]
        for submission in matching
    ]
    return ActivitySection(
        code="icsr",
        title="Квитанции о подаче ИСНР",
        headers=[
            "Дата получения ООО «АРС»",
            "Номер случая",
            "№ квитанции",
            "№ первичного отчёта Росздравнадзора",
        ],
        rows=rows,
        empty_message="За выбранный период поданные ИСНР не найдены.",
    )


def plan_product_label(plan: Any) -> str:
    product_name = plan.product.product_name if plan.product else ""
    substance = plan.active_substance
    inn_name = ""
    if substance:
        inn_name = substance.inn_name or substance.substance_name or ""
    if product_name and inn_name and product_name.casefold() != inn_name.casefold():
        return f"{product_name} / {inn_name}"
    return product_name or inn_name


def plan_marketing_authorisations(plan: Any) -> str:
    values: list[str] = []
    for link in getattr(plan, "psur_products", []) or []:
        if getattr(link, "is_deleted", False) or not getattr(link, "included_in_report", True):
            continue
        value = (getattr(link, "marketing_authorisation_number", None) or "").strip()
        if not value and getattr(link, "product", None):
            value = (link.product.authorization_number or "").strip()
        if value and value not in values:
            values.append(value)
    if not values and getattr(plan, "product", None):
        value = (plan.product.authorization_number or "").strip()
        if value:
            values.append(value)
    return "; ".join(values)


def psur_section(db: Session, submissions: list[Any]) -> ActivitySection:
    plans = {plan.id: plan for plan in crud.list_psur_plans(db)}
    matching = [
        (submission, plans.get(submission.pbrer_id))
        for submission in submissions
        if submission.pbrer_id and plans.get(submission.pbrer_id)
    ]
    matching.sort(
        key=lambda item: (
            submission_activity_date(item[0]) or date.min,
            plan_product_label(item[1]),
        )
    )
    rows = []
    for submission, plan in matching:
        rows.append(
            [
                format_date(submission_activity_date(submission)),
                plan_product_label(plan),
                plan_marketing_authorisations(plan),
                f"{format_date(plan.reporting_period_start)}–{format_date(plan.reporting_period_end)}",
                submission.submission_number or "",
                submission.acknowledgement_code or "",
            ]
        )
    return ActivitySection(
        code="psur",
        title="Квитанции о подаче ПООБ",
        headers=[
            "Дата подачи",
            "Препарат / МНН",
            "№ регистрационного удостоверения",
            "Отчётный период",
            "№ отчёта в АИС",
            "№ уведомления",
        ],
        rows=rows,
        empty_message="За выбранный период поданные ПООБ не найдены.",
    )


def literature_details(result: Any) -> str:
    prefix_values: list[str] = []
    if result.active_substance:
        prefix_values.append(result.active_substance.inn_name or result.active_substance.substance_name)
    for link in getattr(result, "products", []) or []:
        if not getattr(link, "is_deleted", False) and getattr(link, "product", None):
            name = link.product.product_name
            if name and name not in prefix_values:
                prefix_values.append(name)
    title = (result.publication_title or "").strip()
    detail = " / ".join(value for value in prefix_values if value)
    if detail and title:
        detail = f"{detail} — {title}"
    else:
        detail = detail or title
    comment = (result.specialist_comment or "").strip()
    if comment:
        detail = f"{detail}. {comment}" if detail else comment
    return detail


def literature_receipt(result: Any) -> str:
    linked_case = getattr(result, "linked_case", None)
    if not linked_case:
        return "Не применимо"
    codes = [
        submission.acknowledgement_code
        for submission in getattr(linked_case, "submissions", []) or []
        if not submission.is_deleted
        and (submission.submission_status or "").lower() in SUBMITTED_STATUSES
        and submission.acknowledgement_code
    ]
    return "; ".join(dict.fromkeys(codes)) or "Не применимо"


def literature_section(db: Session, date_from: date, date_to: date) -> ActivitySection:
    results = [
        result
        for result in crud.list_literature_results(db)
        if in_period(literature_activity_date(result), date_from, date_to)
    ]
    results.sort(key=lambda result: (literature_activity_date(result) or date.min, result.created_at, result.id))
    rows = []
    for result in results:
        activity_date = literature_activity_date(result)
        linked_case = getattr(result, "linked_case", None)
        code = linked_case.case_number if linked_case else f"LIT-{activity_date.year if activity_date else 'NA'}-{result.id[:8].upper()}"
        rows.append(
            [
                code,
                RESULT_TYPE_LABELS.get(result.result_type, RESULT_TYPE_LABELS["other"]),
                literature_details(result),
                literature_receipt(result),
            ]
        )
    return ActivitySection(
        code="literature",
        title="Иная активность за период",
        headers=["Код активности", "Активность", "Детали", "Регуляторная квитанция"],
        rows=rows,
        empty_message="За выбранный период результаты литературного мониторинга не найдены.",
    )


def build_activity_report(
    db: Session,
    *,
    date_from: date,
    date_to: date,
    services: Iterable[str],
    generated_on: date | None = None,
) -> ActivityReport:
    validate_period(date_from, date_to)
    selected = normalize_services(services)
    generated_on = generated_on or date.today()
    period_label, preliminary_note = describe_period(date_from, date_to, generated_on)
    submissions = included_submissions(db, date_from, date_to) if {"icsr", "psur"} & set(selected) else []

    section_builders = {
        "icsr": lambda: icsr_section(submissions),
        "psur": lambda: psur_section(db, submissions),
        "literature": lambda: literature_section(db, date_from, date_to),
    }
    sections = [section_builders[code]() for code in selected]
    return ActivityReport(
        date_from=date_from,
        date_to=date_to,
        generated_on=generated_on,
        selected_services=selected,
        period_label=period_label,
        preliminary_note=preliminary_note,
        sections=sections,
    )


def build_activity_report_xlsx(report: ActivityReport) -> bytes:
    rows: list[WorkbookRow] = [
        WorkbookRow(["Отчёт для бухгалтерии: оказанные услуги"], style=1, height=32, merge_to_column=6),
        WorkbookRow([report.period_label], style=2, height=24, merge_to_column=6),
        WorkbookRow(
            [
                f"Сформировано: {format_date(report.generated_on)} · "
                f"Выбрано услуг: {len(report.selected_services)} · Всего записей: {report.total_rows}"
            ],
            style=3,
            height=20,
            merge_to_column=6,
        ),
        WorkbookRow([], style=0, height=8),
    ]

    for section in report.sections:
        rows.append(WorkbookRow([section.title], style=4, height=24, merge_to_column=6))
        rows.append(WorkbookRow(section.headers, style=5, height=34))
        if section.rows:
            for index, row in enumerate(section.rows):
                rows.append(WorkbookRow(row, style=7 if index % 2 else 6, height=30))
        else:
            rows.append(WorkbookRow([section.empty_message], style=8, height=28, merge_to_column=len(section.headers)))
        rows.append(
            WorkbookRow(
                [f"Итого записей по услуге «{SERVICE_LABELS[section.code]}»: {len(section.rows)}"],
                style=9,
                height=22,
                merge_to_column=6,
            )
        )
        rows.append(WorkbookRow([], style=0, height=8))

    if report.preliminary_note:
        rows.append(WorkbookRow([report.preliminary_note], style=10, height=42, merge_to_column=6))

    return build_single_sheet_xlsx(
        sheet_name="Отчёт",
        rows=rows,
        column_widths=[22, 42, 54, 34, 20, 20],
    )


def build_single_sheet_xlsx(
    *,
    sheet_name: str,
    rows: list[WorkbookRow],
    column_widths: list[float],
) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml())
        archive.writestr("_rels/.rels", package_rels_xml())
        archive.writestr("xl/workbook.xml", workbook_xml(sheet_name))
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml())
        archive.writestr("xl/styles.xml", styles_xml())
        archive.writestr("xl/worksheets/sheet1.xml", worksheet_xml(rows, column_widths))
    return output.getvalue()


def worksheet_xml(rows: list[WorkbookRow], column_widths: list[float]) -> str:
    xml_rows: list[str] = []
    merged_ranges: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        height = f' ht="{row.height}" customHeight="1"' if row.height else ""
        cells = []
        for column_index, value in enumerate(row.values, start=1):
            cells.append(cell_xml(column_index, row_index, value, row.style))
        xml_rows.append(f'<row r="{row_index}"{height}>{"".join(cells)}</row>')
        if row.merge_to_column and row.merge_to_column > 1:
            merged_ranges.append(f"A{row_index}:{column_name(row.merge_to_column)}{row_index}")

    columns = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(column_widths, start=1)
    )
    merged_xml = ""
    if merged_ranges:
        merged_xml = (
            f'<mergeCells count="{len(merged_ranges)}">'
            + "".join(f'<mergeCell ref="{cell_range}"/>' for cell_range in merged_ranges)
            + "</mergeCells>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
        '<sheetViews><sheetView workbookViewId="0" showGridLines="0">'
        '<pane ySplit="4" topLeftCell="A5" activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>"
        '<sheetFormatPr defaultRowHeight="18"/>'
        f"<cols>{columns}</cols>"
        f'<sheetData>{"".join(xml_rows)}</sheetData>'
        f"{merged_xml}"
        '<pageMargins left="0.25" right="0.25" top="0.4" bottom="0.4" header="0.2" footer="0.2"/>'
        '<pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0" paperSize="9"/>'
        "</worksheet>"
    )


def cell_xml(column_index: int, row_index: int, value: Any, style: int) -> str:
    reference = f"{column_name(column_index)}{row_index}"
    text = "" if value is None else str(value)
    escaped_text = escape(text, {'"': "&quot;"})
    preserve = ' xml:space="preserve"' if text != text.strip() or "\n" in text else ""
    return (
        f'<c r="{reference}" t="inlineStr" s="{style}">'
        f'<is><t{preserve}>{escaped_text}</t></is></c>'
    )


def styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="6">'
        '<font><sz val="10"/><color rgb="FF17324A"/><name val="Calibri"/></font>'
        '<font><b/><sz val="18"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
        '<font><b/><sz val="12"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
        '<font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
        '<font><i/><sz val="10"/><color rgb="FF61768A"/><name val="Calibri"/></font>'
        '<font><b/><sz val="10"/><color rgb="FF17324A"/><name val="Calibri"/></font>'
        '</fonts>'
        '<fills count="7">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF1A4058"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF1B7F83"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFE8F0F3"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFEDF3F5"/><bgColor indexed="64"/></patternFill></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FFFFFFFF"/><bgColor indexed="64"/></patternFill></fill>'
        '</fills>'
        '<borders count="3">'
        '<border><left/><right/><top/><bottom/><diagonal/></border>'
        '<border><left style="thin"><color rgb="FFCCD9DF"/></left><right style="thin"><color rgb="FFCCD9DF"/></right><top style="thin"><color rgb="FFCCD9DF"/></top><bottom style="thin"><color rgb="FFCCD9DF"/></bottom><diagonal/></border>'
        '<border><left style="medium"><color rgb="FF1B7F83"/></left><right/><top/><bottom/><diagonal/></border>'
        '</borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="11">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="2" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="4" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="2" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="3" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="6" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="4" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
        '<xf numFmtId="0" fontId="4" fillId="5" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>'
        '<xf numFmtId="0" fontId="5" fillId="5" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="5" borderId="2" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>'
        '</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    )


def workbook_xml(sheet_name: str) -> str:
    safe_name = escape(sheet_name[:31], {'"': "&quot;"})
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{safe_name}" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    )


def workbook_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>'
    )


def package_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )


def content_types_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )


def column_name(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters
