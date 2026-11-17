"""Pinned, offline, unmodified official ICH XSD closure. No recipient claim."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname
from lxml import etree as E

BASE = Path(__file__).with_name('resources') / 'ich-r3-1.11'
MANIFEST_SHA256 = '8b91ad9b752db05e8fba63bbea23ca4c0b7cab46f46d7466fbc0f2542b2287a0'
MAX_XML_BYTES = 32 * 1024 * 1024

def sha(data):
    return hashlib.sha256(data).hexdigest()

def _profile():
    raw = (BASE/'manifest.json').read_bytes()
    if sha(raw) != MANIFEST_SHA256:
        raise ValueError('Контрольная сумма локального профиля ICH не совпадает.')
    result = json.loads(raw)
    for relative, expected in result['files'].items():
        path = (BASE/relative).resolve()
        if not path.is_relative_to(BASE.resolve()) or sha(path.read_bytes()) != expected:
            raise ValueError('Нарушена целостность локального комплекта ICH: '+relative)
    return {**result, 'manifest_sha256': MANIFEST_SHA256}

def profile():
    try:
        return _profile()
    except OSError:
        raise ValueError('Локальный комплект ICH отсутствует или недоступен. Восстановите закреплённые файлы профиля.') from None

class LocalResolver(E.Resolver):
    def __init__(self, manifest):
        self.allowed = {(BASE/p).resolve(): h for p,h in manifest['files'].items() if p.endswith('.xsd')}

    def resolve(self, url, pubid, context):
        parsed = urlsplit(url)
        if parsed.scheme == 'file':
            if parsed.netloc:
                raise ValueError('Сетевые XSD запрещены.')
            path = Path(url2pathname(parsed.path)).resolve()
        elif not parsed.scheme or (len(parsed.scheme)==1 and url[1:3] in (':/', ':\\')):
            path = Path(url).resolve()
        else:
            raise ValueError('Сетевые XSD запрещены.')
        if path not in self.allowed:
            raise ValueError('XSD вне доверенного локального комплекта.')
        raw=path.read_bytes()
        if sha(raw)!=self.allowed[path]:
            raise ValueError('XSD изменён после выбора профиля.')
        return self.resolve_string(raw, context, base_url=path.as_uri())

def validate_xml(xml, manifest=None):
    manifest = manifest or profile()
    if len(xml)>MAX_XML_BYTES or b'<!doctype' in xml.lower() or b'<!entity' in xml.lower():
        return [dict(path='/', message='XML превышает лимит либо содержит DTD/entity.')]
    parser=E.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
    parser.resolvers.add(LocalResolver(manifest))
    schema=E.XMLSchema(E.parse(str((BASE/manifest['entry_point']).resolve()),parser))
    try:
        document=E.fromstring(xml,E.XMLParser(resolve_entities=False,no_network=True,load_dtd=False,huge_tree=False))
    except E.XMLSyntaxError:
        return [dict(path='/',message='XML не является корректным документом.')]
    if document.getroottree().docinfo.doctype:
        return [dict(path='/',message='DTD запрещён независимо от кодировки XML.')]
    if schema.validate(document):
        return []
    return [dict(path=e.path or '/',message=e.message[:1000]) for e in list(schema.error_log)[:30]]
