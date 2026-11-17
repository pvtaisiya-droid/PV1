"""Versioned local-only transport settings; no secrets returned to the web UI."""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def dumps(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def registry(path=None):
    location=Path(path or os.environ.get('PV_AS2_CONFIG',''))
    if not location.is_file():raise ValueError('Локальный контур AS2 не настроен: PV_AS2_CONFIG.')
    data=json.loads(location.read_text('utf-8'))
    if data.get('schema')!='pv1-as2-local-1':raise ValueError('Неизвестная версия конфигурации AS2.')
    if len({c['version'] for c in data['versions']})!=len(data['versions']):raise ValueError('Повтор версии AS2.')
    for cfg in data['versions']:
        if cfg.get('contour')!='local_stand' or cfg.get('enabled') is not True:
            raise ValueError('В этом выпуске разрешён только включённый local_stand. РЗН отключён.')
        if cfg['a']['as2_id']==cfg['b']['as2_id']:raise ValueError('Стороны стенда должны различаться.')
        for side in ('a','b'):
            for field in ('url','mdn_url'):
                url=urlsplit(cfg[side][field])
                if url.scheme!='http' or url.hostname!='127.0.0.1' or not url.port or url.username or url.password or url.query or url.fragment:
                    raise ValueError('AS2 разрешён только на явном loopback IPv4 без перенаправления.')
            if urlsplit(cfg[side]['url']).path!='/as2' or urlsplit(cfg[side]['mdn_url']).path!='/mdn':raise ValueError('Неверные пути AS2/MDN.')
            if urlsplit(cfg[side]['url']).netloc!=urlsplit(cfg[side]['mdn_url']).netloc:raise ValueError('MDN должен принадлежать тому же локальному процессу.')
        if cfg.get('algorithms')!={'signature':'sha256','encryption':'aes_256_cbc','key_encryption':'rsaes_pkcs1v15'}:raise ValueError('Неподдержанный локальный набор алгоритмов.')
        if not 1<=cfg['http_timeout']<=10 or not 1<=cfg['max_connect_attempts']<=3:raise ValueError('Неверная политика повторов.')
    return data


def configuration(version=None,path=None):
    data=registry(path);version=version or data['active']
    matches=[c for c in data['versions'] if c['version']==version]
    if len(matches)!=1:raise ValueError('Версия AS2-конфигурации отсутствует; старые версии нельзя удалять до завершения обмена.')
    return {**matches[0],'_runtime':data.get('runtime',{})}


def public(cfg):
    fields=('version','contour','xml_sender_id','xml_receiver_id','algorithms','http_timeout','max_connect_attempts','mdn_wait_seconds','ack_wait_seconds','ack_profile')
    result={k:cfg[k] for k in fields}
    for side in ('a','b'):
        result[side]={k:cfg[side][k] for k in ('as2_id','url','mdn_url','fingerprint')}
    return result


def config_hash(cfg):
    return digest(dumps(public(cfg)).encode())


def base_url(cfg,side='a'):
    port=cfg.get('_runtime',{}).get('ports',{}).get(side)
    if port is not None:
        if not isinstance(port,int) or not 1024<=port<=65535:raise ValueError('Invalid isolated runtime port.')
        return f'http://127.0.0.1:{port}'
    url=urlsplit(cfg[side]['url']);return f'{url.scheme}://{url.netloc}'
