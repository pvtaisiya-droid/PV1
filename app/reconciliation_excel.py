from __future__ import annotations

from datetime import date
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile


LABELS = {
    "ru": {
        "company": "ARS PharmRussia",
        "partner": "Партнер",
        "period": "Период сверки",
        "generated": "Дата формирования",
        "contact": "Контактное лицо партнера",
        "email": "Email",
        "our_count": "Количество сообщений от нашей компании",
        "partner_count": "Количество сообщений от партнера",
        "matched": "Количество совпавших сообщений",
        "discrepancies": "Количество расхождений",
        "prepared_by": "Подготовил",
        "status": "Статус сверки",
        "sign_off": "Подтверждение сверки",
        "our_responsible": "ФИО ответственного лица нашей компании",
        "partner_contact": "ФИО контактного лица партнера",
        "date": "Дата",
        "comments": "Комментарии",
    },
    "en": {
        "company": "ARS PharmRussia",
        "partner": "Partner",
        "period": "Reconciliation period",
        "generated": "Generated at",
        "contact": "Partner contact",
        "email": "Email",
        "our_count": "Cases from our company",
        "partner_count": "Cases from partner",
        "matched": "Matched cases",
        "discrepancies": "Discrepancies",
        "prepared_by": "Prepared by",
        "status": "Reconciliation status",
        "sign_off": "Sign-off",
        "our_responsible": "Our responsible person",
        "partner_contact": "Partner contact name",
        "date": "Date",
        "comments": "Comments",
    },
}

CASE_HEADERS = [
    "код сообщения / case ID",
    "дата получения нашей компанией",
    "дата передачи партнеру",
    "партнер",
    "препарат",
    "активное вещество",
    "пациент",
    "страна",
    "нежелательная реакция / adverse event",
    "seriousness",
    "тип сообщения",
    "источник сообщения",
    "краткое описание",
    "статус сверки",
    "комментарий",
]

PARTNER_HEADERS = [
    "код сообщения партнера",
    "внутренний код сообщения",
    "дата получения партнером",
    "дата передачи нашей компании",
    "партнер",
    "препарат",
    "активное вещество",
    "пациент",
    "страна",
    "нежелательная реакция / adverse event",
    "seriousness",
    "тип сообщения",
    "краткое описание",
    "статус сверки",
    "комментарий",
]


def build_reconciliation_workbook(
    *,
    partner_name: str,
    period_start: date,
    period_end: date,
    generated_at: date,
    contact_name: str | None,
    contact_email: str | None,
    prepared_by: str | None,
    status: str,
    our_items: list[Any],
    partner_items: list[Any],
    discrepancy_items: list[Any],
    language: str = "ru",
) -> bytes:
    labels = LABELS.get(language, LABELS["ru"])
    sheets = [
        (
            "Cover" if language == "en" else "Обложка",
            cover_rows(
                labels,
                partner_name,
                period_start,
                period_end,
                generated_at,
                contact_name,
                contact_email,
                prepared_by,
                status,
                len(our_items),
                len(partner_items),
                count_matched(our_items + partner_items),
                len(discrepancy_items),
            ),
        ),
        ("Cases from our company", case_rows(CASE_HEADERS, our_items)),
        ("Cases from partner", partner_rows(PARTNER_HEADERS, partner_items)),
        ("Discrepancies", case_rows(CASE_HEADERS, discrepancy_items)),
        ("Sign-off", signoff_rows(labels, contact_name, status)),
    ]
    return build_xlsx(sheets)


def cover_rows(
    labels: dict[str, str],
    partner_name: str,
    period_start: date,
    period_end: date,
    generated_at: date,
    contact_name: str | None,
    contact_email: str | None,
    prepared_by: str | None,
    status: str,
    our_count: int,
    partner_count: int,
    matched_count: int,
    discrepancy_count: int,
) -> list[list[Any]]:
    return [
        [labels["company"]],
        [],
        [labels["partner"], partner_name],
        [labels["period"], f"{period_start} - {period_end}"],
        [labels["generated"], generated_at],
        [labels["contact"], contact_name or ""],
        [labels["email"], contact_email or ""],
        [labels["our_count"], our_count],
        [labels["partner_count"], partner_count],
        [labels["matched"], matched_count],
        [labels["discrepancies"], discrepancy_count],
        [labels["prepared_by"], prepared_by or ""],
        [labels["status"], status],
    ]


def case_rows(headers: list[str], items: list[Any]) -> list[list[Any]]:
    rows = [headers]
    for item in items:
        rows.append(
            [
                value(item, "internal_case_number") or value(item, "partner_case_number"),
                value(item, "receipt_date_our_company"),
                value(item, "transfer_date_our_company"),
                value(item, "partner_name"),
                value(item, "product_name"),
                value(item, "active_substance"),
                value(item, "patient"),
                value(item, "country"),
                value(item, "adverse_event"),
                value(item, "seriousness"),
                value(item, "case_type"),
                value(item, "source_type"),
                value(item, "short_description"),
                value(item, "reconciliation_status"),
                value(item, "reviewer_comment"),
            ]
        )
    return rows


def partner_rows(headers: list[str], items: list[Any]) -> list[list[Any]]:
    rows = [headers]
    for item in items:
        rows.append(
            [
                value(item, "partner_case_number") or value(item, "partner_case_id"),
                value(item, "internal_case_number"),
                value(item, "receipt_date_partner"),
                value(item, "transfer_date_partner"),
                value(item, "partner_name"),
                value(item, "product_name"),
                value(item, "active_substance"),
                value(item, "patient"),
                value(item, "country"),
                value(item, "adverse_event"),
                value(item, "seriousness"),
                value(item, "case_type"),
                value(item, "short_description"),
                value(item, "reconciliation_status"),
                value(item, "reviewer_comment"),
            ]
        )
    return rows


def signoff_rows(labels: dict[str, str], contact_name: str | None, status: str) -> list[list[Any]]:
    return [
        [labels["sign_off"]],
        [],
        [labels["our_responsible"], ""],
        [labels["date"], ""],
        [labels["partner_contact"], contact_name or ""],
        [labels["date"], ""],
        [labels["comments"], ""],
        [labels["status"], status],
    ]


def value(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def count_matched(items: list[Any]) -> int:
    return sum(1 for item in items if value(item, "reconciliation_status") in {"matched", "confirmed"})


def build_xlsx(sheets: list[tuple[str, list[list[Any]]]]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml(len(sheets)))
        archive.writestr("_rels/.rels", package_rels_xml())
        archive.writestr("xl/workbook.xml", workbook_xml(sheets))
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml(len(sheets)))
        archive.writestr("xl/styles.xml", styles_xml())
        for index, (_, rows) in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", worksheet_xml(rows))
    return output.getvalue()


def worksheet_xml(rows: list[list[Any]]) -> str:
    xml_rows = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, cell_value in enumerate(row, start=1):
            reference = f"{column_name(column_index)}{row_index}"
            cells.append(cell_xml(reference, cell_value, style=1 if row_index == 1 else 0))
        xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetViews><sheetView workbookViewId=\"0\"><pane ySplit=\"1\" topLeftCell=\"A2\" activePane=\"bottomLeft\" state=\"frozen\"/></sheetView></sheetViews>"
        "<sheetData>"
        + "".join(xml_rows)
        + "</sheetData></worksheet>"
    )


def cell_xml(reference: str, value_to_write: Any, style: int = 0) -> str:
    if value_to_write is None:
        value_to_write = ""
    if isinstance(value_to_write, date):
        value_to_write = value_to_write.isoformat()
    text = escape(str(value_to_write), {'"': "&quot;"})
    style_attr = f' s="{style}"' if style else ""
    return f'<c r="{reference}" t="inlineStr"{style_attr}><is><t>{text}</t></is></c>'


def workbook_xml(sheets: list[tuple[str, list[list[Any]]]]) -> str:
    sheet_nodes = []
    for index, (name, _) in enumerate(sheets, start=1):
        sheet_nodes.append(
            f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheets>"
        + "".join(sheet_nodes)
        + "</sheets></workbook>"
    )


def workbook_rels_xml(sheet_count: int) -> str:
    rels = []
    for index in range(1, sheet_count + 1):
        rels.append(
            f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        )
    rels.append(
        f'<Relationship Id="rId{sheet_count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(rels)
        + "</Relationships>"
    )


def package_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )


def content_types_xml(sheet_count: int) -> str:
    sheet_overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        + sheet_overrides
        + "</Types>"
    )


def styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF36A0DE"/><bgColor indexed="64"/></patternFill></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="1" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>'
        "</styleSheet>"
    )


def column_name(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters
