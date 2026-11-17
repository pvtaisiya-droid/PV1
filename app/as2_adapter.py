"""Only PV1-to-engine integration boundary. Authenticated loopback, never arbitrary URLs."""
import base64
import json
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from urllib.error import HTTPError
from app.as2_settings import base_url,configuration


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def call(path,body=None,*,cfg=None,side='a'):
    cfg=cfg or configuration()
    token=Path(cfg[side]['token_file']).read_text('ascii').strip()
    request=Request(base_url(cfg,side)+'/control/'+path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    try:
        with build_opener(ProxyHandler({}),NoRedirect()).open(request,timeout=cfg['http_timeout']+2) as response:
            raw=response.read(48*1024*1024+1)
            if len(raw)>48*1024*1024:raise ValueError('Слишком большой ответ локального движка.')
            return json.loads(raw)
    except HTTPError as exc:
        if exc.code==404:return None
        raise ValueError('Локальный движок отклонил запрос: HTTP '+str(exc.code)) from None


def submit(attempt_id,raw,cfg):
    return call('send',dict(request_id=attempt_id,config_version=cfg['version'],payload=base64.b64encode(raw).decode(),kind='icsr'),cfg=cfg)
