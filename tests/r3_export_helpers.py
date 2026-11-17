"""Synthetic snapshot; invented MedDRA codes are NOT dictionary validation."""
import hashlib
import json
from pathlib import Path

def specimen(storage, study=False):
    from app.clinical_values import CONTRACT, normalize
    from app.r3_export import dump
    records={name:[] for name in ('CaseClinicalRecord','Reaction','CaseProduct','CaseDrugDosage','CaseDrugSubstance',
        'CaseDrugIndication','CaseDrugRechallenge','CaseProductReactionAssessment','Patient','CaseIdentifier','CaseNarrative',
        'CaseLiteratureReference','CaseIdentityDecision','Attachment','E2BTransmission','AuditTrail','SafetyReport','IncomingRequestCaseLink','IncomingRequest','IcsrMessage')}
    data=dict(schema='pv1-clinical-1',case=dict(id='case-synthetic',worldwide_case_id='RU-SYNTH-0001',local_sender_case_id='SYNTH-CASE-01',
        e2b_identity_profile='ich-r3-identity-dates-v1',e2b_sender_scope=dump(['2.16.840.1.113883.3.989.2.1.3.11','SYNTH-SENDER'])),records=records)
    def record(kind,key,values,**links):
        fields={}
        for code,value in values.items():
            args=value if isinstance(value,dict) else dict(raw=str(value))
            fields[code]=normalize(CONTRACT[kind]['fields'][code],**args,origin='manual',context={'author_id':'synthetic-author','change_reason':'Synthetic confirmed fact'})
        r=dict(id=key,case_id=data['case']['id'],kind=kind,fields_json=dump(fields),interpretation_profile='manual-clinical-v1',is_deleted=False,**links)
        records['CaseClinicalRecord'].append(r);return r
    record('report','report',{'C.1.3':'2' if study else '1','C.1.4':'20260301','C.1.5':'20260304','C.1.6.1':'true','C.1.7':'false','C.1.8.2':'1'})
    for i in (1,2):
        record('reporter',f'reporter{i}',{'C.2.r.1.2':'Ирина','C.2.r.1.4':f'Синтетическая {i}', 'C.2.r.3':'RU','C.2.r.4':'1',**({'C.2.r.5':'1'} if i==1 else {})})
    record('sender','sender',{'C.3.1':'1','C.3.2':'Синтетическая организация','C.3.4.5':'RU'})
    record('patient','patient',{'D.1':'ПС','D.2.1':'1980','D.5':'2','D.3':'55.2500','D.9.1':'20260401','D.9.3':dict(null_flavor='UNK')})
    record('parent','parent',{'D.10.1':dict(null_flavor='MSK'),'D.10.2.1':'196002','D.10.6':'2'},parent_id='patient')
    for i in (1,2):
        record('medical_history',f'history{i}',{'D.7.1.r.1a':'27.0','D.7.1.r.1b':str(99900100+i),'D.7.1.r.2':'2019','D.7.1.r.4':'2020','D.7.1.r.5':'Синтетический анамнез'},parent_id='patient')
    record('parent_medical_history','parent-history',{'D.10.7.1.r.1a':'27.0','D.10.7.1.r.1b':'99900201','D.10.7.1.r.5':'Анамнез родителя'},parent_id='parent')
    record('drug_history','drug-history',{'D.8.r.1':'Исторический препарат','D.8.r.4':'2020','D.8.r.5':'202101'},parent_id='patient')
    record('death_cause','death',{'D.9.2.r.1a':'27.0','D.9.2.r.1b':'99900301','D.9.2.r.2':'Синтетическая причина'},parent_id='patient')
    record('test','lab',{'F.r.1':'20260302','F.r.2.1':'Синтетический анализ','F.r.3.2':dump({'attributes':{'{http://www.w3.org/2001/XMLSchema-instance}type':'PQ','value':'1.2500'},'parts':[]}),
        'F.r.3.3':dump({'value':'mg/L','parts':[]}), 'F.r.4':'0.5000','F.r.5':'2.0000','F.r.6':'Проверка & <значений>'},parent_id='patient')
    record('narrative','narrative',{'H.1':'Подтверждённый русский текст: ёж, <символы> & кавычки "…".'})
    record('sender_comment','sender-comment',{'H.4':'Ручная подтверждённая правка отправителя'})
    record('summary','summary',{'H.5.r.1a':'Краткое описание','H.5.r.1b':'rus'})
    pdf=Path(storage)/'synthetic.pdf';pdf.parent.mkdir(parents=True,exist_ok=True);pdf.write_bytes(b'%PDF-1.4\nSynthetic document only\n%%EOF')
    records['Attachment'].append(dict(id='document',case_id='case-synthetic',file_name='synthetic.pdf',storage_path=str(pdf.resolve()),checksum_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),mime_type='application/pdf',is_deleted=False))
    record('source_document','source-document',{'C.1.6.1.r.1':'Синтетическое приложение'},attachment_id='document')
    record('literature','literature',{'C.4.r.1':'Synthetic reference, 2026. No medical evidence.'})
    for i in (1,2):
        rid,pid=f'reaction{i}',f'product{i}'
        records['Reaction'].append(dict(id=rid,case_id='case-synthetic',reported_term='Одинаковое название реакции',meddra_llt_code=str(99901000+i),meddra_version='27.0',seriousness_hospitalization=True,outcome='recovered/resolved',is_deleted=False))
        record('reaction_extra','reaction-extra'+str(i),{'E.i.1.1b':'rus','E.i.4':'20260301','E.i.5':'20260304','E.i.9':'RU'},reaction_id=rid)
        records['CaseProduct'].append(dict(id=pid,case_id='case-synthetic',reported_product_name='Одинаковый препарат',drug_role='suspect' if i==1 else 'concomitant',is_deleted=False))
        for j in (1,2) if i==1 else (1,):
            records['CaseDrugDosage'].append(dict(id=f'dosage{i}-{j}',case_product_id=pid,dose_value='0.2500',dose_unit='mg',interval_value='1',interval_unit='d',start_raw='202602',end_raw='20260301',dosage_text='Синтетический режим',is_deleted=False))
        records['CaseDrugSubstance'].append(dict(id=f'substance{i}',case_product_id=pid,reported_substance_name='Синтетическое вещество',strength_value='0.2500',strength_unit='mg',denominator_value='1',is_deleted=False))
        records['CaseDrugIndication'].append(dict(id=f'indication{i}',case_product_id=pid,reported_text='Синтетическое показание',meddra_llt_code='99902001',meddra_version='27.0',is_deleted=False))
        records['CaseProductReactionAssessment'].append(dict(id=f'assessment{i}',case_product_id=pid,reaction_id=rid,assessment_source='Врач',assessment_method_text='Клиническая оценка',relatedness='Возможная связь',causality_method='manual_text',is_deleted=False))
        records['CaseDrugRechallenge'].append(dict(id=f'rechallenge{i}',case_product_id=pid,reaction_id=rid,rechallenge_code='4',code_system='2.16.840.1.113883.3.989.2.1.1.16',code_version='1.0',is_deleted=False))
        record('drug_extra','drug-extra'+str(i),{'G.k.5a':'3.2500','G.k.5b':'mg','G.k.8':'1'},case_product_id=pid)
        record('pair_interval','interval'+str(i),{'G.k.9.i.3.1a':'2','G.k.9.i.3.1b':'d'},case_product_id=pid,reaction_id=rid)
    if study:
        record('study','study',{'C.5.2':'Синтетическое исследование','C.5.3':'SYNTH-STUDY','C.5.4':'1'})
        record('study_registration','registration',{'C.5.1.r.1':'SYNTH-REG','C.5.1.r.2':'RU'},parent_id='study')
        record('pv_decision','pv',{'local.expectedness':'unknown','local.reportability':'undecided','local.document_version':'RSI-test-1'})
    return data

def revision(data):
    from app.r3_export import dump
    raw=dump(data)
    return dict(id='revision-synthetic',case_id=data['case']['id'],schema_version='pv1-clinical-1',snapshot_json=raw,checksum_sha256=hashlib.sha256(raw.encode()).hexdigest())

def envelope():
    return dict(sender_id='SYNTH-SENDER',receiver_id='SYNTH-RECEIVER',batch_id='SYNTH-BATCH',message_id='SYNTH-MESSAGE',created_ts='20261111120000+0000',scenario='initial',purpose='local_test')
