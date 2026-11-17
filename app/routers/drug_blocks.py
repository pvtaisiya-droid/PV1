"""A bounded editor for drug repeats, using the existing cases, RBAC and audit."""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request, Query, Body, Form
from pydantic import ValidationError
from sqlalchemy.orm import Session
from app import models, drug_data
from app.drug_schemas import SCHEMAS, DrugWrite
from app.auth import require_permission
from app.database import get_db
from app.security import require_csrf
from app.unit_of_work import atomic_write
from app.templating import templates
from app.ui_helpers import redirect_with_message
from app.drug_labels import LABELS, ROLES

router=APIRouter()
Kind=Literal['dosage','indication','substance','assessment','rechallenge']
TITLES={'product':'Препарат в случае','dosage':'Режим дозирования','indication':'Показание',
    'substance':'Действующее вещество','assessment':'Оценка причинности','rechallenge':'Повторное назначение (rechallenge)'}


def case_or_404(db,case_id):
    case=db.get(models.Case,case_id)
    if not case or case.is_deleted:raise HTTPException(404,'Case not found')
    return case


def payload_for(kind,values):
    try:return SCHEMAS[kind].model_validate(values)
    except ValidationError as exc:
        raise HTTPException(422,'Проверьте поля, числовые значения и роль препарата; неподдерживаемые поля не принимаются.') from None


@router.get('/api/cases/{case_id}/drug-products',dependencies=[Depends(require_permission('view'))])
def api_list(case_id:str,db:Session=Depends(get_db)):
    case_or_404(db,case_id)
    return [drug_data.read_product(db,p) for p in db.query(models.CaseProduct).filter_by(case_id=case_id,is_deleted=False).all()]


@router.post('/api/cases/{case_id}/drug-products',dependencies=[Depends(require_permission('create'))])
@atomic_write
def api_add(case_id:str,payload:dict=Body(...),expected_case_version:int=Query(...,gt=0),db:Session=Depends(get_db)):
    try:return drug_data.serialize(drug_data.save(db,case_id=case_id,kind='product',payload=payload_for('product',payload),expected_case_version=expected_case_version))
    except ValueError as exc:raise HTTPException(400,str(exc)) from None


@router.get('/api/cases/{case_id}/drug-products/{product_id}',dependencies=[Depends(require_permission('view'))])
def api_read(case_id:str,product_id:str,db:Session=Depends(get_db)):
    case_or_404(db,case_id)
    return drug_data.read_product(db,drug_data.get_parent(db,case_id,product_id))


@router.patch('/api/cases/{case_id}/drug-products/{product_id}',dependencies=[Depends(require_permission('edit'))])
@atomic_write
def api_patch(case_id:str,product_id:str,payload:dict=Body(...),expected_case_version:int=Query(...,gt=0),
              expected_version:int=Query(...,gt=0),db:Session=Depends(get_db)):
    try:return drug_data.serialize(drug_data.save(db,case_id=case_id,kind='product',row_id=product_id,
        payload=payload_for('product',payload),expected_case_version=expected_case_version,expected_version=expected_version))
    except ValueError as exc:raise HTTPException(400,str(exc)) from None


@router.post('/api/cases/{case_id}/drug-products/{product_id}/{kind}',dependencies=[Depends(require_permission('create'))])
@atomic_write
def api_child_add(case_id:str,product_id:str,kind:Kind,payload:dict=Body(...),expected_case_version:int=Query(...,gt=0),
                  expected_parent_version:int=Query(...,gt=0),db:Session=Depends(get_db)):
    try:return drug_data.serialize(drug_data.save(db,case_id=case_id,product_id=product_id,kind=kind,
        payload=payload_for(kind,payload),expected_case_version=expected_case_version,expected_parent_version=expected_parent_version))
    except ValueError as exc:raise HTTPException(400,str(exc)) from None


@router.patch('/api/cases/{case_id}/drug-products/{product_id}/{kind}/{row_id}',dependencies=[Depends(require_permission('edit'))])
@atomic_write
def api_child_patch(case_id:str,product_id:str,kind:Kind,row_id:str,payload:dict=Body(...),expected_case_version:int=Query(...,gt=0),
    expected_parent_version:int=Query(...,gt=0),expected_version:int=Query(...,gt=0),db:Session=Depends(get_db)):
    try:return drug_data.serialize(drug_data.save(db,case_id=case_id,product_id=product_id,kind=kind,row_id=row_id,
        payload=payload_for(kind,payload),expected_case_version=expected_case_version,expected_parent_version=expected_parent_version,expected_version=expected_version))
    except ValueError as exc:raise HTTPException(400,str(exc)) from None


@router.post('/api/cases/{case_id}/drug-products/{product_id}/{kind}/{row_id}/archive',dependencies=[Depends(require_permission('soft_delete'))])
@atomic_write
def api_child_archive(case_id:str,product_id:str,kind:Kind,row_id:str,payload:DrugWrite,
    expected_case_version:int=Query(...,gt=0),expected_parent_version:int=Query(...,gt=0),expected_version:int=Query(...,gt=0),db:Session=Depends(get_db)):
    try:return drug_data.serialize(drug_data.save(db,case_id=case_id,product_id=product_id,kind=kind,row_id=row_id,
        payload=payload,expected_case_version=expected_case_version,expected_parent_version=expected_parent_version,expected_version=expected_version,archive=True))
    except ValueError as exc:raise HTTPException(400,str(exc)) from None


@router.get('/cases/{case_id}/drug-products',dependencies=[Depends(require_permission('view'))])
def drug_page(case_id:str,request:Request,db:Session=Depends(get_db)):
    case=case_or_404(db,case_id)
    products=db.query(models.CaseProduct).filter_by(case_id=case_id,is_deleted=False).all()
    return templates.TemplateResponse(request,'drug_products.html',dict(request=request,case=case,
        products=[drug_data.read_product(db,p) for p in products],titles=TITLES, labels=LABELS, roles=ROLES,
        reaction_names={r.id:r.reported_term for r in db.query(models.Reaction).filter_by(case_id=case_id)},
        catalog_names={p.id:p.product_name for p in db.query(models.Product).filter_by(is_deleted=False)},
        active_page='cases',validation=request.query_params.get('validation'),message=request.query_params.get('message')))


@router.get('/cases/{case_id}/drug-editor/{kind}',dependencies=[Depends(require_permission('view'))])
def editor(case_id:str,kind:Literal['product','dosage','indication','substance','assessment','rechallenge'],request:Request,
    product_id:str|None=None,row_id:str|None=None,db:Session=Depends(get_db)):
    from app.drug_labels import LABELS
    case=case_or_404(db,case_id)
    parent=drug_data.get_parent(db,case_id,product_id) if product_id else None
    row=db.get(drug_data.model_for(kind),row_id) if row_id else None
    if row_id and (not row or row.is_deleted or (row.case_id!=case_id if kind=='product' else not parent or row.case_product_id!=parent.id)):
        raise HTTPException(404,'Record does not belong to selected case/product')
    if kind!='product' and parent is None:raise HTTPException(404,'Parent product required')
    return templates.TemplateResponse(request,'drug_editor.html',dict(request=request,case=case,parent=parent,row=drug_data.serialize(row) if row else {},
        kind=kind,title=TITLES[kind],labels=LABELS,roles=ROLES,fields=[f for f in SCHEMAS[kind].model_fields if f not in ('change_reason','coded_identifiers')],
        reactions=db.query(models.Reaction).filter_by(case_id=case_id,is_deleted=False).all(),
        catalog=db.query(models.Product).filter_by(is_deleted=False).all() if kind=='product' else db.query(models.Substance).filter_by(is_deleted=False).all() if kind=='substance' else [],
        active_page='cases',validation=request.query_params.get('validation')))


@router.post('/cases/{case_id}/drug-editor/{kind}',dependencies=[Depends(require_csrf)])
@atomic_write
async def editor_save(case_id:str,kind:Literal['product','dosage','indication','substance','assessment','rechallenge'],request:Request,
    expected_case_version:int=Form(...,gt=0),product_id:str|None=Form(None),row_id:str|None=Form(None),
    expected_parent_version:int|None=Form(None),expected_version:int|None=Form(None),db:Session=Depends(get_db)):
    require_permission('edit' if row_id else 'create')(request)
    raw=await request.form()
    values={k:(None if v=='' else v) for k,v in raw.items() if k in SCHEMAS[kind].model_fields and k!='coded_identifiers'}
    if kind=='product':
        # An unrelated HTML edit must not silently reinterpret a historical role.
        existing=db.get(models.CaseProduct,row_id) if row_id else None
        if existing and existing.drug_role not in ROLES and values.get('drug_role')==existing.drug_role:
            values.pop('drug_role')
        names=('code','code_system','version','null_flavor','display_name')
        lists=[raw.getlist('identifier_'+n) for n in names]
        values['coded_identifiers']=[dict(zip(names,[v or None for v in row])) for row in zip(*lists) if any(row)]
    try:
        drug_data.save(db,case_id=case_id,kind=kind,payload=payload_for(kind,values),product_id=product_id if kind!='product' else None,row_id=row_id,
            expected_case_version=expected_case_version,expected_parent_version=expected_parent_version,expected_version=expected_version)
    except ValueError as exc:
        return redirect_with_message(f'/cases/{case_id}/drug-products',validation=str(exc))
    return redirect_with_message(f'/cases/{case_id}/drug-products',message='Данные препарата сохранены с аудитом.')


@router.post('/cases/{case_id}/drug-products/{product_id}/{kind}/{row_id}/archive',
    dependencies=[Depends(require_permission('soft_delete')),Depends(require_csrf)])
@atomic_write
def archive_form(case_id:str,product_id:str,kind:Kind,row_id:str,change_reason:str=Form(...),
    expected_case_version:int=Form(...,gt=0),expected_parent_version:int=Form(...,gt=0),expected_version:int=Form(...,gt=0),db:Session=Depends(get_db)):
    try:drug_data.save(db,case_id=case_id,product_id=product_id,kind=kind,row_id=row_id,payload=DrugWrite(change_reason=change_reason),
        expected_case_version=expected_case_version,expected_parent_version=expected_parent_version,expected_version=expected_version,archive=True)
    except ValueError as exc:return redirect_with_message(f'/cases/{case_id}/drug-products',validation=str(exc))
    return redirect_with_message(f'/cases/{case_id}/drug-products',message='Запись архивирована; история и источник сохранены.')
