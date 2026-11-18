"""Pure snapshot → ICH XML. Never reads current clinical ORM objects or source XML."""
import base64
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import uuid
from lxml import etree as E
from app.r3_profile import BASE, MAX_XML_BYTES, profile, validate_xml, sha

H='urn:hl7-org:v3'
X='http://www.w3.org/2001/XMLSchema-instance'
NS={'h':H,'xsi':X}
OID='2.16.840.1.113883.3.989.2.1.'
MEDDRA='2.16.840.1.113883.6.163'
SCHEMA='pv1-clinical-1'

def dump(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,default=str,separators=(',',':'))

def populated(value):
    return value not in (None,'',[],{})

def active(data,name):
    return [r for r in data['records'].get(name,[]) if not r.get('is_deleted')]

class Node:
    def __init__(self, xml, layout):
        self.xml,self.layout,self.cache,self.order=xml,layout,{},{}

    def child(self, key, force=False):
        if key in self.cache and not force:
            return self.cache[key]
        spec=self.layout['nodes'][key]
        xml=E.Element('{'+H+'}'+spec['tag'],spec['attrs'])
        node=Node(xml,self.layout)
        self.xml.append(xml);self.order[xml]=spec['order']
        self.xml[:]=sorted(self.xml,key=lambda c:self.order.get(c,-1))
        if not force:self.cache[key]=node
        for const in spec['constants']:node.child(const)
        return node

    def path(self, trail, repeat_at=None):
        node=self
        for i,key in enumerate(trail):
            node=node.child(key,force=(i==repeat_at))
        return node

def sub(parent,tag,attrs=None,text=None,index=None):
    n=E.Element('{'+H+'}'+tag,attrs or {})
    n.text=text
    if index is None:parent.append(n)
    else:parent.insert(index,n)
    return n

class Exporter:
    def __init__(self,data,envelope,storage_root):
        self.data,self.envelope,self.storage_root=data,envelope,Path(storage_root).resolve()
        self.manifest=profile()
        self.layout=json.loads((BASE/'layout.json').read_text('utf-8'))
        self.findings=[];self.used_documents=set();self.objects={};self.scope={};self.addresses={}
        self.root=E.Element('{'+H+'}MCCI_IN200100UV01',nsmap={None:H,'xsi':X},ITSVersion='XML_1.0')
        self.tree=Node(self.root,self.layout)
        self.base={k:self.tree.path(t) for k,t in self.layout['base'].items()}
        self.message=self.base['investigation'].xml.getparent().getparent().getparent()
        self.control=self.base['investigation'].xml.getparent().getparent()

    def issue(self,rule,path,element,message,level='error',source=None):
        self.findings.append(dict(rule=rule,version='1',source=source or 'PV1 local exporter 1; ICH IG 5.03 / Appendix I(G) 1.03',
                                  path=path,element=element,level=level,message=message))

    def ref(self,kind,row_id):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, self.envelope['revision_checksum']+'/'+kind+'/'+row_id))

    def node(self,kind,anchor):
        r=self.layout['recipes'][kind]
        return anchor.path(r['trail'],r['repeat_at'])

    def value(self,node,definition,fact,path):
        code=definition['element'];state=fact.get('state')
        raw=fact.get('raw');nf=fact.get('null_flavor')
        if state in ('missing','cleared','empty') and raw is None and not nf:return
        if state in ('invalid','unsupported','ambiguous','legacy_unverified'):
            self.issue('VALUE-STATE',path,code,'Значение не подтверждено для сериализации: '+state);return
        if raw is None and not nf:return
        if nf and raw is not None:
            self.issue('VALUE-NF',path,code,'Одновременно переданы значение и nullFlavor.');return
        kind=definition['type'];attr=definition['attribute']
        if nf and nf not in ('NI','INV','DER','OTH','NINF','PINF','UNC','MSK','NA','UNK','ASKU','NAV','NASK','TRC'):
            self.issue('VALUE-NF',path,code,'Неизвестный код nullFlavor; не подставлен другой.');return
        if nf:
            self.issue('NF-CONFORMANCE',path,code,'Синтаксис nullFlavor сохранён; допустимость для конкретного элемента требует проверки правил ICH/получателя.','not_performed')
        if not nf and kind=='date':
            from app.e2b_r3_identity import source_date
            f=source_date([E.Element('v',value=str(raw))],path,code)
            if f['state'] not in ('parsed','partial'):
                self.issue('DATE-SYNTAX',path,code,'Исходная дата не соответствует точному TS ICH; части не достраиваются.');return
        if not nf and kind=='decimal':
            try:
                if not Decimal(raw).is_finite():raise InvalidOperation()
            except (InvalidOperation,TypeError):
                self.issue('NUMBER',path,code,'Число не является конечным десятичным значением.');return
        if not nf and kind=='boolean':
            if raw not in ('true','false','1','0'):
                self.issue('BOOLEAN',path,code,'Неверное boolean-значение.');return
        target=node.path(definition['trail']).xml
        if code=='F.r.3.4':target.set('{'+X+'}type','ED')
        if fact.get('system'):
            if target.get('codeSystem') and target.get('codeSystem') != fact['system']:
                self.issue('CODE-SYSTEM',path,code,'Полученная система кодирования отличается от закреплённого контекста ICH.');return
            target.set('codeSystem',fact['system'])
        if fact.get('system_version'):
            target.set('codeSystemVersion',fact['system_version'])
        if kind=='code' and attr=='code' and raw is not None and not target.get('codeSystem'):
            self.issue('CODE-SYSTEM',path,code,'Для кода не задана система; namespace не угадывается.');return
        if target.get('codeSystem')==MEDDRA and attr=='code' and raw is not None and not re.fullmatch(r'[0-9]{8}',str(raw)):
            self.issue('MEDDRA-CODE',path,code,'Требуется восьмизначный код соответствующего уровня MedDRA.');return
        if nf:
            if any(k in target.attrib for k in ('value','code')):
                self.issue('NF-CONFLICT',path,code,'В одном XML-значении конфликтуют код/число и nullFlavor.');return
            target.set('nullFlavor',nf)
        elif kind in ('quantity','json'):
            if code not in ('F.r.3.2','F.r.3.3'):
                self.issue('UNMAPPED',path,code,'Для составного значения нет подтверждённого преобразования.');return
            try:self.quantity(target,json.loads(raw),code,path)
            except (ValueError,TypeError,KeyError):self.issue('QUANTITY',path,code,'Составное значение анализа не соответствует поддерживаемому PQ/IVL_PQ/RTO_PQ_PQ.')
        elif attr:
            if target.get('nullFlavor'):
                self.issue('NF-CONFLICT',path,code,'Код/число конфликтует с nullFlavor соседнего поля.');return
            target.set(attr,str(raw))
        else:
            target.text=str(raw)
        if code in ('D.3','D.10.4'):target.set('unit','kg')
        if code in ('D.4','D.10.5'):target.set('unit','cm')
        self.addresses[target]=dict(path=path,element=code)

    def quantity(self,target,q,code,path):
        if code=='F.r.3.3':
            if q.get('value') is not None:target.set('unit',str(q['value']))
            for part in q.get('parts',[]):
                if part['type'] not in ('low','high','center','width','numerator','denominator'):raise ValueError()
                nodes=target.findall('{'+H+'}'+part['type'])
                if not nodes:raise ValueError()
                if part.get('unit') is not None:nodes[0].set('unit',str(part['unit']))
            return
        attrs=q.get('attributes',{});typ=attrs.get('{'+X+'}type','PQ')
        if typ not in ('PQ','IVL_PQ','RTO_PQ_PQ'):raise ValueError()
        allowed={'{'+X+'}type','value','unit','nullFlavor','inclusive','operator'}
        if set(attrs)-allowed:raise ValueError()
        target.set('{'+X+'}type',typ)
        for k,v in attrs.items():target.set(k,str(v))
        if populated(q.get('text')) and q['text'].strip():raise ValueError()
        for p in q.get('parts',[]):
            if p['type'] not in ('low','high','center','width','numerator','denominator') or set(p.get('attributes',{}))-allowed:raise ValueError()
            sub(target,p['type'],{k:str(v) for k,v in p.get('attributes',{}).items()})

    def clinical(self,row,anchor):
        kind=row['kind'];path='CaseClinicalRecord/'+row['id'];values=json.loads(row['fields_json'])
        if kind in ('study_site','pv_decision'):
            self.issue('LOCAL-ONLY',path,'local.*','Внутреннее решение/сведения PV1 не являются произвольным расширением ICH; сохранены в ревизии.','warning');return None
        if row.get('interpretation_profile')=='legacy_unverified':
            self.issue('LEGACY',path,kind,'Историческая интерпретация не подтверждена; требуется проверка в новой ревизии.')
        if kind not in self.layout['recipes']:
            if any(populated(v.get('raw')) or v.get('null_flavor') for v in values.values()):self.issue('UNMAPPED',path,kind,'Вид клинической записи не поддержан экспортёром.')
            return None
        node=self.node(kind,anchor);fields=self.layout['recipes'][kind]['fields']
        for code,fact in values.items():
            if not populated(fact.get('raw')) and not fact.get('null_flavor'):continue
            if code=='C.1.2':
                self.issue('SOURCE-CREATION',path+'/'+code,code,'Входящее время создания остаётся источником; исходящее C.1.2 задано в параметрах этого артефакта.','warning');continue
            if code in ('C.1.11.1','C.1.11.2'):
                self.issue('SCENARIO',path+'/'+code,code,'Amendment/nullification требуют отдельного поддержанного сценария; автоматическое переиспользование исходного признака запрещено.');continue
            if code.startswith('local.'):
                if code=='local.contacts':self.contacts(node,kind,fact,path);continue
                # A free-form patient identifier, pregnancy or unmapped narrative may be clinically meaningful.
                self.issue('UNMAPPED',path+'/'+code,code,'Заполненное локальное клиническое поле не имеет однозначного представления ICH; перенесите факт в подтверждённый элемент до экспорта.');continue
            if code in ('C.1.6.1.r.2','C.4.r.2'):
                self.document(node,fields[code],row,path+'/'+code);continue
            if code not in fields:
                self.issue('UNMAPPED',path+'/'+code,code,'Преобразование не подтверждено (в том числе IDMP/TBD); значение не пропущено молча.');continue
            self.value(node,fields[code],fact,path+'/'+code)
        # Attachment explicitly linked to document/literature has a purpose even without source metadata.
        document_code={'source_document':'C.1.6.1.r.2','literature':'C.4.r.2'}.get(kind)
        if row.get('attachment_id') and document_code and document_code not in values:
            self.document(node,fields[document_code],row,path+'/'+document_code)
        return node

    def contacts(self,node,kind,fact,path):
        # C.3.4.6/7/8 own typed sender contacts. C.2 has no general telecom element in ICH.
        if kind=='reporter':
            self.issue('UNMAPPED',path+'/local.contacts','C.2.r','Контакты первичного источника не имеют общего подтверждённого элемента ICH C.2; нужен содержательный разбор.');return
        try:contacts=json.loads(fact['raw'])
        except (ValueError,TypeError):contacts=None
        if not isinstance(contacts,list):self.issue('CONTACT',path,'C.3','Некорректный список контактов.');return
        for contact in contacts:
            value=contact.get('value') if isinstance(contact,dict) else None
            if not value or not value.startswith(('tel:','fax:','mailto:')):
                self.issue('CONTACT',path,'C.3','Тип контакта не установлен; не присваивается автоматически.');continue
            existing=node.xml.findall('{'+H+'}telecom')
            if any(n.get('value')==value for n in existing):continue
            sub(node.xml,'telecom',{'value':value},index=1)

    def document(self,node,definition,row,path):
        attachments={r['id']:r for r in active(self.data,'Attachment')}
        item=attachments.get(row.get('attachment_id'))
        if not item:
            self.issue('DOCUMENT',path,definition['element'],'Нет однозначной ссылки на файл в выбранной ревизии.');return
        from app.recovery_paths import resolve_storage
        location=resolve_storage(item.get('storage_path') or '')
        if not location.is_relative_to(self.storage_root) or not location.is_file():
            self.issue('DOCUMENT',path,definition['element'],'Файл ревизии отсутствует или находится вне защищённого хранилища.');return
        if location.stat().st_size>8*1024*1024:
            self.issue('DOCUMENT-SIZE',path,definition['element'],'Превышен локальный лимит одного документа 8 MiB.');return
        raw=location.read_bytes()
        if sha(raw)!=item.get('checksum_sha256'):
            self.issue('DOCUMENT-HASH',path,definition['element'],'Файл изменён относительно контрольной суммы ревизии.');return
        target=node.path(definition['trail']).xml
        mime=item.get('mime_type') or ('application/pdf' if str(item.get('file_name','')).lower().endswith('.pdf') else None)
        if mime not in ('application/pdf','text/plain','image/jpeg','image/png'):
            self.issue('DOCUMENT-MIME',path,definition['element'],'Назначение MIME файла не входит в поддержанный локальный набор.');return
        target.set('mediaType',mime);target.set('representation','B64');target.text=base64.b64encode(raw).decode('ascii')
        self.used_documents.add(item['id'])

    def scalar(self,name,row,node):
        recipes=self.layout['recipes'][name]['fields'];path=name+'/'+row['id']
        for field,d in recipes.items():
            value=row.get(field);nf=None
            if name=='Reaction' and field=='outcome':
                meanings={'unknown':'0','recovered/resolved':'1','recovering/resolving':'2','not recovered/not resolved/ongoing':'3','recovered/resolved with sequelae':'4','fatal':'5'}
                if value is not None and value not in meanings:self.issue('OUTCOME',path+'/'+field,d['element'],'Исход не имеет подтверждённого кода ICH.');continue
                value=meanings.get(value)
            if name=='Reaction' and field.startswith('seriousness_'):
                if value is False:
                    self.issue('SERIOUSNESS',path+'/'+field,d['element'],'ICH E.i.3.2 задаёт true/NI; явное false нельзя незаметно заменить NI.');continue
                if value is None:
                    sources=json.loads(row.get('current_sources_json') or '{}')
                    fact=sources.get(field,{}).get('source_fact',{}).get('criteria',{}).get(field) or json.loads(row.get('source_facts_json') or '{}').get('criteria',{}).get(field,{})
                    nf=fact.get('null_flavor')
                    if nf and nf!='NI':self.issue('SERIOUSNESS',path+'/'+field,d['element'],'Для E.i.3.2 поддержаны true/NI; полученный другой nullFlavor требует исправления.');continue
            if name=='CaseDrugDosage' and field in ('start_raw','end_raw'):nf=row.get(field.replace('_raw','_null_flavor'))
            if name=='CaseDrugDosage' and field=='route_code':nf=row.get('route_null_flavor')
            if name in ('CaseDrugIndication','CaseDrugRechallenge') and field in ('meddra_llt_code','rechallenge_code'):nf=row.get('null_flavor')
            if value is None and not nf:continue
            if isinstance(value,bool):value='true' if value else 'false'
            fact=dict(raw=str(value) if value is not None else None,null_flavor=nf,state='manual')
            if name=='CaseDrugDosage':
                system={'route_code':'route_system','dosage_form_code':'dosage_form_system','parent_route_code':'parent_route_system'}.get(field)
                if system:fact['system']=row.get(system)
            if name=='CaseDrugRechallenge' and field=='rechallenge_code':fact['system']=row.get('code_system')
            self.value(node,d,fact,path+'/'+field)
        unmapped={
            'Reaction':('reporter_comment','null_flavor'),
            'CaseProduct':('active_substance_text','indication_text','indication_meddra_pt_code','dose_value','dose_unit','route','frequency','dosage_form','therapy_start_date','therapy_end_date','therapy_start_date_raw','therapy_end_date_raw','rechallenge_result','dechallenge_result','causality_text','duration_text','expiry_date','expiry_date_raw','batch_lot_number','null_flavor'),
            'CaseDrugDosage':('frequency_text','dose_numerator_value','dose_numerator_unit','dose_denominator_value','dose_denominator_unit'),
            'CaseDrugSubstance':('identifier_code','identifier_system','identifier_version','quantity_null_flavor'),
            'CaseDrugIndication':('legacy_meddra_pt_code',),
            'CaseDrugRechallenge':('reported_text',),
            'CaseProductReactionAssessment':('assessment_comment','causality_reporter','causality_company'),
        }.get(name,())
        for field in unmapped:
            if populated(row.get(field)):self.issue('UNMAPPED',path+'/'+field,'G.k','Заполненное поле не имеет однозначного преобразования в выбранной версии; требуется содержательное исправление, не скрытое исключение.')
        if name=='CaseProduct' and json.loads(row.get('coded_identifiers_json') or '[]'):
            self.issue('IDMP',path+'/coded_identifiers_json','G.k.2.1','MPID/PhPID namespaces не подтверждены; коды сохранены в ревизии, экспорт заблокирован до разбора.')
        if name=='CaseProductReactionAssessment' and row.get('causality_method') not in (None,'manual_text','imported_text'):
            self.issue('CAUSALITY',path+'/causality_method','G.k.9.i.2','Рассчитанная методика не превращается в свободный текст автоматически; подтвердите отдельное представление оценки.')

    def projection_coverage(self):
        """Legacy projections must not hide facts absent from the canonical blocks."""
        records=active(self.data,'CaseClinicalRecord')
        def represented(name,row,field,code,linked):
            value=row.get(field)
            if not populated(value):return
            candidates=[json.loads(r['fields_json']).get(code,{}) for r in linked]
            def equal(f):
                raw=f.get('raw')
                if raw is None:return False
                if isinstance(value,bool):return raw==str(value).lower()
                if isinstance(value,(int,float)):
                    try:return Decimal(str(value))==Decimal(raw)
                    except InvalidOperation:return False
                if field=='sex':return raw=={'male':'1','female':'2'}.get(value,value)
                if field=='age_unit':return raw=={'years':'a','months':'mo','weeks':'wk','days':'d'}.get(value,value)
                if field in ('onset_date','end_date','date_of_birth','death_date','initial_received_date','latest_received_date'):return raw[:8]==str(value).replace('-','')[:8]
                return str(value)==str(raw)
            if not any(equal(f) for f in candidates):
                self.issue('PROJECTION-COVERAGE',name+'/'+row['id']+'/'+field,code,'Заполненное совместимое/историческое поле не представлено тем же фактом в клиническом блоке ревизии. Нужна содержательная сверка.')
        patient_fields={'patient_initials':'D.1','birth_date_raw':'D.2.1','date_of_birth':'D.2.1','age_value':'D.2.2a','age_unit':'D.2.2b','age_group':'D.2.3','weight_kg':'D.3','height_cm':'D.4','sex':'D.5','medical_history_text':'D.7.2','death_date':'D.9.1','death_date_raw':'D.9.1','patient_identifier':'local.patient_id','pregnancy_status':'local.pregnancy_status','country_code':'local.country','death_cause':'local.death_cause'}
        parent_fields={'patient_initials':'D.10.1','birth_date_raw':'D.10.2.1','date_of_birth':'D.10.2.1','age_value':'D.10.2.2a','age_unit':'D.10.2.2b','weight_kg':'D.10.4','height_cm':'D.10.5','sex':'D.10.6','medical_history_text':'D.10.7.2'}
        reaction_fields={'term_language':'E.i.1.1b','verbatim_term':'E.i.1.2','onset_date_raw':'E.i.4','onset_date':'E.i.4','end_date_raw':'E.i.5','end_date':'E.i.5','medically_confirmed':'E.i.8','term_country_code':'E.i.9','duration_text':'local.duration_text'}
        for name,link in (('Patient','patient_id'),('Reaction','reaction_id'),('CaseProduct','case_product_id'),('CaseNarrative','narrative_id'),('CaseLiteratureReference','literature_id')):
            for row in active(self.data,name):
                linked=[r for r in records if r.get(link)==row['id']]
                fields=patient_fields if name=='Patient' else reaction_fields if name=='Reaction' else {'action_taken':'G.k.8'} if name=='CaseProduct' else {'citation':'C.4.r.1'} if name=='CaseLiteratureReference' else {'narrative_text':{'narrative':'H.1','reporter_comment':'H.2','sender_comment':'H.4','summary':'H.5.r.1a'}.get(row.get('narrative_type'),'local.legacy_text'),'language':'H.5.r.1b'}
                if name=='Patient' and row.get('patient_role')=='parent':fields={**{k:'local.'+k for k in patient_fields},**parent_fields}
                if name=='CaseLiteratureReference':fields={**fields,**{k:'local.'+k for k in ('title','authors','journal','publication_year','volume','issue','pages','doi','pmid','additional_source_text')}}
                for field,code in fields.items():
                    if field=='duration_text' and any('E.i.6a' in json.loads(r['fields_json']) for r in linked):
                        expected=[' '.join(str(json.loads(r['fields_json']).get(c,{}).get('raw') or '') for c in ('E.i.6a','E.i.6b')).strip() for r in linked]
                        if row.get(field) in expected:continue
                    represented(name,row,field,code,linked)
        for field,kind,code in (('narrative','narrative','H.1'),('company_comment','sender_comment','H.4')):
            represented('Case',self.data['case'],field,code,[r for r in records if r['kind']==kind])
        for field,code in (('initial_received_date','C.1.4'),('latest_received_date','C.1.5')):
            represented('Case',self.data['case'],field,code,[r for r in records if r['kind']=='report'])
        for name in ('CaseIdentifier','CaseIdentityDecision','E2BTransmission','IcsrMessage','AuditTrail','SafetyReport','IncomingRequestCaseLink','IncomingRequest'):
            if active(self.data,name):
                self.issue('INTERNAL-EVIDENCE',name,'local.*','Служебные записи, источники импорта и внутренние aliases сохранены в ревизии. В XML выводятся только явно назначенные клинические идентификаторы/факты, не весь журнал PV1.','warning')
        if self.data['case'].get('medical_review_comment'):
            self.issue('UNMAPPED','Case/'+self.data['case']['id']+'/medical_review_comment','H','Медицинский комментарий не назначен подтверждённому виду narrative; требуется явный перенос факта в клинический блок.')

    def run(self):
        self.envelope_nodes()
        for name,anchor in (('Reaction',self.base['patient']),('CaseProduct',self.base['patient'])):
            for row in active(self.data,name):
                node=self.node(name,anchor);self.objects[row['id']]=node
                sub(node.xml,'id',{'root':self.ref(name,row['id'])},index=0)
                self.scalar(name,row,node)
        # Clinical records use their explicit snapshot links, never names or positions.
        pending=active(self.data,'CaseClinicalRecord');done=set()
        while pending:
            available=[r for r in pending if not r.get('parent_id') or r['parent_id'] in done]
            if not available:
                self.issue('LINK','CaseClinicalRecord','', 'Не разрешены родительские связи снимка.');break
            for row in available:
                kind=row['kind'];recipe=self.layout['recipes'].get(kind,{})
                anchor=self.base.get(recipe.get('anchor'))
                if row.get('parent_id'):anchor=self.objects.get(row['parent_id'])
                if row.get('case_product_id'):anchor=self.objects.get(row['case_product_id'])
                elif row.get('reaction_id'):anchor=self.objects.get(row['reaction_id'])
                if kind not in ('pv_decision','study_site') and anchor is None:
                    self.issue('LINK','CaseClinicalRecord/'+row['id'],kind,'Целевой повтор отсутствует в ревизии.');continue
                node=self.clinical(row,anchor)
                if node:
                    self.objects[row['id']]=node
                    if kind.startswith('pair_'):
                        if row.get('reaction_id') not in self.objects:self.issue('LINK',row['id'],'G.k.9.i','Нет целевой реакции.')
                        else:
                            ref=sub(node.xml,'actReference',{'classCode':'OBS','moodCode':'EVN'})
                            sub(ref,'id',{'root':self.ref('Reaction',row['reaction_id'])})
                done.add(row['id'])
            pending=[r for r in pending if r not in available]
        for name in ('CaseDrugDosage','CaseDrugSubstance','CaseDrugIndication','CaseDrugRechallenge','CaseProductReactionAssessment'):
            for row in active(self.data,name):
                product=self.objects.get(row.get('case_product_id'))
                if product is None:self.issue('LINK',name+'/'+row['id'],'G.k','Нет целевого препарата.');continue
                node=self.node(name,self.base['assessment'] if name=='CaseProductReactionAssessment' else product)
                self.scalar(name,row,node)
                if name in ('CaseDrugRechallenge','CaseProductReactionAssessment'):
                    if row.get('reaction_id') not in self.objects:self.issue('LINK',name+'/'+row['id'],'G.k.9.i','Нет целевой реакции.');continue
                    if name=='CaseDrugRechallenge':
                        rel=sub(node.xml,'outboundRelationship1',{'typeCode':'REFR'});ref=sub(rel,'actReference',{'classCode':'OBS','moodCode':'EVN'})
                    else:
                        rel=sub(node.xml,'subject1',{'typeCode':'SUBJ'});ref=sub(rel,'adverseEffectReference',{'classCode':'OBS','moodCode':'EVN'})
                    sub(ref,'id',{'root':self.ref('Reaction',row['reaction_id'])})
                    if name=='CaseProductReactionAssessment':self.product_link(node,row['case_product_id'])
        for row in active(self.data,'CaseProduct'):
            role={'suspect':'1','concomitant':'2','interacting':'3','not_administered':'4'}.get(row.get('drug_role'))
            if not role:self.issue('DRUG-ROLE','CaseProduct/'+row['id']+'/drug_role','G.k.1','Роль препарата не установлена.');continue
            node=self.node('drug_role',self.base['assessment'])
            self.value(node,self.layout['recipes']['drug_role']['fields']['drug_role'],dict(raw=role),'CaseProduct/'+row['id']+'/drug_role')
            self.product_link(node,row['id'])
        self.projection_coverage()
        self.local_rules()
        for item in active(self.data,'Attachment'):
            if item['id'] not in self.used_documents:
                filename=str(item.get('file_name','')).lower()
                if filename.endswith(('.xml','.zip')):
                    self.issue('SOURCE-DOCUMENT','Attachment/'+item['id'],'C.1.6','Исходный XML/ZIP — свидетельство импорта, не вложение исходящего ICSR.','warning')
                else:self.issue('DOCUMENT-PURPOSE','Attachment/'+item['id'],'C.1.6/C.4','Файл не связан с назначением source_document/literature в ревизии; включение/исключение требует содержательного решения.')
        for rule,message in [('MEDDRA','Лицензированный MedDRA: существование кода/версия/LLT→PT не проверены.'),('DICTIONARIES','Не выполнена полная проверка UCUM/ISO/IDMP и версий внешних справочников.'),('ICH-ALL-RULES','Реализован указанный локальный набор правил, не все условные правила IG.'),('RZN','Совместимость с РЗН не подтверждена. Общий XSD не является доказательством принятия.')]:
            self.issue(rule,'profile','',message,'not_performed')
        xml=E.tostring(self.root,encoding='UTF-8',xml_declaration=True,pretty_print=True)
        if len(xml)>MAX_XML_BYTES:self.issue('XML-SIZE','/','','XML превышает 32 MiB.')
        xsd_errors=validate_xml(xml,self.manifest)
        for e in xsd_errors:
            address={}
            try:
                candidates=self.root.getroottree().xpath(e['path'],namespaces=NS)
                node=candidates[0] if candidates else None
                while node is not None:
                    if node in self.addresses:address=self.addresses[node];break
                    mapped=next((v for k,v in self.addresses.items() if node in k.iterancestors()),None)
                    if mapped:address=mapped;break
                    node=node.getparent()
            except E.XPathError:pass
            self.issue('XSD',address.get('path','envelope')+' | '+e['path'],address.get('element','N/C'),e['message'],source='Official ICH package 1.11, MCCI_IN200100UV01.xsd')
        return dict(xml=xml, findings=self.findings, locally_valid=not any(f['level']=='error' for f in self.findings),xsd_valid=not xsd_errors,
                    profile=self.manifest, documents=sorted(self.used_documents), pv_decisions=[r for r in active(self.data,'CaseClinicalRecord') if r['kind']=='pv_decision'])

    def product_link(self,node,product_id):
        rel=sub(node.xml,'subject2',{'typeCode':'SUBJ'});ref=sub(rel,'productUseReference',{'classCode':'SBADM','moodCode':'EVN'})
        sub(ref,'id',{'root':self.ref('CaseProduct',product_id)})

    def envelope_nodes(self):
        env=self.envelope;now=env['created_ts']
        for parent,kind,oid in ((self.root,'batch_id','22'),(self.message,'message_id','1')):
            sub(parent,'id',{'root':OID+'3.'+oid,'extension':env[kind]},index=0)
            sub(parent,'creationTime',{'value':now},index=1)
        for tag,attrs in [('responseModeCode',{'code':'D'}),('interactionId',{'extension':'MCCI_IN200100UV01','root':'2.16.840.1.113883.1.6'}),('name',{'code':'ichicsr','codeSystem':OID+'1.1','codeSystemVersion':'1.01'})]:
            sub(self.root,tag,attrs,index=len(self.root)-1)
        for i,(tag,attrs) in enumerate([('interactionId',{'extension':'PORR_IN049016UV','root':'2.16.840.1.113883.1.6'}),('processingCode',{'code':'P'}),('processingModeCode',{'code':'T'}),('acceptAckCode',{'code':'AL'})],2):sub(self.message,tag,attrs,index=i)
        for parent,batch in ((self.message,False),(self.root,True)):
            for party,code,suffix in [('receiver','RCV','14' if batch else '12'),('sender','SND','13' if batch else '11')]:
                rel=sub(parent,party,{'typeCode':code},index=len(parent)-1 if not batch else None)
                device=sub(rel,'device',{'classCode':'DEV','determinerCode':'INSTANCE'})
                sub(device,'id',{'extension':env[party+'_id'],'root':OID+'3.'+suffix})
        if self.control.find('{'+H+'}code') is None:
            sub(self.control,'code',{'code':'PORR_TE049016UV','codeSystem':'2.16.840.1.113883.1.18'},index=0)
        sub(self.control,'effectiveTime',{'value':now},index=1)
        case=self.data['case'];inv=self.base['investigation'].xml
        for field,code,suffix in [('local_sender_case_id','C.1.1','1'),('worldwide_case_id','C.1.8.1','2')]:
            value=case.get(field)
            if not value:self.issue('CASE-ID','Case/'+field,code,'Нет подтверждённого идентификатора случая в ревизии.');continue
            sub(inv,'id',{'root':OID+'3.'+suffix,'extension':value},index=0)
        scope=case.get('e2b_sender_scope')
        try:sender=json.loads(scope)[1] if scope else None
        except (ValueError,IndexError,TypeError):sender=None
        if sender!=env['sender_id']:
            self.issue('SENDER-SCOPE','Case/e2b_sender_scope','C.1.1/N.2.r.2','ID случая относится к другому либо неподтверждённому отправителю. Нужна подтверждённая идентичность в ревизии; конверт её не меняет.')
        if case.get('e2b_identity_profile') not in ('ich-r3-identity-dates-v1','manual-r3-identity-v1'):
            self.issue('IDENTITY-PROVENANCE','Case/e2b_identity_profile','C.1.1/C.1.8.1','Происхождение идентификаторов не подтверждено для экспорта.')

    def local_rules(self):
        n={'h':H}
        def count(xp):return len(self.root.xpath(xp,namespaces=n))
        records=active(self.data,'CaseClinicalRecord')
        for kind in ('report','patient','sender'):
            rows=[r for r in records if r['kind']==kind]
            if len(rows)!=1:self.issue('SINGLETON','CaseClinicalRecord/'+kind,'C/D','Требуется один текущий блок '+kind+'.')
        reports=[r for r in records if r['kind']=='report']
        report=json.loads(reports[0]['fields_json']) if len(reports)==1 else {}
        for code in ('C.1.3','C.1.4','C.1.5'):
            f=report.get(code,{})
            if not f.get('raw') or f.get('null_flavor'):self.issue('REQUIRED','report/'+code,code,'Обязательное значение отсутствует; не заменяется заглушкой.')
        for code in ('C.1.4','C.1.5'):
            raw=report.get(code,{}).get('raw') or ''
            if len(raw)<8 or not raw[:8].isdigit():self.issue('DATE-PRECISION','report/'+code,code,'ICH требует как минимум точный день.')
            elif raw[:8]>datetime.now(timezone.utc).strftime('%Y%m%d'):self.issue('DATE-FUTURE','report/'+code,code,'Дата получения не может быть будущей по IG.')
        if (report.get('C.1.4',{}).get('raw') or '')[:8]>(report.get('C.1.5',{}).get('raw') or '')[:8]:self.issue('DATE-ORDER','report/C.1.5','C.1.5','Последняя дата получения раньше первоначальной.')
        for node in self.root.xpath('.//*[@codeSystem=$system][@code]',system=MEDDRA):
            if not node.get('codeSystemVersion'):
                address=self.addresses.get(node,{})
                self.issue('MEDDRA-VERSION',address.get('path','clinical'),address.get('element','MedDRA'),'Для заполненного кода MedDRA не указана версия; версия не подставляется.')
        reporters=[r for r in records if r['kind']=='reporter']
        primary=[r for r in reporters if json.loads(r['fields_json']).get('C.2.r.5',{}).get('raw')=='1']
        if len(primary)!=1:self.issue('PRIMARY-SOURCE','CaseClinicalRecord/reporter','C.2.r.5','ICH требует ровно один primary source for regulatory purposes.')
        for r in primary:
            fields=json.loads(r['fields_json'])
            for code in ('C.2.r.3','C.2.r.4'):
                if not fields.get(code,{}).get('raw'):self.issue('PRIMARY-REQUIRED','CaseClinicalRecord/'+r['id']+'/'+code,code,'Для primary source обязательны страна и квалификация.')
        for r in active(self.data,'Reaction'):
            for field,element in [('meddra_llt_code','E.i.2.1b'),('meddra_version','E.i.2.1a')]:
                if not r.get(field):self.issue('REQUIRED','Reaction/'+r['id']+'/'+field,element,'Обязательны LLT и версия; PT не подставляется вместо LLT.')
        if not active(self.data,'Reaction'):self.issue('MINIMUM','Reaction','E.i','Нет реакции.')
        if not any(r.get('drug_role') in ('suspect','interacting') and r.get('reported_product_name') for r in active(self.data,'CaseProduct')):self.issue('MINIMUM','CaseProduct','G.k','Нет идентифицируемого подозреваемого/взаимодействующего препарата.')
        if report.get('C.1.3',{}).get('raw')=='2' and not any(r['kind']=='study' for r in records):self.issue('STUDY','CaseClinicalRecord/study','C.5','Для сообщения исследования требуется блок исследования; он не выводится из канала.')

def export_revision(revision,envelope,storage_root):
    raw=revision['snapshot_json']
    if len(raw.encode('utf-8'))>20*1024*1024:raise ValueError('Снимок превышает локальный лимит 20 MiB.')
    data=json.loads(raw)
    if revision['schema_version']!=SCHEMA or data.get('schema')!=SCHEMA:raise ValueError('Неподдерживаемая версия структуры ревизии.')
    if sha(dump(data).encode())!=revision['checksum_sha256']:raise ValueError('Нарушена контрольная сумма ревизии.')
    if data.get('case',{}).get('id')!=revision['case_id']:raise ValueError('Ревизия не соответствует случаю.')
    if sum(len(r) for r in data.get('records',{}).values())>10000:raise ValueError('Слишком много записей в ревизии для локального экспорта.')
    return Exporter(data,{**envelope,'revision_checksum':revision['checksum_sha256']},storage_root).run()
