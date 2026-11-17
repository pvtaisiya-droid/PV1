from tests.form_helpers import post_current
from uuid import uuid4


def create_manual_case_for_assessment(client) -> str:
    from app.database import SessionLocal
    from app.models import Case

    suffix = uuid4().hex[:8]
    case_number = f"CAUS-{suffix}"
    response = post_current(client,
        "/cases/manual",
        data={
            "case_number": case_number,
            "source_type": "email",
            "reporter_name": "Assessment Reporter",
            "initial_received_date": "2026-08-17",
            "patient_initials": "CD",
            "reported_product_name": "Assessment medicine",
            "drug_role": "suspect",
            "reported_term": "Assessment reaction",
            "workflow_status": "medical_review",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        return db.query(Case.id).filter(Case.case_number == case_number).scalar()


def test_naranjo_score_and_category_boundaries():
    from app.causality import (
        NARANJO_QUESTIONS,
        WHO_UMC_CATEGORIES,
        WHO_UMC_DESCRIPTIONS,
        calculate_naranjo_score,
        classify_naranjo_score,
    )

    assert set(WHO_UMC_DESCRIPTIONS) == {value for value, _ in WHO_UMC_CATEGORIES}
    assert "повторное назначение проводилось и было необходимо" in WHO_UMC_DESCRIPTIONS["certain"]
    assert "дополнительные данные" in WHO_UMC_DESCRIPTIONS["conditional_unclassified"]

    unknown = {question.key: "unknown" for question in NARANJO_QUESTIONS}
    assert calculate_naranjo_score(unknown) == 0
    assert classify_naranjo_score(0) == "doubtful"

    maximum = {question.key: "yes" for question in NARANJO_QUESTIONS}
    maximum["q5"] = "no"
    maximum["q6"] = "no"
    assert calculate_naranjo_score(maximum) == 13
    assert classify_naranjo_score(13) == "definite"
    assert classify_naranjo_score(8) == "probable"
    assert classify_naranjo_score(4) == "possible"


def test_case_supports_multiple_who_umc_and_naranjo_assessments(client):
    from app.database import SessionLocal
    from app.models import Case, CaseProductReactionAssessment

    case_id = create_manual_case_for_assessment(client)
    with SessionLocal() as db:
        case = db.query(Case).filter(Case.id == case_id).one()
        product_id = case.case_products[0].id
        reaction_id = case.reactions[0].id

    who_response = post_current(client,
        f"/cases/{case_id}/assessments",
        data={
            "case_product_id": product_id,
            "reaction_id": reaction_id,
            "causality_method": "who_umc",
            "relatedness": "probable_likely",
            "assessor_role": "dru",
            "assessor_name": "Ivan Ivanov",
            "assessor_organization": "DRA unit",
            "assessment_date": "2026-08-17",
            "assessment_comment": "Temporal relationship and positive dechallenge.",
        },
        follow_redirects=False,
    )
    assert who_response.status_code == 303
    assert who_response.headers["location"].startswith(f"/cases/{case_id}?tab=assessment")

    naranjo_response = post_current(client,
        f"/cases/{case_id}/assessments",
        data={
            "case_product_id": product_id,
            "reaction_id": reaction_id,
            "causality_method": "naranjo",
            "assessor_role": "regulator",
            "assessor_name": "Regulatory Assessor",
            "assessor_organization": "Roszdravnadzor",
            "assessment_date": "2026-08-18",
            "assessment_comment": "Independent regulatory assessment.",
            "q1": "yes",
            "q2": "yes",
            "q3": "yes",
            "q4": "unknown",
            "q5": "no",
            "q6": "unknown",
            "q7": "unknown",
            "q8": "yes",
            "q9": "unknown",
            "q10": "yes",
        },
        follow_redirects=False,
    )
    assert naranjo_response.status_code == 303

    with SessionLocal() as db:
        rows = (
            db.query(CaseProductReactionAssessment)
            .filter(CaseProductReactionAssessment.case_product_id == product_id)
            .order_by(CaseProductReactionAssessment.assessment_date)
            .all()
        )
        assert len(rows) == 2
        assert rows[0].causality_method == "who_umc"
        assert rows[0].relatedness == "probable_likely"
        assert rows[0].assessor_role == "dru"
        assert rows[0].assessor_name == "Ivan Ivanov"
        assert rows[0].naranjo_score is None

        assert rows[1].causality_method == "naranjo"
        assert rows[1].naranjo_score == 8
        assert rows[1].relatedness == "probable"
        assert rows[1].assessor_role == "regulator"
        assert rows[1].assessor_organization == "Roszdravnadzor"
        assert '"q5": "no"' in rows[1].naranjo_answers_json

    page = client.get(f"/cases/{case_id}?tab=assessment")
    assert page.status_code == 200
    assert "WHO‑UMC" in page.text
    assert "Шкала Наранжо" in page.text
    assert "Сомнительная / маловероятная (Unlikely)" in page.text
    assert "Другие лекарства, заболевания, химические вещества" in page.text
    assert "информация недостаточна или противоречива" in page.text
    assert "Ivan Ivanov" in page.text
    assert "Regulatory Assessor" in page.text
    assert "Балл: 8" in page.text


def test_assessment_forms_are_prefilled_for_current_user(client):
    from app.database import SessionLocal
    from app.models import User

    case_id = create_manual_case_for_assessment(client)
    with SessionLocal() as db:
        current_user = (
            db.query(User)
            .filter(User.is_deleted.is_(False), User.is_active.is_(True))
            .order_by(User.created_at.asc())
            .first()
        )
        current_user_name = current_user.full_name or current_user.email

    page = client.get(f"/cases/{case_id}?tab=assessment")

    assert page.status_code == 200
    assert page.text.count(
        f'name="assessor_name" value="{current_user_name}"'
    ) == 2
    assert page.text.count('value="ars" selected') == 2


def test_assessment_rejects_product_from_another_case(client):
    from app.database import SessionLocal
    from app.models import Case, CaseProductReactionAssessment

    first_case_id = create_manual_case_for_assessment(client)
    second_case_id = create_manual_case_for_assessment(client)
    with SessionLocal() as db:
        first_case = db.query(Case).filter(Case.id == first_case_id).one()
        second_case = db.query(Case).filter(Case.id == second_case_id).one()
        foreign_product_id = second_case.case_products[0].id
        first_reaction_id = first_case.reactions[0].id
        assessments_before = db.query(CaseProductReactionAssessment).count()

    response = post_current(client,
        f"/cases/{first_case_id}/assessments",
        data={
            "case_product_id": foreign_product_id,
            "reaction_id": first_reaction_id,
            "causality_method": "who_umc",
            "relatedness": "possible",
            "assessor_role": "ars",
            "assessor_name": "ARS Assessor",
            "assessment_date": "2026-08-17",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "validation=" in response.headers["location"]
    with SessionLocal() as db:
        assert db.query(CaseProductReactionAssessment).count() == assessments_before
