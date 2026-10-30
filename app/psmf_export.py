from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable
from zipfile import ZIP_DEFLATED, ZipFile

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents
from sqlalchemy.orm import Session

from app.models import Attachment, DocumentVersion, PSMFBuildDocument


BRAND_LOGO_PATH = Path(__file__).resolve().parent / "static" / "brand" / "ars-pharmrussia-logo.png"
from app.config import get_settings

DOCUMENT_UPLOAD_ROOT = get_settings().storage_root / "documents"

PAGE_WIDTH, PAGE_HEIGHT = A4
LEFT_MARGIN = 20 * mm
RIGHT_MARGIN = 15 * mm
TOP_MARGIN = 35 * mm
BOTTOM_MARGIN = 18 * mm
CONTENT_WIDTH = PAGE_WIDTH - LEFT_MARGIN - RIGHT_MARGIN

PARTNER_SOURCE_MODULES = {"partners", "contracts", "products", "psur"}
EMPTY_EXPORT_TEXTS = {
    "",
    "Раздел подлежит заполнению ответственным лицом. При подготовке финальной версии необходимо проверить актуальность данных, заполнить применимые сведения и утвердить компонент.",
    "На дату формирования МФСФ сведения отсутствуют / не применимо.",
}

PARTNER_ANNEX_CODES = {
    "B-1-PARTNER-ORG": "Приложение Б-1.1",
    "B-3-PARTNER-CONTRACTS": "Приложение Б-3.1",
    "V-1-PARTNER-SOURCES": "Приложение В-1.1",
    "G-1-PARTNER-SYSTEMS": "Приложение Г-1.1",
    "D-1-PARTNER-SOP": "Приложение Д-1.1",
    "E-2-PARTNER-EFFECTIVENESS": "Приложение Е-2.1",
    "ZH-1-PARTNER-AUDIT-PLAN": "Приложение Ж-1.1",
    "ZH-2-PARTNER-AUDITS": "Приложение Ж-2.1",
    "ZH-3-PARTNER-FINDINGS": "Приложение Ж-3.1",
    "Z-2-PARTNER-MONITORING": "Приложение З-2.1",
    "I-2-PARTNER-CHANGELOG": "Приложение И-2.1",
    "Z-1-PARTNER-PRODUCTS": "Приложение З-1",
    "Z-4-PARTNER-PSUR": "Приложение З-4",
    "ZH-4-PARTNER-TRAINING": "Приложение Ж-4",
    "Z-3-PARTNER-RISK-MINIMIZATION": "Приложение З-3",
    "Z-5-PARTNER-ICSR": "Приложение З-5",
    "Z-6-PARTNER-SIGNALS": "Приложение З-6",
    "K-PARTNER-OTHER": "Приложение К",
}

ANNEX_GROUP_TITLES = {
    "А": "ПРИЛОЖЕНИЕ А. СВЕДЕНИЯ ОБ УПОЛНОМОЧЕННОМ ЛИЦЕ ПО ФАРМАКОНАДЗОРУ",
    "Б": "ПРИЛОЖЕНИЕ Б. ОРГАНИЗАЦИОННАЯ СТРУКТУРА ДЕРЖАТЕЛЯ РЕГИСТРАЦИОННОГО УДОСТОВЕРЕНИЯ",
    "В": "ПРИЛОЖЕНИЕ В. ИСТОЧНИКИ ДАННЫХ БЕЗОПАСНОСТИ",
    "Г": "ПРИЛОЖЕНИЕ Г. КОМПЬЮТЕРИЗИРОВАННЫЕ СИСТЕМЫ И БАЗЫ ДАННЫХ",
    "Д": "ПРИЛОЖЕНИЕ Д. ПЕРЕЧЕНЬ ПРОЦЕДУРНЫХ ДОКУМЕНТОВ",
    "Е": "ПРИЛОЖЕНИЕ Е. ЭФФЕКТИВНОСТЬ СИСТЕМЫ ФАРМАКОНАДЗОРА",
    "Ж": "ПРИЛОЖЕНИЕ Ж. СИСТЕМА КАЧЕСТВА",
    "З": "ПРИЛОЖЕНИЕ З. ЛЕКАРСТВЕННЫЕ ПРЕПАРАТЫ",
    "И": "ПРИЛОЖЕНИЕ И. КОНТРОЛЬ ЗАПИСЕЙ И ДОКУМЕНТАЦИИ",
    "К": "ПРИЛОЖЕНИЕ К. ПРОЧЕЕ",
    "Л": "ПРИЛОЖЕНИЕ Л. ПЕРЕЧЕНЬ ДЕРЖАТЕЛЕЙ РЕГИСТРАЦИОННЫХ УДОСТОВЕРЕНИЙ, ПРИМЕНЯЮЩИХ ДАННУЮ СИСТЕМУ ФАРМАКОНАДЗОРА",
}
ANNEX_LETTER_ORDER = {letter: index for index, letter in enumerate("АБВГДЕЖЗИКЛ")}


@dataclass(frozen=True)
class PdfArtifact:
    name: str
    title: str
    content: bytes


@dataclass(frozen=True)
class PSMFPdfPackage:
    main: PdfArtifact
    common_annexes: tuple[PdfArtifact, ...]
    partner_annexes: tuple[PdfArtifact, ...]
    full: PdfArtifact
    archive_name: str
    archive: bytes


@dataclass(frozen=True)
class AnnexEntry:
    row: dict[str, Any]
    blocks: tuple[dict[str, Any], ...]
    code: str
    partner_specific: bool


class NumberedCanvas(canvas.Canvas):
    """Canvas that writes local ``page X of Y`` numbering into the header."""

    def __init__(self, *args, **kwargs):
        canvas.Canvas.__init__(self, *args, **kwargs)
        self._saved_page_states: list[dict[str, Any]] = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        page_count = len(self._saved_page_states)
        for page_number, state in enumerate(self._saved_page_states, start=1):
            self.__dict__.update(state)
            self._draw_page_count(page_number, page_count)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def _draw_page_count(self, page_number: int, page_count: int) -> None:
        font_name = getattr(self, "_psmf_font_name", "Helvetica")
        self.saveState()
        self.setFont(font_name, 6.5)
        self.drawString(PAGE_WIDTH - RIGHT_MARGIN - 45 * mm, PAGE_HEIGHT - 12.3 * mm, "Страница:")
        self.drawRightString(
            PAGE_WIDTH - RIGHT_MARGIN - 2 * mm,
            PAGE_HEIGHT - 12.3 * mm,
            f"{page_number} из {page_count}",
        )
        self.restoreState()


class PSMFDocTemplate(BaseDocTemplate):
    def __init__(
        self,
        output: BytesIO,
        *,
        header_title: str,
        document_code: str,
        generated_at: datetime,
        build_type: str,
        regular_font: str,
        bold_font: str,
        title: str,
    ) -> None:
        super().__init__(
            output,
            pagesize=A4,
            leftMargin=LEFT_MARGIN,
            rightMargin=RIGHT_MARGIN,
            topMargin=TOP_MARGIN,
            bottomMargin=BOTTOM_MARGIN,
            title=title,
            author="ООО «АРС»",
        )
        self.header_title = header_title
        self.document_code = document_code
        self.generated_at = generated_at
        self.build_type = build_type
        self.regular_font = regular_font
        self.bold_font = bold_font
        frame = Frame(
            self.leftMargin,
            self.bottomMargin,
            self.width,
            self.height,
            id="psmf-content",
        )
        self.addPageTemplates(
            [PageTemplate(id="psmf", frames=[frame], onPage=self._draw_page_furniture)]
        )

    def _draw_page_furniture(self, pdf_canvas, _doc) -> None:
        pdf_canvas._psmf_font_name = self.regular_font
        pdf_canvas.saveState()
        header_top = PAGE_HEIGHT - 7 * mm
        header_bottom = PAGE_HEIGHT - 28 * mm
        logo_width = 28 * mm
        right_width = 50 * mm
        center_left = LEFT_MARGIN + logo_width
        right_left = PAGE_WIDTH - RIGHT_MARGIN - right_width

        pdf_canvas.setStrokeColor(colors.HexColor("#9CA3AF"))
        pdf_canvas.setLineWidth(0.45)
        pdf_canvas.rect(LEFT_MARGIN, header_bottom, CONTENT_WIDTH, header_top - header_bottom)
        pdf_canvas.line(center_left, header_bottom, center_left, header_top)
        pdf_canvas.line(right_left, header_bottom, right_left, header_top)
        pdf_canvas.line(right_left, header_bottom + 7 * mm, PAGE_WIDTH - RIGHT_MARGIN, header_bottom + 7 * mm)
        pdf_canvas.line(right_left, header_bottom + 14 * mm, PAGE_WIDTH - RIGHT_MARGIN, header_bottom + 14 * mm)

        if BRAND_LOGO_PATH.exists():
            pdf_canvas.drawImage(
                str(BRAND_LOGO_PATH),
                LEFT_MARGIN + 5 * mm,
                header_bottom + 4 * mm,
                width=18 * mm,
                height=13 * mm,
                preserveAspectRatio=True,
                mask="auto",
            )
        else:
            pdf_canvas.setFont(self.bold_font, 10)
            pdf_canvas.drawCentredString(LEFT_MARGIN + logo_width / 2, header_bottom + 9 * mm, "ARS")

        pdf_canvas.setFillColor(colors.black)
        pdf_canvas.setFont(self.bold_font, 7.3)
        pdf_canvas.drawCentredString(
            (center_left + right_left) / 2,
            header_bottom + 13.3 * mm,
            "ООО «АРС»",
        )
        header_lines = wrap_canvas_text(self.header_title, 42)
        y = header_bottom + 8.7 * mm
        pdf_canvas.setFont(self.bold_font, 6.5)
        for line in header_lines[:2]:
            pdf_canvas.drawCentredString((center_left + right_left) / 2, y, line)
            y -= 3.1 * mm

        pdf_canvas.setFont(self.regular_font, 6.5)
        pdf_canvas.drawString(right_left + 2 * mm, header_bottom + 9.2 * mm, self.document_code[:42])
        pdf_canvas.drawString(
            right_left + 2 * mm,
            header_bottom + 2.4 * mm,
            f"Дата формирования: {format_date(self.generated_at)}",
        )

        pdf_canvas.setFont(self.regular_font, 6.2)
        pdf_canvas.setFillColor(colors.HexColor("#4B5563"))
        pdf_canvas.drawString(LEFT_MARGIN, 8 * mm, "Регуляторным органам. Конфиденциально.")
        pdf_canvas.drawRightString(
            PAGE_WIDTH - RIGHT_MARGIN,
            8 * mm,
            "Черновая сборка" if self.build_type == "DRAFT" else "Финальная сборка",
        )
        if self.build_type == "DRAFT":
            pdf_canvas.saveState()
            pdf_canvas.setFillColor(colors.Color(0.70, 0.70, 0.70, alpha=0.18))
            pdf_canvas.setFont(self.bold_font, 42)
            pdf_canvas.translate(PAGE_WIDTH / 2, PAGE_HEIGHT / 2)
            pdf_canvas.rotate(35)
            pdf_canvas.drawCentredString(0, 0, "ЧЕРНОВИК")
            pdf_canvas.restoreState()
        pdf_canvas.restoreState()

    def afterFlowable(self, flowable) -> None:
        if not isinstance(flowable, Paragraph):
            return
        level = getattr(flowable.style, "toc_level", None)
        if level is None:
            return
        text = flowable.getPlainText()
        key = f"toc-{self.page}-{abs(hash((text, self.page)))}"
        self.canv.bookmarkPage(key)
        self.canv.addOutlineEntry(text, key, level=int(level), closed=False)
        self.notify("TOCEntry", (int(level), text, self.page, key))


def build_psmf_pdf_package(
    preview: dict[str, Any],
    *,
    build_type: str,
    build_version: str,
    generated_by: str,
    db: Session | None = None,
    build_id: str | None = None,
) -> PSMFPdfPackage:
    build_type = build_type.upper()
    generated_at = preview.get("generated_at") or datetime.now()
    settings = preview["settings"]
    partner = preview["partner"]
    main_rows, common_entries, partner_entries = partition_preview_rows(preview)

    common_artifacts = tuple(
        build_annex_artifact(
            entry,
            preview=preview,
            build_type=build_type,
            build_version=build_version,
            db=db,
            build_id=build_id,
        )
        for entry in common_entries
    )
    partner_artifacts = tuple(
        build_annex_artifact(
            entry,
            preview=preview,
            build_type=build_type,
            build_version=build_version,
            db=db,
            build_id=build_id,
        )
        for entry in partner_entries
    )
    annex_index = [artifact.title for artifact in (*common_artifacts, *partner_artifacts)]
    main_content = build_main_pdf(
        preview,
        rows=main_rows,
        annex_index=annex_index,
        build_type=build_type,
        build_version=build_version,
        generated_by=generated_by,
    )
    partner_name = safe_filename(partner.partner_name or "Партнер")
    date_stamp = generated_at.strftime("%Y%m%d")
    main = PdfArtifact(
        name=f"МФСФ_основная_часть_{partner_name}.pdf",
        title="Основная часть МФСФ",
        content=main_content,
    )
    full_name = f"МФСФ_{partner_name}_{date_stamp}_в.{safe_filename(build_version)}.pdf"
    full = PdfArtifact(
        name=full_name,
        title=f"МФСФ для {partner.partner_name}",
        content=merge_pdf_artifacts((main, *common_artifacts, *partner_artifacts)),
    )
    archive_name = f"Комплект_МФСФ_{partner_name}_{date_stamp}_в.{safe_filename(build_version)}.zip"
    archive = build_package_archive(main, common_artifacts, partner_artifacts, full)
    return PSMFPdfPackage(
        main=main,
        common_annexes=common_artifacts,
        partner_annexes=partner_artifacts,
        full=full,
        archive_name=archive_name,
        archive=archive,
    )


def partition_preview_rows(
    preview: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[AnnexEntry], list[AnnexEntry]]:
    main_rows: list[dict[str, Any]] = []
    common_entries: list[AnnexEntry] = []
    partner_entries: list[AnnexEntry] = []
    for row in preview["rows"]:
        component = row["component"]
        if component.component_type in {"MAIN_SECTION", "SUBSECTION"}:
            if component.code != "9":
                main_rows.append(row)
            continue
        if component.component_type != "ANNEX":
            continue
        common_blocks = tuple(
            block_row for block_row in row["blocks"] if not is_partner_block(block_row["block"])
        )
        partner_blocks = tuple(
            block_row for block_row in row["blocks"] if is_partner_block(block_row["block"])
        )
        component_has_content = not is_empty_export_text(row.get("content"))
        if common_blocks or (component.scope != "PARTNER_SPECIFIC" and component_has_content and not partner_blocks):
            common_entries.append(
                AnnexEntry(
                    row=row,
                    blocks=common_blocks,
                    code=component.code,
                    partner_specific=False,
                )
            )
        if partner_blocks or component.scope == "PARTNER_SPECIFIC":
            partner_entries.append(
                AnnexEntry(
                    row=row,
                    blocks=partner_blocks,
                    code=partner_annex_code(component.code, partner_blocks),
                    partner_specific=True,
                )
            )
    common_entries.sort(key=lambda entry: annex_sort_key(entry.code))
    partner_entries = [entry for entry in partner_entries if not entry.code.startswith("Приложение Л")]
    partner_entries.sort(key=lambda entry: annex_sort_key(entry.code))
    return main_rows, common_entries, partner_entries


def is_partner_block(block: Any) -> bool:
    return block.block_type == "PARTNER_BLOCK" or block.source_module in PARTNER_SOURCE_MODULES


def is_empty_export_text(value: str | None) -> bool:
    return " ".join((value or "").split()) in {" ".join(item.split()) for item in EMPTY_EXPORT_TEXTS}


def partner_annex_code(component_code: str, blocks: Iterable[dict[str, Any]]) -> str:
    for block_row in blocks:
        mapped = PARTNER_ANNEX_CODES.get(block_row["block"].code)
        if mapped:
            return mapped
    return component_code


def build_main_pdf(
    preview: dict[str, Any],
    *,
    rows: list[dict[str, Any]],
    annex_index: list[str],
    build_type: str,
    build_version: str,
    generated_by: str,
) -> bytes:
    output = BytesIO()
    regular_font, bold_font = register_fonts()
    styles = build_styles(regular_font, bold_font)
    settings = preview["settings"]
    partner = preview["partner"]
    generated_at = preview["generated_at"]
    document = PSMFDocTemplate(
        output,
        header_title="Мастер-файл системы фармаконадзора",
        document_code=settings.psmf_template_version,
        generated_at=generated_at,
        build_type=build_type,
        regular_font=regular_font,
        bold_font=bold_font,
        title=f"МФСФ для {partner.partner_name}",
    )
    story: list[Any] = [
        Spacer(1, 22 * mm),
        Paragraph("МАСТЕР-ФАЙЛ<br/>СИСТЕМЫ ФАРМАКОНАДЗОРА", styles["cover_title"]),
        Spacer(1, 6 * mm),
        Paragraph(escape(partner.partner_name), styles["cover_partner"]),
        Spacer(1, 10 * mm),
        metadata_table(
            [
                ["Версия сборки", build_version],
                ["Тип сборки", "Черновая" if build_type == "DRAFT" else "Финальная"],
                ["Дата формирования", format_date(generated_at)],
                ["Сформировал", generated_by or ""],
            ],
            styles,
        ),
        Spacer(1, 9 * mm),
        approval_table(styles),
        Spacer(1, 12 * mm),
        Paragraph(
            "РЕГУЛЯТОРНЫМ ОРГАНАМ, КОНФИДЕНЦИАЛЬНО!",
            styles["confidential_title"],
        ),
        Paragraph(
            "Информация, содержащаяся в настоящем мастер-файле системы фармаконадзора, "
            "предназначена для использования уполномоченными получателями. Все страницы "
            "документа содержат конфиденциальную информацию.",
            styles["small_justified"],
        ),
        PageBreak(),
        Paragraph("ОГЛАВЛЕНИЕ", styles["section_title"]),
    ]
    toc = TableOfContents()
    toc.levelStyles = [styles["toc_0"], styles["toc_1"]]
    story.extend([toc, PageBreak()])

    first_component = True
    for row in rows:
        component = row["component"]
        if component.component_type == "MAIN_SECTION" and not first_component:
            story.append(PageBreak())
        first_component = False
        style = styles["heading_1"] if component.component_type == "MAIN_SECTION" else styles["heading_2"]
        heading = f"{component.code}. {component.title_ru or component.title}" if component.code != "ВВЕДЕНИЕ" else component.title_ru or component.title
        story.append(Paragraph(escape(heading), style))
        add_text_content(story, row.get("content"), styles)
        for block_row in row["blocks"]:
            story.append(
                Paragraph(
                    escape(render_block_title(block_row["block"], partner.partner_name)),
                    styles["heading_3"],
                )
            )
            add_block_content(story, block_row, styles)

    story.extend([PageBreak(), Paragraph("9. ПРИЛОЖЕНИЯ К МФСФ", styles["heading_1"])])
    if annex_index:
        for title in annex_index:
            story.append(Paragraph(escape(title), styles["annex_index"]))
    else:
        story.append(Paragraph("Приложения в сборку не включены.", styles["body"]))
    document.multiBuild(story, canvasmaker=NumberedCanvas)
    return output.getvalue()


def build_annex_artifact(
    entry: AnnexEntry,
    *,
    preview: dict[str, Any],
    build_type: str,
    build_version: str,
    db: Session | None,
    build_id: str | None,
) -> PdfArtifact:
    component = entry.row["component"]
    partner = preview["partner"]
    settings = preview["settings"]
    generated_at = preview["generated_at"]
    regular_font, bold_font = register_fonts()
    styles = build_styles(regular_font, bold_font)
    output = BytesIO()
    document = PSMFDocTemplate(
        output,
        header_title=f"МФСФ. {entry.code}",
        document_code=settings.psmf_template_version,
        generated_at=generated_at,
        build_type=build_type,
        regular_font=regular_font,
        bold_font=bold_font,
        title=f"{entry.code}. {component.title_ru or component.title}",
    )
    group = component.parent
    group_title = annex_group_title(entry.code, group)
    version = entry.row.get("version")
    version_text = version.version if version else component.current_version or build_version
    story: list[Any] = [
        Spacer(1, 6 * mm),
        Paragraph("МАСТЕР-ФАЙЛ СИСТЕМЫ ФАРМАКОНАДЗОРА", styles["annex_master_title"]),
        Spacer(1, 2 * mm),
        Paragraph(escape(group_title.upper()), styles["annex_group_title"]),
        Spacer(1, 3 * mm),
        Paragraph(
            escape(f"{entry.code}. {component.title_ru or component.title}"),
            styles["annex_title"],
        ),
        Spacer(1, 3 * mm),
        metadata_table(
            [
                ["Версия заполнения", version_text],
                ["Дата заполнения", format_date(generated_at)],
                ["Партнер", partner.partner_name if entry.partner_specific else "Общая часть ООО «АРС»"],
            ],
            styles,
        ),
        Spacer(1, 5 * mm),
    ]
    if not entry.partner_specific or component.scope == "PARTNER_SPECIFIC":
        add_text_content(story, entry.row.get("content"), styles)
    for block_row in entry.blocks:
        add_block_content(story, block_row, styles)
        story.append(Spacer(1, 3 * mm))
    story.extend([Spacer(1, 7 * mm), annex_signature_table(styles)])
    document.multiBuild(story, canvasmaker=NumberedCanvas)

    attachment_paths = linked_pdf_paths(
        db,
        component_id=component.id,
        block_ids=[block_row["block"].id for block_row in entry.blocks],
        partner_id=partner.id,
        partner_specific=entry.partner_specific,
        component_scope=component.scope,
        annex_code=entry.code,
        build_id=build_id,
    )
    content = merge_pdf_bytes([output.getvalue(), *(path.read_bytes() for path in attachment_paths)])
    file_code = entry.code.replace("Приложение ", "").strip()
    if entry.partner_specific:
        name = f"Приложение {safe_filename(file_code)}.pdf"
    else:
        name = f"{safe_filename(file_code)}_в.{safe_filename(version_text)}.pdf"
    return PdfArtifact(
        name=name,
        title=f"{entry.code}. {component.title_ru or component.title}",
        content=content,
    )


def linked_pdf_paths(
    db: Session | None,
    *,
    component_id: str,
    block_ids: list[str],
    partner_id: str,
    partner_specific: bool,
    component_scope: str,
    annex_code: str,
    build_id: str | None = None,
) -> list[Path]:
    if db is None:
        return []
    if build_id:
        snapshots = (
            db.query(PSMFBuildDocument)
            .join(DocumentVersion, PSMFBuildDocument.document_version_id == DocumentVersion.id)
            .filter(
                PSMFBuildDocument.build_id == build_id,
                PSMFBuildDocument.annex_code == annex_code,
                PSMFBuildDocument.is_deleted.is_(False),
                DocumentVersion.is_deleted.is_(False),
            )
            .order_by(
                PSMFBuildDocument.inclusion_order.asc(),
                PSMFBuildDocument.created_at.asc(),
            )
            .all()
        )
        paths = []
        for snapshot in snapshots:
            version = snapshot.document_version
            if Path(version.file_name or version.storage_path or "").suffix.lower() != ".pdf":
                continue
            path = resolve_document_path(version.storage_path)
            if path and path.exists():
                paths.append(path)
        return paths
    object_ids = [component_id, *block_ids]
    rows = (
        db.query(Attachment)
        .filter(
            Attachment.is_deleted.is_(False),
            Attachment.related_object_type.in_(("PSMFBlock", "PSMFComponent")),
            Attachment.related_object_id.in_(object_ids),
            Attachment.storage_path.is_not(None),
        )
        .order_by(Attachment.document_date.asc(), Attachment.created_at.asc())
        .all()
    )
    paths: list[Path] = []
    for attachment in rows:
        if Path(attachment.file_name or attachment.storage_path or "").suffix.lower() != ".pdf":
            continue
        if partner_specific:
            if attachment.partner_id not in (None, partner_id):
                continue
            if attachment.related_object_id == component_id and component_scope != "PARTNER_SPECIFIC" and attachment.partner_id is None:
                continue
        elif attachment.partner_id is not None:
            continue
        path = resolve_document_path(attachment.storage_path)
        if path and path.exists():
            paths.append(path)
    return paths


def resolve_document_path(storage_path: str | None) -> Path | None:
    if not storage_path:
        return None
    candidate = (Path.cwd() / storage_path).resolve()
    try:
        candidate.relative_to(DOCUMENT_UPLOAD_ROOT)
    except ValueError:
        return None
    return candidate


def add_text_content(story: list[Any], content: str | None, styles: dict[str, ParagraphStyle]) -> None:
    text = (content or "").strip()
    if not text:
        return
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    for part in paragraphs:
        story.append(Paragraph(escape(part).replace("\n", "<br/>"), styles["body"]))


def add_block_content(story: list[Any], block_row: dict[str, Any], styles: dict[str, ParagraphStyle]) -> None:
    source_data = block_row.get("source_data")
    if source_data and source_data.get("headers") and source_data.get("rows"):
        story.append(source_table(source_data, styles))
        return
    add_text_content(story, block_row.get("content"), styles)


def source_table(source_data: dict[str, Any], styles: dict[str, ParagraphStyle]) -> Table:
    headers = source_data.get("headers") or []
    rows = source_data.get("rows") or []
    data = [[Paragraph(escape(str(value or "")), styles["table_header"]) for value in headers]]
    for row in rows:
        data.append([Paragraph(escape(str(value or "")), styles["table_cell"]) for value in row])
    widths = proportional_widths(headers, CONTENT_WIDTH)
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E5E7EB")),
                ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#6B7280")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def proportional_widths(headers: list[Any], total_width: float) -> list[float]:
    if not headers:
        return [total_width]
    weights = [max(1.0, min(4.0, len(str(header or "")) / 12)) for header in headers]
    weight_sum = sum(weights)
    return [total_width * weight / weight_sum for weight in weights]


def metadata_table(rows: list[list[Any]], styles: dict[str, ParagraphStyle]) -> Table:
    data = [
        [Paragraph(escape(str(label)), styles["meta_label"]), Paragraph(escape(str(value or "")), styles["meta_value"])]
        for label, value in rows
    ]
    table = Table(data, colWidths=[48 * mm, CONTENT_WIDTH - 48 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.45, colors.HexColor("#6B7280")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F3F4F6")),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def approval_table(styles: dict[str, ParagraphStyle]) -> Table:
    data = [
        ["", "Должность, организация", "Фамилия, имя, отчество", "Подпись / дата"],
        ["Составил", "", "", ""],
        ["Согласовал", "", "", ""],
        ["Утвердил", "", "", ""],
    ]
    formatted = [
        [Paragraph(escape(value), styles["table_header"] if row_index == 0 else styles["table_cell"]) for value in row]
        for row_index, row in enumerate(data)
    ]
    table = Table(formatted, colWidths=[25 * mm, 50 * mm, 55 * mm, CONTENT_WIDTH - 130 * mm], rowHeights=[8 * mm, 13 * mm, 13 * mm, 13 * mm])
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#4B5563")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F6")),
            ]
        )
    )
    return table


def annex_signature_table(styles: dict[str, ParagraphStyle]) -> Table:
    data = [[
        Paragraph("УЛФ<br/><font size='6'>Должность</font>", styles["signature"]),
        Paragraph("____________________<br/><font size='6'>ФИО</font>", styles["signature"]),
        Paragraph("____________________<br/><font size='6'>Подпись / дата</font>", styles["signature"]),
    ]]
    table = Table(data, colWidths=[CONTENT_WIDTH / 3] * 3)
    table.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "BOTTOM")]))
    return table


def build_styles(regular_font: str, bold_font: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    styles: dict[str, ParagraphStyle] = {
        "body": ParagraphStyle(
            "PSMFBody",
            parent=base["Normal"],
            fontName=regular_font,
            fontSize=9.5,
            leading=12.8,
            alignment=TA_JUSTIFY,
            spaceAfter=5,
        ),
        "small_justified": ParagraphStyle(
            "PSMFSmallJustified",
            parent=base["Normal"],
            fontName=regular_font,
            fontSize=8,
            leading=10.5,
            alignment=TA_JUSTIFY,
        ),
        "cover_title": ParagraphStyle(
            "PSMFCoverTitle",
            parent=base["Title"],
            fontName=bold_font,
            fontSize=16,
            leading=21,
            alignment=TA_CENTER,
        ),
        "cover_partner": ParagraphStyle(
            "PSMFCoverPartner",
            parent=base["Normal"],
            fontName=bold_font,
            fontSize=13,
            leading=17,
            alignment=TA_CENTER,
        ),
        "confidential_title": ParagraphStyle(
            "PSMFConfidentialTitle",
            parent=base["Normal"],
            fontName=bold_font,
            fontSize=9,
            leading=12,
            alignment=TA_CENTER,
            spaceAfter=4,
        ),
        "section_title": ParagraphStyle(
            "PSMFSectionTitle",
            parent=base["Heading1"],
            fontName=bold_font,
            fontSize=12,
            leading=15,
            alignment=TA_CENTER,
            spaceAfter=8,
        ),
        "heading_1": ParagraphStyle(
            "PSMFHeading1",
            parent=base["Heading1"],
            fontName=bold_font,
            fontSize=11.5,
            leading=14.5,
            alignment=TA_LEFT,
            spaceBefore=5,
            spaceAfter=5,
            keepWithNext=True,
        ),
        "heading_2": ParagraphStyle(
            "PSMFHeading2",
            parent=base["Heading2"],
            fontName=bold_font,
            fontSize=10.2,
            leading=13,
            alignment=TA_LEFT,
            spaceBefore=4,
            spaceAfter=4,
            keepWithNext=True,
        ),
        "heading_3": ParagraphStyle(
            "PSMFHeading3",
            parent=base["Heading3"],
            fontName=bold_font,
            fontSize=9.5,
            leading=12,
            alignment=TA_LEFT,
            spaceBefore=4,
            spaceAfter=3,
            keepWithNext=True,
        ),
        "heading_3_center": ParagraphStyle(
            "PSMFHeading3Center",
            parent=base["Heading3"],
            fontName=bold_font,
            fontSize=9.5,
            leading=12,
            alignment=TA_CENTER,
            spaceBefore=4,
            spaceAfter=4,
            keepWithNext=True,
        ),
        "annex_master_title": ParagraphStyle(
            "PSMFAnnexMasterTitle",
            parent=base["Normal"],
            fontName=bold_font,
            fontSize=10.5,
            leading=14,
            alignment=TA_CENTER,
        ),
        "annex_group_title": ParagraphStyle(
            "PSMFAnnexGroupTitle",
            parent=base["Normal"],
            fontName=bold_font,
            fontSize=9.5,
            leading=12.5,
            alignment=TA_CENTER,
        ),
        "annex_title": ParagraphStyle(
            "PSMFAnnexTitle",
            parent=base["Normal"],
            fontName=bold_font,
            fontSize=10,
            leading=13,
            alignment=TA_CENTER,
        ),
        "annex_index": ParagraphStyle(
            "PSMFAnnexIndex",
            parent=base["Normal"],
            fontName=regular_font,
            fontSize=9.5,
            leading=12.5,
            leftIndent=6 * mm,
            firstLineIndent=-4 * mm,
            spaceAfter=2,
        ),
        "meta_label": ParagraphStyle(
            "PSMFMetaLabel",
            parent=base["Normal"],
            fontName=bold_font,
            fontSize=8,
            leading=10,
        ),
        "meta_value": ParagraphStyle(
            "PSMFMetaValue",
            parent=base["Normal"],
            fontName=regular_font,
            fontSize=8,
            leading=10,
        ),
        "table_header": ParagraphStyle(
            "PSMFTableHeader",
            parent=base["Normal"],
            fontName=bold_font,
            fontSize=7.2,
            leading=9,
            alignment=TA_CENTER,
        ),
        "table_cell": ParagraphStyle(
            "PSMFTableCell",
            parent=base["Normal"],
            fontName=regular_font,
            fontSize=7.2,
            leading=9,
            alignment=TA_LEFT,
        ),
        "signature": ParagraphStyle(
            "PSMFSignature",
            parent=base["Normal"],
            fontName=regular_font,
            fontSize=8,
            leading=10,
            alignment=TA_CENTER,
        ),
        "toc_0": ParagraphStyle(
            "PSMFTOC0",
            parent=base["Normal"],
            fontName=bold_font,
            fontSize=9,
            leading=12,
            leftIndent=0,
            firstLineIndent=0,
            spaceBefore=3,
        ),
        "toc_1": ParagraphStyle(
            "PSMFTOC1",
            parent=base["Normal"],
            fontName=regular_font,
            fontSize=8.5,
            leading=11,
            leftIndent=8 * mm,
            firstLineIndent=0,
        ),
    }
    styles["heading_1"].toc_level = 0
    styles["heading_2"].toc_level = 1
    return styles


def register_fonts() -> tuple[str, str]:
    regular_name = "PSMFTimes"
    bold_name = "PSMFTimesBold"
    if regular_name in pdfmetrics.getRegisteredFontNames():
        return regular_name, bold_name
    font_dir = Path("C:/Windows/Fonts")
    candidates = [
        (font_dir / "times.ttf", font_dir / "timesbd.ttf"),
        (font_dir / "arial.ttf", font_dir / "arialbd.ttf"),
    ]
    for regular_path, bold_path in candidates:
        if regular_path.exists() and bold_path.exists():
            pdfmetrics.registerFont(TTFont(regular_name, str(regular_path)))
            pdfmetrics.registerFont(TTFont(bold_name, str(bold_path)))
            return regular_name, bold_name
    return "Helvetica", "Helvetica-Bold"


def merge_pdf_artifacts(artifacts: Iterable[PdfArtifact]) -> bytes:
    writer = PdfWriter()
    page_index = 0
    for artifact in artifacts:
        reader = PdfReader(BytesIO(artifact.content))
        writer.add_outline_item(artifact.title, page_index)
        for page in reader.pages:
            writer.add_page(page)
        page_index += len(reader.pages)
    writer.add_metadata({"/Title": "Мастер-файл системы фармаконадзора", "/Author": "ООО «АРС»"})
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def merge_pdf_bytes(parts: Iterable[bytes]) -> bytes:
    writer = PdfWriter()
    added = False
    for content in parts:
        if not content:
            continue
        reader = PdfReader(BytesIO(content))
        for page in reader.pages:
            writer.add_page(page)
            added = True
    if not added:
        return b""
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def build_package_archive(
    main: PdfArtifact,
    common_annexes: Iterable[PdfArtifact],
    partner_annexes: Iterable[PdfArtifact],
    full: PdfArtifact,
) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(f"1. Основная часть/{main.name}", main.content)
        for artifact in common_annexes:
            archive.writestr(f"2. Общие приложения/{artifact.name}", artifact.content)
        for artifact in partner_annexes:
            archive.writestr(f"3. Приложения Партнера/{artifact.name}", artifact.content)
        archive.writestr(full.name, full.content)
    return output.getvalue()


def safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    return cleaned[:120] or "document"


def render_block_title(block: Any, partner_name: str) -> str:
    return (block.title_ru or block.code or "").replace("{{partner_name}}", partner_name)


def annex_group_title(code: str, group: Any | None) -> str:
    match = re.search(r"Приложение\s+([А-ЯЁ])", code or "")
    if match and match.group(1) in ANNEX_GROUP_TITLES:
        return ANNEX_GROUP_TITLES[match.group(1)]
    if group:
        return (group.title_ru or group.title or "ПРИЛОЖЕНИЯ К МФСФ").upper()
    return "ПРИЛОЖЕНИЯ К МФСФ"


def annex_sort_key(code: str) -> tuple[int, tuple[int, ...], str]:
    match = re.search(r"Приложение\s+([А-ЯЁ])(?:-([0-9.]+))?", code or "")
    if not match:
        return 999, (), code
    number_parts = tuple(int(part) for part in (match.group(2) or "0").split(".") if part)
    return ANNEX_LETTER_ORDER.get(match.group(1), 998), number_parts, code


def format_date(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        value = value.date()
    try:
        return value.strftime("%d.%m.%Y")
    except AttributeError:
        return str(value)


def wrap_canvas_text(value: str, width: int) -> list[str]:
    words = (value or "").split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if current and len(candidate) > width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines or [""]
