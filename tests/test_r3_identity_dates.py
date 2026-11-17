import copy
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

FIXTURE = Path(__file__).parent / "fixtures/e2b_r3_identity/source.xml"
H = {"h": "urn:hl7-org:v3"}


def source(**replacements):
    text = FIXTURE.read_text(encoding="utf-8")
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.encode()


def test_context_order_and_source_dates():
    from app.e2b_import import E2BParser
    from app.e2b_r3_identity import source_date
    parser = E2BParser()
    root = ET.fromstring(source())
    first = parser.parse(ET.tostring(root)).message
    assert first["batch_identifier"] == "Batch-MixedCase-07"
    assert first["message_identifier"] == "Message-NotVersion-99"
    assert first["message_version"] is None
    assert first["local_sender_case_id"] == "Sender-Case-X"
    assert first["worldwide_case_id"] == "ZZ-SYNTH-0001"
    assert first["first_received_date"]["value"] == "2026-01-05"
    latest = first["latest_received_date"]
    assert latest["value"] == "2026-02-03"
    assert latest["parsed"] == "2026-02-03T14:15:16.125+05:30"
    assert latest["timezone"] == "+0530" and latest["precision"] == "fraction"
    inv = root.find("h:PORR_IN049016UV/h:controlActProcess/h:subject/h:investigationEvent", H)
    ids = inv.findall("h:id", H)
    inv.remove(ids[1]); inv.insert(0, ids[1])
    reordered = parser.parse(ET.tostring(root)).message
    for key in ("message_identifier", "local_sender_case_id", "worldwide_case_id", "message_version"):
        assert reordered[key] == first[key]
    # A standalone PORR has no batch ID; do not infer one from its message ID.
    bare = parser.parse(ET.tostring(root.find("h:PORR_IN049016UV", H))).message
    assert bare["batch_identifier"] is None and bare["message_identifier"] == first["message_identifier"]
    assert not any(w.get("blocking") for w in bare["identity_warnings"])
    for xml, state, precision in [
        (None, "missing", None), ('<low nullFlavor="NASK"/>', "null_flavor", None),
        ('<low value="2026"/>', "partial", "year"), ('<low value="202602"/>', "partial", "month"),
        ('<low value="20260230"/>', "invalid", None),
        ('<low value="20260101" nullFlavor="UNK"/>', "invalid", None),
    ]:
        nodes = [] if xml is None else [ET.fromstring(xml)]
        fact = source_date(nodes, "test/low", "C.1.4")
        assert (fact["state"], fact["precision"], fact["value"]) == (state, precision, None)
        if nodes:
            assert fact["candidates"] == [dict(nodes[0].attrib)]
    inv.append(copy.deepcopy(ids[0]))
    duplicate = parser.parse(ET.tostring(root)).message
    assert duplicate["local_sender_case_id"] is None
    assert any(w.get("blocking") for w in duplicate["identity_warnings"])
    root.append(copy.deepcopy(root.find("h:PORR_IN049016UV", H)))
    assert any(w["type"] == "r3_message_context" for w in parser.parse(ET.tostring(root)).warnings)
    unknown = parser.parse(source(**{".3.2\" extension=": ".3.999\" extension="})).message
    assert unknown["worldwide_case_id"] is None
    assert any(w.get("blocking") for w in unknown["identity_warnings"])


def test_scoped_matching_and_conflict_preview(client):
    from app.database import SessionLocal
    from app.models import Case, E2BTransmission, E2BImportBatch, E2BImportItem
    from app.e2b_import import E2BParser, E2BImportService, json_dumps
    from app.e2b_r3_identity import PROFILE
    from tests.test_e2b_import import seed_product, upload_xml, preview_data, latest_batch
    from tests.form_helpers import post_current
    seed_product()
    message = E2BParser().parse(source()).message
    with SessionLocal() as db:
        a = Case(case_number="A", worldwide_case_id="ZZ-SYNTH-0001", local_sender_case_id="Sender-Case-X",
                 e2b_identity_profile=PROFILE, e2b_sender_scope=message["sender_scope"])
        b = Case(case_number="B", worldwide_case_id="WW-B", local_sender_case_id="LOCAL-B",
                 e2b_identity_profile=PROFILE, e2b_sender_scope=message["sender_scope"])
        other = Case(case_number="OTHER", worldwide_case_id="WW-OTHER", local_sender_case_id="Sender-Case-X",
                     e2b_identity_profile=PROFILE, e2b_sender_scope=json.dumps(["oid", "other"]))
        legacy = Case(case_number="OLD", worldwide_case_id="OLD-LOCAL", local_sender_case_id="OLD-WW")
        db.add_all([a,b,other,legacy]); db.commit()
        service = E2BImportService()
        assert service.find_existing_case(db, message).id == a.id
        scoped = {**message, "worldwide_case_id":"WW-OTHER", "sender_scope":other.e2b_sender_scope}
        assert service.find_existing_case(db, scoped).id == other.id
        with pytest.raises(ValueError, match="разные случаи"):
            service.find_existing_case(db, {**message,"local_sender_case_id":"LOCAL-B"})
        with pytest.raises(ValueError, match="историческим"):
            service.find_existing_case(db, {**message,"worldwide_case_id":"OLD-WW"})
        with pytest.raises(ValueError, match="пространство"):
            service.find_existing_case(db, {**message,"sender_scope":None})
        with pytest.raises(ValueError):
            service.find_existing_case(db, {"local_sender_case_id":"Sender-Case-X"})
        batch = E2BImportBatch(source_filename="synthetic.xml",zip_checksum_sha256="a"*64)
        db.add(batch); db.flush()
        item = E2BImportItem(batch_id=batch.id,original_path="synthetic.xml",file_name="synthetic.xml",file_role="xml")
        db.add(item); db.flush()
        second_scope = json.dumps(["oid", "second-sender"])
        alias_message = {**message,"sender_scope":second_scope,"local_sender_case_id":"ALIAS-ONE"}
        db.add(E2BTransmission(batch_id=batch.id,import_item_id=item.id,case_id=a.id,
            local_sender_case_id="ALIAS-ONE",normalized_json=json_dumps({"message":alias_message})))
        db.commit()
        assert service.find_existing_case(db, alias_message).id == a.id
        with pytest.raises(ValueError, match="предыдущим источником"):
            service.find_existing_case(db, {**alias_message,"local_sender_case_id":"ALIAS-TWO"})
    response = upload_xml(client, source(**{"Sender-Case-X":"LOCAL-B"}))
    preview_path = response.headers["location"].split("?",1)[0]
    preview = client.get(preview_path)
    assert preview.status_code == 200 and "Автоматическое объединение остановлено" in preview.text
    response = post_current(client, preview_path+"/confirm", data=preview_data(client, preview_path,"confirm"), follow_redirects=False)
    assert response.status_code == 303 and not response.headers["location"].startswith("/cases/")
    with SessionLocal() as db:
        assert db.query(Case).count() == 4 and db.query(E2BTransmission).count() == 1
    assert latest_batch().status != "imported"


def test_import_readback_preserves_source_and_registration(client):
    from tests.test_e2b_import import seed_product, upload_xml, preview_data
    from tests.form_helpers import post_current
    from app.database import SessionLocal
    from app.models import Case, E2BTransmission, SafetyReport, IncomingRequest
    from app.e2b_r3_identity import PROFILE
    seed_product()
    xml = source(**{"<low value=\"20260105\"/>":"<low nullFlavor=\"UNK\"/>"})
    response = upload_xml(client, xml)
    preview_path = response.headers["location"].split("?",1)[0]
    preview = client.get(preview_path)
    assert "Batch-MixedCase-07" in preview.text and "Message-NotVersion-99" in preview.text
    assert "C.1.4" in preview.text and "null_flavor" in preview.text
    response = post_current(client, preview_path+"/confirm", data=preview_data(client,preview_path,"confirm"), follow_redirects=False)
    assert response.headers["location"].startswith("/cases/")
    with SessionLocal() as db:
        case = db.query(Case).one(); tx = db.query(E2BTransmission).one()
        report = db.query(SafetyReport).one(); incoming = db.query(IncomingRequest).one()
        case_id, report_id = case.id, report.id
        assert case.initial_received_date is None and report.received_date is None
        assert case.latest_received_date == date(2026,2,3)
        assert case.e2b_identity_profile == PROFILE and tx.message_version is None
        assert report.received_at is not None and incoming.received_date_basis == "pv1_registration"
        assert incoming.received_date == incoming.created_at.date()
        assert incoming.source_received_date("first_received_date") is None
        assert incoming.source_received_date("latest_received_date") == date(2026,2,3)
        message = json.loads(tx.normalized_json)["message"]
        assert message["import_source"]["item_id"] == tx.import_item_id
        assert Path(tx.item.stored_path).read_bytes() == xml
    payload = client.get(f"/api/cases/{case_id}").json()
    facts = payload["e2b_source_facts"][0]
    assert facts["dates"]["first_received_date"]["state"] == "null_flavor"
    assert facts["dates"]["first_received_date"]["null_flavor"] == "UNK"
    assert facts["dates"]["latest_received_date"]["timezone"] == "+0530"
    assert facts["identifiers"]["batch"]["value"] == "Batch-MixedCase-07"
    assert facts["registered_at"] and facts["checksum_sha256"]
    assert client.get(f"/api/safety-reports/{report_id}").json()["e2b_source_facts"][0]["import_item_id"] == facts["import_item_id"]
    for path in [f"/cases/{case_id}",f"/safety-reports/{report_id}"]:
        response = client.get(path)
        assert response.status_code == 200
        assert "C.1.5" in response.text and "регистрация" in response.text.lower()


def test_upgrade_0028_preserves_linked_history(tmp_path):
    import sqlalchemy as sa
    from tests.test_migrations import migrate_test_database, assert_matches_current_models
    from migration_support.schema_20260930 import metadata
    path = tmp_path / "upgrade.db"
    result = migrate_test_database(path,"20261002_0028")
    assert result.returncode == 0, result.stdout+result.stderr
    engine = sa.create_engine(f"sqlite:///{path.as_posix()}")
    timestamp = datetime(2025,1,2)
    with engine.begin() as conn:
        for i in range(2):
            for table,values in [
                ("tblSafetyReports",dict(id=f"report{i}",safety_report_number=f"OLD-SR-{i}",received_at=timestamp,received_date=date(2025,1,1))),
                ("tblCases",dict(id=f"case{i}",case_number=f"OLD-{i}",safety_report_id=f"report{i}",worldwide_case_id=f"OLD-WW-{i}",local_sender_case_id=f"OLD-L-{i}",initial_received_date=date(2025,1,1))),
                ("tblPatients",dict(id=f"patient{i}",case_id=f"case{i}",patient_initials="SY")),
            ]:
                defaults = {
                    "tblCases": dict(number_is_provisional=False,case_version=3,workflow_status="new",is_locked=False),
                    "tblPatients": dict(patient_role="patient"),
                    "tblSafetyReports": dict(message_direction="needs_review",triage_status="new",is_valid_icsr=False,
                        minimum_criteria_patient=False,minimum_criteria_reporter=False,minimum_criteria_product=False,minimum_criteria_event=False),
                }[table]
                conn.execute(metadata.tables[table].insert().values(created_at=timestamp,updated_at=timestamp,
                    is_active=True,is_deleted=False,version=1,**defaults,**values))
    engine.dispose()
    with sqlite3.connect(path) as conn:
        before={t:conn.execute(f'SELECT * FROM "{t}" ORDER BY id').fetchall() for t in ["tblCases","tblPatients","tblSafetyReports"]}
    result = migrate_test_database(path)
    assert result.returncode == 0,result.stdout+result.stderr
    with sqlite3.connect(path) as conn:
        for table,old in before.items():
            new=conn.execute(f'SELECT * FROM "{table}" ORDER BY id').fetchall()
            assert [r[:len(old[0])] for r in new] == old
        assert conn.execute('SELECT e2b_identity_profile,e2b_sender_scope FROM tblCases').fetchall() == [(None,None),(None,None)]
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        assert conn.execute('SELECT version_num FROM alembic_version').fetchone()[0] == '20261110_0032'
    assert_matches_current_models(path)
