from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import fitz


W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@dataclass
class Section:
    code: str
    title: str
    content: str


@dataclass
class StyledParagraph:
    text: str
    style: str


@dataclass
class PdfLine:
    text: str
    page: int
    y: float
    is_bold: bool


def docx_paragraphs(path: Path) -> list[str]:
    return [paragraph.text for paragraph in docx_styled_paragraphs(path)]


def docx_styled_paragraphs(path: Path) -> list[StyledParagraph]:
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    paragraphs: list[StyledParagraph] = []
    for para in root.iter(f"{W_NS}p"):
        parts: list[str] = []
        for node in para.iter():
            if node.tag == f"{W_NS}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{W_NS}tab":
                parts.append("\t")
        text = "".join(parts).strip()
        if text:
            style = ""
            p_pr = para.find(f"{W_NS}pPr")
            if p_pr is not None:
                p_style = p_pr.find(f"{W_NS}pStyle")
                if p_style is not None:
                    style = p_style.attrib.get(f"{W_NS}val", "")
            paragraphs.append(StyledParagraph(normalize_text(text), style))
    return paragraphs


def pdf_paragraphs(path: Path) -> list[str]:
    doc = fitz.open(path)
    paragraphs: list[str] = []
    for page in doc:
        text = page.get_text("text")
        for line in text.splitlines():
            line = normalize_text(line)
            if line:
                paragraphs.append(line)
    return paragraphs


def pdf_lines(path: Path) -> list[PdfLine]:
    doc = fitz.open(path)
    lines: list[PdfLine] = []
    for page_index, page in enumerate(doc, start=1):
        page_dict = page.get_text("dict")
        for block in page_dict["blocks"]:
            for line in block.get("lines", []):
                text = normalize_text("".join(span["text"] for span in line["spans"]))
                if not text:
                    continue
                is_bold = any(span.get("flags", 0) & 16 for span in line["spans"])
                lines.append(PdfLine(text=text, page=page_index, y=line["bbox"][1], is_bold=is_bold))
    return lines


def normalize_text(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = text.replace("\u200b", "")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def normalize_title(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"\bмфсф\b", "мастер файл", text)
    text = re.sub(r"\bмф\b", "мастер файл", text)
    text = re.sub(r"[^0-9a-zа-я]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_noise(line: str) -> bool:
    if re.fullmatch(r"\d+", line):
        return True
    if re.search(r"^Версия\s+\d", line, re.I):
        return True
    if "Стр." in line and re.search(r"\d+\s+из\s+\d+", line):
        return True
    return False


def heading_match(line: str) -> tuple[str, str] | None:
    line = normalize_text(line)
    patterns = [
        r"^(?P<code>\d+(?:\.\d+)+)\.?\s+(?P<title>\S.+)$",
        r"^(?P<code>\d+)\.?\s+(?P<title>РАЗДЕЛ\b.+)$",
        r"^(?P<code>ВВЕДЕНИЕ|СПИСОК СОКРАЩЕНИЙ)\.?\s*(?P<title>.*)$",
        r"^(?P<code>В\.\d+)\.?\s+(?P<title>\S.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, line, re.I)
        if match:
            code = match.group("code").upper().replace("B.", "В.")
            title = normalize_text(match.group("title") or code)
            return code, title
    return None


def sections_from_paragraphs(paragraphs: list[str], allowed_codes: set[str]) -> dict[str, Section]:
    sections: dict[str, Section] = {}
    current_code: str | None = None
    current_title = ""
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf, current_code, current_title
        if current_code:
            content = "\n\n".join(p for p in buf if p and not is_noise(p)).strip()
            sections[current_code] = Section(current_code, current_title, content)
        buf = []

    for raw in paragraphs:
        line = normalize_text(raw)
        match = heading_match(line)
        if match and match[0] in allowed_codes:
            flush()
            current_code, current_title = match
            continue
        if current_code and not is_noise(line):
            buf.append(line)
    flush()
    return sections


def sections_from_styled_docx(
    paragraphs: list[StyledParagraph],
    components: list[dict[str, str]],
) -> dict[str, Section]:
    """Use real Word heading styles to avoid confusing the TOC with body headings."""
    heading_styles = {"1", "2", "3"}
    sections: dict[str, Section] = {}
    current_component: dict[str, str] | None = None
    component_index = 0
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        if current_component:
            code = current_component["code"]
            title = current_component.get("title_ru") or current_component.get("title") or code
            content = "\n\n".join(p for p in buf if p and not is_noise(p)).strip()
            sections[code] = Section(code, title, content)
        buf = []

    for paragraph in paragraphs:
        line = normalize_text(paragraph.text)
        if paragraph.style in heading_styles and component_index < len(components):
            flush()
            current_component = components[component_index]
            component_index += 1
            continue
        if current_component and not is_noise(line):
            buf.append(line)
    flush()
    return sections


def _title_tokens(component: dict[str, str]) -> set[str]:
    title = normalize_title(component.get("title_ru") or component.get("title") or "")
    return {token for token in title.split() if len(token) >= 5}


def _line_matches_component(line: str, component: dict[str, str]) -> bool:
    code = (component["code"] or "").upper()
    line_clean = normalize_text(line).upper()
    if code and re.match(rf"^{re.escape(code)}\.?(\s|$)", line_clean):
        return True
    if code != "0" and re.match(r"^\d", code):
        return False
    line_title = normalize_title(line)
    comp_title = normalize_title(component.get("title_ru") or component.get("title") or "")
    if comp_title and (line_title in comp_title or comp_title in line_title):
        return True
    title_tokens = _title_tokens(component)
    if title_tokens:
        overlap = len(title_tokens.intersection(line_title.split()))
        if overlap >= min(3, len(title_tokens)):
            return True
    return False


def sections_from_pdf_headings(lines: list[PdfLine], components: list[dict[str, str]]) -> dict[str, Section]:
    sections: dict[str, Section] = {}
    current_component: dict[str, str] | None = None
    component_index = 0
    buf: list[str] = []
    skipping_heading = False

    def flush() -> None:
        nonlocal buf
        if current_component:
            code = current_component["code"]
            title = current_component.get("title_ru") or current_component.get("title") or code
            content = "\n\n".join(p for p in buf if p and not is_noise(p)).strip()
            sections[code] = Section(code, title, content)
        buf = []

    for pdf_line in lines:
        if pdf_line.page < 4:
            continue
        if pdf_line.y < 105:
            continue
        line = normalize_text(pdf_line.text)
        if not line or is_noise(line):
            continue

        remaining = components[component_index : component_index + 4]
        matched_offset = None
        if pdf_line.is_bold:
            for offset, component in enumerate(remaining):
                if _line_matches_component(line, component):
                    matched_offset = offset
                    break
        if matched_offset is not None:
            # If a parent heading has no body before the next child heading, close it empty.
            for skipped in range(matched_offset):
                flush()
                current_component = components[component_index + skipped]
            flush()
            component_index += matched_offset
            current_component = components[component_index]
            component_index += 1
            skipping_heading = True
            continue

        if skipping_heading and pdf_line.is_bold:
            # Multi-line heading title following a numeric heading marker.
            continue
        skipping_heading = False

        if current_component:
            buf.append(line)
    flush()
    return sections


def get_components(db_path: Path) -> list[dict[str, str]]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT c.id, c.code, c.title_ru, c.title, c.status, c.current_version_id,
               c.version AS expected_parent_version, v.row_version AS expected_content_version,
               v.content AS current_content
        FROM psmf_components c
        LEFT JOIN psmf_component_versions v ON v.id = c.current_version_id
        WHERE c.is_deleted = 0
          AND c.scope = 'GLOBAL'
          AND c.component_type IN ('MAIN_SECTION', 'SUBSECTION')
        ORDER BY c.component_order, c.code
        """
    ).fetchall()
    con.close()
    return [dict(row) for row in rows]


def best_section_for_component(
    component: dict[str, str],
    draft: dict[str, Section],
    approved: dict[str, Section],
) -> tuple[str, str]:
    code = component["code"]
    if code in draft and draft[code].content:
        return draft[code].content, "draft_v4"
    if code in approved and approved[code].content:
        return approved[code].content, "approved_v3"
    return component.get("current_content") or "", "unchanged"


def backup_db(db_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = db_path.with_name(f"{db_path.stem}.before_psmf_import_{stamp}{db_path.suffix}")
    shutil.copy2(db_path, backup)
    return backup


def update_components(db_path: Path, mapping: list[dict[str, str]], apply: bool) -> None:
    if not apply:
        return
    con = sqlite3.connect(db_path)
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
    try:
        con.execute('BEGIN IMMEDIATE')
        for item in mapping:
            if item["source"] == "unchanged":
                continue
            cursor = con.execute(
                """
                UPDATE psmf_component_versions
                SET content = ?,
                    change_summary = ?,
                    updated_at = ?,
                    row_version = row_version + 1
                WHERE id = ? AND row_version = ? AND is_deleted = 0
                """,
                (
                    item["content"],
                    f"Импорт текста МФСФ из документов: {item['source']}",
                    now,
                    item["current_version_id"],
                    item['expected_content_version'],
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError('PSMF content changed. Rebuild the import preview.')
            cursor = con.execute(
                """
                UPDATE psmf_components
                SET status = 'draft',
                    updated_at = ?,
                    version = version + 1
                WHERE id = ? AND version = ? AND current_version_id IS ? AND is_deleted = 0
                """,
                (now, item["id"], item['expected_parent_version'], item['current_version_id']),
            )
            if cursor.rowcount != 1:
                raise RuntimeError('PSMF component changed. Rebuild the import preview.')
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def reset_component_versions(db_path: Path, mapping: list[dict[str, str]], apply: bool) -> None:
    if not apply:
        return
    con = sqlite3.connect(db_path)
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")
    try:
        con.execute('BEGIN IMMEDIATE')
        for item in mapping:
            cursor = con.execute(
                'UPDATE psmf_components SET version = version + 1 WHERE id = ? AND version = ? AND current_version_id IS ? AND is_deleted = 0',
                (item['id'], item['expected_parent_version'], item['current_version_id']),
            )
            current = con.execute(
                'SELECT row_version FROM psmf_component_versions WHERE id = ? AND is_deleted = 0',
                (item['current_version_id'],),
            ).fetchone()
            expected_text = (item['expected_content_version'],) if item['current_version_id'] else None
            if cursor.rowcount != 1 or current != expected_text:
                raise RuntimeError('PSMF inputs changed. Rebuild the import preview.')
            con.execute(
                "DELETE FROM psmf_component_versions WHERE component_id = ?",
                (item["id"],),
            )
            approved_id = str(uuid.uuid4())
            draft_id = str(uuid.uuid4())
            empty_parent_text = "Содержимое раздела приведено в подразделах."
            approved_content = item["approved_content"] or item["draft_content"] or empty_parent_text
            draft_content = item["draft_content"] or approved_content
            con.execute(
                """
                INSERT INTO psmf_component_versions (
                    id, component_id, version, content, status, change_summary,
                    created_by_text, created_at, updated_at, approved_by_text,
                    approved_at, approval_comment, is_locked, is_active, is_deleted
                )
                VALUES (?, ?, '3', ?, 'approved', ?, 'Codex', ?, ?, 'Импорт из утвержденного МФСФ v3', ?, ?, 1, 1, 0)
                """,
                (
                    approved_id,
                    item["id"],
                    approved_content,
                    "Утвержденная версия 3 из МФСФ_09012024 в3.pdf",
                    now,
                    now,
                    now,
                    "Импорт утвержденной версии 3",
                ),
            )
            con.execute(
                """
                INSERT INTO psmf_component_versions (
                    id, component_id, version, content, status, change_summary,
                    created_by_text, created_at, updated_at, is_locked, is_active, is_deleted
                )
                VALUES (?, ?, '4', ?, 'draft', ?, 'Codex', ?, ?, 0, 1, 0)
                """,
                (
                    draft_id,
                    item["id"],
                    draft_content,
                    "Черновая версия 4 из МФСФ в 4.docx",
                    now,
                    now,
                ),
            )
            con.execute(
                """
                UPDATE psmf_components
                SET status = 'draft',
                    current_version = '4',
                    current_version_id = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (draft_id, now, item["id"]),
            )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--docx-v4", required=True)
    parser.add_argument("--pdf-v3", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reset-versions", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db)
    components = get_components(db_path)
    allowed_codes = {row["code"].upper() for row in components}
    v4_sections = sections_from_styled_docx(docx_styled_paragraphs(Path(args.docx_v4)), components)
    v3_sections = sections_from_pdf_headings(pdf_lines(Path(args.pdf_v3)), components)

    mapping: list[dict[str, str]] = []
    for component in components:
        content, source = best_section_for_component(component, v4_sections, v3_sections)
        approved_content = v3_sections.get(component["code"], Section(component["code"], "", "")).content
        draft_content = v4_sections.get(component["code"], Section(component["code"], "", "")).content
        mapping.append(
            {
                "id": component["id"],
                "code": component["code"],
                "title": component.get("title_ru") or component.get("title") or "",
                "status": component["status"],
                "current_version_id": component["current_version_id"],
                "expected_parent_version": component['expected_parent_version'],
                "expected_content_version": component['expected_content_version'],
                "source": source,
                "chars": str(len(content)),
                "content": content,
                "approved_source": "approved_v3" if approved_content else "fallback",
                "approved_chars": str(len(approved_content)),
                "draft_source": "draft_v4" if draft_content else "fallback",
                "draft_chars": str(len(draft_content)),
                "approved_content": approved_content,
                "draft_content": draft_content,
            }
        )

    backup = None
    if args.apply:
        backup = backup_db(db_path)
        if args.reset_versions:
            reset_component_versions(db_path, mapping, apply=True)
        else:
            update_components(db_path, mapping, apply=True)

    report = {
        "backup": str(backup) if backup else None,
        "component_count": len(components),
        "v4_sections_found": sorted(v4_sections),
        "v3_sections_found": sorted(v3_sections),
        "filled_from_v4": [m["code"] for m in mapping if m["source"] == "draft_v4"],
        "filled_from_v3": [m["code"] for m in mapping if m["source"] == "approved_v3"],
        "unchanged": [m["code"] for m in mapping if m["source"] == "unchanged"],
        "approved_from_v3": [m["code"] for m in mapping if m["approved_source"] == "approved_v3"],
        "draft_from_v4": [m["code"] for m in mapping if m["draft_source"] == "draft_v4"],
        "approved_fallback": [m["code"] for m in mapping if m["approved_source"] == "fallback"],
        "draft_fallback": [m["code"] for m in mapping if m["draft_source"] == "fallback"],
        "mapping": [
            {k: v for k, v in m.items() if k not in {"content", "approved_content", "draft_content"}}
            for m in mapping
        ],
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in report if k != "mapping"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
