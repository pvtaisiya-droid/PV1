from tests.form_helpers import post_current
import base64
import io
import zipfile
from pathlib import Path
from tests.test_concurrent_forms import Forms


def make_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def e2b_xml(
    *,
    message_id: str = "MSG-E2B-001",
    case_id: str = "WW-CASE-001",
    receiver: str = "Receiver A",
    version: str = "1",
    date_value: str = "20260715",
    extra: str = "",
) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ichicsr>
  <safetyreport>
    <safetyreportid>{case_id}</safetyreportid>
    <messagenumb>{message_id}</messagenumb>
    <safetyreportversion>{version}</safetyreportversion>
    <messagedate>{date_value}</messagedate>
    <receiptdate>{date_value}</receiptdate>
    <occurcountry>DE</occurcountry>
    <senderorganization>Partner One</senderorganization>
    <receiverorganization>{receiver}</receiverorganization>
    <primarysource>
      <reporterfamilyname>Reporter</reporterfamilyname>
      <reporteremail>reporter@example.test</reporteremail>
      <reportercountry>DE</reportercountry>
    </primarysource>
    <patient>
      <patientinitial>AB</patientinitial>
      <patientsex>2</patientsex>
      <patientonsetage>42</patientonsetage>
      <patientonsetageunit>years</patientonsetageunit>
      <patientbirthdate>1984</patientbirthdate>
      <patientreaction>
        <reactionmeddrallt>Headache</reactionmeddrallt>
        <reactionmeddraptcode>10019211</reactionmeddraptcode>
        <reactionstartdate>{date_value}</reactionstartdate>
        <reactionoutcome>recovering</reactionoutcome>
        <seriousnessother>1</seriousnessother>
      </patientreaction>
      <patientdrug>
        <drugcharacterization>1</drugcharacterization>
        <medicinalproduct>Test Product</medicinalproduct>
        <activesubstancename>Test Substance</activesubstancename>
        <drugstructuredosagenumb>10</drugstructuredosagenumb>
        <drugstructuredosageunit>mg</drugstructuredosageunit>
        <drugadministrationroute>oral</drugadministrationroute>
        <drugstartdate>202607</drugstartdate>
      </patientdrug>
    </patient>
    <narrativeincludeclinical>Patient experienced headache.</narrativeincludeclinical>
    {extra}
  </safetyreport>
</ichicsr>
""".encode()


def e2b_r3_xml_with_attachment() -> tuple[bytes, bytes]:
    document = b"%PDF-1.4 embedded E2B document"
    encoded = base64.b64encode(document).decode("ascii")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<MCCI_IN200100UV01 xmlns="urn:hl7-org:v3">
  <id extension="MSG-R3-ATT-01" root="2.16.840.1.113883.3.989.2.1.3.22"/>
  <creationTime value="20260714162914+0300"/>
  <PORR_IN049016UV>
    <receiver><device><id extension="Receiver A"/></device></receiver>
    <sender><device><id extension="Partner One"/></device></sender>
    <controlActProcess>
      <subject>
        <investigationEvent>
          <id extension="WW-R3-ATT-01" root="2.16.840.1.113883.3.989.2.1.3.1"/>
          <id extension="LOCAL-R3-ATT-01" root="2.16.840.1.113883.3.989.2.1.3.2"/>
          <availabilityTime value="20260714"/>
          <reference><document><text representation="B64" mediaType="application/pdf"><reference value="source.pdf"/>{encoded}</text></document></reference>
          <component>
            <adverseEventAssessment>
              <subject1>
                <primaryRole>
                  <player1><administrativeGenderCode code="2"/></player1>
                  <subjectOf2><observation>
                    <id root="RID1"/><code code="29" displayName="reaction"/>
                    <value code="10019211" codeSystemVersion="29.0"><originalText>Headache</originalText></value>
                  </observation></subjectOf2>
                  <subjectOf2><organizer><code displayName="drugInformation"/>
                    <component><substanceAdministration>
                      <id root="DID1"/>
                      <consumable><instanceOfKind><kindOfProduct><name>PACLITAXEL</name></kindOfProduct></instanceOfKind></consumable>
                    </substanceAdministration></component>
                  </organizer></subjectOf2>
                </primaryRole>
              </subject1>
            </adverseEventAssessment>
          </component>
        </investigationEvent>
      </subject>
    </controlActProcess>
  </PORR_IN049016UV>
</MCCI_IN200100UV01>""".encode()
    return xml, document


def upload_package(client, package: bytes, filename: str = "package.zip"):
    return post_current(client,
        "/safety-reports/e2b-imports",
        files={"file": (filename, package, "application/zip")},
        follow_redirects=False,
    )


def upload_xml(client, xml: bytes, filename: str = "message.xml"):
    return post_current(client,
        "/safety-reports/e2b-imports",
        files={"file": (filename, xml, "application/xml")},
        follow_redirects=False,
    )


def preview_data(client, preview_path, action):
    return Forms(client.get(preview_path).text).forms[f"{preview_path}/{action}"]


def latest_batch():
    from app.database import SessionLocal
    from app.models import E2BImportBatch

    with SessionLocal() as db:
        return db.query(E2BImportBatch).order_by(E2BImportBatch.created_at.desc()).first()


def seed_product(name: str = "Test Product", *, company: bool = True, code: str = "TP-001"):
    from app.database import SessionLocal
    from app.models import Product

    with SessionLocal() as db:
        product = Product(
            product_code=code,
            product_name=name,
            product_name_normalized=name,
            is_company_product=company,
            is_active=True,
        )
        db.add(product)
        db.commit()
        return product.id


def test_e2b_zip_preview_and_confirm_creates_safety_message_and_case(client):
    seed_product()
    package = make_zip(
        {
            "case.xml": e2b_xml(),
            "CIOMS.pdf": b"%PDF-1.4 cioms",
            "source.txt": b"source document",
        }
    )

    response = upload_package(client, package)
    assert response.status_code == 303
    preview = client.get(response.headers["location"])
    assert preview.status_code == 200
    assert "MSG-E2B-001" in preview.text
    assert "Headache" in preview.text
    assert "Company product found" in preview.text

    preview_path = response.headers["location"].split("?", 1)[0]
    confirm = post_current(client, f"{preview_path}/confirm", data=preview_data(client, preview_path, "confirm"), follow_redirects=False)
    assert confirm.status_code == 303
    assert confirm.headers["location"].startswith("/cases/")

    from app.database import SessionLocal
    from app.models import Case, E2BImportBatch, E2BTransmission, IncomingRequest, SafetyReport

    with SessionLocal() as db:
        batch = db.query(E2BImportBatch).first()
        assert batch.status == "imported"
        case = db.query(Case).filter(Case.worldwide_case_id == "WW-CASE-001").one()
        assert case.local_sender_case_id is None
        assert case.reactions[0].reported_term == "Headache"
        assert case.case_products[0].reported_product_name == "Test Product"
        assert case.case_products[0].therapy_start_date is None
        assert case.case_products[0].therapy_start_date_raw == "202607"
        assert case.case_products[0].therapy_start_date_precision == "month"
        assert db.query(SafetyReport).filter(SafetyReport.import_batch_id == batch.id).count() == 1
        assert db.query(IncomingRequest).filter(IncomingRequest.case_id == case.id).count() == 1
        assert db.query(E2BTransmission).filter(E2BTransmission.case_id == case.id).count() == 1


def test_e2b_zip_without_company_product_can_be_marked_not_pv_ars(client):
    package = make_zip({"case.xml": e2b_xml(message_id="MSG-NOT-PV", case_id="WW-NOT-PV")})
    response = upload_package(client, package)
    assert response.status_code == 303
    preview_path = response.headers["location"].split("?", 1)[0]
    preview = client.get(response.headers["location"])
    assert preview.status_code == 200
    assert "not_pv_ars_candidate" in preview.text
    assert "Mark not PV ARS" in preview.text

    confirm = post_current(client, f"{preview_path}/confirm", data=preview_data(client, preview_path, "confirm"), follow_redirects=False)
    assert confirm.status_code == 303
    assert confirm.headers["location"].startswith(preview_path)

    marked = post_current(client, f"{preview_path}/mark-not-pv-ars", data=preview_data(client, preview_path, "mark-not-pv-ars"), follow_redirects=False)
    assert marked.status_code == 303
    assert marked.headers["location"].startswith(preview_path)

    from app.database import SessionLocal
    from app.models import Case, E2BImportBatch, IncomingRequest

    with SessionLocal() as db:
        batch = db.query(E2BImportBatch).one()
        assert batch.status == "not_pv_ars"
        request = db.query(IncomingRequest).one()
        assert request.message_category == "not_pv_ars"
        assert request.status == "closed_no_case"
        assert request.triage_result == "no_case_required"
        assert db.query(Case).count() == 0


def test_multiple_xml_for_same_case_are_grouped_before_import(client):
    package = make_zip(
        {
            "receiver-a.xml": e2b_xml(message_id="MSG-A", receiver="Receiver A"),
            "receiver-b.xml": e2b_xml(message_id="MSG-B", receiver="Receiver B"),
        }
    )
    response = upload_package(client, package)
    assert response.status_code == 303
    preview = client.get(response.headers["location"])
    assert preview.status_code == 200
    assert "MSG-A" in preview.text
    assert "MSG-B" in preview.text

    from app.database import SessionLocal
    from app.models import E2BImportBatch

    with SessionLocal() as db:
        batch = db.query(E2BImportBatch).one()
        assert batch.detected_xml_count == 2
        assert batch.detected_case_count == 1


def test_single_xml_can_be_uploaded_without_zip(client):
    response = upload_xml(client, e2b_xml(message_id="MSG-XML-ONLY", case_id="WW-XML-ONLY"))
    assert response.status_code == 303
    preview = client.get(response.headers["location"])
    assert preview.status_code == 200
    assert "MSG-XML-ONLY" in preview.text

    from app.database import SessionLocal
    from app.models import E2BImportBatch, E2BImportItem

    with SessionLocal() as db:
        batch = db.query(E2BImportBatch).one()
        assert batch.source_filename == "message.xml"
        assert batch.detected_xml_count == 1
        assert db.query(E2BImportItem).filter(E2BImportItem.status == "parsed").count() == 1


def test_nested_zip_is_scanned_and_repeated_nested_package_is_deduplicated(client):
    nested = make_zip({"case.xml": e2b_xml(message_id="MSG-NESTED", case_id="WW-NESTED")})
    package = make_zip({"first.zip": nested, "repeated.zip": nested})
    response = upload_package(client, package)
    assert response.status_code == 303
    preview = client.get(response.headers["location"])
    assert preview.status_code == 200
    assert "MSG-NESTED" in preview.text

    from app.database import SessionLocal
    from app.models import E2BImportBatch, E2BImportItem

    with SessionLocal() as db:
        batch = db.query(E2BImportBatch).one()
        assert batch.detected_xml_count == 1
        assert batch.detected_case_count == 1
        assert db.query(E2BImportItem).filter(E2BImportItem.status == "duplicate").count() == 1


def test_multiple_distinct_cases_are_blocked_before_confirmation(client):
    package = make_zip(
        {
            "case-a.xml": e2b_xml(message_id="MSG-A", case_id="WW-CASE-A"),
            "case-b.xml": e2b_xml(message_id="MSG-B", case_id="WW-CASE-B"),
        }
    )
    response = upload_package(client, package)
    assert response.status_code == 303

    from app.database import SessionLocal
    from app.models import Case, E2BImportBatch, E2BImportIssue

    with SessionLocal() as db:
        batch = db.query(E2BImportBatch).one()
        assert batch.status == "error"
        assert batch.detected_case_count == 2
        issue = db.query(E2BImportIssue).filter(E2BImportIssue.issue_type == "multiple_cases_not_supported").one()
        assert issue.is_blocking is True

    confirm = post_current(client, f"{response.headers['location'].split('?', 1)[0]}/confirm", data=preview_data(client, response.headers['location'].split('?', 1)[0], "confirm"), follow_redirects=False)
    assert confirm.status_code == 303
    assert confirm.headers["location"].startswith(response.headers["location"].split("?", 1)[0])
    with SessionLocal() as db:
        assert db.query(Case).count() == 0


def test_r3_embedded_base64_document_is_saved_as_attachment(client):
    seed_product(name="PACLITAXEL", code="PACLITAXEL-001")
    xml, document = e2b_r3_xml_with_attachment()
    response = upload_xml(client, xml, filename="r3-with-attachment.xml")
    assert response.status_code == 303
    preview_path = response.headers["location"].split("?", 1)[0]
    confirm = post_current(client, f"{preview_path}/confirm", data=preview_data(client, preview_path, "confirm"), follow_redirects=False)
    assert confirm.status_code == 303
    assert confirm.headers["location"].startswith("/cases/")

    from app.database import SessionLocal
    from app.models import Attachment

    with SessionLocal() as db:
        attachment = db.query(Attachment).filter(Attachment.e2b_source == "embedded_b64").one()
        assert attachment.file_name == "source.pdf"
        assert attachment.mime_type == "application/pdf"
        assert attachment.file_size_bytes == len(document)
        assert Path(attachment.storage_path).read_bytes() == document


def test_repeated_zip_is_detected(client):
    package = make_zip({"case.xml": e2b_xml()})
    assert upload_package(client, package).status_code == 303
    second = upload_package(client, package)
    assert second.status_code == 303

    from app.database import SessionLocal
    from app.models import E2BImportBatch

    with SessionLocal() as db:
        batches = db.query(E2BImportBatch).order_by(E2BImportBatch.created_at.asc()).all()
        assert len(batches) == 2
        assert batches[1].status == "repeat_upload"
        assert batches[1].duplicate_batch_id == batches[0].id


def test_unsafe_zip_path_blocks_import(client):
    package = make_zip({"../evil.xml": e2b_xml()})
    response = upload_package(client, package)
    assert response.status_code == 303
    preview = client.get(response.headers["location"])
    assert "Unsafe ZIP member path" in preview.text

    batch = latest_batch()
    assert batch.status == "error"
    assert batch.error_count >= 1


def test_xml_external_entity_is_blocked(client):
    xml = b"""<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><ichicsr>&xxe;</ichicsr>"""
    response = upload_package(client, make_zip({"case.xml": xml}))
    assert response.status_code == 303
    preview = client.get(response.headers["location"])
    assert "DOCTYPE" in preview.text or "external entity" in preview.text

    batch = latest_batch()
    assert batch.status == "error"


def test_parser_preserves_null_flavor_and_incomplete_dates():
    from app.e2b_import import E2BParser

    parsed = E2BParser().parse(
        e2b_xml(
            date_value="2026",
            extra='<patientreaction nullFlavor="UNK"><reactionmeddrallt>Fatigue</reactionmeddrallt></patientreaction>',
        )
    )
    assert parsed.message["message_date"]["precision"] == "year"
    assert parsed.products[0]["therapy_start_date"]["precision"] == "month"
    assert any(reaction["null_flavor"] == "UNK" for reaction in parsed.reactions)


def test_parser_reads_hl7_e2b_r3_observations_and_drugs():
    from app.e2b_import import E2BParser

    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<MCCI_IN200100UV01 xmlns="urn:hl7-org:v3">
  <id extension="CN-PBT-012178-01" root="2.16.840.1.113883.3.989.2.1.3.22"/>
  <creationTime value="20260714162914+0530"/>
  <PORR_IN049016UV>
    <id extension="CN-PBT-012178" root="2.16.840.1.113883.3.989.2.1.3.1"/>
    <receiver><device><id extension="Health Canada"/></device></receiver>
    <sender><device><id extension="Panacea Biotec Pharma Limited"/></device></sender>
    <controlActProcess>
      <subject>
        <investigationEvent>
          <id extension="CN-PBT-012178" root="2.16.840.1.113883.3.989.2.1.3.1"/>
          <id extension="CN-PBT-012178" root="2.16.840.1.113883.3.989.2.1.3.2"/>
          <availabilityTime value="20260630"/>
          <reference><document><code displayName="literatureReference"/><bibliographicDesignationText>PMID: 123456</bibliographicDesignationText></document></reference>
          <component>
            <adverseEventAssessment>
              <subject1>
                <primaryRole>
                  <player1><administrativeGenderCode code="1"/></player1>
                  <subjectOf2><observation><code code="3"/><value value="69"/></observation></subjectOf2>
                  <subjectOf2><observation>
                    <id root="RID1"/><code code="29" displayName="reaction"/>
                    <effectiveTime><low nullFlavor="NASK"/><high nullFlavor="NASK"/></effectiveTime>
                    <value code="10080342" codeSystemVersion="29.0"><originalText>Peripheral neuropathy</originalText></value>
                    <outboundRelationship2><observation><code displayName="otherMedicallyImportantCondition"/><value value="true"/></observation></outboundRelationship2>
                  </observation></subjectOf2>
                  <subjectOf2><organizer><code displayName="drugInformation"/>
                    <component><substanceAdministration>
                      <id root="DID1"/>
                      <consumable><instanceOfKind><kindOfProduct><name>PACLITAXEL</name><asManufacturedProduct><subjectOf><approval><holder><role><playingOrganization><name>PBT</name></playingOrganization></role></holder></approval></subjectOf></asManufacturedProduct></kindOfProduct></instanceOfKind></consumable>
                      <outboundRelationship2><substanceAdministration><effectiveTime><low value="2024"/><high nullFlavor="NASK"/></effectiveTime></substanceAdministration></outboundRelationship2>
                    </substanceAdministration></component>
                  </organizer></subjectOf2>
                </primaryRole>
              </subject1>
            </adverseEventAssessment>
          </component>
        </investigationEvent>
      </subject>
    </controlActProcess>
  </PORR_IN049016UV>
</MCCI_IN200100UV01>"""

    parsed = E2BParser().parse(xml)

    assert parsed.e2b_version == "E2B(R3)"
    assert parsed.message["message_identifier"] == "CN-PBT-012178-01"
    assert parsed.message["worldwide_case_id"] == "CN-PBT-012178"
    assert parsed.message["sender"] == "Panacea Biotec Pharma Limited"
    assert parsed.patient["sex"] == "male"
    assert parsed.reactions[0]["meddra_pt_code"] == "10080342"
    assert parsed.reactions[0]["is_serious"] is True
    assert parsed.products[0]["reported_product_name"] == "PACLITAXEL"
    assert parsed.products[0]["active_substances"] == []
    assert parsed.products[0]["therapy_start_date"]["precision"] == "year"
    assert parsed.warnings == []
