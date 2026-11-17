"""Bounded manual review of legacy IDs using an already linked R3 source."""
import hashlib
import json
from pathlib import Path

from sqlalchemy import or_
from sqlalchemy.orm.exc import StaleDataError

from app import models
from app.audit import log_audit
from app.concurrency import protect_snapshot, require_version
from app.e2b_r3_identity import PROFILE

MANUAL_PROFILE = "manual-r3-identity-v1"
INPUTS = ("Case", "E2BImportBatch", "E2BImportItem", "E2BTransmission", "CaseIdentityDecision")
FIELDS = ("worldwide_case_id", "local_sender_case_id", "e2b_sender_scope", "e2b_identity_profile")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def current_values(case):
    return {key: getattr(case, key) for key in FIELDS}


def interpretation(message):
    if (message.get("identity_profile") != PROFILE
            or any(w.get("blocking") for w in message.get("identity_warnings", []))
            or not all(message.get(k) for k in ("worldwide_case_id", "local_sender_case_id", "sender_scope"))):
        raise ValueError("Недостаточно доказательств: нужен однозначный R3 с C.1.1, C.1.8.1 и пространством отправителя.")
    return dict(worldwide_case_id=message["worldwide_case_id"],
                local_sender_case_id=message["local_sender_case_id"],
                e2b_sender_scope=message["sender_scope"], e2b_identity_profile=MANUAL_PROFILE)


def read_source(item):
    from app.e2b_import import UPLOAD_ROOT, E2BParser
    if item is None or item.is_deleted or not item.stored_path:
        raise ValueError("Нет доступного исходного R3; требуется исходник, уже связанный с историческим случаем.")
    path = Path(item.stored_path).resolve()
    if not path.is_relative_to(UPLOAD_ROOT.resolve()) or not path.is_file():
        raise ValueError("Связанный исходный файл недоступен в защищённом хранилище.")
    content = path.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    if checksum != item.checksum_sha256:
        raise ValueError("Контрольная сумма исходника изменилась; решение не применяется.")
    message = E2BParser().parse(content).message
    return path, checksum, message


def evidence(tx, case_id):
    if (tx is None or tx.is_deleted or tx.case_id != case_id or tx.item is None
            or tx.batch.is_deleted or tx.item.batch_id != tx.batch_id):
        raise ValueError("Нет подходящего исходника, связанного с выбранным случаем.")
    path, checksum, message = read_source(tx.item)
    if checksum != tx.xml_checksum_sha256:
        raise ValueError("Исходник не согласован с сохранённой передачей; требуется расследование.")
    return path, checksum, message, interpretation(message)


def history(db, case_id):
    return db.query(models.CaseIdentityDecision).filter_by(case_id=case_id).order_by(
        models.CaseIdentityDecision.case_version_before.desc()).all()


def validate_manual_case(db, case):
    """Manual provenance never relaxes the ordinary matching rules."""
    rows = history(db, case.id)
    if not rows or json.loads(rows[0].confirmed_json) != current_values(case):
        raise ValueError("Ручное решение не согласовано с текущими ID; сопоставление остановлено.")
    row = rows[0]
    tx = db.get(models.E2BTransmission, row.evidence_transmission_id)
    _, checksum, _, values = evidence(tx, case.id)
    if checksum != row.evidence_checksum or values != json.loads(row.confirmed_json):
        raise ValueError("Доказательство ручного решения изменилось; сопоставление остановлено.")


def review_context(db, batch):
    from app.e2b_import import E2BImportService
    messages, source_errors = [], []
    for item in batch.items:
        if item.is_deleted or item.file_role != "xml":
            continue
        try:
            _, _, message = read_source(item)
            interpretation(message)
            stored = json.loads(item.normalized_json or "{}").get("message", {})
            if interpretation(stored) != interpretation(message):
                raise ValueError("Предпросмотр не согласован с исходным XML; перепроверьте пакет.")
            messages.append(message)
        except ValueError as exc:
            source_errors.append(str(exc))
    values = {m[k] for m in messages for k in ("worldwide_case_id", "local_sender_case_id")}
    possible = db.query(models.Case).filter(models.Case.is_deleted.is_(False), or_(
        models.Case.worldwide_case_id.in_(values), models.Case.local_sender_case_id.in_(values))).all() if values else []
    cases = [case for case in possible if case.e2b_identity_profile is None or any(
        case.worldwide_case_id == m["worldwide_case_id"] or
        (case.local_sender_case_id == m["local_sender_case_id"] and case.e2b_sender_scope == m["sender_scope"])
        for m in messages)]
    # Include related aliases without listing unrelated cases.
    for tx in db.query(models.E2BTransmission).filter(models.E2BTransmission.is_deleted.is_(False),
            models.E2BTransmission.local_sender_case_id.in_(values)).all() if values else []:
        alias = json.loads(tx.normalized_json or "{}").get("message", {})
        related = alias.get("identity_profile") == PROFILE and any(
            alias.get("local_sender_case_id") == m["local_sender_case_id"] and
            alias.get("sender_scope") == m["sender_scope"] for m in messages)
        if related and tx.case and not tx.case.is_deleted and tx.case not in cases:
            cases.append(tx.case)
    try:
        E2BImportService().preview_case(db, batch)
        blocking = None
    except ValueError as exc:
        blocking = str(exc)
    candidates = []
    for case in cases:
        sources = []
        for tx in case.e2b_transmissions:
            try:
                _, checksum, message, confirmed = evidence(tx, case.id)
                sources.append(dict(tx=tx, checksum=checksum, message=message, confirmed=confirmed, error=None))
            except ValueError as exc:
                sources.append(dict(tx=tx, error=str(exc)))
        candidates.append(dict(case=case, sources=sources, history=history(db, case.id)))
    return dict(messages=messages, source_errors=source_errors, candidates=candidates, blocking=blocking)


def save_decision(db, *, batch_id, case_id, transmission_id, reason, author_id,
                  expected_case_version, expected_batch_version, expected_snapshot, supersedes_id):
    from app.crud import assert_case_editable
    from app.e2b_import import E2BImportService
    protect_snapshot(db, INPUTS, expected_snapshot)
    batch = db.get(models.E2BImportBatch, batch_id)
    case = db.get(models.Case, case_id)
    if not batch or batch.is_deleted or not case or case.is_deleted:
        raise ValueError("Пакет или случай недоступен.")
    require_version(batch, expected_batch_version)
    require_version(case, expected_case_version)
    if batch.status in ("imported", "cancelled", "not_pv_ars"):
        raise ValueError("Для этого состояния пакета решение не применяется.")
    context = review_context(db, batch)
    if context["source_errors"] or len(context["messages"]) != 1:
        raise ValueError("Для ручного разбора нужен один однозначный входящий R3; перепроверьте пакет.")
    if case_id not in {entry["case"].id for entry in context["candidates"]}:
        raise ValueError("Выбранный случай не относится к конфликту пакета.")
    if len(reason.strip()) < 20:
        raise ValueError("Укажите содержательное основание (не менее 20 символов), объясняющее сверку исходника и исторических ID.")
    tx = db.get(models.E2BTransmission, transmission_id)
    _, checksum, message, confirmed = evidence(tx, case_id)
    incoming = interpretation(context["messages"][0])
    if (incoming["worldwide_case_id"] != confirmed["worldwide_case_id"] or
            (incoming["e2b_sender_scope"] == confirmed["e2b_sender_scope"] and
             incoming["local_sender_case_id"] != confirmed["local_sender_case_id"])):
        raise ValueError("Связанный источник противоречит входящим ID; требуется отдельное расследование.")
    rows = history(db, case_id)
    latest = rows[0] if rows else None
    if (supersedes_id or None) != (latest.id if latest else None):
        raise StaleDataError("Решение уже изменилось. Откройте форму заново.")
    before = current_values(case)
    if case.e2b_identity_profile is not None:
        if case.e2b_identity_profile != MANUAL_PROFILE:
            raise ValueError("Назначение ID уже установлено автоматическим источником; изменение идентичности здесь запрещено.")
        validate_manual_case(db, case)
        if confirmed != before:
            raise ValueError("Изменение уже подтверждённой идентичности требует отдельного расследования.")
    else:
        old_ids = {case.worldwide_case_id, case.local_sender_case_id} - {None, ""}
        source_ids = {confirmed["worldwide_case_id"], confirmed["local_sender_case_id"]}
        if not old_ids or not old_ids.issubset(source_ids):
            raise ValueError("Исторические ID не подтверждаются связанным исходником; автоматическая замена запрещена.")
    for other in db.query(models.Case).filter(models.Case.id != case.id).all():
        if (other.worldwide_case_id == confirmed["worldwide_case_id"] or
                (other.e2b_sender_scope == confirmed["e2b_sender_scope"] and
                 other.local_sender_case_id == confirmed["local_sender_case_id"])):
            raise ValueError("Подтверждаемые ID заняты другим случаем; объединение или создание дубля запрещено.")
    # Check existing parsed sources of this case without reinterpreting their JSON.
    for linked in case.e2b_transmissions:
        known = json.loads(linked.normalized_json or "{}").get("message", {})
        if not linked.is_deleted and known.get("identity_profile") != PROFILE:
            try:
                _, _, known, _ = evidence(linked, case.id)
            except ValueError:
                # Unavailable/uninterpretable evidence cannot confirm a case;
                # the selected evidence must pass the strict check above.
                continue
        if not linked.is_deleted and known.get("identity_profile") == PROFILE:
            if (known.get("worldwide_case_id") not in (None, confirmed["worldwide_case_id"]) or
                    (known.get("sender_scope") == confirmed["e2b_sender_scope"] and
                     known.get("local_sender_case_id") not in (None, confirmed["local_sender_case_id"]))):
                raise ValueError("Другой связанный источник противоречит решению; требуется расследование.")
    version = case.version
    assert_case_editable(case)
    row = models.CaseIdentityDecision(case_id=case.id, case_version_before=version,
        batch_id=batch.id, evidence_transmission_id=tx.id, evidence_checksum=checksum,
        before_json=encoded(before), confirmed_json=encoded(confirmed),
        evidence_json=encoded(dict(parser_profile=PROFILE, identifiers=message["identifiers"],
            item_id=tx.import_item_id, batch_id=tx.batch_id, input_snapshot=expected_snapshot,
            input_batch_version=batch.version, scope="exact case identity; ordinary matching required")),
        reason=reason.strip(), author_id=author_id, supersedes_id=supersedes_id or None)
    for key, value in confirmed.items():
        setattr(case, key, value)
    db.add(row)
    db.flush()
    log_audit(db, entity_type="CaseIdentityDecision", entity_id=row.id, action="confirm_identity",
        case_id=case.id, old_value=encoded(before), new_value=encoded(confirmed),
        change_reason=reason.strip(), user_id=author_id, source_module="identity_review")
    try:
        E2BImportService().preview_case(db, batch)
        result = "Решение сохранено. Сопоставление повторно проверено; импорт подтвердите отдельно."
    except ValueError:
        result = "Решение сохранено. Неоднозначность остаётся; импорт заблокирован, смотрите предпросмотр."
    return row, result
