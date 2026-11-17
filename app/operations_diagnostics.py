"""Positive allowlist: never copy arbitrary config, clinical snapshots, facts or logs."""
import io
import json
import re
import hashlib
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
from app import models as m,operations as op

ERRORS={'verified','unknown_correlation','mdn_party_mismatch','mdn_signature_or_mic_failed','certificate_or_crypto_failed','message_crypto_failed',
        'protocol_processing_failed','configuration_changed','connection_refused_before_delivery','http_result_unknown','restart_during_http','manual_hold',
        'local_engine_or_artifact_unavailable','handoff_result_unknown_no_blind_repeat','engine_history_replaced','engine_task_conflict','certificate_invalid_before_http'}
def checksum(value):return value if isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) else None
def code(value):return value if value in ERRORS else 'unclassified' if value else None

def package(db,row):
    artifact=db.get(m.IcsrExportArtifact,row.artifact_id);dispatch=db.get(m.AS2Dispatch,row.dispatch_id) if row.dispatch_id else None
    report=dict(format='pv1-operations-diagnostic-1',created_at=op.stamp(m.utcnow()),application='PV1-1.0.0',operations='stage-5-v1',engine='pyas2lib-1.4.4',
        case_id=row.case_id,submission_id=row.id,artifact_id=artifact.id,revision_id=artifact.revision_id,contour=row.contour,config_version=row.config_version,
        exporter=artifact.exporter_version,profile=artifact.profile_id,rules=artifact.rules_version,profile_sha256=checksum(artifact.profile_manifest_sha256),
        xml_sha256=checksum(artifact.xml_sha256),xml_bytes=artifact.byte_length,locally_valid=artifact.locally_valid,xsd_valid=artifact.xsd_valid,
        manual_hold=row.manual_hold,deadline_precision=row.deadline_precision,deadline_utc=op.stamp(row.deadline_utc),deadline_date=row.deadline_raw if row.deadline_precision=='date' else None,
        omissions=['XML','decrypted ACK','documents','free text reasons and source descriptions','URLs/paths','tokens/passwords/private keys','database/logs','patient/reporter identifiers'])
    pol=op.policy(db,row.config_version)
    if pol:report['policy']={k:getattr(pol,k) for k in ('id','number','autopublish','mdn_seconds','ack_seconds','publication_seconds','approaching_seconds','certificate_days','health_seconds','reminder_seconds','reminder_limit')}
    if dispatch:
        cfg=json.loads(dispatch.config_json)
        report['as2_settings']=dict(from_id=cfg['a']['as2_id'],to_id=cfg['b']['as2_id'],
            algorithms={k:cfg['algorithms'][k] for k in ('signature','encryption','key_encryption')},
            http_timeout=cfg['http_timeout'],max_connect_attempts=cfg['max_connect_attempts'])
        report['transport']={k:getattr(dispatch,k) for k in ('id','state','transport_state','ack_state','send_hold','hold_state')}
        report['transport']['error']=code(dispatch.last_error)
        report['certificates']={side:checksum(cfg[side].get('fingerprint')) for side in ('a','b')}
        report['attempts']=[dict(id=a.id,number=a.number,state=a.state,message_id=a.message_id,engine_id=a.engine_id,send_count=a.send_count,
            created_at=op.stamp(a.created_at),updated_at=op.stamp(a.updated_at),mic_algorithm=a.mic_algorithm,error=code(a.error)) for a in db.query(m.AS2Attempt).filter_by(dispatch_id=dispatch.id).order_by(m.AS2Attempt.number)]
        report['events']=[]
        for event in db.query(m.AS2Event).filter_by(dispatch_id=dispatch.id).order_by(m.AS2Event.created_at):
            meta=json.loads(event.metadata_json)
            report['events'].append(dict(id=event.id,sequence=event.sequence,engine_id=event.engine_id,kind=event.kind,processing=event.processing_state,
                archived_at=op.stamp(event.created_at),error=code(meta.get('reason')),raw_sha256=checksum(event.raw_sha256),payload_sha256=checksum(event.payload_sha256),
                verification={k:meta[k] for k in ('signature_verified','mic_verified','encryption_verified','trusted') if type(meta.get(k)) is bool}))
    report['manual_history']=[dict(id=a.id,kind=a.kind,at=op.stamp(a.occurred_at)) for a in op.actions(db,row)]
    report['publication_history']=[dict(id=p.id,previous_id=p.previous_id,source_key=p.source_key,status=p.status,checked_at=op.stamp(p.checked_at),published_at=op.stamp(p.published_at)) for p in db.query(m.OpsPublication).filter_by(submission_id=row.id)]
    report['incidents']=[{k:getattr(i,k) for k in ('id','rule','status','severity','active_condition','reminder_count')} for i in db.query(m.OpsIncident).filter_by(submission_id=row.id)]
    root=Path(__file__).resolve().parent
    report['code_sha256']={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ('operations.py','operations_monitor.py','operations_diagnostics.py','as2_engine.py','as2_worker.py','as2_queue.py','r3_export.py')}
    buffer=io.BytesIO()
    with ZipFile(buffer,'w',ZIP_DEFLATED) as archive:archive.writestr('diagnostic.json',json.dumps(report,ensure_ascii=False,indent=2).encode('utf-8'))
    return buffer.getvalue()
