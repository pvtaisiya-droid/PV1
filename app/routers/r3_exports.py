from uuid import uuid4
import json
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import Response, RedirectResponse
from sqlalchemy.orm import Session
from app import models as m, r3_artifacts as service
from app.database import get_db
from app.auth import require_permission
from app.security import require_csrf
from app.templating import templates
from app.unit_of_work import atomic_write

router=APIRouter(dependencies=[Depends(require_permission('view')),Depends(require_permission('export'))])

@router.post('/api/cases/{case_id}/revisions/{revision_id}/r3-artifacts',dependencies=[Depends(require_permission('icsr_workflow'))])
@atomic_write
def prepare(case_id:str,revision_id:str,payload:service.PrepareRequest,db:Session=Depends(get_db)):
    try:return service.read(service.prepare(db,case_id,revision_id,payload))
    except ValueError as exc:raise HTTPException(400,str(exc)) from None

@router.get('/api/cases/{case_id}/r3-artifacts/{artifact_id}')
def artifact(case_id:str,artifact_id:str,db:Session=Depends(get_db)):
    return service.read(service.artifact(db,case_id,artifact_id))

@router.get('/api/cases/{case_id}/r3-artifacts/{artifact_id}/download')
def download(case_id:str,artifact_id:str,db:Session=Depends(get_db)):
    row=service.artifact(db,case_id,artifact_id)
    return Response(service.verified_bytes(row),media_type='application/xml',headers={
        'Content-Disposition':f'attachment; filename="pv1-r3-{row.id}.xml"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

@router.get('/cases/{case_id}/r3-export')
def page(case_id:str,request:Request,revision_id:str|None=None,artifact_id:str|None=None,db:Session=Depends(get_db)):
    case=db.get(m.Case,case_id)
    if not case or case.is_deleted:raise HTTPException(404,'Случай не найден.')
    revisions=db.query(m.IcsrRevision).filter_by(case_id=case_id).order_by(m.IcsrRevision.revision_number.desc()).all()
    source=service.revision(db,case_id,revision_id) if revision_id else None
    result=service.read(service.artifact(db,case_id,artifact_id)) if artifact_id else None
    artifacts=db.query(m.IcsrExportArtifact).filter_by(case_id=case_id).order_by(m.IcsrExportArtifact.created_at.desc()).all()
    return templates.TemplateResponse(request,'r3_export.html',dict(request=request,case=case,revisions=revisions,source=source,
        artifacts=[service.read(r) for r in artifacts],result=result,key=str(uuid4()),active_page='cases'))

@router.post('/cases/{case_id}/r3-export',dependencies=[Depends(require_permission('icsr_workflow')),Depends(require_csrf)])
@atomic_write
async def form(case_id:str,request:Request,db:Session=Depends(get_db)):
    data=dict(await request.form());data.pop('csrf',None);revision_id=data.pop('revision_id','')
    for key in ('batch_id','message_id','previous_artifact_id'):
        if not data.get(key):data[key]=None
    try:
        payload=service.PrepareRequest(**data)
        result=service.prepare(db,case_id,revision_id,payload)
    except ValueError as exc:raise HTTPException(400,str(exc)) from None
    return RedirectResponse(f'/cases/{case_id}/r3-export?artifact_id={result.id}',status_code=303)
