from uuid import uuid4
from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import RedirectResponse,Response
from sqlalchemy.orm import Session
from app import models as m,as2_queue as service
from app.as2_settings import registry,public
from app.database import get_db
from app.auth import require_permission
from app.security import require_csrf
from app.templating import templates
from app.unit_of_work import atomic_write

router=APIRouter(dependencies=[Depends(require_permission('view')),Depends(require_permission('export'))])


@router.post('/api/cases/{case_id}/r3-artifacts/{artifact_id}/as2',dependencies=[Depends(require_permission('icsr_workflow'))])
@atomic_write
def enqueue(case_id:str,artifact_id:str,payload:service.Enqueue,db:Session=Depends(get_db)):
    try:return service.serialize(db,service.enqueue(db,case_id,artifact_id,payload))
    except ValueError as exc:raise HTTPException(400,str(exc)) from None


@router.get('/api/cases/{case_id}/as2/{dispatch_id}')
def read(case_id:str,dispatch_id:str,db:Session=Depends(get_db)):
    return service.serialize(db,service.owned(db,case_id,dispatch_id))


@router.post('/api/cases/{case_id}/as2/{dispatch_id}/{action}',dependencies=[Depends(require_permission('icsr_workflow'))])
@atomic_write
def action(case_id:str,dispatch_id:str,action:str,payload:service.Action,db:Session=Depends(get_db)):
    return service.serialize(db,service.action(db,case_id,dispatch_id,action,payload))


@router.get('/api/cases/{case_id}/as2/{dispatch_id}/events/{event_id}/{kind}')
def archive(case_id:str,dispatch_id:str,event_id:str,kind:str,db:Session=Depends(get_db)):
    service.owned(db,case_id,dispatch_id);row=db.get(m.AS2Event,event_id)
    if kind not in ('raw','payload') or not row or row.dispatch_id!=dispatch_id:raise HTTPException(404,'Архив не принадлежит заданию.')
    try:raw=service.evidence(row,kind)
    except ValueError as exc:raise HTTPException(409,str(exc)) from None
    return Response(raw,media_type='application/octet-stream',headers={'Content-Disposition':f'attachment; filename="as2-{event_id}-{kind}.bin"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


@router.get('/as2')
def monitor(request:Request,db:Session=Depends(get_db)):
    rows=db.query(m.AS2Dispatch).order_by(m.AS2Dispatch.created_at.desc()).limit(50).all()
    unknown=db.query(m.AS2Event).filter(m.AS2Event.dispatch_id.is_(None)).order_by(m.AS2Event.created_at.desc()).limit(50).all()
    return templates.TemplateResponse(request,'as2.html',dict(request=request,case=None,rows=[service.serialize(db,r) for r in rows],unknown=unknown,artifacts=[],selected=None,settings=[],config_error=None,key=str(uuid4()),active_page='cases'))


@router.get('/api/as2/unmatched/{event_id}/{kind}')
def unmatched_archive(event_id:str,kind:str,db:Session=Depends(get_db)):
    row=db.get(m.AS2Event,event_id)
    if kind not in ('raw','payload') or not row or row.dispatch_id is not None:raise HTTPException(404,'Несопоставленный архив не найден.')
    try:raw=service.evidence(row,kind)
    except ValueError as exc:raise HTTPException(409,str(exc)) from None
    return Response(raw,media_type='application/octet-stream',headers={'Content-Disposition':f'attachment; filename="as2-{event_id}-{kind}.bin"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


@router.get('/cases/{case_id}/as2')
def page(case_id:str,request:Request,artifact_id:str|None=None,db:Session=Depends(get_db)):
    case=db.get(m.Case,case_id)
    if not case or case.is_deleted:raise HTTPException(404,'Случай не найден.')
    artifacts=db.query(m.IcsrExportArtifact).filter_by(case_id=case_id,locally_valid=True).order_by(m.IcsrExportArtifact.created_at.desc()).all()
    selected=service.r3_artifacts.artifact(db,case_id,artifact_id) if artifact_id else None
    error=None;settings=[]
    try:settings=[public(c) for c in registry()['versions']]
    except ValueError as exc:error=str(exc)
    rows=db.query(m.AS2Dispatch).filter_by(case_id=case_id).order_by(m.AS2Dispatch.created_at.desc()).all()
    return templates.TemplateResponse(request,'as2.html',dict(request=request,case=case,rows=[service.serialize(db,r) for r in rows],unknown=[],artifacts=artifacts,selected=selected,settings=settings,config_error=error,key=str(uuid4()),active_page='cases'))


@router.post('/cases/{case_id}/as2/enqueue',dependencies=[Depends(require_permission('icsr_workflow')),Depends(require_csrf)])
@atomic_write
async def form_enqueue(case_id:str,request:Request,db:Session=Depends(get_db)):
    data=dict(await request.form());data.pop('csrf',None);artifact_id=data.pop('artifact_id','')
    try:service.enqueue(db,case_id,artifact_id,service.Enqueue(**data))
    except ValueError as exc:raise HTTPException(400,str(exc)) from None
    return RedirectResponse(f'/cases/{case_id}/as2',status_code=303)


@router.post('/cases/{case_id}/as2/{dispatch_id}/{action}',dependencies=[Depends(require_permission('icsr_workflow')),Depends(require_csrf)])
@atomic_write
async def form_action(case_id:str,dispatch_id:str,action:str,request:Request,db:Session=Depends(get_db)):
    data=dict(await request.form());data.pop('csrf',None)
    try:service.action(db,case_id,dispatch_id,action,service.Action(**data))
    except ValueError as exc:raise HTTPException(400,str(exc)) from None
    return RedirectResponse(f'/cases/{case_id}/as2',status_code=303)
