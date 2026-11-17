"""Documented synthetic contract, deliberately NOT an RZN ACK parser."""
import json

PROFILE='pv1-local-ack-1'


def parse(raw):
    if len(raw)>1024*1024:raise ValueError('ACK превышает локальный лимит.')
    data=json.loads(raw)
    if not isinstance(data,dict) or set(data)!={'profile','batch_id','artifact_sha256','original_as2_message_id','results'} or data.get('profile')!=PROFILE:
        raise ValueError('Неизвестный профиль/структура ACK; требуется разбор.')
    if not isinstance(data['original_as2_message_id'],str) or not data['original_as2_message_id'] or not isinstance(data['batch_id'],str) or not data['batch_id'] or not isinstance(data['artifact_sha256'],str) or len(data['artifact_sha256'])!=64:
        raise ValueError('Нет точных идентификаторов ACK.')
    if not isinstance(data['results'],list) or not 1<=len(data['results'])<=100:raise ValueError('Неверное число результатов ACK.')
    seen=set()
    for result in data['results']:
        if not isinstance(result,dict) or set(result)!={'message_id','code','detail'} or not isinstance(result['message_id'],str) or not result['message_id'] or not isinstance(result['detail'],str):
            raise ValueError('Неверная структура результата ACK.')
        if result['message_id'] in seen:raise ValueError('Повтор идентификатора результата ACK.')
        seen.add(result['message_id'])
        if result['code'] not in ('accepted','rejected','review'):result['interpretation']='unknown'
        else:result['interpretation']=result['code']
    return data
