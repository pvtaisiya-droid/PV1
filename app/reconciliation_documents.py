from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from app.reconciliation_excel import build_reconciliation_workbook


RECONCILIATION_DOCUMENT_DIR = Path("uploads") / "reconciliations"
SUPPORTED_RECONCILIATION_FORMATS = {"xlsx", "docx"}


def generate_reconciliation_document_file(
    reconciliation,
    *,
    document_format: str = "xlsx",
) -> dict[str, Any]:
    document_format = normalize_document_format(document_format)
    RECONCILIATION_DOCUMENT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"partner_reconciliation_{reconciliation.id}.{document_format}"
    path = RECONCILIATION_DOCUMENT_DIR / filename
    content = build_reconciliation_document_bytes(
        reconciliation,
        document_format=document_format,
    )
    path.write_bytes(content)
    return {
        "document_path": str(path),
        "document_filename": filename,
        "document_format": document_format,
        "file_size_bytes": len(content),
        "mime_type": mime_type_for_document_format(document_format),
    }


def build_reconciliation_document_bytes(
    reconciliation,
    *,
    document_format: str = "xlsx",
) -> bytes:
    document_format = normalize_document_format(document_format)
    our_items = [item for item in reconciliation.items if item.source_side == "our_company"]
    partner_items = [item for item in reconciliation.items if item.source_side == "partner"]
    discrepancy_items = [
        item
        for item in reconciliation.items
        if item.reconciliation_status not in {"matched", "confirmed"}
    ]
    if document_format == "xlsx":
        return build_reconciliation_workbook(
            partner_name=reconciliation.partner.partner_name if reconciliation.partner else "",
            period_start=reconciliation.period_start,
            period_end=reconciliation.period_end,
            generated_at=date.today(),
            contact_name=reconciliation.contact_name,
            contact_email=reconciliation.contact_email,
            prepared_by=reconciliation.prepared_by,
            status=reconciliation.reconciliation_status,
            our_items=our_items,
            partner_items=partner_items,
            discrepancy_items=discrepancy_items,
            language=reconciliation.language,
        )
    return build_reconciliation_docx(
        reconciliation,
        our_items=our_items,
        partner_items=partner_items,
        discrepancy_items=discrepancy_items,
    )


def normalize_document_format(document_format: str | None) -> str:
    value = (document_format or "xlsx").strip().lower()
    if value not in SUPPORTED_RECONCILIATION_FORMATS:
        raise ValueError("Unsupported reconciliation document format.")
    return value


def mime_type_for_document_format(document_format: str) -> str:
    if document_format == "docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def build_reconciliation_docx(
    reconciliation,
    *,
    our_items: list[Any],
    partner_items: list[Any],
    discrepancy_items: list[Any],
) -> bytes:
    document_xml = docx_document_xml(
        reconciliation,
        our_items=our_items,
        partner_items=partner_items,
        discrepancy_items=discrepancy_items,
    )
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", docx_content_types_xml())
        archive.writestr("_rels/.rels", docx_package_rels_xml())
        archive.writestr("word/document.xml", document_xml)
    return output.getvalue()


def docx_document_xml(
    reconciliation,
    *,
    our_items: list[Any],
    partner_items: list[Any],
    discrepancy_items: list[Any],
) -> str:
    partner_name = reconciliation.partner.partner_name if reconciliation.partner else ""
    paragraphs = [
        paragraph("Сверка сообщений по безопасности", bold=True),
        paragraph(f"Партнер: {partner_name}"),
        paragraph(f"Период сверки: {reconciliation.period_start} - {reconciliation.period_end}"),
        paragraph(f"Дата формирования: {date.today()}"),
        paragraph(f"Ответственный: {reconciliation.prepared_by or ''}"),
        paragraph(f"Контакт партнера: {reconciliation.contact_name or ''}"),
        paragraph(f"Email: {reconciliation.contact_email or ''}"),
        paragraph(f"Итого сообщений: {len(our_items) + len(partner_items)}"),
        paragraph(f"Расхождений: {len(discrepancy_items)}"),
        paragraph("Таблица сообщений", bold=True),
        table_xml(
            [
                [
                    "Источник",
                    "Case ID",
                    "Дата",
                    "Препарат",
                    "Нежелательная реакция",
                    "Seriousness",
                    "Статус",
                    "Комментарий",
                ],
                *[docx_item_row(item) for item in our_items + partner_items],
            ]
        ),
        paragraph("Комментарии", bold=True),
        paragraph(reconciliation.comments or ""),
        paragraph("Подтверждение партнера", bold=True),
        paragraph("ФИО контактного лица партнера: ______________________________"),
        paragraph("Дата: ______________________________"),
        paragraph("Комментарии партнера: ______________________________"),
    ]
    body = "".join(paragraphs)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}<w:sectPr/></w:body></w:document>"
    )


def docx_item_row(item: Any) -> list[str]:
    source_side = value(item, "source_side") or ""
    date_value = (
        value(item, "receipt_date_our_company")
        or value(item, "receipt_date_partner")
        or ""
    )
    return [
        source_side,
        value(item, "internal_case_number") or value(item, "partner_case_number") or "",
        str(date_value),
        value(item, "product_name") or "",
        value(item, "adverse_event") or "",
        value(item, "seriousness") or "",
        value(item, "reconciliation_status") or "",
        value(item, "reviewer_comment") or value(item, "discrepancy_comment") or "",
    ]


def paragraph(text: object, *, bold: bool = False) -> str:
    text_xml = escape(str(text or ""))
    run = f"<w:r><w:t>{text_xml}</w:t></w:r>"
    if bold:
        run = f"<w:r><w:rPr><w:b/></w:rPr><w:t>{text_xml}</w:t></w:r>"
    return f"<w:p>{run}</w:p>"


def table_xml(rows: list[list[object]]) -> str:
    row_xml = []
    for row in rows:
        cells = "".join(f"<w:tc>{paragraph(cell)}</w:tc>" for cell in row)
        row_xml.append(f"<w:tr>{cells}</w:tr>")
    return "<w:tbl>" + "".join(row_xml) + "</w:tbl>"


def value(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def docx_content_types_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )


def docx_package_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
