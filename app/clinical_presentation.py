"""Read-only labels and bounded form adapters; source clinical semantics stay intact."""
import json
from copy import deepcopy
from app.clinical_values import CONTRACT
from app.drug_labels import LABELS as DRUG_LABELS

LABELS={**DRUG_LABELS, 'reported_term':'Текст реакции', 'outcome':'Исход реакции',
    'C.1.3':'Тип сообщения', 'C.1.4':'Первоначальное получение', 'C.1.5':'Последняя информация',
    'C.5.2':'Название исследования', 'C.5.3':'Номер протокола', 'C.5.4':'Тип исследования',
    'H.1':'Описание случая', 'H.4':'Комментарий отправителя', 'D.1':'Обозначение пациента',
    'F.r.1':'Дата анализа', 'F.r.2.1':'Название анализа', 'F.r.3.2':'Результат', 'F.r.3.3':'Единица результата',
    'local.contacts':'Контакты', 'local.expectedness':'Ожидаемость', 'local.reportability':'Решение о представлении',
    'local.scope':'Направление', 'local.document_version':'Документ / версия RSI', 'local.blinding':'Ослепление',
    'value':'Значение','use':'Назначение','raw':'Исходная строка','state':'Состояние','null_flavor':'nullFlavor',
    'system':'Система','system_version':'Версия словаря','precision':'Точность','timezone':'Часовой пояс',
    'origin':'Происхождение','attributes':'Атрибуты','parts':'Составные части','text':'Текст','code':'Код',
    'root':'Пространство ID','extension':'Значение ID','version':'Версия','case_product_id':'Связь с препаратом',
    'parent_id':'Родительский блок','reaction_id':'Связь с реакцией','attachment_id':'Документ'}
STATES={'missing':'отсутствует','null_flavor':'получено неизвестное значение','invalid':'ошибка разбора',
    'cleared':'очищено вручную','manual':'ручной ввод','received_unvalidated':'получено, словарь не проверен',
    'partial':'неполная дата','source_xml':'исходное сообщение','unknown':'неизвестно','undecided':'решение не принято',
    'expected':'ожидаемая','unexpected':'неожидаемая','submit':'представлять — решение ФН',
    'not_submit':'не представлять — решение ФН','registered':'зарегистрированный препарат','study':'КИ',
    'blinded':'ослеплено','open':'открыто'}
OPTIONS={'local.expectedness':('unknown','expected','unexpected'),'local.reportability':('undecided','submit','not_submit'),
    'local.scope':('unknown','registered','study'),'local.blinding':('unknown','blinded','open')}

def label(key):
    if key in LABELS:return LABELS[key]
    for definition in CONTRACT.values():
        if key in definition['fields']:return definition['fields'][key]['label']
    return key

def kind_label(kind):
    return CONTRACT.get(kind,{}).get('label',{'reaction':'Реакция','product':'Препарат','dosage':'Режим дозирования',
        'substance':'Вещество','indication':'Показание','assessment':'Причинно-следственная связь','rechallenge':'Повторное назначение'}.get(kind,kind))

def display(value):
    if value is None:return 'Не указано'
    if value is True:return 'Да'
    if value is False:return 'Нет'
    return STATES.get(str(value),str(value))

def decoded(value):
    if isinstance(value,str):
        try:return json.loads(value)
        except (ValueError,TypeError):pass
    return value

def brief(values):
    selected=[]
    for key,v in values.items():
        if key in ('interpretation_profile','kind'):continue
        raw=v.get('raw',v.get('value')) if isinstance(v,dict) else v
        if raw is not None and not isinstance(raw,(dict,list)):
            selected.append(label(key)+': '+display(raw)[:90])
        if len(selected)==3:break
    return ' · '.join(selected) or 'Значения не указаны'

def structured(kind,key,value):
    value=value or {};data=value.get('value')
    if data is None:data=decoded(value.get('raw'))
    if key=='local.contacts':
        return dict(mode='rows',columns=[('value','Контакт (например tel: или mailto:)'),('use','Назначение')],rows=data if isinstance(data,list) else [])
    if key=='local.product_identifiers':
        return dict(mode='rows',columns=[('root','Пространство'),('extension','Идентификатор')],rows=data if isinstance(data,list) else [])
    if key in ('F.r.3.2','F.r.3.3'):
        data=data if isinstance(data,dict) else {}
        if data.get('parts') or any(k not in ('attributes','parts','value') for k in data):return dict(mode='preserved')
        attrs=data.get('attributes',{})
        if key=='F.r.3.2' and any(k not in ('value','{http://www.w3.org/2001/XMLSchema-instance}type') for k in attrs):return dict(mode='preserved')
        return dict(mode='scalar',value=attrs.get('value','') if key=='F.r.3.2' else data.get('value',''))
    return dict(mode='text')

def form_raw(form,kind,key,current):
    shape=structured(kind,key,current)
    if shape['mode']=='text':return form.get('raw:'+key) or None
    if shape['mode']=='preserved':raise ValueError('Составной результат сохранён без потерь. Для нового простого результата добавьте отдельную запись анализа.')
    original=deepcopy((current or {}).get('value'))
    if shape['mode']=='rows':
        names=[n for n,_ in shape['columns']];columns=[form.getlist('part:'+key+':'+n) for n in names]
        count=max(map(len,columns),default=0);old=original if isinstance(original,list) else []
        rows=[]
        for index in range(count):
            entered={n:(columns[j][index] if index<len(columns[j]) else '') for j,n in enumerate(names)}
            if not any(entered.values()):
                if index<len(old) and any(k not in names for k in old[index]):raise ValueError('У контакта/ID есть дополнительные исходные атрибуты; используйте явную очистку поля.')
                continue
            row=deepcopy(old[index]) if index<len(old) else {};row.update(entered);rows.append(row)
        return json.dumps(rows,ensure_ascii=False)
    value=form.get('scalar:'+key,'')
    if not value:return None
    data=original if isinstance(original,dict) else {'parts':[]}
    if key=='F.r.3.2':
        from decimal import Decimal,InvalidOperation
        try:
            if not Decimal(value).is_finite():raise InvalidOperation()
        except InvalidOperation:raise ValueError('Результат анализа: введите число, неизвестность или отдельное текстовое описание.') from None
        data['attributes']={**data.get('attributes',{}),'{http://www.w3.org/2001/XMLSchema-instance}type':'PQ','value':value}
    else:data['value']=value
    return json.dumps(data,ensure_ascii=False)
