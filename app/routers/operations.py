from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import Response,RedirectResponse
from sqlalchemy.orm import Session
from app import models as m,operations as service
from app.database import get_db
from app.auth import require_permission
from app.security import require_csrf
from app.templating import templates
from app.unit_of_work import atomic_write
from app.as2_settings import registry

router=APIRouter(dependencies=[Depends(require_permission('view')),Depends(require_permission('export'))])
write=[Depends(require_permission('icsr_workflow'))]

def invoke(fn,*args):
    try:return fn(*args)
    except ValueError as exc:raise HTTPException(400,str(exc)) from None

@router.post('/api/cases/{case_id}/r3-artifacts/{artifact_id}/operations',dependencies=write)
@atomic_write
def open_record(case_id:str,artifact_id:str,payload:service.Open,db:Session=Depends(get_db)):
    return service.read(db,invoke(service.open_submission,db,case_id,artifact_id,payload))

@router.get('/api/cases/{case_id}/operations/{key}')
def read(case_id:str,key:str,db:Session=Depends(get_db)):return service.read(db,service.owned(db,case_id,key))

def do_manual(db,case_id,key,payload):
    # Commit the hold and its audit before the short network reconciliation; no HTTP in DB transaction.
    row=invoke(service.manual,db,case_id,key,payload)
    if payload.operation=='decide':
        dispatch_id=row.dispatch_id;db.commit()
        if dispatch_id:
            from app.as2_worker import sync_hold
            sync_hold(dispatch_id)
        db.expire_all()
    return service.read(db,row)

@router.post('/api/cases/{case_id}/operations/{key}/manual',dependencies=write)
def manual(case_id:str,key:str,payload:service.Manual,db:Session=Depends(get_db)):return do_manual(db,case_id,key,payload)

@router.post('/api/cases/{case_id}/operations/{key}/publication',dependencies=write)
@atomic_write
def publication(case_id:str,key:str,payload:service.Publication,db:Session=Depends(get_db)):
    return service.read(db,invoke(service.publication,db,case_id,key,payload))

@router.post('/api/operations/policies/{config_version}',dependencies=[Depends(require_permission('manage_system_settings'))])
@atomic_write
def policy(config_version:str,payload:service.PolicyWrite,db:Session=Depends(get_db)):
    return service.columns(invoke(service.save_policy,db,config_version,payload))

@router.post('/api/operations/incidents/{key}',dependencies=write)
@atomic_write
def incident(key:str,payload:service.IncidentWrite,db:Session=Depends(get_db)):
    return service.columns(invoke(service.incident_action,db,key,payload))

def download(db,case_id,key,kind,payload):
    from app.concurrency import lock_snapshot_inputs
    from app.crud import audit_user_id
    lock_snapshot_inputs(db,('OpsSubmission',));row=service.owned(db,case_id,key);service.version(row,payload.expected_version)
    if kind=='diagnostic':
        from app.operations_diagnostics import package
        raw=package(db,row);mime='application/zip';filename='diagnostic-'+row.id+'.zip'
    elif kind=='xml':
        raw=service.r3_artifacts.verified_bytes(db.get(m.IcsrExportArtifact,row.artifact_id));mime='application/xml';filename='artifact-'+row.artifact_id+'.xml'
    else:raise HTTPException(404,'Неизвестный вид выгрузки.')
    service.record(db,kind+'_download',payload.reason,subject=row,actor=audit_user_id(db));db.commit()
    return Response(raw,media_type=mime,headers={'Content-Disposition':f'attachment; filename="{filename}"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

@router.post('/api/cases/{case_id}/operations/{key}/download/{kind}')
@atomic_write
def export(case_id:str,key:str,kind:str,payload:service.Write,db:Session=Depends(get_db)):return download(db,case_id,key,kind,payload)

def page_context(db,case_id=None,artifact_id=None):
    case=db.get(m.Case,case_id) if case_id else None
    if case_id and (not case or case.is_deleted):raise HTTPException(404,'Случай не найден.')
    rows=db.query(m.OpsSubmission).filter_by(case_id=case_id).all() if case_id else []
    artifacts=db.query(m.IcsrExportArtifact).filter_by(case_id=case_id,locally_valid=True).all() if case_id else []
    selected=service.r3_artifacts.artifact(db,case_id,artifact_id) if artifact_id else None
    try:configs=registry()['versions'];error=None
    except ValueError as exc:configs=[];error=str(exc)
    return dict(case=case,rows=[service.read(db,r) for r in rows],artifacts=artifacts,selected=selected,configs=configs,config_error=error,
        policies={c['version']:service.policy(db,c['version']) for c in configs},users=db.query(m.User).filter_by(is_deleted=False,is_active=True).all(),
        attachments=db.query(m.Attachment).filter_by(case_id=case_id,is_deleted=False).all() if case_id else [],
        incidents=db.query(m.OpsIncident).order_by(m.OpsIncident.since.desc()).limit(100).all(),incident_history=db.query(m.OpsAction).filter(m.OpsAction.incident_id.is_not(None)).order_by(m.OpsAction.occurred_at).all(),display_time=service.display,active_page='cases')

@router.get('/operations')
@router.get('/cases/{case_id}/operations')
def page(request:Request,case_id:str|None=None,artifact_id:str|None=None,db:Session=Depends(get_db)):
    return templates.TemplateResponse(request,'operations.html',dict(request=request,**page_context(db,case_id,artifact_id)))

@router.post('/cases/{case_id}/operations/open',dependencies=write+[Depends(require_csrf)])
@atomic_write
async def form_open(case_id:str,request:Request,db:Session=Depends(get_db)):
    data=dict(await request.form());data.pop('csrf',None);artifact_id=data.pop('artifact_id')
    try:service.open_submission(db,case_id,artifact_id,service.Open(**data))
    except ValueError as exc:raise HTTPException(400,str(exc)) from None
    return RedirectResponse(f'/cases/{case_id}/operations',303)

@router.post('/cases/{case_id}/operations/{key}/{operation}',dependencies=[Depends(require_csrf)])
async def form_operation(case_id:str,key:str,operation:str,request:Request,db:Session=Depends(get_db)):
    data=dict(await request.form());data.pop('csrf',None)
    data={k:(v or None) for k,v in data.items()}
    try:
        if operation in ('xml','diagnostic'):return download(db,case_id,key,operation,service.Write(**data))
        require_permission('icsr_workflow')(request)
        if operation=='manual':do_manual(db,case_id,key,service.Manual(**data))
        elif operation=='publication':service.publication(db,case_id,key,service.Publication(**data))
        else:raise HTTPException(404,'Неизвестное действие.')
    except ValueError as exc:raise HTTPException(400,str(exc)) from None
    return RedirectResponse(f'/cases/{case_id}/operations',303)

@router.post('/operations/policy/{config_version}',dependencies=[Depends(require_csrf),Depends(require_permission('manage_system_settings'))])
@atomic_write
async def form_policy(config_version:str,request:Request,db:Session=Depends(get_db)):
    data=dict(await request.form());data.pop('csrf',None)
    try:service.save_policy(db,config_version,service.PolicyWrite(**data))
    except ValueError as exc:raise HTTPException(400,str(exc)) from None
    return RedirectResponse('/operations',303)

@router.post('/operations/incident/{key}',dependencies=write+[Depends(require_csrf)])
@atomic_write
async def form_incident(key:str,request:Request,db:Session=Depends(get_db)):
    data=dict(await request.form());data.pop('csrf',None)
    try:service.incident_action(db,key,service.IncidentWrite(**{k:v or None for k,v in data.items()}))
    except ValueError as exc:raise HTTPException(400,str(exc)) from None
    return RedirectResponse('/operations',303)
