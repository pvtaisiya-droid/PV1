from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def numbering_database(tmp_path):
    from app import models  # noqa: F401
    from app.database import Base

    database_path = tmp_path / "numbering.db"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
        future=True,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    yield session_factory
    engine.dispose()


def create_partner(db, code: str, name: str):
    from app import crud, schemas

    return crud.create_partner(
        db,
        schemas.PartnerCreate(partner_code=code, partner_name=name),
    )


def create_case(db, partner_id: str | None = None, **values):
    from app import crud, schemas

    return crud.create_case(
        db,
        schemas.CaseCreate(partner_id=partner_id, **values),
    )


def test_legacy_partner_code_is_preserved_and_new_cases_are_numbered_automatically(
    numbering_database,
):
    from app import crud, schemas
    from app.models import Partner

    with numbering_database() as db:
        partner = Partner(partner_code="Легаси-01", partner_name="Старый партнёр")
        db.add(partner)
        db.commit()
        db.refresh(partner)

        updated = crud.update_partner(
            db,
            partner,
            schemas.PartnerCreate(
                partner_code="Легаси-01",
                partner_name="Старый партнёр — новое название",
            ),
        )
        case = create_case(db, updated.id)

        assert updated.partner_code == "Легаси-01"
        assert case.case_number == "2026-001"
        assert case.full_case_code == "Легаси-01-2026-001"


def test_partner_scoped_sequences_and_full_codes(numbering_database):
    with numbering_database() as db:
        partner_a = create_partner(db, "ABC", "Partner A")
        partner_b = create_partner(db, "PBT", "Partner B")

        a_first = create_case(
            db,
            partner_a.id,
            local_sender_case_id="C.1.1-A",
            worldwide_case_id="C.1.8.1-A",
        )
        a_second = create_case(db, partner_a.id)
        b_first = create_case(db, partner_b.id)

        assert a_first.case_number == "2026-001"
        assert a_second.case_number == "2026-002"
        assert b_first.case_number == "2026-001"
        assert a_first.full_case_code == "ABC-2026-001"
        assert b_first.full_case_code == "PBT-2026-001"
        assert a_first.local_sender_case_id == "C.1.1-A"
        assert a_first.worldwide_case_id == "C.1.8.1-A"


def test_unassigned_sequence_finalization_history_and_audit(numbering_database):
    from app.case_numbering import finalize_provisional_number
    from app.models import AuditTrail, CaseIdentifier

    with numbering_database() as db:
        partner = create_partner(db, "ABC", "Partner A")
        partner_existing = create_case(db, partner.id)
        first = create_case(db)
        second = create_case(db)

        assert first.case_number == "UNASSIGNED-2026-001"
        assert second.case_number == "UNASSIGNED-2026-002"
        assert first.full_case_code == "UNASSIGNED-2026-001"
        assert first.number_is_provisional is True

        finalized = finalize_provisional_number(db, first, partner)
        assert partner_existing.case_number == "2026-001"
        assert finalized.case_number == "2026-002"
        assert finalized.full_case_code == "ABC-2026-002"
        assert finalized.number_is_provisional is False
        history = (
            db.query(CaseIdentifier)
            .filter(
                CaseIdentifier.case_id == first.id,
                CaseIdentifier.identifier_type == "previous_internal_number",
            )
            .one()
        )
        assert history.identifier_value == "UNASSIGNED-2026-001"
        assert (
            db.query(AuditTrail)
            .filter(
                AuditTrail.case_id == first.id,
                AuditTrail.action == "number_finalized",
            )
            .count()
            == 1
        )


def test_year_rollover_unbounded_sequence_and_database_uniqueness(numbering_database):
    from app.case_numbering import assign_case_number
    from app.models import Case

    with numbering_database() as db:
        partner_a = create_partner(db, "ABC", "Partner A")
        partner_b = create_partner(db, "PBT", "Partner B")

        reserved = Case(case_number="", partner_id=partner_a.id, workflow_status="new")
        assign_case_number(
            db,
            reserved,
            partner_id=partner_a.id,
            registered_at=datetime(2026, 12, 1, tzinfo=timezone.utc),
            supplied_number="2026-999",
        )
        db.add(reserved)
        db.commit()
        after_999 = create_case(db, partner_a.id)
        assert after_999.case_number == "2026-1000"

        next_year = Case(case_number="", partner_id=partner_a.id, workflow_status="new")
        assign_case_number(
            db,
            next_year,
            partner_id=partner_a.id,
            registered_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )
        db.add(next_year)
        db.commit()
        assert next_year.case_number == "2027-001"

        same_number_other_partner = Case(
            case_number="",
            partner_id=partner_b.id,
            workflow_status="new",
        )
        assign_case_number(
            db,
            same_number_other_partner,
            partner_id=partner_b.id,
            registered_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )
        db.add(same_number_other_partner)
        db.commit()
        assert same_number_other_partner.case_number == "2027-001"

        duplicate = Case(
            case_number="2027-ANOTHER",
            partner_id=partner_a.id,
            numbering_partner_id=partner_a.id,
            numbering_scope=f"PARTNER:{partner_a.id}",
            numbering_year=2027,
            numbering_sequence=1,
            workflow_status="new",
        )
        db.add(duplicate)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_followup_and_related_data_do_not_change_case_number(numbering_database):
    from app import crud, schemas
    from app.models import CaseProduct

    with numbering_database() as db:
        partner_a = create_partner(db, "ABC", "Partner A")
        partner_b = create_partner(db, "PBT", "Partner B")
        case = create_case(
            db,
            partner_a.id,
            report_type="phone",
            local_sender_case_id="C.1.1-FOLLOWUP",
            worldwide_case_id="C.1.8.1-FOLLOWUP",
        )
        original_number = case.case_number

        crud.add_followup(
            db,
            case,
            schemas.FollowUpCreate(
                received_date=date(2026, 8, 18),
                message_direction="needs_review",
                description="Follow-up",
            ),
        )
        assert case.case_number == original_number
        assert case.case_version == 2
        assert case.version_label == "F01"
        assert case.local_sender_case_id == "C.1.1-FOLLOWUP"
        assert case.worldwide_case_id == "C.1.8.1-FOLLOWUP"

        db.add(
            CaseProduct(
                case_id=case.id,
                reported_product_name="Updated product information",
                active_substance_text="Updated INN",
                drug_role="suspect",
            )
        )
        case.report_type = "email"
        db.commit()
        crud.add_case_partner(db, case, partner_b)
        assert case.case_number == original_number

        case.is_deleted = True
        db.commit()
        next_case = create_case(db, partner_a.id)
        assert next_case.case_number == "2026-002"


def test_general_and_partner_exports_keep_numbering_context(numbering_database):
    from app import crud, schemas

    with numbering_database() as db:
        partner = create_partner(db, "ABC", "Partner A")
        assigned = create_case(db, partner.id)
        unassigned = create_case(db)
        crud.create_case_identifier(
            db,
            assigned,
            schemas.CaseIdentifierCreate(
                identifier_value="PARTNER-EXT-1",
                partner_id=partner.id,
                is_primary=True,
            ),
        )

        general_csv = crud.export_cases_csv(db)
        assert "full case code" in general_csv
        assert "technical case id" in general_csv
        assert "C.1.1 sender case safety report identifier" in general_csv
        assert "C.1.8.1 worldwide unique case identification number" in general_csv
        assert "ABC-2026-001" in general_csv
        assert "UNASSIGNED-2026-001" in general_csv
        assert "PARTNER-EXT-1" in general_csv
        assert "additional case numbers" in general_csv
        assert assigned.id in general_csv
        assert unassigned.id in general_csv

        partner_csv = crud.export_cases_csv(db, partner_id=partner.id)
        assert "full case code" not in partner_csv
        assert "2026-001" in partner_csv
        assert "PARTNER-EXT-1" in partner_csv
        assert "additional case numbers" in partner_csv
        assert "UNASSIGNED" not in partner_csv


def test_concurrent_creations_are_unique_and_partner_scoped(numbering_database):
    with numbering_database() as db:
        partner_a = create_partner(db, "ABC", "Partner A")
        partner_b = create_partner(db, "PBT", "Partner B")
        partner_a_id = partner_a.id
        partner_b_id = partner_b.id

    def create_for(partner_id: str) -> str:
        with numbering_database() as worker_db:
            return create_case(worker_db, partner_id).case_number

    partner_ids = [partner_a_id] * 8 + [partner_b_id] * 8
    with ThreadPoolExecutor(max_workers=8) as executor:
        numbers = list(executor.map(create_for, partner_ids))

    a_numbers = numbers[:8]
    b_numbers = numbers[8:]
    assert len(set(a_numbers)) == 8
    assert len(set(b_numbers)) == 8
    assert sorted(a_numbers) == [f"2026-{value:03d}" for value in range(1, 9)]
    assert sorted(b_numbers) == [f"2026-{value:03d}" for value in range(1, 9)]
