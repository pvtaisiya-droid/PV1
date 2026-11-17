"""Internal incidents only: no email, SMTP or external notification adapter."""
import argparse
from datetime import date,datetime,timedelta,timezone
import json
import os
from pathlib import Path
import time
if __name__=='__main__' and any(not os.environ.get(k) for k in ('DATABASE_URL','PV_STORAGE_ROOT','PV_AS2_CONFIG')):
    raise SystemExit('Explicit isolated database, storage and AS2 config are required.')
from app import models as m,operations as op,as2_adapter
from app.database import SessionLocal
from app.concurrency import lock_snapshot_inputs
from app.as2_settings import dumps


def save_signal(db,key,facts,now=None):
    lock_snapshot_inputs(db,('OpsSignal',))
    row=db.get(m.OpsSignal,key);now=now or m.utcnow()
    if row:row.observed_at=now;row.facts_json=dumps(facts)
    else:db.add(m.OpsSignal(key=key,observed_at=now,facts_json=dumps(facts)))

def collect_health():
    observations={}
    for side in ('a','b'):
        try:
            result=as2_adapter.call('status',side=side)
            observations['engine-'+side]=dict(available=True,engine_id=result['engine_id'],certificates=result.get('certificates',[]),outgoing_disabled=result.get('outgoing_disabled',False))
        except Exception:observations['engine-'+side]=dict(available=False,reason='authenticated_control_unavailable')
    return observations

def evaluate(db,now=None,observations=None):
    now=op.utc(now or m.utcnow());lock_snapshot_inputs(db,('OpsIncident','OpsSignal'))
    for key,facts in (observations or {}).items():save_signal(db,key,facts,now)
    db.flush();conditions={}
    def add(scope,rule,owner,summary,basis,action,*,subject=None,policy=None,severity='warning',since=None):
        conditions[(scope,rule)]=dict(owner_id=owner,summary=summary,basis=basis,required_action=action,
            submission_id=subject.id if subject else None,policy_id=policy.id if policy else None,severity=severity,since=since or now)
    for row in db.query(m.OpsSubmission).all():
        pol=op.policy(db,row.config_version);scope='submission:'+row.id
        emit=lambda rule,summary,basis,action,**kw:add(scope,rule,row.owner_id,summary,basis,action,subject=row,policy=pol,**kw)
        if not pol:
            emit('policy_missing','Не задана эксплуатационная политика','Версия контура '+row.config_version,'Задать обоснованные пороги и известную настройку автопубликации.')
        manual=op.actions(db,row,'manual_submitted');results=op.actions(db,row,'manual_result')
        dispatch=db.get(m.AS2Dispatch,row.dispatch_id) if row.dispatch_id else None
        attempt=db.query(m.AS2Attempt).filter_by(dispatch_id=row.dispatch_id).order_by(m.AS2Attempt.number.desc()).first() if row.dispatch_id else None
        possible=bool(dispatch and (dispatch.transport_state=='positive' or (attempt and attempt.state not in ('prepared','cancelled','not_delivered'))))
        if (manual and possible) or len(manual)>1:
            emit('possible_duplicate','Возможна двойная подача','Есть ручная подача и возможная/подтверждённая автоматическая доставка либо несколько ручных подач.','Сверить номера и доказательства обоих каналов с ФН; не повторять автоматически.',severity='critical')
        codes={json.loads(r.facts_json).get('result') for r in results}
        if {'accepted','rejected'}<=codes or (dispatch and (('accepted' in codes and dispatch.ack_state=='rejected') or ('rejected' in codes and dispatch.ack_state=='accepted'))):
            emit('channel_conflict','Результаты каналов противоречат друг другу','Сохраняются все зарегистрированные результаты, без выбора последнего.','Разобрать основания и подтвердить итог отдельно; исходные факты не стирать.',severity='critical')
        supplemented={f.get('submission_action_id') for a in results if (f:=json.loads(a.facts_json)).get('evidence') or f.get('reference_number') or f.get('evidence_reference')}
        if any(not json.loads(a.facts_json).get('confirmation_complete') and a.id not in supplemented for a in manual):
            emit('manual_evidence','Подтверждение ручной подачи неполное','Номер и защищённое доказательство пока не зарегистрированы.','Дополнить результат ручной подачи доказательством; не выдумывать номер.')
        if dispatch:
            if dispatch.send_hold and dispatch.hold_state=='engine_pending':
                emit('hold_pending','Запрет ещё не подтверждён движком','Передача могла начаться; приёмник продолжает работу.','Проверить доступность worker/engine и историю до новой подачи.',severity='critical')
            if dispatch.state in ('uncertain','requires_review','mdn_negative','ack_rejected'):
                emit('transport_review','Транспортный/прикладной результат требует разбора','AS2: '+dispatch.state,'Сверить архив ответов; не превращать неопределённость в недоставку.',severity='critical')
            if pol and attempt and not dispatch.send_hold and attempt.state not in ('prepared','cancelled','not_delivered'):
                elapsed=(now-op.utc(attempt.created_at)).total_seconds()
                if dispatch.transport_state not in ('positive','negative','conflict') and elapsed>=pol.mdn_seconds:
                    emit('mdn_wait','Превышено ожидание MDN',f'Политика {pol.id}: {pol.mdn_seconds} с от начала попытки.','Проверить движок и несопоставленные ответы; не создавать новый транспорт.',since=op.utc(attempt.created_at))
                if dispatch.transport_state=='positive' and dispatch.ack_state not in ('accepted','rejected','conflict') and elapsed>=pol.ack_seconds:
                    emit('ack_wait','Превышено ожидание ACK',f'Политика {pol.id}: {pol.ack_seconds} с от начала попытки.','Проверить входящий ACK/контур и эскалировать ФН по сроку сообщения.',since=op.utc(attempt.created_at))
        if row.deadline_raw:
            submitted=[op.instant(json.loads(a.facts_json)['submitted_at']) for a in manual]
            if row.deadline_precision=='date':
                due=date.fromisoformat(row.deadline_raw);late=now.astimezone(op.MSK).date()>due
                approaching=bool(pol and 0<=(due-now.astimezone(op.MSK).date()).days<=pol.approaching_seconds//86400)
                if submitted:late=min(submitted).astimezone(op.MSK).date()>due;approaching=False
            else:
                due=op.utc(row.deadline_utc);late=(min(submitted) if submitted else now)>due;approaching=bool(pol and not submitted and 0<=(due-now).total_seconds()<=pol.approaching_seconds)
            if late or approaching:
                emit('submission_deadline','Срок представления нарушен/не подтверждён' if late else 'Приближается срок представления',row.deadline_raw+'; '+row.deadline_basis,
                    'Ответственному ФН проверить фактическое представление и необходимость резервной подачи. Дата без времени не превращается в точный timestamp.',severity='critical' if late else 'warning')
        for source_key,source in op.sources(db,row).items():
            if source['kind']=='automatic':
                accepted=db.query(m.AS2Event).join(m.AS2AckResult,m.AS2AckResult.event_id==m.AS2Event.id).filter(m.AS2Event.attempt_id==source['id'],m.AS2Event.processing_state=='applied',m.AS2AckResult.interpretation=='accepted').first()
                if not accepted:continue
            pub=db.query(m.OpsPublication).filter_by(submission_id=row.id,source_key=source_key).order_by(m.OpsPublication.checked_at.desc()).first()
            if pub and pub.status=='confirmed_manual':continue
            elapsed=(now-op.instant(source['time'])).total_seconds()
            if (pub and pub.status=='problem') or (pol and (pol.autopublish=='disabled' or elapsed>=pol.publication_seconds)):
                emit('publication:'+source['id'],'Публикация требует проверки/действия',f'Подача {source_key}; автопубликация {pol.autopublish if pol else "unknown"}; политика {pol.id if pol else "не задана"}.',
                    'Проверить АИС вручную и зарегистрировать источник результата.'+(' При выключенной автопубликации требуется действие сотрудника в АИС.' if pol and pol.autopublish=='disabled' else ''),severity='critical' if pub and pub.status=='problem' else 'warning')
    for cfg_version in {p.config_version for p in db.query(m.OpsPolicy).all()}:
        pol=op.policy(db,cfg_version);scope='config:'+cfg_version
        for key in ('worker','engine-a','engine-b'):
            signal=db.get(m.OpsSignal,key)
            if not signal and (now-op.utc(pol.created_at)).total_seconds()<pol.health_seconds:continue
            facts=json.loads(signal.facts_json) if signal else {}
            bad=not signal or (now-op.utc(signal.observed_at)).total_seconds()>pol.health_seconds or (key!='worker' and not facts.get('available'))
            if bad:add(scope,key,pol.owner_id,'Нет актуального подтверждения доступности '+key,'Наблюдение heartbeat/аутентифицированного control API, не предположение о доставке.','Техническому ответственному проверить собственные процессы и состояние очереди.',policy=pol,severity='critical')
            if key=='engine-a':
                for cert in facts.get('certificates',[]):
                    if cert.get('config_version')!=cfg_version:continue
                    remaining=(op.instant(cert['expires_at'])-now).total_seconds()
                    if remaining<=pol.certificate_days*86400:
                        add(scope,'certificate-'+cert['side'],pol.owner_id,'Сертификат истёк или приближается окончание срока','Публичный сертификат; окончание '+cert['expires_at'],
                            'Подготовить ротацию и сохранить старые ключи для поздних ответов; не отключать проверку срока.',policy=pol,severity='critical' if remaining<=0 else 'warning')
    from app.as2_settings import configuration
    active_policy=op.policy(db,configuration()['version'])
    if active_policy:
        for event in db.query(m.AS2Event).filter(m.AS2Event.dispatch_id.is_(None),m.AS2Event.kind.in_(('mdn','ack','incoming','as2')),m.AS2Event.processing_state.in_(('unmatched','review'))):
            add('event:'+event.id,'unmatched_response',active_policy.owner_id,'Входящий ответ требует сопоставления',
                'Архивное событие '+event.id+'; автоматическая привязка не установлена.','Проверить защищённый оригинал в AS2-мониторе; не связывать с последней отправкой по предположению.',policy=active_policy,severity='critical',since=op.utc(event.created_at))
    current={}
    for row in db.query(m.OpsIncident).order_by(m.OpsIncident.cycle):current[(row.scope,row.rule)]=row
    for key,values in conditions.items():
        row=current.get(key)
        if not row or not row.active_condition:
            row=m.OpsIncident(scope=key[0],rule=key[1],cycle=row.cycle+1 if row else 1,last_seen=now,last_reminder=now,**values)
            db.add(row);db.flush();op.record(db,'incident_opened','Системное наблюдение; конкретное основание и наличие политики указаны в задаче.',incident=row,now=now)
        else:
            # Closing a task does not clear the condition or alter an ACK/publication.
            row.last_seen=now
            fields=('summary','basis','required_action','severity','policy_id')
            changed=any(getattr(row,field)!=values[field] for field in fields)
            for field in fields:setattr(row,field,values[field])
            if changed:
                row.version+=1;op.record(db,'incident_changed','Существенно изменилось основание или политика существующего инцидента.',incident=row,now=now)
            pol=db.get(m.OpsPolicy,row.policy_id) if row.policy_id else None
            if pol and row.severity=='critical' and row.reminder_count<pol.reminder_limit and (now-op.utc(row.last_reminder)).total_seconds()>=pol.reminder_seconds:
                row.reminder_count+=1;row.last_reminder=now;row.version+=1
                op.record(db,'incident_reminder','Повторное внутреннее напоминание в пределах явной политики.',incident=row,now=now,facts=dict(number=row.reminder_count))
    for key,row in current.items():
        if key not in conditions and row.active_condition:
            row.active_condition=False;row.version+=1
            if row.status!='closed':row.status='resolved'
            op.record(db,'condition_cleared','Условие отсутствует при повторном наблюдении; исходные результаты подачи не изменены.',incident=row,now=now)
    db.commit()
    return len(conditions)

def tick(now=None):
    observations=collect_health()  # no database transaction around network calls
    with SessionLocal() as db:return evaluate(db,now,observations)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--once',action='store_true');parser.add_argument('--stop-file',required=True);args=parser.parse_args()
    from app.local_runtime import runtime_guard
    stop=Path(args.stop_file)
    with runtime_guard():
        while not stop.exists():
            try:tick()
            except Exception:print('operations_monitor_unavailable',flush=True)
            if args.once:break
            for _ in range(60):
                if stop.exists():break
                time.sleep(.25)

if __name__=='__main__':main()
