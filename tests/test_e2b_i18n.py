"""Language selection belongs to the interface, not the imported source."""

from html.parser import HTMLParser
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from tests.form_helpers import post_current
from tests.test_e2b_import import e2b_xml, make_zip, preview_data, seed_product, upload_package


class VisibleText(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.hidden = 0
        self.parts = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag in {"details", "script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"details", "script", "style"}:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

    @property
    def text(self):
        return " ".join(self.parts)


def test_all_e2b_system_keys_have_russian_translations():
    from app.e2b_i18n import E2B_RU, ISSUES, LABELS
    from app.i18n import TRANSLATIONS

    template = "\n".join(Path(path).read_text(encoding="utf-8") for path in
        ["app/templates/e2b_import_preview.html", "app/templates/package_import_review.html", "app/templates/_package_document_picker.html", "app/templates/_e2b_product_matching.html"])
    keys = set(re.findall(r"_\('([^']+)'", template))
    keys.update(key for labels in LABELS.values() for key in labels.values())
    keys.update(key for pair in ISSUES.values() for key in pair)
    keys.update(E2B_RU)
    allowed = {"PV System", "XML", "PDF", "CIOMS", "MedDRA", "PMID", "DOI"}
    missing = {key for key in keys - allowed if not re.search("[А-Яа-яЁё]", TRANSLATIONS["ru"].get(key, ""))}
    assert not missing


def test_russian_preview_preserves_source_and_historical_diagnostics(client, tmp_path):
    from app.database import SessionLocal
    from app.models import E2BImportBatch

    package = make_zip({
        "first/message.xml": e2b_xml(),
        "second/message.xml": e2b_xml(case_id="WW-CASE-002", message_id="MSG-002"),
        "source.pdf": b"%PDF-1.4 synthetic source",
    })
    uploaded = upload_package(client, package)
    path = uploaded.headers["location"].split("?", 1)[0]
    with SessionLocal() as db:
        batch = db.query(E2BImportBatch).one()
        before = (batch.normalized_json, [(row.id, row.message) for row in batch.issues])
        assert Path(batch.storage_dir).resolve().is_relative_to(tmp_path.resolve())
        assert Path(batch.source_zip_path).read_bytes() == package

    ru = client.get(uploaded.headers["location"] + "&lang=ru")
    # A pre-restart worker can render the updated template without the new context.
    from app.templating import templates
    legacy_context = dict(ru.context)
    legacy_context.pop("e2b")
    legacy_html = templates.env.get_template("e2b_import_preview.html").render(**legacy_context)
    assert "MSG-E2B-001" in legacy_html
    visible = VisibleText(ru.text).text
    assert len(ru.context["preview"]["case_groups"]) == 2
    for text in ["Предпросмотр импорта E2B", "Источник E2B загружен", "Источники XML",
                 "Препарат нашей компании не сопоставлен", "Женский", "лет", "В процессе выздоровления",
                 "Подозреваемый", "мг", "Внутрь", "Headache", "Test Product", "Partner One", "Receiver A"]:
        assert text in visible
    for text in ["E2B import preview", "not_found", "multiple_cases_not_supported", "female", "years", "Company product"]:
        assert text not in visible
    assert "not_pv_ars_candidate" in ru.text  # Still available in technical details.
    en = client.get(path, params={"lang": "en"})
    assert "E2B import preview" in VisibleText(en.text).text
    assert "XML sources" in VisibleText(en.text).text
    assert 'lang="en"' in en.text
    again = client.get(path, params={"lang": "ru"})
    assert "Предпросмотр импорта E2B" in again.text
    with SessionLocal() as db:
        batch = db.query(E2BImportBatch).one()
        assert (batch.normalized_json, [(row.id, row.message) for row in batch.issues]) == before

    checked = post_current(client, f"{path}/recheck", data=preview_data(client, path, "recheck"))
    assert checked.status_code == 200
    assert "Пакет E2B проверен повторно." in VisibleText(checked.text).text
    assert 'lang="ru"' in checked.text
    cancelled = post_current(client, f"{path}/cancel", data=preview_data(client, path, "cancel"))
    assert cancelled.status_code == 200
    assert "Импорт E2B отменён." in cancelled.text
    assert 'lang="ru"' in cancelled.text


def test_unknown_diagnostics_and_legacy_errors_are_localized_and_escaped(client):
    from app.database import SessionLocal
    from app.models import E2BImportBatch, E2BImportIssue

    uploaded = upload_package(client, make_zip({"broken.xml": b"<ichicsr>"}))
    path = uploaded.headers["location"].split("?", 1)[0]
    with SessionLocal() as db:
        batch = db.query(E2BImportBatch).one()
        db.add(E2BImportIssue(batch_id=batch.id, issue_type="future_code",
            message="Unrecognized English diagnostic <script>alert(1)</script>", is_blocking=True))
        db.commit()
    page = client.get(path, params={"error": "Import failed: <script>alert(2)</script>"})
    visible = VisibleText(page.text).text
    assert "Ошибка разбора XML" in visible
    assert "Другое замечание E2B" in visible
    assert "Не удалось выполнить операцию E2B" in visible
    assert "Unrecognized English diagnostic" not in visible
    assert "Import failed:" not in visible
    assert "&lt;script&gt;" in page.text
    assert "<script>alert(" not in page.text


@pytest.mark.parametrize("language,expected", [("ru", "Импорт завершён."), ("en", "E2B import completed.")])
def test_confirmation_notice_uses_selected_language_after_redirect(client, language, expected):
    seed_product()
    uploaded = upload_package(client, make_zip({"case.xml": e2b_xml()}))
    path = uploaded.headers["location"].split("?", 1)[0]
    client.get(path, params={"lang": language})
    result = post_current(client, f"{path}/confirm", data=preview_data(client, path, "confirm"))
    assert result.status_code == 200
    assert expected in result.text, result.context.get("error") or result.context.get("validation")
    assert f'lang="{language}"' in result.text


def test_unknown_values_and_parameterized_notice_are_safe():
    from app.e2b_i18n import E2BDisplay

    display = E2BDisplay("ru")
    assert display.value(0, "outcome") == "Код исхода: 0"
    assert display.value("unexpected_status", "status") == "Неизвестное значение"
    assert display.value("Unlisted source route", "route") == "Значение в источнике: Unlisted source route"
    assert display.notice("Unsafe ZIP member path: {path}")["details"]
    issue = SimpleNamespace(issue_type="multiple_cases_not_supported", message="A changed legacy diagnostic")
    assert "Проверьте" in display.issue(issue)["message"] or "проверьте" in display.issue(issue)["message"]


def test_e2b_http_errors_and_upload_validation_follow_language(client):
    for language, expected in [("ru", "Пакет импорта E2B не найден"), ("en", "E2B import batch not found")]:
        missing = client.get("/safety-reports/e2b-imports/missing", params={"lang": language})
        assert missing.status_code == 404
        assert missing.json()["detail"] == expected
    client.get("/safety-reports/new?lang=ru")
    rejected = post_current(client, "/safety-reports/e2b-imports", data={"origin": "registration"},
        files={"file": ("unsupported.txt", b"synthetic", "text/plain")})
    assert rejected.status_code == 200
    assert "Можно импортировать только XML E2B и ZIP-пакеты." in rejected.text
    assert 'lang="ru"' in rejected.text
