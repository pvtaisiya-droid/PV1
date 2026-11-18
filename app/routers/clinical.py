"""Clinical records, revision and managed incoming-message workflow."""
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, Depends, Request, HTTPException, Query, Body, Form
from sqlalchemy.orm import Session
from app import models as m, clinical_data as data, clinical_messages as messages, clinical_revision as revisions
from app.clinical_values import ClinicalWrite, CONTRACT
from app.auth import require_permission, require_any_permission
from app.database import get_db
from app.security import require_csrf
from app.unit_of_work import atomic_write
from app.templating import templates
from app.ui_helpers import redirect_with_message
from app.clinical_presentation import form_raw

router = APIRouter()


class PlanWrite(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mappings: dict[str, str]
    reason: str = Field(min_length=10, max_length=2000)
    sequence_reason: str = Field(default='', max_length=2000)
    attachment_item_ids: list[str] = Field(default_factory=list)
    product_choices: dict[str, dict[str, str]] = Field(default_factory=dict)


def case(db, cid):
    row = db.get(m.Case, cid)
    if not row or row.is_deleted:
        raise HTTPException(404, "Случай не найден.")
    return row


def read_records(db, cid):
    case(db, cid)
    return [data.serialize(r) for r in db.query(m.CaseClinicalRecord).filter_by(case_id=cid).order_by(m.CaseClinicalRecord.created_at)]


@router.get('/api/cases/{case_id}/clinical', dependencies=[Depends(require_permission('view'))])
def api_read(case_id: str, db: Session = Depends(get_db)):
    return dict(records=read_records(db, case_id), validation=revisions.validate(revisions.snapshot(db, case_id)), contract=CONTRACT)


@router.post('/api/cases/{case_id}/clinical/{kind}', dependencies=[Depends(require_permission('create'))])
@atomic_write
def api_create(case_id: str, kind: str, payload: ClinicalWrite, expected_case_version: int = Query(..., gt=0), db: Session = Depends(get_db)):
    try:
        return data.serialize(data.save(db, case_id, kind, payload, expected_case_version))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.patch('/api/cases/{case_id}/clinical/{kind}/{row_id}', dependencies=[Depends(require_permission('edit'))])
@atomic_write
def api_patch(case_id: str, kind: str, row_id: str, payload: ClinicalWrite, expected_case_version: int = Query(..., gt=0), expected_version: int = Query(..., gt=0), db: Session = Depends(get_db)):
    try:
        return data.serialize(data.save(db, case_id, kind, payload, expected_case_version, row_id=row_id, expected_version=expected_version))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post('/api/cases/{case_id}/clinical/{kind}/{row_id}/archive', dependencies=[Depends(require_permission('soft_delete'))])
@atomic_write
def api_archive(case_id: str, kind: str, row_id: str, payload: ClinicalWrite, expected_case_version: int = Query(..., gt=0), expected_version: int = Query(..., gt=0), db: Session = Depends(get_db)):
    try:
        return data.serialize(data.save(db, case_id, kind, payload, expected_case_version, row_id=row_id, expected_version=expected_version, archive=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get('/cases/{case_id}/clinical', dependencies=[Depends(require_permission('view'))])
def page(case_id: str, request: Request, kind: str | None = None, row_id: str | None = None, db: Session = Depends(get_db)):
    owner = case(db, case_id)
    if kind and kind not in CONTRACT:
        raise HTTPException(404, "Блок не найден.")
    records = read_records(db, case_id)
    row = next((r for r in records if r['id'] == row_id and r['kind'] == kind and not r['is_deleted']), None)
    if row_id and not row:
        raise HTTPException(404, "Запись не принадлежит этому блоку случая.")
    return templates.TemplateResponse(request, 'clinical.html', dict(request=request, case=owner, contract=CONTRACT,
        records=records, kind=kind, row=row or {}, fields=CONTRACT[kind]['fields'] if kind else {},
        products=db.query(m.CaseProduct).filter_by(case_id=case_id, is_deleted=False).all(),
        reactions=db.query(m.Reaction).filter_by(case_id=case_id, is_deleted=False).all(),
        attachments=db.query(m.Attachment).filter_by(case_id=case_id, is_deleted=False).all(),
        revisions=db.query(m.IcsrRevision).filter_by(case_id=case_id).order_by(m.IcsrRevision.revision_number.desc()).all(),
        validation=revisions.validate(revisions.snapshot(db, case_id)), active_page='cases'))


@router.post('/cases/{case_id}/clinical/{kind}', dependencies=[Depends(require_csrf),
    Depends(require_any_permission('create', 'edit', 'soft_delete'))])
@atomic_write
async def form_save(case_id: str, kind: str, request: Request, db: Session = Depends(get_db)):
    raw = await request.form()
    row_id = raw.get('row_id') or None
    archive = raw.get('action') == 'archive'
    require_permission('soft_delete' if archive else 'edit' if row_id else 'create')(request)
    if kind not in CONTRACT:
        raise HTTPException(404, "Блок не найден.")
    # Only explicitly checked fields change. Empty strings on untouched controls never clear data.
    links = {key: raw.get(key) or None for key in ('parent_id', 'case_product_id', 'reaction_id', 'attachment_id') if raw.get('change:' + key)}
    try:
        current=next((r for r in read_records(db,case_id) if r['id']==row_id and r['kind']==kind),{})
        values = {key: (None if raw.get('clear:' + key) else dict(raw=None if raw.get('nf:'+key) else form_raw(raw,kind,key,current.get('fields',{}).get(key)),
            null_flavor=raw.get('nf:' + key) or None, system=raw.get('system:' + key) or None,
            system_version=raw.get('version:' + key) or None)) for key in CONTRACT[kind]['fields'] if raw.get('change:' + key)}
        payload = ClinicalWrite(fields=values, change_reason=raw.get('change_reason', ''), **links)
        data.save(db, case_id, kind, payload, int(raw['expected_case_version']), row_id=row_id,
                  expected_version=int(raw['expected_version']) if row_id else None, archive=archive)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return redirect_with_message(f'/cases/{case_id}/clinical', message='Сохранено с аудитом.')


@router.get('/api/cases/{case_id}/revisions', dependencies=[Depends(require_permission('view'))])
def revision_list(case_id: str, db: Session = Depends(get_db)):
    case(db, case_id)
    return [dict(id=r.id, revision_number=r.revision_number, checksum=r.checksum_sha256, reason=r.reason, created_at=r.created_at)
            for r in db.query(m.IcsrRevision).filter_by(case_id=case_id).order_by(m.IcsrRevision.revision_number)]


@router.get('/api/cases/{case_id}/revisions/{revision_id}', dependencies=[Depends(require_permission('view'))])
def revision_read(case_id: str, revision_id: str, db: Session = Depends(get_db)):
    case(db, case_id)
    row = db.get(m.IcsrRevision, revision_id)
    if not row or row.case_id != case_id:
        raise HTTPException(404, 'Ревизия не найдена.')
    return dict(id=row.id, snapshot=json.loads(row.snapshot_json), checksum=row.checksum_sha256, validation=json.loads(row.validation_json))


@router.get('/cases/{case_id}/revisions/{revision_id}', dependencies=[Depends(require_permission('view'))])
def revision_page(case_id:str,revision_id:str,request:Request,db:Session=Depends(get_db)):
    result=revision_read(case_id,revision_id,db)
    return templates.TemplateResponse(request,'clinical_revision.html',dict(request=request,case=case(db,case_id),revision=result,active_page='cases'))


@router.post('/api/cases/{case_id}/revisions', dependencies=[Depends(require_permission('edit'))])
@atomic_write
def revision_create(case_id: str, reason: str = Body(..., embed=True), expected_case_version: int = Query(..., gt=0), db: Session = Depends(get_db)):
    try:
        row = revisions.create(db, case_id, reason, expected_case_version=expected_case_version)
        db.commit()
        return dict(id=row.id, revision_number=row.revision_number)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post('/cases/{case_id}/revisions', dependencies=[Depends(require_permission('edit')), Depends(require_csrf)])
@atomic_write
def revision_form(case_id: str, reason: str = Form(...), expected_case_version: int = Form(...), db: Session = Depends(get_db)):
    try:
        revisions.create(db, case_id, reason, expected_case_version=expected_case_version)
        db.commit()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return redirect_with_message(f'/cases/{case_id}/clinical', message='Создана неизменяемая ревизия.')


@router.get('/api/e2b/batches/{batch_id}/messages', dependencies=[Depends(require_permission('view'))])
def list_messages(batch_id: str, db: Session = Depends(get_db)):
    return [dict(id=r.id, ordinal=r.ordinal, item_id=r.item_id, status=r.status, case_id=r.case_id, issues=json.loads(r.issues_json))
            for r in db.query(m.IcsrMessage).filter_by(batch_id=batch_id, is_deleted=False).order_by(m.IcsrMessage.item_id, m.IcsrMessage.ordinal)]


@router.get('/api/e2b/messages/{message_id}', dependencies=[Depends(require_permission('view'))])
def message_read(message_id: str, db: Session = Depends(get_db)):
    try:
        return messages.inspect_message(db, messages.message(db, message_id))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post('/api/e2b/messages/{message_id}/plans', dependencies=[Depends(require_permission('edit'))])
@atomic_write
def plan_create(message_id: str, request: Request, payload: PlanWrite, db: Session = Depends(get_db)):
    try:
        source = messages.message(db, message_id)
        if source.status == 'review' and messages.existing(db, source) is None:
            require_permission('create')(request)
        row = messages.prepare(db, source, payload.mappings, payload.reason, payload.sequence_reason,
                               payload.attachment_item_ids, payload.product_choices)
        return dict(id=row.id, preview=json.loads(row.preview_json))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post('/api/e2b/plans/{plan_id}/apply', dependencies=[Depends(require_permission('edit'))])
@atomic_write
def plan_apply(plan_id: str, request: Request, choices: dict[str, dict[str, Literal['accept','keep']]] = Body(..., embed=True), db: Session = Depends(get_db)):
    plan = db.get(m.IcsrReviewPlan, plan_id)
    if not plan:
        raise HTTPException(404, 'Просмотр не найден.')
    if plan.case_id is None:
        require_permission('create')(request)
    try:
        return dict(case_id=messages.apply(db, plan, choices))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get('/e2b/batches/{batch_id}/messages', dependencies=[Depends(require_permission('view'))])
def messages_page(batch_id: str, request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, 'clinical_messages.html', dict(request=request, messages=list_messages(batch_id, db), active_page='safety_reports'))


@router.get('/e2b/messages/{message_id}', dependencies=[Depends(require_permission('view'))])
def message_page(message_id: str, request: Request, plan_id: str | None = None, db: Session = Depends(get_db)):
    plan = db.get(m.IcsrReviewPlan, plan_id) if plan_id else None
    if plan_id and (not plan or plan.source_message_id != message_id):
        raise HTTPException(404, 'Просмотр не принадлежит сообщению.')
    try:
        info = messages.inspect_message(db, messages.message(db, message_id))
    except ValueError as exc:
        info = dict(message_id=message_id, status='identity_conflict', issues=[dict(message=str(exc))], nodes=[])
    return templates.TemplateResponse(request, 'clinical_message.html', dict(request=request, info=info, plan=plan,
        preview=json.loads(plan.preview_json) if plan else None, active_page='safety_reports'))


@router.post('/e2b/messages/{message_id}', dependencies=[Depends(require_permission('edit')), Depends(require_csrf)])
@atomic_write
async def message_form(message_id: str, request: Request, db: Session = Depends(get_db)):
    raw = await request.form()
    try:
        if raw.get('plan_id'):
            plan = db.get(m.IcsrReviewPlan, raw['plan_id'])
            if not plan or plan.source_message_id != message_id:
                raise HTTPException(404, 'Просмотр не принадлежит сообщению.')
            if plan.case_id is None:
                require_permission('create')(request)
            choices = {}
            for name, value in raw.items():
                if name.startswith('choice|'):
                    _, key, field = name.split('|', 2)
                    choices.setdefault(key, {})[field] = value
            cid = messages.apply(db, plan, choices)
            return redirect_with_message(f'/cases/{cid}/clinical', message='Сообщение применено; ревизия сохранена.')
        mappings = {k.removeprefix('match:'): v for k, v in raw.items() if k.startswith('match:')}
        source = messages.message(db, message_id)
        if source.status == 'review' and messages.existing(db, source) is None:
            require_permission('create')(request)
        products = {k.removeprefix('product:'): dict(product_id=v,
                    input_token=raw.get('product-token:' + k.removeprefix('product:'), ''))
                    for k, v in raw.items() if k.startswith('product:') and v}
        plan = messages.prepare(db, source, mappings, raw.get('reason', ''), raw.get('sequence_reason', ''),
                                raw.getlist('attachment_item_ids'), products)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return redirect_with_message(f'/e2b/messages/{message_id}?plan_id={plan.id}', message='Проверьте каждое изменение.')
