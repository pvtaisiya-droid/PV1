from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import mimetypes
import os
import re
import shutil
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from uuid import uuid4
from xml.etree import ElementTree as ET

from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app import schemas
from app.audit import log_audit
from app.case_numbering import assign_case_number
from app.config import get_settings
from app.crud import assert_case_editable, generate_number
from app.models import (
    Attachment,
    Case,
    CaseDrugSubstance,
    CaseLiteratureReference,
    CaseNarrative,
    CaseProduct,
    E2BImportBatch,
    E2BImportIssue,
    E2BImportItem,
    E2BTransmission,
    IncomingRequest,
    IncomingRequestCaseLink,
    IncomingRequestProduct,
    Partner,
    Patient,
    Product,
    Reaction,
    SafetyReport,
    SafetyReportProduct,
    Substance,
    utcnow,
)


IMPORTER_VERSION = "e2b-importer-2"
DEFAULT_MAX_ZIP_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_UNPACKED_BYTES = 500 * 1024 * 1024
DEFAULT_MAX_SINGLE_FILE_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_EMBEDDED_BYTES = 25 * 1024 * 1024
UPLOAD_ROOT = get_settings().storage_root / "e2b_imports"


@contextmanager
def transaction_directory(db: Session, batch: E2BImportBatch, prefix: str):
    """Write to a new generation; a rollback never removes previous files."""
    root = UPLOAD_ROOT.resolve()
    directory = (Path(batch.storage_dir or (root / batch.id)) / f"{prefix}-{uuid4()}").resolve()
    if root not in directory.parents:
        raise ValueError("Stored package path is outside E2B import storage.")
    try:
        directory.mkdir(parents=True, exist_ok=False)
        yield directory
        db.commit()
    except BaseException:
        db.rollback()
        # Re-resolve immediately before recursive deletion, including on Windows.
        resolved = directory.resolve()
        if root not in resolved.parents:
            raise ValueError("Transaction cleanup path is outside E2B import storage.")
        if directory.exists():
            shutil.rmtree(directory)
        raise


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


MAX_ZIP_BYTES = _env_int("PV_E2B_MAX_ZIP_BYTES", DEFAULT_MAX_ZIP_BYTES)
MAX_UNPACKED_BYTES = _env_int("PV_E2B_MAX_UNPACKED_BYTES", DEFAULT_MAX_UNPACKED_BYTES)
MAX_SINGLE_FILE_BYTES = _env_int("PV_E2B_MAX_SINGLE_FILE_BYTES", DEFAULT_MAX_SINGLE_FILE_BYTES)
MAX_EMBEDDED_BYTES = _env_int("PV_E2B_MAX_EMBEDDED_BYTES", DEFAULT_MAX_EMBEDDED_BYTES)
MAX_NESTED_ZIP_DEPTH = _env_int("PV_E2B_MAX_NESTED_ZIP_DEPTH", 3)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str, sort_keys=True)


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_key(value: str | None) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    return re.sub(r"\s+", " ", text).strip().casefold()


def local_name(tag: str) -> str:
    if "}" in tag:
        tag = tag.rsplit("}", 1)[1]
    return tag.split(":")[-1]


def canonical_name(tag: str) -> str:
    return re.sub(r"[^a-z0-9]", "", local_name(tag).lower())


def element_text(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    text = "".join(element.itertext()).strip()
    if text:
        return re.sub(r"\s+", " ", text)
    for attr in ("value", "extension", "code", "displayName", "nullFlavor"):
        if element.attrib.get(attr):
            return element.attrib[attr].strip()
    return None


def element_null_flavor(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    return element.attrib.get("nullFlavor")


def all_elements(root: ET.Element, names: set[str] | None = None) -> list[ET.Element]:
    if names is None:
        return list(root.iter())
    return [element for element in root.iter() if canonical_name(element.tag) in names]


def first_element(root: ET.Element, *names: str) -> ET.Element | None:
    wanted = {canonical_name(name) for name in names}
    for element in root.iter():
        if canonical_name(element.tag) in wanted:
            return element
    return None


def child_elements(root: ET.Element | None, *names: str) -> list[ET.Element]:
    if root is None:
        return []
    wanted = {canonical_name(name) for name in names}
    return [child for child in list(root) if canonical_name(child.tag) in wanted]


def first_child(root: ET.Element | None, *names: str) -> ET.Element | None:
    children = child_elements(root, *names)
    return children[0] if children else None


def child_attr(root: ET.Element | None, attr: str, *names: str) -> str | None:
    child = first_child(root, *names)
    if child is None:
        return None
    return child.attrib.get(attr)


def first_text(root: ET.Element, *names: str) -> str | None:
    return element_text(first_element(root, *names))


def first_attr(root: ET.Element, attr: str, *names: str) -> str | None:
    wanted = {canonical_name(name) for name in names}
    for element in root.iter():
        if canonical_name(element.tag) in wanted and element.attrib.get(attr):
            return element.attrib[attr].strip()
    return None


def child_text(root: ET.Element, *names: str) -> str | None:
    return first_text(root, *names)


def parse_partial_date(value: str | None) -> dict[str, Any]:
    raw = clean_text(value)
    if not raw:
        return {"value": None, "raw": None, "precision": None, "null_flavor": None}
    compact = re.sub(r"[^0-9]", "", raw)
    precision = None
    parsed: date | None = None
    if re.fullmatch(r"\d{8,}", compact):
        try:
            parsed = date(int(compact[:4]), int(compact[4:6]), int(compact[6:8]))
            precision = "day"
        except ValueError:
            precision = "invalid"
    elif re.fullmatch(r"\d{6}", compact):
        precision = "month"
    elif re.fullmatch(r"\d{4}", compact):
        precision = "year"
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            parsed = date.fromisoformat(raw)
            precision = "day"
        except ValueError:
            precision = "invalid"
    return {"value": parsed, "raw": raw, "precision": precision or "text", "null_flavor": None}


def parsed_date_value(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    text = clean_text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def date_field(data: dict[str, Any] | None, key: str = "value") -> date | None:
    if not isinstance(data, dict):
        return None
    return parsed_date_value(data.get(key))


def bool_from_code(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y", "serious"}


def to_float(value: str | None) -> float | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def normalize_sex(value: str | None) -> str | None:
    text = (value or "").strip().lower()
    mapping = {"1": "male", "2": "female", "m": "male", "f": "female", "male": "male", "female": "female"}
    return mapping.get(text, text or None)


def normalize_drug_role(value: str | None) -> str:
    text = (value or "").strip().lower()
    mapping = {
        "1": "suspect",
        "2": "concomitant",
        "3": "interacting",
        "4": "treatment",
        "suspect": "suspect",
        "concomitant": "concomitant",
        "interacting": "interacting",
        "treatment": "treatment",
    }
    return mapping.get(text, "unknown")


def normalize_seriousness(message: dict[str, Any], reactions: list[dict[str, Any]]) -> str:
    serious_values = [
        message.get("seriousness"),
        *(reaction.get("is_serious") for reaction in reactions),
    ]
    return "serious" if any(value is True or bool_from_code(str(value)) for value in serious_values) else "non-serious"


def date_to_str(value: date | None) -> str | None:
    return value.isoformat() if value else None


def message_received_date(message: dict[str, Any]) -> date | None:
    return (
        date_field(message.get("company_received_date"))
        or date_field(message.get("first_received_date"))
        or date_field(message.get("message_date"))
    )


def reaction_death_flag(reaction: dict[str, Any]) -> bool:
    outcome = (reaction.get("outcome") or "").casefold()
    return bool(reaction.get("seriousness_death")) or "fatal" in outcome or "death" in outcome


def parsed_case_flags(parsed: dict[str, Any]) -> dict[str, Any]:
    reactions = parsed.get("reactions") or []
    patient = parsed.get("patient") or {}
    message = parsed.get("message") or {}
    is_death = bool(patient.get("death_date", {}).get("raw") or patient.get("death_cause")) or any(
        reaction_death_flag(reaction) for reaction in reactions
    )
    is_serious = normalize_seriousness(message, reactions) == "serious" or is_death
    received = message_received_date(message)
    due_days = 15 if is_serious else 90
    due_date = received + timedelta(days=due_days) if received else None
    return {
        "is_serious": is_serious,
        "has_death": is_death,
        "expectedness": "requires_assessment",
        "submission_due_date": date_to_str(due_date),
        "submission_due_days": due_days,
    }


def safe_zip_member_name(name: str) -> bool:
    if not name or name.endswith("/"):
        return False
    if "\x00" in name:
        return False
    posix = PurePosixPath(name)
    windows = PureWindowsPath(name)
    parts = list(posix.parts) + list(windows.parts)
    return not (posix.is_absolute() or windows.is_absolute() or ".." in parts)


def role_for_file(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".xml"):
        return "xml"
    if lower.endswith(".pdf"):
        if "cioms" in lower:
            return "cioms"
        if "literature" in lower or "article" in lower:
            return "literature_pdf"
        return "pdf"
    return "other"


def mime_for_file(path: str) -> str | None:
    return mimetypes.guess_type(path)[0]


def safe_file_name(value: str | None, default: str = "document.bin") -> str:
    candidate = clean_text(value) or default
    candidate = PurePosixPath(candidate.replace("\\", "/")).name
    candidate = PureWindowsPath(candidate).name
    candidate = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", candidate).strip(" .")
    return candidate or default


@dataclass
class ParsedXml:
    message: dict[str, Any]
    patient: dict[str, Any]
    reactions: list[dict[str, Any]] = field(default_factory=list)
    products: list[dict[str, Any]] = field(default_factory=list)
    reporter: dict[str, Any] = field(default_factory=dict)
    literature: dict[str, Any] = field(default_factory=dict)
    narrative: dict[str, Any] = field(default_factory=dict)
    embedded_documents: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    e2b_version: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "patient": self.patient,
            "reactions": self.reactions,
            "products": self.products,
            "reporter": self.reporter,
            "literature": self.literature,
            "narrative": self.narrative,
            "embedded_documents": self.embedded_documents,
            "warnings": self.warnings,
            "e2b_version": self.e2b_version,
        }


class E2BParser:
    def parse(self, xml_bytes: bytes) -> ParsedXml:
        if len(xml_bytes) > MAX_SINGLE_FILE_BYTES:
            raise ValueError("XML exceeds configured E2B file size limit.")
        prefix = xml_bytes[:4096].lower()
        if b"<!doctype" in prefix or b"<!entity" in prefix:
            raise ValueError("XML contains external entity or DOCTYPE declaration.")
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as exc:
            raise ValueError(f"XML is not well-formed: {exc}") from exc

        version = self.detect_version(root)
        if version == "E2B(R3)":
            message = self.extract_message_r3(root)
            patient = self.extract_patient_r3(root)
            reactions = self.extract_reactions_r3(root)
            products = self.extract_products_r3(root)
            reporter = self.extract_reporter_r3(root)
            literature = self.extract_literature_r3(root)
            narrative = self.extract_narrative_r3(root)
            embedded = self.extract_embedded_documents(root)
        else:
            message = self.extract_message(root)
            patient = self.extract_patient(root)
            reactions = self.extract_reactions(root)
            products = self.extract_products(root)
            reporter = self.extract_reporter(root)
            literature = self.extract_literature(root)
            narrative = self.extract_narrative(root)
            embedded = self.extract_embedded_documents(root)
        warnings = []
        if not message.get("message_identifier"):
            warnings.append({"type": "missing_message_identifier", "message": "Message identifier was not found."})
        if not message.get("worldwide_case_id") and not message.get("local_sender_case_id"):
            warnings.append({"type": "missing_case_identifier", "message": "Case identifier was not found."})
        if not reactions:
            warnings.append({"type": "missing_reaction", "message": "No reaction/event block was detected."})
        if not products:
            warnings.append({"type": "missing_product", "message": "No drug/product block was detected."})
        return ParsedXml(
            message=message,
            patient=patient,
            reactions=reactions,
            products=products,
            reporter=reporter,
            literature=literature,
            narrative=narrative,
            embedded_documents=embedded,
            warnings=warnings,
            e2b_version=version,
        )

    def detect_version(self, root: ET.Element) -> str:
        names = {canonical_name(element.tag) for element in root.iter()}
        root_text = local_name(root.tag).lower()
        if "ichicsr" in names or "safetyreport" in names:
            return "E2B(R2)"
        if "porrin049016uv" in root_text or "mcci" in root_text or any("icsr" in name for name in names):
            return "E2B(R3)"
        return "E2B-like XML"

    def porr_root(self, root: ET.Element) -> ET.Element:
        porr = first_element(root, "PORR_IN049016UV")
        return porr if porr is not None else root

    def investigation_event(self, root: ET.Element) -> ET.Element | None:
        return first_element(root, "investigationEvent")

    def adverse_event_assessment(self, root: ET.Element) -> ET.Element | None:
        return first_element(root, "adverseEventAssessment")

    def party_device_id(self, root: ET.Element, party_name: str) -> str | None:
        for party in all_elements(root, {party_name}):
            identifier = first_child(first_child(party, "device"), "id")
            if identifier is not None and identifier.attrib.get("extension"):
                return identifier.attrib["extension"]
        return None

    def observation_value(self, observation: ET.Element | None) -> str | None:
        value = first_child(observation, "value")
        if value is None:
            return None
        return (
            value.attrib.get("displayName")
            or value.attrib.get("value")
            or value.attrib.get("code")
            or element_text(value)
        )

    def first_observation_by_code(
        self,
        root: ET.Element,
        *,
        code: str | None = None,
        display_name: str | None = None,
    ) -> ET.Element | None:
        for observation in all_elements(root, {"observation", "observationevent", "investigationcharacteristic"}):
            code_element = first_child(observation, "code")
            if code_element is None:
                continue
            if code and code_element.attrib.get("code") == code:
                return observation
            if display_name and code_element.attrib.get("displayName") == display_name:
                return observation
        return None

    def extract_country_r3(self, root: ET.Element | None) -> str | None:
        if root is None:
            return None
        for country in all_elements(root, {"country"}):
            code = first_child(country, "code")
            if code is not None and code.attrib.get("code"):
                return code.attrib["code"]
        return None

    def extract_message_r3(self, root: ET.Element) -> dict[str, Any]:
        porr = self.porr_root(root)
        investigation = self.investigation_event(root)
        root_id = first_child(root, "id")
        porr_id = first_child(porr, "id")
        inv_ids = child_elements(investigation, "id")
        message_identifier = root_id.attrib.get("extension") if root_id is not None else None
        worldwide_case_id = (
            inv_ids[0].attrib.get("extension")
            if inv_ids
            else (porr_id.attrib.get("extension") if porr_id is not None else None)
        )
        local_sender_case_id = inv_ids[1].attrib.get("extension") if len(inv_ids) > 1 else worldwide_case_id
        message_date = parse_partial_date(
            child_attr(root, "value", "creationTime")
            or child_attr(porr, "value", "creationTime")
        )
        received_raw = child_attr(investigation, "value", "availabilityTime")
        effective = first_child(investigation, "effectiveTime")
        received_raw = child_attr(effective, "value", "low") or received_raw
        received = parse_partial_date(received_raw)
        followup_type = None
        for related in all_elements(root, {"relatedinvestigation"}):
            code_element = first_child(related, "code")
            if code_element is not None and code_element.attrib.get("displayName"):
                followup_type = code_element.attrib["displayName"]
                break
        sender = self.party_device_id(porr, "sender") or self.party_device_id(root, "sender")
        receiver = self.party_device_id(porr, "receiver") or self.party_device_id(root, "receiver")
        return {
            "message_identifier": message_identifier,
            "message_number": message_identifier,
            "message_version": message_identifier.rsplit("-", 1)[-1] if message_identifier and "-" in message_identifier else None,
            "worldwide_case_id": worldwide_case_id,
            "local_sender_case_id": local_sender_case_id,
            "message_type": "ICHICSR",
            "followup_type": followup_type,
            "message_date": message_date,
            "first_received_date": received,
            "latest_received_date": received,
            "company_received_date": received,
            "country": self.extract_country_r3(root),
            "sender_country": None,
            "sender": sender,
            "receiver": receiver,
            "source": followup_type,
            "seriousness": None,
            "is_literature": first_element(root, "bibliographicDesignationText") is not None,
            "is_duplicate": False,
            "related_messages": [],
        }

    def extract_patient_r3(self, root: ET.Element) -> dict[str, Any]:
        assessment_node = self.adverse_event_assessment(root)
        assessment = assessment_node if assessment_node is not None else root
        primary = first_element(assessment, "primaryRole")
        player = first_element(primary, "player1") if primary is not None else None
        name = first_child(player, "name")
        gender = first_child(player, "administrativeGenderCode")
        age_obs = self.first_observation_by_code(assessment, code="3")
        age_group_obs = self.first_observation_by_code(assessment, code="4", display_name="ageGroup")
        return {
            "initials": element_text(name),
            "identifier": None,
            "sex": normalize_sex(gender.attrib.get("code") if gender is not None else None),
            "age_value": to_float(self.observation_value(age_obs)),
            "age_unit": "years" if age_obs is not None else None,
            "birth_date": parse_partial_date(None),
            "age_group": self.observation_value(age_group_obs),
            "weight": None,
            "height": None,
            "country": self.extract_country_r3(assessment),
            "medical_history": self.extract_organizer_text(assessment, "relevantMedicalHistoryAndConcurrentConditions"),
            "concomitant_conditions": None,
            "death_cause": None,
            "death_date": parse_partial_date(None),
        }

    def extract_organizer_text(self, root: ET.Element, display_name: str) -> str | None:
        values = []
        for organizer in all_elements(root, {"organizer"}):
            code_element = first_child(organizer, "code")
            if code_element is None or code_element.attrib.get("displayName") != display_name:
                continue
            for observation in all_elements(organizer, {"observation"}):
                value = self.observation_value(observation)
                if value:
                    values.append(value)
        return "; ".join(values) or None

    def extract_reactions_r3(self, root: ET.Element) -> list[dict[str, Any]]:
        reactions = []
        for observation in all_elements(root, {"observation"}):
            code_element = first_child(observation, "code")
            if code_element is None:
                continue
            if code_element.attrib.get("displayName") != "reaction" and code_element.attrib.get("code") != "29":
                continue
            value = first_child(observation, "value")
            if value is None:
                continue
            effective = first_child(observation, "effectiveTime")
            low = first_child(effective, "low")
            high = first_child(effective, "high")
            onset = parse_partial_date(low.attrib.get("value") if low is not None else None)
            end = parse_partial_date(high.attrib.get("value") if high is not None else None)
            if low is not None and low.attrib.get("nullFlavor") and not onset["raw"]:
                onset["null_flavor"] = low.attrib.get("nullFlavor")
            if high is not None and high.attrib.get("nullFlavor") and not end["raw"]:
                end["null_flavor"] = high.attrib.get("nullFlavor")
            subobservations = [sub for sub in all_elements(observation, {"observation"}) if sub is not observation]

            def sub_bool(display: str) -> bool:
                for sub in subobservations:
                    sub_code = first_child(sub, "code")
                    if sub_code is None or sub_code.attrib.get("displayName") != display:
                        continue
                    sub_value = first_child(sub, "value")
                    return bool_from_code(sub_value.attrib.get("value") if sub_value is not None else None)
                return False

            outcome = None
            for sub in subobservations:
                sub_code = first_child(sub, "code")
                if sub_code is not None and sub_code.attrib.get("displayName") == "outcome":
                    outcome = self.observation_value(sub)
                    break
            term = element_text(value) or value.attrib.get("displayName") or value.attrib.get("code")
            serious_flags = {
                "seriousness_death": sub_bool("resultsInDeath"),
                "seriousness_life_threatening": sub_bool("isLifeThreatening"),
                "seriousness_hospitalization": sub_bool("requiresInpatientHospitalization"),
                "seriousness_disability": sub_bool("resultsInPersistentOrSignificantDisability"),
                "seriousness_congenital_anomaly": sub_bool("congenitalAnomalyBirthDefect"),
                "seriousness_other_medically_important": sub_bool("otherMedicallyImportantCondition"),
            }
            reactions.append(
                {
                    "reported_term": term or "Unknown reaction",
                    "verbatim_term": term,
                    "meddra_llt_code": None,
                    "meddra_llt_name": None,
                    "meddra_pt_code": value.attrib.get("code"),
                    "meddra_pt_name": value.attrib.get("displayName"),
                    "meddra_version": value.attrib.get("codeSystemVersion"),
                    "term_language": None,
                    "term_country_code": self.extract_country_r3(observation),
                    "onset_date": onset,
                    "end_date": end,
                    "duration_text": None,
                    "outcome": outcome,
                    "is_serious": any(serious_flags.values()),
                    **serious_flags,
                    "medically_confirmed": None,
                    "comment": None,
                    "null_flavor": element_null_flavor(value),
                }
            )
        return reactions

    def extract_products_r3(self, root: ET.Element) -> list[dict[str, Any]]:
        products = []
        for administration in all_elements(root, {"substanceadministration"}):
            identifier = first_child(administration, "id")
            if identifier is None or not (identifier.attrib.get("root") or "").startswith("DID"):
                continue
            product_names = []
            for kind in all_elements(administration, {"kindofproduct"}):
                name = first_child(kind, "name")
                if name is not None and element_text(name):
                    product_names.append(element_text(name))
            if not product_names:
                continue
            start = parse_partial_date(None)
            end = parse_partial_date(None)
            for relationship in child_elements(administration, "outboundRelationship2"):
                nested_admin = first_element(relationship, "substanceAdministration")
                effective = first_child(nested_admin, "effectiveTime")
                if effective is None:
                    continue
                low = first_child(effective, "low")
                high = first_child(effective, "high")
                start = parse_partial_date(low.attrib.get("value") if low is not None else None)
                end = parse_partial_date(high.attrib.get("value") if high is not None else None)
                if low is not None and low.attrib.get("nullFlavor") and not start["raw"]:
                    start["null_flavor"] = low.attrib.get("nullFlavor")
                if high is not None and high.attrib.get("nullFlavor") and not end["raw"]:
                    end["null_flavor"] = high.attrib.get("nullFlavor")
                break
            indication = None
            for relationship in child_elements(administration, "inboundRelationship"):
                observation = first_element(relationship, "observation")
                obs_code = first_child(observation, "code")
                if obs_code is not None and obs_code.attrib.get("displayName") == "indication":
                    indication = self.observation_value(observation)
                    break
            products.append(
                {
                    "reported_product_name": product_names[0],
                    "normalized_product_name": product_names[0],
                    "active_substances": product_names[1:],
                    "drug_role": "suspect",
                    "indication": indication,
                    "dose_value": None,
                    "dose_unit": None,
                    "frequency": None,
                    "route": None,
                    "dosage_form": None,
                    "therapy_start_date": start,
                    "therapy_end_date": end,
                    "duration_text": None,
                    "batch_lot_number": first_text(administration, "lotNumberText"),
                    "expiry_date": parse_partial_date(None),
                    "authorization_country_code": self.extract_country_r3(administration),
                    "authorization_number": None,
                    "authorization_holder": None,
                    "action_taken": None,
                    "dechallenge_result": None,
                    "rechallenge_result": None,
                    "causality_text": None,
                    "comment": None,
                    "null_flavor": None,
                }
            )
        return products

    def extract_reporter_r3(self, root: ET.Element) -> dict[str, Any]:
        assigned = first_element(root, "assignedEntity")
        person = first_element(assigned, "assignedPerson") if assigned is not None else None
        names = [element_text(part) for part in all_elements(person, {"prefix", "given", "family"})] if person is not None else []
        organization = first_element(assigned, "representedOrganization") if assigned is not None else None
        return {
            "source_type": None,
            "name": " ".join(part for part in names if part) or None,
            "organization": first_text(organization, "name") if organization is not None else self.party_device_id(root, "sender"),
            "department": None,
            "profession": None,
            "address": None,
            "country": self.extract_country_r3(assigned),
            "phone": None,
            "email": None,
            "medically_confirmed": None,
        }

    def regex_value(self, text: str | None, pattern: str) -> str | None:
        if not text:
            return None
        match = re.search(pattern, text, flags=re.IGNORECASE)
        return match.group(1) if match else None

    def extract_literature_r3(self, root: ET.Element) -> dict[str, Any]:
        citation = first_text(root, "bibliographicDesignationText")
        return {
            "is_literature": bool(citation),
            "title": None,
            "authors": None,
            "journal": None,
            "year": None,
            "volume": None,
            "issue": None,
            "pages": None,
            "doi": self.regex_value(citation, r"\bdoi[:\s]+([^;\s]+)"),
            "pmid": self.regex_value(citation, r"\bPMID[:\s]+([0-9]+)"),
            "citation": citation,
            "additional_source_text": None,
        }

    def extract_narrative_r3(self, root: ET.Element) -> dict[str, Any]:
        investigation = self.investigation_event(root)
        narrative = first_text(investigation, "text") if investigation is not None else first_text(root, "text")
        comments = []
        for observation in all_elements(root, {"observationevent"}):
            code_element = first_child(observation, "code")
            if code_element is not None and code_element.attrib.get("displayName") == "comment":
                value = self.observation_value(observation)
                if value:
                    comments.append(value)
        return {
            "medical_narrative": narrative,
            "sender_comment": "; ".join(comments) or None,
            "company_comment": None,
            "diagnosis": None,
            "test_results": self.extract_organizer_text(root, "testsAndProceduresRelevantToTheInvestigation"),
        }

    def extract_message(self, root: ET.Element) -> dict[str, Any]:
        message_date = parse_partial_date(first_text(root, "messagedate", "creationTime", "transmissiondate"))
        first_received = parse_partial_date(first_text(root, "receiptdate", "receivedate", "datefirstreceived", "firstreceivedate"))
        latest_received = parse_partial_date(first_text(root, "receiptdate", "latestreceivedate", "datelatestreceived"))
        company_received = parse_partial_date(first_text(root, "receiptdate", "companyreceivedate", "receiveddate"))
        message_identifier = (
            first_text(root, "messageidentifier", "messagenumb", "messageid")
            or first_attr(root, "extension", "id")
            or first_attr(root, "root", "id")
            or first_text(root, "safetyreportid")
        )
        worldwide_case_id = first_text(
            root,
            "worldwideuniquecaseidentificationnumber",
            "worldwidecaseid",
            "safetyreportid",
        )
        local_sender_case_id = first_text(root, "sendercaseid", "companynumb", "localcaseid")
        message_version = first_text(root, "safetyreportversion", "messageversion", "versionNumber")
        message_type = first_text(root, "messagetype", "reporttype", "typeCode")
        followup_type = first_text(root, "reporttype", "safetyreportversion", "casetype")
        sender = first_text(root, "senderorganization", "sender", "sendername", "messagesenderidentifier")
        receiver = first_text(root, "receiverorganization", "receiver", "receivername", "messagereceiveridentifier")
        source = first_text(root, "reporttype", "primarysourcetype", "qualification")
        country = first_text(root, "occurcountry", "primarysourcecountry", "country")
        sender_country = first_text(root, "sendercountry", "reportercountry", "primarysourcecountry")
        seriousness = first_text(root, "serious", "seriousness", "seriousnesscriteria")
        literature_flag = bool(first_text(root, "literaturereference", "literaturereport", "pmid", "doi"))
        duplicate_flag = bool_from_code(first_text(root, "duplicate", "duplicateflag"))
        return {
            "message_identifier": message_identifier,
            "message_number": message_identifier,
            "message_version": message_version,
            "worldwide_case_id": worldwide_case_id,
            "local_sender_case_id": local_sender_case_id,
            "message_type": message_type,
            "followup_type": followup_type,
            "message_date": message_date,
            "first_received_date": first_received,
            "latest_received_date": latest_received,
            "company_received_date": company_received,
            "country": country,
            "sender_country": sender_country,
            "sender": sender,
            "receiver": receiver,
            "source": source,
            "seriousness": seriousness,
            "is_literature": literature_flag,
            "is_duplicate": duplicate_flag,
            "related_messages": [],
        }

    def extract_patient(self, root: ET.Element) -> dict[str, Any]:
        birth = parse_partial_date(first_text(root, "patientbirthdate", "birthdate"))
        death = parse_partial_date(first_text(root, "patientdeathdate", "deathdate"))
        return {
            "initials": first_text(root, "patientinitial", "patientinitials", "initials"),
            "identifier": first_text(root, "patientidentifier", "patientid", "patientnumber"),
            "sex": normalize_sex(first_text(root, "patientsex", "sex", "gender")),
            "age_value": to_float(first_text(root, "patientonsetage", "patientage", "age")),
            "age_unit": first_text(root, "patientonsetageunit", "patientageunit", "ageunit"),
            "birth_date": birth,
            "age_group": first_text(root, "patientagegroup", "agegroup"),
            "weight": to_float(first_text(root, "patientweight", "weight")),
            "height": to_float(first_text(root, "patientheight", "height")),
            "country": first_text(root, "patientcountry", "country"),
            "medical_history": first_text(root, "patientmedicalhistorytext", "medicalhistorytext", "medicalhistory"),
            "concomitant_conditions": first_text(root, "patientepisodenamelist", "concomitantconditions"),
            "death_cause": first_text(root, "patientdeathcause", "deathcause"),
            "death_date": death,
        }

    def extract_reactions(self, root: ET.Element) -> list[dict[str, Any]]:
        blocks = all_elements(root, {"patientreaction", "reaction", "reactionevent", "adversereaction"})
        if not blocks:
            blocks = [element for element in root.iter() if canonical_name(element.tag).endswith("reaction")]
        reactions = []
        for block in blocks:
            term = child_text(block, "reactionmeddrallt", "reactionmeddrapt", "reactionreportedterm", "primarysourcereaction", "reportedterm")
            code = child_text(block, "reactionmeddralltcode", "reactionmeddraptcode", "meddracode")
            if not term and not code:
                continue
            onset = parse_partial_date(child_text(block, "reactionstartdate", "onsetdate", "startdate"))
            end = parse_partial_date(child_text(block, "reactionenddate", "enddate"))
            serious = any(
                bool_from_code(child_text(block, name))
                for name in [
                    "seriousnessdeath",
                    "seriousnesslifethreatening",
                    "seriousnesshospitalization",
                    "seriousnessdisabling",
                    "seriousnesscongenitalanomali",
                    "seriousnessother",
                ]
            ) or bool_from_code(child_text(block, "reactionseriousness", "serious"))
            reactions.append(
                {
                    "reported_term": term or code or "Unknown reaction",
                    "verbatim_term": child_text(block, "primarysourcereaction", "reactionreportedterm"),
                    "meddra_llt_code": child_text(block, "reactionmeddralltcode"),
                    "meddra_llt_name": child_text(block, "reactionmeddrallt"),
                    "meddra_pt_code": child_text(block, "reactionmeddraptcode", "meddracode"),
                    "meddra_pt_name": child_text(block, "reactionmeddrapt", "meddrapt"),
                    "meddra_version": child_text(block, "reactionmeddraversionllt", "reactionmeddraversionpt"),
                    "term_language": child_text(block, "reactiontermhighlighted", "language"),
                    "term_country_code": child_text(block, "reactioncountry"),
                    "onset_date": onset,
                    "end_date": end,
                    "duration_text": child_text(block, "reactionduration", "duration"),
                    "outcome": child_text(block, "reactionoutcome", "outcome"),
                    "is_serious": serious,
                    "seriousness_death": bool_from_code(child_text(block, "seriousnessdeath")),
                    "seriousness_life_threatening": bool_from_code(child_text(block, "seriousnesslifethreatening")),
                    "seriousness_hospitalization": bool_from_code(child_text(block, "seriousnesshospitalization")),
                    "seriousness_disability": bool_from_code(child_text(block, "seriousnessdisabling")),
                    "seriousness_congenital_anomaly": bool_from_code(child_text(block, "seriousnesscongenitalanomali")),
                    "seriousness_other_medically_important": bool_from_code(child_text(block, "seriousnessother")),
                    "medically_confirmed": None,
                    "comment": child_text(block, "reactioncomment", "comment"),
                    "null_flavor": element_null_flavor(block),
                }
            )
        return reactions

    def extract_products(self, root: ET.Element) -> list[dict[str, Any]]:
        blocks = all_elements(root, {"patientdrug", "drug", "medication", "medicinalproduct"})
        products = []
        for block in blocks:
            name = child_text(block, "medicinalproduct", "drugname", "productname", "brandname")
            substance_names = [
                element_text(element)
                for element in all_elements(block, {"activesubstancename", "activesubstance", "substancename"})
                if element_text(element)
            ]
            if not name and not substance_names:
                continue
            start = parse_partial_date(child_text(block, "drugstartdate", "startdate"))
            end = parse_partial_date(child_text(block, "drugenddate", "enddate"))
            expiry = parse_partial_date(child_text(block, "drugexpirationdate", "expirydate"))
            products.append(
                {
                    "reported_product_name": name,
                    "normalized_product_name": name,
                    "active_substances": substance_names,
                    "drug_role": normalize_drug_role(child_text(block, "drugcharacterization", "role")),
                    "indication": child_text(block, "drugindication", "indication"),
                    "dose_value": child_text(block, "drugstructuredosagenumb", "dosevalue", "dose"),
                    "dose_unit": child_text(block, "drugstructuredosageunit", "doseunit"),
                    "frequency": child_text(block, "drugseparatedosagenumb", "drugintervaldosageunitnumb", "frequency"),
                    "route": child_text(block, "drugadministrationroute", "route"),
                    "dosage_form": child_text(block, "drugdosageform", "dosageform"),
                    "therapy_start_date": start,
                    "therapy_end_date": end,
                    "duration_text": child_text(block, "drugtreatmentduration", "duration"),
                    "batch_lot_number": child_text(block, "drugbatchnumb", "batchlotnumber"),
                    "expiry_date": expiry,
                    "authorization_country_code": child_text(block, "drugauthorizationcountry", "authorizationcountry"),
                    "authorization_number": child_text(block, "drugauthorizationnumb", "authorizationnumber"),
                    "authorization_holder": child_text(block, "drugauthorizationholder", "authorizationholder"),
                    "action_taken": child_text(block, "actiondrug", "actiontaken"),
                    "dechallenge_result": child_text(block, "drugrecurreadministration", "dechallenge"),
                    "rechallenge_result": child_text(block, "drugrecuraction", "rechallenge"),
                    "causality_text": child_text(block, "drugreactionrelatedness", "causality"),
                    "comment": child_text(block, "drugadditional", "comment"),
                    "null_flavor": element_null_flavor(block),
                }
            )
        return products

    def extract_reporter(self, root: ET.Element) -> dict[str, Any]:
        return {
            "source_type": first_text(root, "qualification", "primarysourcetype"),
            "name": first_text(root, "reportergivename", "reporterfamilyname", "reportername"),
            "organization": first_text(root, "reporterorganization", "senderorganization"),
            "department": first_text(root, "reporterdepartment"),
            "profession": first_text(root, "qualification", "profession"),
            "address": first_text(root, "reporterstreet", "reporteraddress"),
            "country": first_text(root, "reportercountry", "primarysourcecountry"),
            "phone": first_text(root, "reportertel", "sendertelephone"),
            "email": first_text(root, "reporteremail", "senderemail"),
            "medically_confirmed": first_text(root, "medicallyconfirm"),
        }

    def extract_literature(self, root: ET.Element) -> dict[str, Any]:
        citation = first_text(root, "literaturereference", "bibliographicreference", "citation")
        return {
            "is_literature": bool(citation or first_text(root, "pmid", "doi")),
            "title": first_text(root, "articletitle", "publicationtitle", "title"),
            "authors": first_text(root, "authors", "author"),
            "journal": first_text(root, "journal", "journalsource"),
            "year": first_text(root, "publicationyear", "year"),
            "volume": first_text(root, "volume"),
            "issue": first_text(root, "issue"),
            "pages": first_text(root, "pages"),
            "doi": first_text(root, "doi"),
            "pmid": first_text(root, "pmid"),
            "citation": citation,
            "additional_source_text": first_text(root, "literatureadditionalinformation"),
        }

    def extract_narrative(self, root: ET.Element) -> dict[str, Any]:
        narrative = first_text(root, "narrativeincludeclinical", "clinicalnarrative", "narrative")
        return {
            "medical_narrative": narrative,
            "sender_comment": first_text(root, "sendercomment", "reportercomment"),
            "company_comment": first_text(root, "companycomment"),
            "diagnosis": first_text(root, "senderdiagnosis", "diagnosis"),
            "test_results": first_text(root, "testresult", "labdata", "laboratorydata"),
        }

    def extract_embedded_documents(self, root: ET.Element) -> list[dict[str, Any]]:
        documents: list[dict[str, Any]] = []
        seen_checksums: set[str] = set()
        candidates = [
            element
            for element in root.iter()
            if (
                canonical_name(element.tag) in {"includedocument", "attachment", "observationmedia"}
                or (
                    canonical_name(element.tag) == "text"
                    and (element.attrib.get("representation") or "").upper() == "B64"
                )
            )
        ]
        for element in candidates:
            text = "".join(element.itertext()).strip()
            encoded = re.sub(r"\s+", "", text)
            if not encoded or len(encoded) < 16:
                continue
            try:
                decoded = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                continue
            checksum = sha256_bytes(decoded)
            if checksum in seen_checksums:
                continue
            seen_checksums.add(checksum)
            mime_type = element.attrib.get("mediaType") or "application/octet-stream"
            extension = mimetypes.guess_extension(mime_type) or ".bin"
            reference = first_child(element, "reference")
            referenced_name = reference.attrib.get("value") if reference is not None else None
            file_name = safe_file_name(
                element.attrib.get("filename") or referenced_name,
                f"embedded-document-{len(documents) + 1}{extension}",
            )
            if len(decoded) > MAX_EMBEDDED_BYTES:
                documents.append(
                    {
                        "file_name": file_name,
                        "mime_type": mime_type,
                        "size_bytes": len(decoded),
                        "sha256": checksum,
                        "too_large": True,
                    }
                )
                continue
            documents.append(
                {
                    "file_name": file_name,
                    "mime_type": mime_type,
                    "size_bytes": len(decoded),
                    "sha256": checksum,
                    "content_b64": base64.b64encode(decoded).decode("ascii"),
                    "too_large": False,
                }
            )
        return documents


class E2BPackageService:
    def __init__(self) -> None:
        self.parser = E2BParser()

    def prepare_batch(
        self,
        db: Session,
        *,
        filename: str,
        content: bytes,
        uploaded_by_user_id: str | None,
    ) -> E2BImportBatch:
        source_suffix = Path(filename).suffix.lower()
        if source_suffix not in {".zip", ".xml"}:
            raise ValueError("Only E2B XML files and ZIP packages can be imported.")
        settings = get_settings()
        max_upload_size = (
            min(settings.max_upload_bytes, MAX_ZIP_BYTES)
            if source_suffix == ".zip"
            else min(max(settings.max_upload_bytes, 1), MAX_SINGLE_FILE_BYTES)
        )
        if len(content) > max_upload_size:
            raise ValueError("E2B source file exceeds configured upload size limit.")
        checksum = sha256_bytes(content)
        duplicate = (
            db.query(E2BImportBatch)
            .filter(
                E2BImportBatch.zip_checksum_sha256 == checksum,
                E2BImportBatch.is_deleted.is_(False),
            )
            .order_by(E2BImportBatch.uploaded_at.desc())
            .first()
        )
        batch = E2BImportBatch(
            source_filename=Path(filename).name,
            source_size_bytes=len(content),
            zip_checksum_sha256=checksum,
            uploaded_by_user_id=uploaded_by_user_id,
            status="uploaded" if not duplicate else "repeat_upload",
            duplicate_batch_id=duplicate.id if duplicate else None,
            importer_version=IMPORTER_VERSION,
        )
        db.add(batch)
        db.flush()

        storage_dir = UPLOAD_ROOT / batch.id
        files_dir = storage_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        source_file = storage_dir / f"source{source_suffix}"
        source_file.write_bytes(content)
        batch.storage_dir = str(storage_dir)
        batch.source_zip_path = str(source_file)

        try:
            discovered = self.unpack_and_parse(db, batch, source_file, files_dir)
            batch.file_manifest_json = json_dumps(discovered)
            self.apply_product_scope_checks(db, batch, discovered)
            self.apply_multi_case_guard(db, batch, discovered)
            self.update_batch_summary(batch, discovered)
            if not batch.status == "repeat_upload":
                batch.status = "requires_review" if batch.error_count == 0 else "error"
        except Exception as exc:
            batch.status = "error"
            self.add_issue(db, batch, None, "error", "package_error", str(exc), True)
        db.flush()
        log_audit(
            db,
            entity_type="E2BImportBatch",
            entity_id=batch.id,
            action="create",
            field_name="status",
            new_value=batch.status,
            user_id=uploaded_by_user_id,
            source_module="E2B Import",
            comment=f"E2B package uploaded: {batch.source_filename}",
        )
        db.commit()
        db.refresh(batch)
        return batch

    def recheck_batch(
        self,
        db: Session,
        batch: E2BImportBatch,
        *,
        checked_by_user_id: str | None,
    ) -> E2BImportBatch:
        if batch.status in {"imported", "not_pv_ars"}:
            raise ValueError("Finalized batch cannot be re-checked.")
        if not batch.source_zip_path or not Path(batch.source_zip_path).exists():
            raise ValueError("Original E2B source file is not available for re-check.")
        with transaction_directory(db, batch, "files") as files_dir:
            self._recheck_batch(db, batch, files_dir, checked_by_user_id)
        return batch

    def _recheck_batch(self, db, batch, files_dir, checked_by_user_id):
        for issue in list(batch.issues):
            db.delete(issue)
        for item in list(batch.items):
            db.delete(item)
        db.flush()
        batch.issues = []
        batch.items = []
        batch.status = "uploaded"
        batch.importer_version = IMPORTER_VERSION
        batch.file_manifest_json = None
        batch.normalized_json = None
        batch.detected_xml_count = 0
        batch.detected_pdf_count = 0
        batch.detected_other_count = 0
        batch.detected_case_count = 0
        batch.detected_message_count = 0
        batch.error_count = 0
        batch.warning_count = 0
        discovered = self.unpack_and_parse(db, batch, Path(batch.source_zip_path), files_dir)
        batch.file_manifest_json = json_dumps(discovered)
        self.apply_product_scope_checks(db, batch, discovered)
        self.apply_multi_case_guard(db, batch, discovered)
        self.update_batch_summary(batch, discovered)
        batch.status = "requires_review" if batch.error_count == 0 else "error"
        log_audit(
            db,
            entity_type="E2BImportBatch",
            entity_id=batch.id,
            action="recheck",
            field_name="status",
            new_value=batch.status,
            user_id=checked_by_user_id,
            source_module="E2B Import",
            comment=f"E2B package re-checked: {batch.source_filename}",
        )

    def unpack_and_parse(
        self,
        db: Session,
        batch: E2BImportBatch,
        source_file: Path,
        files_dir: Path,
    ) -> list[dict[str, Any]]:
        discovered: list[dict[str, Any]] = []
        seen_checksums: dict[str, E2BImportItem] = {}
        total_unpacked = [0]

        if source_file.suffix.lower() == ".xml":
            self.register_discovered_file(
                db,
                batch,
                files_dir,
                original_path=safe_file_name(batch.source_filename, "message.xml"),
                data=source_file.read_bytes(),
                discovered=discovered,
                seen_checksums=seen_checksums,
            )
            return discovered

        try:
            archive = zipfile.ZipFile(source_file)
        except zipfile.BadZipFile as exc:
            raise ValueError("ZIP cannot be opened.") from exc
        with archive:
            self.scan_zip_archive(
                db,
                batch,
                archive,
                files_dir,
                discovered=discovered,
                seen_checksums=seen_checksums,
                total_unpacked=total_unpacked,
                prefix="",
                depth=0,
            )
        if not any(record["role"] == "xml" for record in discovered):
            self.add_issue(db, batch, None, "error", "xml_not_found", "Package does not contain XML files.", True)
        return discovered

    def scan_zip_archive(
        self,
        db: Session,
        batch: E2BImportBatch,
        archive: zipfile.ZipFile,
        files_dir: Path,
        *,
        discovered: list[dict[str, Any]],
        seen_checksums: dict[str, E2BImportItem],
        total_unpacked: list[int],
        prefix: str,
        depth: int,
    ) -> None:
        for info in archive.infolist():
            if info.is_dir():
                continue
            member_name = info.filename.replace("\\", "/")
            logical_path = f"{prefix}!/{member_name}" if prefix else member_name
            if not safe_zip_member_name(member_name):
                self.add_issue(db, batch, None, "error", "unsafe_path", f"Unsafe ZIP member path: {logical_path}", True)
                continue
            if info.flag_bits & 0x1:
                self.add_issue(db, batch, None, "error", "encrypted_file", f"Encrypted ZIP member is not supported: {logical_path}", True)
                continue
            if info.file_size > MAX_SINGLE_FILE_BYTES:
                self.add_issue(db, batch, None, "error", "file_too_large", f"File exceeds size limit: {logical_path}", True)
                continue
            total_unpacked[0] += info.file_size
            if total_unpacked[0] > MAX_UNPACKED_BYTES:
                self.add_issue(db, batch, None, "error", "package_too_large", "Unpacked package exceeds size limit.", True)
                return
            data = archive.read(info)
            item, _record = self.register_discovered_file(
                db,
                batch,
                files_dir,
                original_path=logical_path,
                data=data,
                discovered=discovered,
                seen_checksums=seen_checksums,
            )
            if not member_name.lower().endswith(".zip") or item.duplicate_of_item_id:
                continue
            if depth >= MAX_NESTED_ZIP_DEPTH:
                self.add_issue(
                    db,
                    batch,
                    item,
                    "warning",
                    "nested_zip_depth_limit",
                    f"Nested ZIP depth limit reached: {logical_path}",
                    False,
                )
                continue
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as nested:
                    self.scan_zip_archive(
                        db,
                        batch,
                        nested,
                        files_dir,
                        discovered=discovered,
                        seen_checksums=seen_checksums,
                        total_unpacked=total_unpacked,
                        prefix=logical_path,
                        depth=depth + 1,
                    )
            except zipfile.BadZipFile:
                self.add_issue(
                    db,
                    batch,
                    item,
                    "warning",
                    "invalid_nested_zip",
                    f"Nested ZIP cannot be opened: {logical_path}",
                    False,
                )

    def register_discovered_file(
        self,
        db: Session,
        batch: E2BImportBatch,
        files_dir: Path,
        *,
        original_path: str,
        data: bytes,
        discovered: list[dict[str, Any]],
        seen_checksums: dict[str, E2BImportItem],
    ) -> tuple[E2BImportItem, dict[str, Any]]:
        checksum = sha256_bytes(data)
        duplicate = seen_checksums.get(checksum)
        file_name = safe_file_name(original_path, "imported-file.bin")
        role = role_for_file(file_name)
        item = E2BImportItem(
            batch_id=batch.id,
            original_path=original_path,
            file_name=file_name,
            file_role=role,
            mime_type=mime_for_file(file_name),
            size_bytes=len(data),
            checksum_sha256=checksum,
            status="duplicate" if duplicate else "detected",
            duplicate_of_item_id=duplicate.id if duplicate else None,
        )
        db.add(item)
        db.flush()
        if duplicate:
            item.stored_path = duplicate.stored_path
        else:
            target = files_dir / item.id / file_name
            target.parent.mkdir(parents=True, exist_ok=True)
            storage_root = files_dir.resolve()
            resolved_target = target.resolve()
            if storage_root not in resolved_target.parents:
                raise ValueError("Stored E2B file path is outside import storage.")
            target.write_bytes(data)
            item.stored_path = str(target)
            seen_checksums[checksum] = item
        record = {
            "id": item.id,
            "path": original_path,
            "file_name": item.file_name,
            "role": role,
            "size_bytes": len(data),
            "checksum_sha256": checksum,
            "mime_type": item.mime_type,
            "status": item.status,
            "duplicate_of_item_id": item.duplicate_of_item_id,
        }
        if role == "xml" and not duplicate:
            self.parse_xml_item(db, batch, item, Path(item.stored_path), record)
        discovered.append(record)
        return item, record

    def parse_xml_item(
        self,
        db: Session,
        batch: E2BImportBatch,
        item: E2BImportItem,
        path: Path,
        record: dict[str, Any],
    ) -> None:
        try:
            parsed = self.parser.parse(path.read_bytes())
        except ValueError as exc:
            item.status = "error"
            self.add_issue(db, batch, item, "error", "xml_parse_error", str(exc), True)
            record["status"] = "error"
            return
        normalized = parsed.as_dict()
        item.status = "parsed"
        item.e2b_version = parsed.e2b_version
        item.message_identifier = parsed.message.get("message_identifier")
        item.case_identifier = (
            parsed.message.get("worldwide_case_id")
            or parsed.message.get("local_sender_case_id")
        )
        item.recipient = parsed.message.get("receiver")
        item.normalized_json = json_dumps(normalized)
        item.validation_json = json_dumps({"warnings": parsed.warnings})
        record.update(
            {
                "status": "parsed",
                "e2b_version": item.e2b_version,
                "message_identifier": item.message_identifier,
                "case_identifier": item.case_identifier,
                "recipient": item.recipient,
                "normalized": normalized,
            }
        )
        for warning in parsed.warnings:
            self.add_issue(db, batch, item, "warning", warning["type"], warning["message"], False)
        if not item.message_identifier:
            self.add_issue(db, batch, item, "error", "missing_message_identifier", "Message identifier is required.", True)

    def update_batch_summary(self, batch: E2BImportBatch, discovered: list[dict[str, Any]]) -> None:
        xml_records = [record for record in discovered if record["role"] == "xml"]
        parsed_records = [record for record in xml_records if record.get("normalized")]
        case_keys = {
            self.case_group_key(record["normalized"]["message"])
            for record in parsed_records
        }
        case_summaries = self.case_summaries(parsed_records)
        product_summary = self.product_summary(parsed_records)
        batch.detected_xml_count = len(xml_records)
        batch.detected_pdf_count = len([record for record in discovered if record["role"] in {"pdf", "cioms", "literature_pdf"}])
        batch.detected_other_count = len([record for record in discovered if record["role"] == "other"])
        batch.detected_case_count = len(case_keys)
        batch.detected_message_count = len(parsed_records)
        batch.error_count = len([issue for issue in batch.issues if issue.severity == "error"])
        batch.warning_count = len([issue for issue in batch.issues if issue.severity == "warning"])
        batch.normalized_json = json_dumps(
            {
                "case_groups": self.group_messages(parsed_records),
                "case_summaries": case_summaries,
                "product_summary": product_summary,
                "package_summary": self.package_summary(case_summaries, product_summary),
                "files": discovered,
                "limits": {
                    "max_zip_bytes": MAX_ZIP_BYTES,
                    "max_unpacked_bytes": MAX_UNPACKED_BYTES,
                    "max_single_file_bytes": MAX_SINGLE_FILE_BYTES,
                    "max_embedded_bytes": MAX_EMBEDDED_BYTES,
                },
            }
        )

    def apply_product_scope_checks(
        self,
        db: Session,
        batch: E2BImportBatch,
        discovered: list[dict[str, Any]],
    ) -> None:
        parsed_records = [record for record in discovered if record.get("normalized")]
        for record in parsed_records:
            normalized = record["normalized"]
            for product in normalized.get("products", []):
                match = self.match_product_scope(db, product)
                product["match"] = match
                product["product_match_status"] = match["status"]
                product["is_company_product"] = match["is_company_product"]
        summary = self.product_summary(parsed_records)
        if parsed_records and not summary["has_company_product"]:
            detail = (
                "No exact company product match was found."
                if summary["total_products"]
                else "No product was available for company product matching."
            )
            self.add_issue(
                db,
                batch,
                None,
                "warning",
                "not_pv_ars_candidate",
                f"{detail} Mark this package as not PV system ARS LLC or correct product dictionaries before import.",
                True,
            )
        elif parsed_records and summary["not_found_count"]:
            self.add_issue(
                db,
                batch,
                None,
                "warning",
                "unknown_product",
                f"{summary['not_found_count']} imported product(s) were not found in the product dictionary.",
                False,
            )

    def apply_multi_case_guard(
        self,
        db: Session,
        batch: E2BImportBatch,
        discovered: list[dict[str, Any]],
    ) -> None:
        parsed_records = [record for record in discovered if record.get("normalized")]
        case_keys = {
            self.case_group_key(record["normalized"]["message"])
            for record in parsed_records
        }
        if len(case_keys) > 1:
            self.add_issue(
                db,
                batch,
                None,
                "error",
                "multiple_cases_not_supported",
                (
                    f"The package contains {len(case_keys)} distinct cases. "
                    "Import one case per package to prevent unrelated medical data from being merged."
                ),
                True,
            )

    def match_product_scope(self, db: Session, product_data: dict[str, Any]) -> dict[str, Any]:
        candidates = [product_data.get("reported_product_name"), product_data.get("normalized_product_name")]
        candidates.extend(product_data.get("active_substances") or [])
        normalized_candidates = {normalize_key(candidate) for candidate in candidates if normalize_key(candidate)}
        products = (
            db.query(Product)
            .filter(Product.is_deleted.is_(False), Product.is_active.is_(True))
            .all()
        )
        exact_matches = [
            product
            for product in products
            if normalize_key(product.product_name) in normalized_candidates
            or normalize_key(product.product_name_normalized) in normalized_candidates
            or normalize_key(product.product_code) in normalized_candidates
        ]
        if len(exact_matches) == 1:
            product = exact_matches[0]
            return {
                "status": "exact_company_product" if product.is_company_product else "exact_non_company_product",
                "product_id": product.id,
                "product_name": product.product_name,
                "is_company_product": bool(product.is_company_product),
            }
        if len(exact_matches) > 1:
            company_matches = [product for product in exact_matches if product.is_company_product]
            return {
                "status": "requires_choice",
                "product_id": company_matches[0].id if len(company_matches) == 1 else None,
                "product_name": company_matches[0].product_name if len(company_matches) == 1 else None,
                "is_company_product": len(company_matches) == 1,
            }
        return {
            "status": "not_found",
            "product_id": None,
            "product_name": None,
            "is_company_product": False,
        }

    def product_summary(self, parsed_records: list[dict[str, Any]]) -> dict[str, Any]:
        products = [
            product
            for record in parsed_records
            for product in record.get("normalized", {}).get("products", [])
        ]
        company_matches = [product for product in products if product.get("match", {}).get("is_company_product")]
        not_found = [product for product in products if product.get("match", {}).get("status") == "not_found"]
        non_company = [product for product in products if product.get("match", {}).get("status") == "exact_non_company_product"]
        return {
            "total_products": len(products),
            "has_company_product": bool(company_matches),
            "company_product_count": len(company_matches),
            "not_found_count": len(not_found),
            "non_company_product_count": len(non_company),
            "status": "pv_ars" if company_matches else "not_pv_ars_candidate",
        }

    def case_summaries(self, parsed_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        summaries = []
        seen: set[str] = set()
        for record in parsed_records:
            normalized = record["normalized"]
            message = normalized["message"]
            key = self.case_group_key(message)
            if key in seen:
                continue
            seen.add(key)
            flags = parsed_case_flags(normalized)
            summaries.append(
                {
                    "case_key": key,
                    "message_identifier": message.get("message_identifier"),
                    "worldwide_case_id": message.get("worldwide_case_id"),
                    "local_sender_case_id": message.get("local_sender_case_id"),
                    **flags,
                }
            )
        return summaries

    def package_summary(
        self,
        case_summaries: list[dict[str, Any]],
        product_summary: dict[str, Any],
    ) -> dict[str, Any]:
        due_dates = [
            summary["submission_due_date"]
            for summary in case_summaries
            if summary.get("submission_due_date")
        ]
        return {
            "case_count": len(case_summaries),
            "serious_case_count": len([summary for summary in case_summaries if summary.get("is_serious")]),
            "death_case_count": len([summary for summary in case_summaries if summary.get("has_death")]),
            "expectedness": "requires_assessment",
            "earliest_submission_due_date": min(due_dates) if due_dates else None,
            "pv_scope_status": product_summary["status"],
            "has_company_product": product_summary["has_company_product"],
        }

    def case_group_key(self, message: dict[str, Any]) -> str:
        return (
            message.get("worldwide_case_id")
            or message.get("local_sender_case_id")
            or message.get("message_identifier")
            or "unknown"
        )

    def group_messages(self, parsed_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for record in parsed_records:
            message = record["normalized"]["message"]
            key = self.case_group_key(message)
            groups.setdefault(
                key,
                {
                    "case_key": key,
                    "worldwide_case_id": message.get("worldwide_case_id"),
                    "local_sender_case_id": message.get("local_sender_case_id"),
                    "messages": [],
                },
            )["messages"].append(
                {
                    "file_name": record["file_name"],
                    "message_identifier": message.get("message_identifier"),
                    "message_version": message.get("message_version"),
                    "receiver": message.get("receiver"),
                    "followup_type": message.get("followup_type"),
                }
            )
        return list(groups.values())

    def add_issue(
        self,
        db: Session,
        batch: E2BImportBatch,
        item: E2BImportItem | None,
        severity: str,
        issue_type: str,
        message: str,
        is_blocking: bool,
        technical_path: str | None = None,
    ) -> E2BImportIssue:
        issue = E2BImportIssue(
            batch=batch,
            item=item,
            severity=severity,
            issue_type=issue_type,
            message=message,
            is_blocking=is_blocking,
            technical_path=technical_path,
        )
        db.add(issue)
        db.flush()
        return issue


class E2BMatchingService:
    def find_partner(self, db: Session, parsed: dict[str, Any]) -> tuple[str | None, str]:
        sender = parsed["message"].get("sender") or parsed.get("reporter", {}).get("organization")
        normalized = normalize_key(sender)
        if not normalized:
            return None, "not_found"
        matches = [
            partner
            for partner in db.query(Partner).filter(Partner.is_deleted.is_(False)).all()
            if normalize_key(partner.partner_name) == normalized or normalize_key(partner.partner_code) == normalized
        ]
        if len(matches) == 1:
            return matches[0].id, "exact"
        return None, "requires_choice" if len(matches) > 1 else "not_found"

    def find_product(self, db: Session, product_data: dict[str, Any]) -> tuple[str | None, str]:
        name = normalize_key(product_data.get("reported_product_name"))
        if not name:
            return None, "not_found"
        matches = [
            product
            for product in db.query(Product).filter(Product.is_deleted.is_(False)).all()
            if normalize_key(product.product_name) == name or normalize_key(product.product_name_normalized) == name
        ]
        if len(matches) == 1:
            return matches[0].id, "exact"
        return None, "requires_choice" if len(matches) > 1 else "not_found"

    def find_substance(self, db: Session, substance_name: str | None) -> tuple[str | None, str]:
        normalized = normalize_key(substance_name)
        if not normalized:
            return None, "not_found"
        matches = [
            substance
            for substance in db.query(Substance).filter(Substance.is_deleted.is_(False)).all()
            if normalize_key(substance.substance_name) == normalized
            or normalize_key(substance.substance_name_normalized) == normalized
            or normalize_key(substance.inn_name) == normalized
        ]
        if len(matches) == 1:
            return matches[0].id, "exact"
        return None, "requires_choice" if len(matches) > 1 else "not_found"


class E2BImportService:
    def __init__(self) -> None:
        self.matching = E2BMatchingService()

    def preview_case(self, db: Session, batch: E2BImportBatch) -> Case | None:
        for item in batch.items:
            if item.file_role == "xml" and item.status == "parsed" and item.normalized_json:
                return self.find_existing_case(db, json.loads(item.normalized_json).get("message", {}))
        return None

    @staticmethod
    def case_token(case: Case | None) -> str:
        return f"{case.id}:{case.version}" if case else "none"

    def confirm_batch(
        self,
        db: Session,
        batch: E2BImportBatch,
        *,
        confirmed_by_user_id: str | None,
        expected_case_token: str,
    ) -> dict[str, Any]:
        if batch.status == "imported":
            return json.loads(batch.import_summary_json or "{}")
        with transaction_directory(db, batch, "embedded") as embedded_dir:
            summary = self._confirm_batch(db, batch, confirmed_by_user_id, expected_case_token, embedded_dir)
        return summary

    def _confirm_batch(self, db, batch, confirmed_by_user_id, expected_case_token, embedded_dir):
        if batch.status == "cancelled":
            raise ValueError("Cancelled package must be re-checked before import.")
        if batch.status == "not_pv_ars":
            raise ValueError("Package is marked as not PV system ARS LLC and cannot be imported.")
        if any(issue.is_blocking for issue in batch.issues):
            raise ValueError("Import has blocking errors.")
        if not self.batch_has_company_product(batch):
            raise ValueError("No company product was found in the E2B package.")
        xml_items = [
            item
            for item in batch.items
            if item.file_role == "xml" and item.status == "parsed" and item.normalized_json
        ]
        if not xml_items:
            raise ValueError("No parsed XML messages are available for import.")
        primary = json.loads(xml_items[0].normalized_json)
        partner_id, partner_match_status = self.matching.find_partner(db, primary)
        existing_case = self.find_existing_case(db, primary["message"])
        if expected_case_token != self.case_token(existing_case):
            raise StaleDataError("Related case has changed. Reload the E2B preview.")
        if existing_case:
            assert_case_editable(existing_case)
        safety_report = self.create_safety_report(db, batch, primary, partner_id)
        incoming_request = self.create_incoming_request(db, batch, primary, partner_id)
        self.attach_parsed_products(db, primary, safety_report, incoming_request)
        case = existing_case or self.create_case(db, batch, primary, partner_id, safety_report.id)
        safety_report.case_id = case.id
        if existing_case:
            self.add_followup_for_existing_case(db, case, primary)
        self.apply_case_details(db, batch, case, primary)
        self.link_incoming_request(db, incoming_request, case, "follow_up" if existing_case else "created_from_message")
        self.save_attachments(db, batch, case, safety_report, embedded_dir=embedded_dir)
        transmissions = []
        for item in xml_items:
            parsed = json.loads(item.normalized_json or "{}")
            transmission = E2BTransmission(
                batch_id=batch.id,
                import_item_id=item.id,
                case_id=case.id,
                safety_report_id=safety_report.id,
                incoming_request_id=incoming_request.id,
                message_identifier=parsed.get("message", {}).get("message_identifier"),
                message_version=parsed.get("message", {}).get("message_version"),
                worldwide_case_id=parsed.get("message", {}).get("worldwide_case_id"),
                local_sender_case_id=parsed.get("message", {}).get("local_sender_case_id"),
                receiver=parsed.get("message", {}).get("receiver"),
                sender=parsed.get("message", {}).get("sender"),
                message_type=parsed.get("message", {}).get("message_type"),
                followup_type=parsed.get("message", {}).get("followup_type"),
                xml_checksum_sha256=item.checksum_sha256,
                normalized_json=item.normalized_json,
            )
            db.add(transmission)
            transmissions.append(transmission)
        batch.status = "imported"
        batch.confirmed_at = utcnow()
        batch.confirmed_by_user_id = confirmed_by_user_id
        batch.imported_at = utcnow()
        batch.created_safety_report_id = safety_report.id
        batch.created_incoming_request_id = incoming_request.id
        batch.created_case_id = case.id
        summary = {
            "safety_report_id": safety_report.id,
            "incoming_request_id": incoming_request.id,
            "case_id": case.id,
            "case_number": case.case_number,
            "existing_case_used": bool(existing_case),
            "partner_match_status": partner_match_status,
            "transmission_count": len(transmissions),
        }
        batch.import_summary_json = json_dumps(summary)
        log_audit(
            db,
            entity_type="E2BImportBatch",
            entity_id=batch.id,
            action="import_confirmed",
            field_name="status",
            old_value="requires_review",
            new_value="imported",
            case_id=case.id,
            user_id=confirmed_by_user_id,
            source_module="E2B Import",
            comment=f"E2B package imported into case {case.case_number}.",
        )
        return summary

    def mark_not_pv_ars(
        self,
        db: Session,
        batch: E2BImportBatch,
        *,
        marked_by_user_id: str | None,
    ) -> dict[str, Any]:
        if batch.status == "imported":
            raise ValueError("Imported batch cannot be marked as not PV system ARS LLC.")
        if batch.status == "cancelled":
            raise ValueError("Cancelled package must be re-checked before marking.")
        if batch.status == "not_pv_ars":
            return json.loads(batch.import_summary_json or "{}")
        xml_items = [
            item
            for item in batch.items
            if item.file_role == "xml" and item.status == "parsed" and item.normalized_json
        ]
        if not xml_items:
            raise ValueError("No parsed XML messages are available.")
        primary = json.loads(xml_items[0].normalized_json)
        partner_id, partner_match_status = self.matching.find_partner(db, primary)
        incoming_request = self.create_not_pv_ars_request(db, batch, primary, partner_id, marked_by_user_id)
        self.attach_parsed_products(db, primary, None, incoming_request)
        old_status = batch.status
        batch.status = "not_pv_ars"
        batch.confirmed_at = utcnow()
        batch.confirmed_by_user_id = marked_by_user_id
        batch.created_incoming_request_id = incoming_request.id
        summary = {
            "incoming_request_id": incoming_request.id,
            "message_number": incoming_request.message_number,
            "partner_match_status": partner_match_status,
            "disposition": "not PV system ARS LLC / не ФН АРС",
            "case_created": False,
        }
        batch.import_summary_json = json_dumps(summary)
        log_audit(
            db,
            entity_type="E2BImportBatch",
            entity_id=batch.id,
            action="marked_not_pv_ars",
            field_name="status",
            old_value=old_status,
            new_value="not_pv_ars",
            user_id=marked_by_user_id,
            source_module="E2B Import",
            comment="E2B package marked as not PV system ARS LLC / не ФН АРС. No case was created.",
        )
        db.commit()
        return summary

    def batch_has_company_product(self, batch: E2BImportBatch) -> bool:
        normalized = json.loads(batch.normalized_json or "{}")
        product_summary = normalized.get("product_summary") or {}
        if product_summary:
            return bool(product_summary.get("has_company_product"))
        for item in batch.items:
            if not item.normalized_json:
                continue
            parsed = json.loads(item.normalized_json)
            for product in parsed.get("products", []):
                if product.get("match", {}).get("is_company_product") or product.get("is_company_product"):
                    return True
        return False

    def find_existing_case(self, db: Session, message: dict[str, Any]) -> Case | None:
        worldwide = clean_text(message.get("worldwide_case_id"))
        local = clean_text(message.get("local_sender_case_id"))
        query = db.query(Case).filter(Case.is_deleted.is_(False))
        if worldwide:
            existing = query.filter(Case.worldwide_case_id == worldwide).first()
            if existing:
                return existing
        if local:
            return query.filter(Case.local_sender_case_id == local).first()
        return None

    def create_safety_report(
        self,
        db: Session,
        batch: E2BImportBatch,
        parsed: dict[str, Any],
        partner_id: str | None,
    ) -> SafetyReport:
        message = parsed["message"]
        received = date_field(message.get("company_received_date")) or date_field(message.get("first_received_date")) or date.today()
        report = SafetyReport(
            safety_report_number=generate_number(db, SafetyReport, "safety_report_number", "SR"),
            received_at=utcnow(),
            received_date=received,
            source_type="e2b_zip",
            message_direction="needs_review",
            partner_id=partner_id,
            reporter_name=parsed.get("reporter", {}).get("name"),
            reporter_email=parsed.get("reporter", {}).get("email"),
            reporter_country_code=parsed.get("reporter", {}).get("country") or message.get("sender_country"),
            raw_subject=f"E2B import {message.get('message_identifier') or batch.source_filename}",
            raw_text=parsed.get("narrative", {}).get("medical_narrative") or batch.source_filename,
            triage_status="in_triage",
            is_valid_icsr=True,
            minimum_criteria_patient=bool(parsed.get("patient")),
            minimum_criteria_reporter=bool(parsed.get("reporter")),
            minimum_criteria_product=bool(parsed.get("products")),
            minimum_criteria_event=bool(parsed.get("reactions")),
            import_batch_id=batch.id,
            e2b_message_identifier=message.get("message_identifier"),
            e2b_message_version=message.get("message_version"),
            e2b_followup_type=message.get("followup_type"),
            source_checksum_sha256=batch.zip_checksum_sha256,
        )
        db.add(report)
        db.flush()
        return report

    def create_incoming_request(
        self,
        db: Session,
        batch: E2BImportBatch,
        parsed: dict[str, Any],
        partner_id: str | None,
    ) -> IncomingRequest:
        message = parsed["message"]
        received = date_field(message.get("company_received_date")) or date_field(message.get("first_received_date")) or date.today()
        row = IncomingRequest(
            message_number=generate_number(db, IncomingRequest, "message_number", "MSG"),
            received_date=received,
            source_text=parsed.get("narrative", {}).get("medical_narrative") or batch.source_filename,
            source_type="e2b_zip",
            source_name=message.get("sender"),
            channel="E2B XML / ZIP",
            request_type="safety_message",
            partner_id=partner_id,
            reporter_name=parsed.get("reporter", {}).get("name"),
            reporter_contact=parsed.get("reporter", {}).get("email") or parsed.get("reporter", {}).get("phone"),
            message_category="e2b_import",
            possible_icsr="yes",
            patient_information=self.patient_summary(parsed.get("patient", {})),
            adverse_event=", ".join(reaction["reported_term"] for reaction in parsed.get("reactions", []) if reaction.get("reported_term"))[:1000],
            seriousness=normalize_seriousness(message, parsed.get("reactions", [])),
            recommended_next_action="Review E2B import preview and complete triage.",
            validity_assessment="Created from confirmed E2B ZIP import.",
            gpt_json_output=json_dumps(parsed),
            human_confirmed=True,
            status="triage_in_progress",
            triage_status="in_progress",
            safety_relevance="safety_related",
            potential_icsr="yes",
            validity_status="valid_icsr",
            has_identifiable_patient=bool(parsed.get("patient", {}).get("initials") or parsed.get("patient", {}).get("identifier")),
            has_identifiable_reporter=bool(parsed.get("reporter")),
            has_suspect_product=bool(parsed.get("products")),
            has_adverse_event=bool(parsed.get("reactions")),
            triage_result="create_case",
        )
        db.add(row)
        db.flush()
        return row

    def attach_parsed_products(
        self,
        db: Session,
        parsed: dict[str, Any],
        safety_report: SafetyReport | None,
        incoming_request: IncomingRequest,
    ) -> None:
        seen: set[tuple[str | None, str]] = set()
        for product_data in parsed.get("products", []):
            product_id, _ = self.matching.find_product(db, product_data)
            reported_name = clean_text(product_data.get("reported_product_name"))
            if not product_id and not reported_name:
                continue
            key = (product_id, (reported_name or "").casefold())
            if key in seen:
                continue
            seen.add(key)
            values = {
                "product_id": product_id,
                "reported_product_name": reported_name,
                "drug_role": product_data.get("drug_role") or "unknown",
            }
            if safety_report:
                safety_report.product_links.append(SafetyReportProduct(**values))
            incoming_request.product_links.append(IncomingRequestProduct(**values))
            if not incoming_request.product_id and product_id:
                incoming_request.product_id = product_id
        db.flush()

    def create_not_pv_ars_request(
        self,
        db: Session,
        batch: E2BImportBatch,
        parsed: dict[str, Any],
        partner_id: str | None,
        marked_by_user_id: str | None,
    ) -> IncomingRequest:
        message = parsed["message"]
        received = date_field(message.get("company_received_date")) or date_field(message.get("first_received_date")) or date.today()
        row = IncomingRequest(
            message_number=generate_number(db, IncomingRequest, "message_number", "MSG"),
            received_date=received,
            source_text=parsed.get("narrative", {}).get("medical_narrative") or batch.source_filename,
            source_type="e2b_zip",
            source_name=message.get("sender"),
            channel="E2B XML / ZIP",
            request_type="safety_message",
            partner_id=partner_id,
            reporter_name=parsed.get("reporter", {}).get("name"),
            reporter_contact=parsed.get("reporter", {}).get("email") or parsed.get("reporter", {}).get("phone"),
            message_category="not_pv_ars",
            possible_icsr="no",
            patient_information=self.patient_summary(parsed.get("patient", {})),
            adverse_event=", ".join(
                reaction["reported_term"] for reaction in parsed.get("reactions", []) if reaction.get("reported_term")
            )[:1000],
            seriousness=normalize_seriousness(message, parsed.get("reactions", [])),
            recommended_next_action=(
                "No further PV ARS processing. Include in partner reconciliation as "
                "not PV system ARS LLC / не ФН АРС if needed."
            ),
            validity_assessment="Marked as not PV system ARS LLC because no company product was matched.",
            gpt_json_output=batch.normalized_json or json_dumps(parsed),
            human_confirmed=True,
            status="closed_no_case",
            triage_status="completed",
            safety_relevance="not_safety_related",
            potential_icsr="no",
            validity_status="not_applicable",
            has_identifiable_patient=bool(parsed.get("patient", {}).get("initials") or parsed.get("patient", {}).get("identifier")),
            has_identifiable_reporter=bool(parsed.get("reporter")),
            has_suspect_product=False,
            has_adverse_event=bool(parsed.get("reactions")),
            triage_result="no_case_required",
            triage_comment="not PV system ARS LLC / не ФН АРС",
            triaged_by=marked_by_user_id,
            triaged_at=utcnow(),
            created_by=marked_by_user_id,
        )
        db.add(row)
        db.flush()
        return row

    def create_case(
        self,
        db: Session,
        batch: E2BImportBatch,
        parsed: dict[str, Any],
        partner_id: str | None,
        safety_report_id: str,
    ) -> Case:
        message = parsed["message"]
        first_received = date_field(message.get("first_received_date")) or date_field(message.get("company_received_date")) or date.today()
        latest_received = date_field(message.get("latest_received_date")) or first_received
        case = Case(
            case_number="",
            worldwide_case_id=message.get("worldwide_case_id"),
            local_sender_case_id=message.get("local_sender_case_id"),
            safety_report_id=safety_report_id,
            partner_id=partner_id,
            case_version=1,
            case_type="spontaneous",
            report_type="e2b",
            initial_received_date=first_received,
            latest_received_date=latest_received,
            country_of_occurrence=message.get("country"),
            source_country_code=message.get("sender_country"),
            seriousness=normalize_seriousness(message, parsed.get("reactions", [])),
            narrative=parsed.get("narrative", {}).get("medical_narrative"),
            company_comment=parsed.get("narrative", {}).get("company_comment"),
            data_review_status="requires_review",
            workflow_status="data_entry",
            e2b_import_batch_id=batch.id,
        )
        assign_case_number(db, case, partner_id=partner_id)
        db.add(case)
        db.flush()
        return case

    def add_followup_for_existing_case(self, db: Session, case: Case, parsed: dict[str, Any]) -> None:
        message = parsed["message"]
        received = date_field(message.get("latest_received_date")) or date_field(message.get("company_received_date"))
        case.case_version += 1
        if received:
            case.latest_received_date = received
        db.add(
            CaseNarrative(
                case_id=case.id,
                narrative_type="e2b_followup_snapshot",
                narrative_text=parsed.get("narrative", {}).get("medical_narrative"),
                source="e2b_import",
            )
        )

    def apply_case_details(self, db: Session, batch: E2BImportBatch, case: Case, parsed: dict[str, Any]) -> None:
        patient_data = parsed.get("patient", {})
        if patient_data and not case.patients:
            db.add(
                Patient(
                    case_id=case.id,
                    patient_initials=patient_data.get("initials"),
                    patient_identifier=patient_data.get("identifier"),
                    sex=patient_data.get("sex"),
                    date_of_birth=date_field(patient_data.get("birth_date")),
                    birth_date_raw=patient_data.get("birth_date", {}).get("raw"),
                    date_of_birth_precision=patient_data.get("birth_date", {}).get("precision"),
                    age_value=patient_data.get("age_value"),
                    age_unit=patient_data.get("age_unit"),
                    age_group=patient_data.get("age_group"),
                    weight_kg=patient_data.get("weight"),
                    height_cm=patient_data.get("height"),
                    country_code=patient_data.get("country"),
                    medical_history_text=patient_data.get("medical_history"),
                    death_cause=patient_data.get("death_cause"),
                    death_date=date_field(patient_data.get("death_date")),
                    death_date_raw=patient_data.get("death_date", {}).get("raw"),
                    death_date_precision=patient_data.get("death_date", {}).get("precision"),
                    import_batch_id=batch.id,
                )
            )
        for reaction_data in parsed.get("reactions", []):
            db.add(
                Reaction(
                    case_id=case.id,
                    reported_term=reaction_data.get("reported_term") or "Unknown reaction",
                    verbatim_term=reaction_data.get("verbatim_term"),
                    meddra_llt_code=reaction_data.get("meddra_llt_code"),
                    meddra_llt_name=reaction_data.get("meddra_llt_name"),
                    meddra_pt_code=reaction_data.get("meddra_pt_code"),
                    meddra_pt_name=reaction_data.get("meddra_pt_name"),
                    meddra_version=reaction_data.get("meddra_version"),
                    term_language=reaction_data.get("term_language"),
                    term_country_code=reaction_data.get("term_country_code"),
                    onset_date=date_field(reaction_data.get("onset_date")),
                    onset_date_raw=reaction_data.get("onset_date", {}).get("raw"),
                    onset_date_precision=reaction_data.get("onset_date", {}).get("precision"),
                    end_date=date_field(reaction_data.get("end_date")),
                    end_date_raw=reaction_data.get("end_date", {}).get("raw"),
                    end_date_precision=reaction_data.get("end_date", {}).get("precision"),
                    duration_text=reaction_data.get("duration_text"),
                    outcome=reaction_data.get("outcome"),
                    is_serious=bool(reaction_data.get("is_serious")),
                    seriousness_death=bool(reaction_data.get("seriousness_death")),
                    seriousness_life_threatening=bool(reaction_data.get("seriousness_life_threatening")),
                    seriousness_hospitalization=bool(reaction_data.get("seriousness_hospitalization")),
                    seriousness_disability=bool(reaction_data.get("seriousness_disability")),
                    seriousness_congenital_anomaly=bool(reaction_data.get("seriousness_congenital_anomaly")),
                    seriousness_other_medically_important=bool(reaction_data.get("seriousness_other_medically_important")),
                    medically_confirmed=reaction_data.get("medically_confirmed"),
                    reporter_comment=reaction_data.get("comment"),
                    null_flavor=reaction_data.get("null_flavor"),
                    import_batch_id=batch.id,
                )
            )
        for product_data in parsed.get("products", []):
            product_id, product_match = self.matching.find_product(db, product_data)
            case_product = CaseProduct(
                case_id=case.id,
                product_id=product_id,
                reported_product_name=product_data.get("reported_product_name"),
                normalized_product_name=product_data.get("normalized_product_name"),
                active_substance_text=", ".join(product_data.get("active_substances") or []),
                drug_role=product_data.get("drug_role") or "unknown",
                product_match_status=product_match,
                indication_text=product_data.get("indication"),
                dose_value=product_data.get("dose_value"),
                dose_unit=product_data.get("dose_unit"),
                route=product_data.get("route"),
                frequency=product_data.get("frequency"),
                dosage_form=product_data.get("dosage_form"),
                therapy_start_date=date_field(product_data.get("therapy_start_date")),
                therapy_start_date_raw=product_data.get("therapy_start_date", {}).get("raw"),
                therapy_start_date_precision=product_data.get("therapy_start_date", {}).get("precision"),
                therapy_end_date=date_field(product_data.get("therapy_end_date")),
                therapy_end_date_raw=product_data.get("therapy_end_date", {}).get("raw"),
                therapy_end_date_precision=product_data.get("therapy_end_date", {}).get("precision"),
                duration_text=product_data.get("duration_text"),
                batch_lot_number=product_data.get("batch_lot_number"),
                expiry_date=date_field(product_data.get("expiry_date")),
                expiry_date_raw=product_data.get("expiry_date", {}).get("raw"),
                authorization_number=product_data.get("authorization_number"),
                authorization_country_code=product_data.get("authorization_country_code"),
                authorization_holder=product_data.get("authorization_holder"),
                action_taken=product_data.get("action_taken"),
                dechallenge_result=product_data.get("dechallenge_result"),
                rechallenge_result=product_data.get("rechallenge_result"),
                causality_text=product_data.get("causality_text"),
                null_flavor=product_data.get("null_flavor"),
                import_batch_id=batch.id,
            )
            db.add(case_product)
            db.flush()
            substance_match_statuses = []
            for substance_name in product_data.get("active_substances") or []:
                substance_id, match_status = self.matching.find_substance(db, substance_name)
                substance_match_statuses.append(match_status)
                db.add(
                    CaseDrugSubstance(
                        case_product_id=case_product.id,
                        substance_id=substance_id,
                        reported_substance_name=substance_name,
                        normalized_substance_name=substance_name,
                        match_status=match_status,
                        import_batch_id=batch.id,
                    )
                )
            case_product.substance_match_status = (
                "exact" if substance_match_statuses and all(status == "exact" for status in substance_match_statuses) else "not_found"
            )
        literature = parsed.get("literature", {})
        if literature.get("is_literature"):
            db.add(
                CaseLiteratureReference(
                    case_id=case.id,
                    title=literature.get("title"),
                    authors=literature.get("authors"),
                    journal=literature.get("journal"),
                    publication_year=int(literature["year"]) if str(literature.get("year") or "").isdigit() else None,
                    volume=literature.get("volume"),
                    issue=literature.get("issue"),
                    pages=literature.get("pages"),
                    doi=literature.get("doi"),
                    pmid=literature.get("pmid"),
                    citation=literature.get("citation"),
                    additional_source_text=literature.get("additional_source_text"),
                    import_batch_id=batch.id,
                )
            )
        narrative = parsed.get("narrative", {})
        if narrative.get("medical_narrative"):
            db.add(
                CaseNarrative(
                    case_id=case.id,
                    narrative_type="medical_narrative",
                    narrative_text=narrative.get("medical_narrative"),
                    source="e2b_import",
                    import_batch_id=batch.id,
                )
            )

    def link_incoming_request(
        self,
        db: Session,
        incoming_request: IncomingRequest,
        case: Case,
        link_type: str,
    ) -> None:
        incoming_request.case_id = case.id
        incoming_request.status = "converted_to_case" if link_type == "created_from_message" else "linked_to_case"
        db.add(
            IncomingRequestCaseLink(
                incoming_request_id=incoming_request.id,
                case_id=case.id,
                link_type=link_type,
                comment="Linked from confirmed E2B import.",
            )
        )

    def save_attachments(
        self,
        db: Session,
        batch: E2BImportBatch,
        case: Case,
        safety_report: SafetyReport,
        *,
        embedded_dir: Path,
    ) -> None:
        checksums_to_attachment: dict[str, Attachment] = {}
        for item in batch.items:
            if item.file_role not in {"xml", "pdf", "cioms", "literature_pdf", "other"}:
                continue
            duplicate = checksums_to_attachment.get(item.checksum_sha256 or "")
            attachment = Attachment(
                case_id=case.id,
                safety_report_id=safety_report.id,
                attachment_type=item.file_role,
                file_name=item.file_name,
                document_title=item.file_name,
                document_type=item.file_role,
                related_object_type="E2BImportBatch",
                related_object_id=batch.id,
                mime_type=item.mime_type,
                file_size_bytes=item.size_bytes,
                storage_path=item.stored_path,
                status="imported",
                checksum_sha256=item.checksum_sha256,
                import_batch_id=batch.id,
                e2b_source="zip_file",
                duplicate_of_attachment_id=duplicate.id if duplicate else None,
                uploaded_at=utcnow(),
            )
            db.add(attachment)
            db.flush()
            if item.checksum_sha256 and item.checksum_sha256 not in checksums_to_attachment:
                checksums_to_attachment[item.checksum_sha256] = attachment

        upload_root = UPLOAD_ROOT.resolve()
        resolved_embedded_dir = embedded_dir.resolve()
        if upload_root not in resolved_embedded_dir.parents:
            raise ValueError("Embedded attachment path is outside E2B import storage.")
        for item in batch.items:
            if item.file_role != "xml" or item.status != "parsed" or not item.normalized_json:
                continue
            normalized = json.loads(item.normalized_json)
            for document in normalized.get("embedded_documents") or []:
                checksum = clean_text(document.get("sha256"))
                duplicate = checksums_to_attachment.get(checksum or "")
                file_name = safe_file_name(document.get("file_name"), "embedded-document.bin")
                storage_path = duplicate.storage_path if duplicate else None
                status = "requires_review" if document.get("too_large") else "imported"
                if not duplicate and document.get("content_b64"):
                    try:
                        content = base64.b64decode(document["content_b64"], validate=True)
                    except (binascii.Error, ValueError) as exc:
                        raise ValueError(f"Embedded E2B document is not valid Base64: {file_name}") from exc
                    if checksum and sha256_bytes(content) != checksum:
                        raise ValueError(f"Embedded E2B document checksum mismatch: {file_name}")
                    target = embedded_dir / item.id / file_name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    resolved_target = target.resolve()
                    if resolved_embedded_dir not in resolved_target.parents:
                        raise ValueError("Embedded E2B document path is outside import storage.")
                    target.write_bytes(content)
                    storage_path = str(target)
                attachment = Attachment(
                    case_id=case.id,
                    safety_report_id=safety_report.id,
                    attachment_type="e2b_embedded_document",
                    file_name=file_name,
                    document_title=file_name,
                    document_type="e2b_embedded_document",
                    related_object_type="E2BImportItem",
                    related_object_id=item.id,
                    mime_type=document.get("mime_type"),
                    file_size_bytes=document.get("size_bytes"),
                    storage_path=storage_path,
                    status=status,
                    comment="Embedded document exceeded the configured extraction limit." if document.get("too_large") else None,
                    checksum_sha256=checksum,
                    import_batch_id=batch.id,
                    e2b_source="embedded_b64",
                    duplicate_of_attachment_id=duplicate.id if duplicate else None,
                    uploaded_at=utcnow(),
                )
                db.add(attachment)
                db.flush()
                if checksum and checksum not in checksums_to_attachment:
                    checksums_to_attachment[checksum] = attachment

    def patient_summary(self, patient: dict[str, Any]) -> str | None:
        parts = [
            patient.get("initials") or patient.get("identifier"),
            patient.get("sex"),
            f"{patient.get('age_value')} {patient.get('age_unit')}" if patient.get("age_value") else None,
        ]
        return "; ".join(part for part in parts if part) or None


def batch_preview_context(batch: E2BImportBatch) -> dict[str, Any]:
    normalized = json.loads(batch.normalized_json or "{}")
    case_groups = normalized.get("case_groups", [])
    case_summaries = normalized.get("case_summaries") or [
        {
            "case_key": group.get("case_key"),
            "worldwide_case_id": group.get("worldwide_case_id"),
            "local_sender_case_id": group.get("local_sender_case_id"),
            "is_serious": None,
            "has_death": None,
            "expectedness": "requires_assessment",
            "submission_due_date": None,
        }
        for group in case_groups
    ]
    product_summary = normalized.get("product_summary") or {
        "scope_checked": False,
        "total_products": None,
        "has_company_product": None,
        "company_product_count": 0,
        "not_found_count": 0,
        "non_company_product_count": 0,
        "status": "not_checked",
    }
    product_summary.setdefault("scope_checked", True)
    package_summary = normalized.get("package_summary") or {
        "case_count": batch.detected_case_count,
        "serious_case_count": 0,
        "death_case_count": 0,
        "expectedness": "requires_assessment",
        "earliest_submission_due_date": None,
        "pv_scope_status": product_summary.get("status"),
        "has_company_product": product_summary.get("has_company_product"),
    }
    return {
        "files": normalized.get("files", []),
        "case_groups": case_groups,
        "case_summaries": case_summaries,
        "product_summary": product_summary,
        "package_summary": package_summary,
        "limits": normalized.get("limits", {}),
        "blocking_errors": [issue for issue in batch.issues if issue.is_blocking],
        "warnings": [issue for issue in batch.issues if not issue.is_blocking],
    }
