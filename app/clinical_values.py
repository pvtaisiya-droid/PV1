"""One lexical value contract for clinical fields; no regional validation claims."""
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET
from pydantic import BaseModel, ConfigDict, Field
from app.e2b_r3_identity import source_date

CONTRACT_VERSION = "clinical-fields-v1"
CONTRACT = json.loads(Path(__file__).with_name("clinical_contract.json").read_text("utf-8"))


class ValueInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw: str | None = None
    null_flavor: str | None = None
    system: str | None = None
    system_version: str | None = None


class ClinicalWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fields: dict[str, ValueInput | None] = Field(default_factory=dict)
    parent_id: str | None = None
    case_product_id: str | None = None
    reaction_id: str | None = None
    attachment_id: str | None = None
    change_reason: str = Field(min_length=10, max_length=2000)


def normalize(definition, raw=None, null_flavor=None, *, present=True,
              system=None, system_version=None, origin="manual", context=None):
    result = dict(raw=raw, value=None, null_flavor=null_flavor, system=system,
                  system_version=system_version, origin=origin, state="missing",
                  context=context, dictionary_validation="not_performed")
    if not present:
        return result
    if null_flavor is not None:
        result["state"] = "null_flavor" if raw is None and null_flavor else "invalid"
        return result
    if raw is None:
        result["state"] = "cleared" if origin == "manual" else "invalid"
        return result
    kind = definition["type"]
    result.update(value=raw, state="received_unvalidated" if origin == "source_xml" else "manual")
    if kind == "date":
        date = source_date([ET.Element("value", {"value": raw})], definition.get("xpath"), definition.get("element"))
        result.update({k: date[k] for k in ("state", "precision", "timezone", "parsed")})
        result["value"] = date["parsed"]
    elif kind == "boolean":
        result["value"] = {"true": True, "false": False, "1": True, "0": False}.get(raw)
        if result["value"] is None:
            result["state"] = "invalid"
    elif kind == "decimal":
        try:
            if not Decimal(raw).is_finite():
                raise InvalidOperation()
            if definition.get('element') in {'D.2.2a','D.2.2.1a','D.3','D.4','D.10.2.2a','D.10.3','D.10.4','D.10.5','G.k.5a','G.k.6a','E.i.6a','G.k.9.i.3.1a','G.k.9.i.3.2a'} and Decimal(raw) < 0:
                raise InvalidOperation()
        except InvalidOperation:
            result.update(state="invalid", value=None)
    elif kind in ("json", "quantity", "quantity_unit"):
        try:
            result["value"] = json.loads(raw)
        except (ValueError, TypeError):
            result.update(state="invalid", value=None)
    if not raw.strip() and kind not in ("json",):
        result.update(state="empty", value=None)
    if definition.get('attribute') == 'code' and definition.get('system') == '2.16.840.1.113883.6.163' and not re.fullmatch(r'[0-9]{8}', raw):
        result.update(state='invalid', value=None)
    return result


def meaningful(value):
    if not isinstance(value, dict) or value.get("state") in ("missing", "null_flavor", "invalid", "unsupported", "ambiguous", "empty", "cleared"):
        return False
    raw = value.get("value")
    if isinstance(raw, str):
        return raw.strip().casefold() not in ("", "unknown", "unknown reaction", "n/a", "неизвестно", "не указано")
    if isinstance(raw, (dict, list)):
        def contents(v):
            if isinstance(v, dict):
                return meaningful(v) if "state" in v else any(contents(x) for x in v.values())
            if isinstance(v, list):
                return any(contents(x) for x in v)
            return isinstance(v, (bool, int, float)) or (isinstance(v, str) and bool(v.strip()))
        return contents(raw)
    return raw is not None


def manual_fields(kind, payload, author_id):
    if kind not in CONTRACT:
        raise ValueError("Неизвестный клинический блок.")
    unknown = set(payload) - set(CONTRACT[kind]["fields"])
    if unknown:
        raise ValueError("Поле не принадлежит выбранному клиническому блоку.")
    result = {}
    for key, value in payload.items():
        args = value.model_dump() if value is not None else {}
        result[key] = normalize(CONTRACT[kind]["fields"][key], **args,
                                origin="manual", context={"author_id": author_id, "confirmed_at": datetime.now(timezone.utc).isoformat()})
        if result[key]["state"] == "invalid":
            raise ValueError(f"{key}: некорректное число, дата, boolean или JSON; исходный текст не преобразован.")
        enums = {'local.expectedness': {'expected','unexpected','unknown'}, 'local.reportability': {'submit','not_submit','undecided'},
                 'local.scope': {'registered','study','unknown'}, 'local.blinding': {'blinded','open','unknown'}}
        if key in enums and result[key]['value'] is not None and result[key]['value'] not in enums[key]:
            raise ValueError(f'{key}: выберите одно из внутренних значений: ' + ', '.join(sorted(enums[key])))
        if key == 'local.contacts' and result[key]['value'] is not None:
            contacts = result[key]['value']
            if not isinstance(contacts, list) or any(not isinstance(v, dict) or not isinstance(v.get('value'), str) for v in contacts):
                raise ValueError('Контакты: JSON-массив объектов с исходной строкой value, например [{"value":"tel:..."}].')
    return result


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
