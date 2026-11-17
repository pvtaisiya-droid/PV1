from datetime import date, datetime
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile


def workbook_text(content: bytes) -> str:
    with ZipFile(BytesIO(content)) as archive:
        assert archive.testzip() is None
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        worksheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    return " ".join(text for text in workbook.itertext()) + " " + " ".join(
        text for text in worksheet.itertext()
    )


def seed_activity_report_rows() -> tuple[str, str]:
    from app.database import SessionLocal
    from app.models import (
        Case,
        CaseIdentifier,
        LiteratureMonitoringPlan,
        LiteratureResult,
        Partner,
        Product,
        PSURPlan,
        PSURProduct,
        Submission,
        Substance,
    )

    with SessionLocal() as db:
        partner = Partner(partner_code="PBT", partner_name="Панацея Биотек")
        substance = Substance(substance_name="Лактулоза", inn_name="Лактулоза")
        product = Product(
            product_code="LIVOLUK-PB",
            product_name="Ливолюк-ПБ",
            mah_partner=partner,
            authorization_number="ЛП-№(006171)-(РГ-RU)",
        )
        db.add_all([partner, substance, product])
        db.flush()

        case = Case(
            case_number="2026-479",
            partner_id=partner.id,
            numbering_partner_id=partner.id,
            numbering_scope=f"partner:{partner.id}",
            numbering_year=2026,
            numbering_sequence=90479,
            number_is_provisional=False,
            initial_received_date=date(2026, 7, 15),
            latest_received_date=date(2026, 7, 15),
            workflow_status="submitted",
        )
        db.add(case)
        db.flush()
        external_number = "CN-PBT-012177-01"
        db.add(
            CaseIdentifier(
                case_id=case.id,
                identifier_value=external_number,
                identifier_type="partner_case_number",
                is_primary=True,
            )
        )
        db.add(
            Submission(
                submission_number="3840835",
                submission_object_type="case",
                case_id=case.id,
                recipient_type="regulator",
                submission_type="icsr",
                submission_format="ais",
                submission_status="acknowledged",
                submitted_at=datetime(2026, 7, 16, 10, 0),
                acknowledged_at=datetime(2026, 7, 17, 10, 0),
                acknowledgement_code="418026",
            )
        )

        psur_plan = PSURPlan(
            active_substance_id=substance.id,
            product_id=product.id,
            psur_type="PSUR",
            reporting_period_start=date(2021, 6, 1),
            reporting_period_end=date(2026, 5, 31),
            data_lock_point=date(2026, 5, 31),
            status="Submitted",
        )
        db.add(psur_plan)
        db.flush()
        db.add(
            PSURProduct(
                psur_plan_id=psur_plan.id,
                product_id=product.id,
                marketing_authorisation_number="ЛП-№(006171)-(РГ-RU)",
                included_in_report=True,
            )
        )
        db.add(
            Submission(
                submission_number="45212",
                submission_object_type="pbrer",
                pbrer_id=psur_plan.id,
                recipient_type="regulator",
                submission_type="psur",
                submission_format="ais",
                submission_status="acknowledged",
                submitted_at=datetime(2026, 8, 19, 10, 0),
                acknowledged_at=datetime(2026, 8, 20, 10, 0),
                acknowledgement_code="482340",
            )
        )

        literature_plan = LiteratureMonitoringPlan(
            partner_id=partner.id,
            active_substance_id=substance.id,
            monitoring_sources="Росздравнадзор",
            frequency="monthly",
            start_date=date(2026, 1, 1),
            status="active",
        )
        db.add(literature_plan)
        db.flush()
        db.add(
            LiteratureResult(
                plan_id=literature_plan.id,
                partner_id=literature_plan.partner_id,
                active_substance_id=literature_plan.active_substance_id,
                publication_title="Новая информация по безопасности CellCept",
                publication_date=date(2026, 8, 24),
                relevance="relevant",
                result_type="safety_information",
                pv_decision="discuss_required",
                processing_status="under_review",
                specialist_comment="Получено сообщение Росздравнадзора.",
            )
        )
        db.commit()
        return case.case_number, external_number


def test_accounting_page_has_period_and_service_report_controls(client):
    from app.accounting_activity_report import previous_quarter_bounds

    date_from, date_to = previous_quarter_bounds(date.today())
    response = client.get("/accounting-data")

    assert response.status_code == 200
    assert "Отчёт по оказанным услугам" in response.text
    assert "Предыдущий квартал" in response.text
    assert "Текущий по сегодня" in response.text
    assert f'value="{date_from}"' in response.text
    assert f'value="{date_to}"' in response.text
    assert 'name="service" value="icsr"' in response.text
    assert 'name="service" value="psur"' in response.text
    assert 'name="service" value="literature"' in response.text


def test_activity_export_matches_accounting_receipt_tables(client, monkeypatch):
    from functools import partial
    from app.routers import accounting_data
    monkeypatch.setattr(accounting_data, "build_activity_report",
                        partial(accounting_data.build_activity_report, generated_on=date(2026, 8, 24)))
    case_number, external_number = seed_activity_report_rows()

    response = client.get(
        "/accounting-data/activity-export",
        params=[
            ("date_from", "2026-07-01"),
            ("date_to", "2026-08-24"),
            ("service", "icsr"),
            ("service", "psur"),
            ("service", "literature"),
        ],
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "accounting-services-2026-07-01-2026-08-24.xlsx" in response.headers[
        "content-disposition"
    ]

    text = workbook_text(response.content)
    assert "Квитанции о подаче ИСНР" in text
    assert "№ первичного отчёта Росздравнадзора" in text
    assert f"{case_number}; {external_number}" in text
    assert "418026" in text
    assert "3840835" in text
    assert "Квитанции о подаче ПООБ" in text
    assert "№ отчёта в АИС" in text
    assert "45212" in text
    assert "482340" in text
    assert "Иная активность за период" in text
    assert "Новая информация по безопасности CellCept" in text
    assert "Предварительный статус" in text


def test_activity_export_only_includes_selected_services(client):
    seed_activity_report_rows()

    response = client.get(
        "/accounting-data/activity-export",
        params={
            "date_from": "2026-07-01",
            "date_to": "2026-08-24",
            "service": "icsr",
        },
    )

    assert response.status_code == 200
    text = workbook_text(response.content)
    assert "Квитанции о подаче ИСНР" in text
    assert "Квитанции о подаче ПООБ" not in text
    assert "Иная активность за период" not in text


def test_activity_export_validates_period_and_service_selection(client):
    response = client.get(
        "/accounting-data/activity-export",
        params={"date_from": "2026-08-24", "date_to": "2026-07-01"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/accounting-data?")
    assert "validation=" in response.headers["location"]


def test_submission_journal_captures_receipt_fields_used_by_report(client):
    seed_activity_report_rows()
    from app.database import SessionLocal
    from app.models import Submission

    page = client.get("/submissions")
    assert page.status_code == 200
    assert 'name="submission_object_type"' in page.text
    assert 'name="pbrer_id"' in page.text
    assert 'name="submission_number"' in page.text
    assert 'name="acknowledgement_code"' in page.text

    with SessionLocal() as db:
        submission = db.query(Submission).filter(Submission.submission_number == "3840835").one()
        submission_id = submission.id
        submission_version = submission.version

    response = client.post(
        f"/submissions/{submission_id}/status",
        data={
            "expected_version": submission_version,
            "submission_status": "acknowledged",
            "submission_number": "3840835",
            "submitted_at": "2026-07-16T10:00",
            "acknowledged_at": "2026-07-17T11:30",
            "acknowledgement_code": "999999",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        updated = db.query(Submission).filter(Submission.id == submission_id).one()
        assert updated.acknowledgement_code == "999999"
        assert updated.acknowledged_at == datetime(2026, 7, 17, 11, 30)
