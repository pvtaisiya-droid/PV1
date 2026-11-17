from tests.test_concurrent_forms import Forms
from datetime import date
from uuid import uuid4


def create_partner(db):
    from app import crud, schemas

    suffix = uuid4().hex[:8]
    return crud.create_partner(
        db,
        schemas.PartnerCreate(
            partner_code=f"DIR-{suffix}",
            partner_name=f"Direction partner {suffix}",
            partner_type="fn",
            reconciliation_frequency="monthly",
        ),
    )


def create_report_and_case(db, partner, *, number, source_type, message_direction):
    from app import crud, schemas

    report = crud.create_safety_report(
        db,
        schemas.SafetyReportCreate(
            safety_report_number=number,
            received_date=date(2026, 4, 10),
            source_type=source_type,
            message_direction=message_direction,
            partner_id=partner.id,
            raw_subject=number,
            raw_text=f"Message {number}",
        ),
    )
    case = crud.create_case_from_report(db, report)
    return report, case


def test_reconciliation_uses_one_initial_item_and_separate_followup(client):
    from app import crud, schemas
    from app.database import SessionLocal
    from app.reconciliation import build_reconciliation_preview

    with SessionLocal() as db:
        partner = create_partner(db)
        partner_report, partner_case = create_report_and_case(
            db,
            partner,
            number=f"PARTNER-{uuid4().hex[:8]}",
            source_type="partner",
            message_direction="partner_to_our_company",
        )
        our_report, our_case = create_report_and_case(
            db,
            partner,
            number=f"OUR-{uuid4().hex[:8]}",
            source_type="email",
            message_direction="our_company_to_partner",
        )
        review_report, review_case = create_report_and_case(
            db,
            partner,
            number=f"REVIEW-{uuid4().hex[:8]}",
            source_type="email",
            message_direction="needs_review",
        )
        followup = crud.add_followup(
            db,
            partner_case,
            schemas.FollowUpCreate(
                received_date=date(2026, 4, 20),
                source_type="email",
                message_direction="our_company_to_partner",
                description="Separate follow-up",
            ),
        )
        preview = build_reconciliation_preview(
            db,
            partner_id=partner.id,
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 30),
        )

        assert len(preview["items"]) == 4
        assert len(preview["partner_items"]) == 1
        assert len(preview["our_items"]) == 2
        assert len(preview["direction_review_items"]) == 1
        assert sum(item["case_type"] == "initial" for item in preview["items"]) == 3
        assert sum(item["case_type"] == "follow_up" for item in preview["items"]) == 1

        partner_item = preview["partner_items"][0]
        assert partner_item["internal_case_id"] == partner_case.id
        assert partner_item["partner_case_number"] == partner_report.safety_report_number
        followup_item = next(item for item in preview["items"] if item["case_type"] == "follow_up")
        assert followup_item["internal_case_id"] == partner_case.id
        assert followup_item["internal_case_number"].endswith(f"-FU{followup.follow_up_number}")
        assert followup_item["source_side"] == "our_company"
        assert preview["direction_review_items"][0]["internal_case_id"] == review_case.id
        assert our_case.id != review_case.id
        assert our_report.source_type == "email"
        partner_case_id = partner_case.id
        followup_id = followup.id

    updated = client.post(
        f"/cases/{partner_case_id}/followups/{followup_id}/direction",
        data={"message_direction": "partner_to_our_company", "expected_version": partner_case.version,
              "expected_followup_version": followup.version},
        follow_redirects=False,
    )
    assert updated.status_code == 303
    with SessionLocal() as db:
        saved_followup = crud.get_followup(db, followup_id)
        assert saved_followup.source_type == "email"
        assert saved_followup.message_direction == "partner_to_our_company"


def test_safety_report_direction_is_required_and_source_type_is_unchanged(client):
    from app.database import SessionLocal
    from app.models import SafetyReport

    page = client.get("/safety-reports")
    assert page.status_code == 200
    assert 'name="message_direction" required' in page.text
    assert "Партнёр → наша компания" in page.text
    assert "Наша компания → партнёр" in page.text

    missing = client.post(
        "/safety-reports",
        data={"source_type": "email", "raw_subject": "Missing direction"},
        follow_redirects=False,
    )
    assert missing.status_code == 422

    created = client.post(
        "/safety-reports",
        data={
            "source_type": "email",
            "message_direction": "partner_to_our_company",
            "raw_subject": "Explicit direction",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303

    with SessionLocal() as db:
        report = db.query(SafetyReport).filter(SafetyReport.raw_subject == "Explicit direction").one()
        report_id = report.id
        assert report.source_type == "email"
        assert report.message_direction == "partner_to_our_company"

    updated = client.post(
        f"/safety-reports/{report_id}/direction",
        data={"message_direction": "our_company_to_partner", "expected_version": report.version},
        follow_redirects=False,
    )
    assert updated.status_code == 303
    with SessionLocal() as db:
        report = db.query(SafetyReport).filter(SafetyReport.id == report_id).one()
        assert report.source_type == "email"
        assert report.message_direction == "our_company_to_partner"


def test_reconciliation_cannot_be_saved_with_unresolved_direction(client):
    from app.database import SessionLocal
    from app.models import PartnerReconciliation

    with SessionLocal() as db:
        partner = create_partner(db)
        create_report_and_case(
            db,
            partner,
            number=f"BLOCK-{uuid4().hex[:8]}",
            source_type="email",
            message_direction="needs_review",
        )
        partner_id = partner.id

    page = client.get(f"/partner-reconciliation?partner_id={partner_id}&period_start=2026-04-01&period_end=2026-04-30")
    response = client.post(
        "/partner-reconciliation/save",
        data={
            "expected_preview_version": Forms(page.text).forms["/partner-reconciliation/save"]["expected_preview_version"],
            "partner_id": partner_id,
            "period_start": "2026-04-01",
            "period_end": "2026-04-30",
            "language": "ru",
            "document_format": "pdf",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "validation=" in response.headers["location"]
    with SessionLocal() as db:
        assert (
            db.query(PartnerReconciliation)
            .filter(PartnerReconciliation.partner_id == partner_id)
            .count()
            == 0
        )
