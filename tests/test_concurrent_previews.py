import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.test_concurrent_forms import Forms
from tests.test_concurrent_remaining import seed, snapshot, change, version


@pytest.mark.parametrize('group,api', [('incoming', False), ('incoming', True), ('safety', False), ('safety', True)])
def test_case_preview_rejects_changed_catalog_then_accepts_reload(client, group, api):
    if group == 'incoming':
        from tests.test_concurrent_incoming import setup, submission, send
        source, case = setup(client)
        url, method, data = submission(client, source, case, 'create-case', api)
        def post(data):
            return send(client, url, method, data, api)
        def reload():
            return submission(client, source, case, 'create-case', api)[2]
    else:
        from tests.test_concurrent_safety_reports import setup, submission, send
        source = setup(client)
        url, data = submission(client, source, 'create-case', api)
        def post(data):
            return send(client, url, data, 'create-case', api)
        def reload():
            return submission(client, source, 'create-case', api)[1]
    from app.database import SessionLocal
    from app.models import Product
    with SessionLocal() as db:
        product = Product(product_code='PREVIEW', product_name='New catalog product')
        db.add(product)
        db.commit()
    before = snapshot()
    assert post(data).status_code == 409
    assert snapshot() == before
    missing = {key: value for key, value in data.items() if key != 'expected_inputs'}
    assert post(missing).status_code == 422
    response = post(reload())
    assert response.status_code in (200, 201, 303), response.text


@pytest.mark.parametrize('action', ['find', 'requests', 'generate'])
def test_psur_preview_protects_external_inputs(client, action):
    from tests.test_concurrent_psur import setup, request
    from app.database import SessionLocal
    from app.models import Product
    ids = setup(client)
    url, data = request(client, ids, action)
    with SessionLocal() as db:
        db.get(Product, ids['product']).product_name = 'Catalog winner'
        db.commit()
    before = snapshot()
    assert client.post(url, data=data, follow_redirects=False).status_code == 409
    assert snapshot() == before
    missing = {key: value for key, value in data.items() if key != 'expected_inputs'}
    assert client.post(url, data=missing, follow_redirects=False).status_code == 422
    url, data = request(client, ids, action)
    response = client.post(url, data=data, follow_redirects=False)
    assert response.status_code == 303 and 'error=' not in response.headers['location']


def onboarding_form(client, ids):
    url = f"/partner-onboarding/{ids['wizard']}/step/1"
    data = Forms(client.get(url).text).forms[url]
    data.update(partner_code='REMAIN', partner_name='Remaining', partner_type='fn', reconciliation_frequency='not_conducted')
    return url, data


def test_onboarding_stale_catalog_and_two_simultaneous_forms(client):
    ids = seed()
    url, data = onboarding_form(client, ids)
    change('Product', ids['product'])
    before = snapshot()
    assert client.post(url, data=data, follow_redirects=False).status_code == 409
    assert snapshot() == before
    url, data = onboarding_form(client, ids)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: client.post(url, data=data, follow_redirects=False), range(2)))
    assert sorted(result.status_code for result in results) == [303, 409]
    assert version('PartnerOnboardingWizard', ids['wizard']) > int(data['expected_version'])


def test_contract_import_stale_preview_and_atomic_failure(client, monkeypatch):
    payload = Path('examples/contract_import_example.json').read_bytes()
    response = client.post('/contract-imports/preview', files={'import_file': ('contract.json', payload, 'application/json')})
    assert response.status_code == 200
    data = Forms(response.text).forms['/contract-imports/confirm']
    data['confirm_review'] = 'yes'
    ids = seed()
    before = snapshot()
    assert client.post('/contract-imports/confirm', data=data, follow_redirects=False).status_code == 409
    assert snapshot() == before
    response = client.post('/contract-imports/preview', files={'import_file': ('contract.json', payload, 'application/json')})
    data = Forms(response.text).forms['/contract-imports/confirm']
    data['confirm_review'] = 'yes'
    from app import crud
    original = crud.log_create_event
    def fail(db, *args, **kwargs):
        original(db, *args, **kwargs)
        db.flush()
        raise RuntimeError('Synthetic import failure after flush')
    monkeypatch.setattr(crud, 'log_create_event', fail)
    with pytest.raises(RuntimeError, match='Synthetic import failure'):
        client.post('/contract-imports/confirm', data=data, follow_redirects=False)
    assert snapshot() == before


def test_psmf_build_stale_inputs_and_partial_file_failure(client, monkeypatch):
    ids = seed()
    url = '/psmf/partner-build'
    def form():
        page = client.get(f"/psmf?tab=partner&partner_id={ids['partner']}&preview=true")
        assert page.status_code == 200
        data = Forms(page.text).forms[url]
        data.update(partner_id=ids['partner'], build_type='DRAFT')
        return data
    data = form()
    change('PSMFSettings', ids['settings'])
    before = snapshot()
    assert client.post(url, data=data, follow_redirects=False).status_code == 409
    assert snapshot() == before
    from app import psmf
    from app.config import get_settings
    from app.database import SessionLocal
    from app.models import PSMFSettings
    with SessionLocal() as db:
        settings = db.get(PSMFSettings, ids['settings'])
        settings.enable_docx_export = True
        settings.enable_pdf_export = True
        db.commit()
    data = form()
    before = snapshot()
    root = get_settings().storage_root
    root.mkdir(parents=True, exist_ok=True)
    old = root / 'existing.pdf'
    old.write_bytes(b'existing content')
    original = Path.write_bytes
    writes = []
    def failing(path, content):
        writes.append(path)
        original(path, content[:10])
        if len(writes) == 2:
            raise OSError('Synthetic partial write')
        return original(path, content)
    monkeypatch.setattr(Path, 'write_bytes', failing)
    with pytest.raises(OSError, match='Synthetic partial write'):
        client.post(url, data=data, follow_redirects=False)
    assert len(writes) == 2
    assert snapshot() == before
    assert [path for path in root.rglob('*') if path.is_file()] == [old]
    assert old.read_bytes() == b'existing content'


def test_related_task_and_document_require_current_parent(client):
    ids = seed()
    for url, data, files in (
        ('/tasks', dict(title='Related task', related_entity_type='Case', related_entity_id=ids['case'], expected_related_version=version('Case', ids['case'])), None),
        ('/documents', dict(case_id=ids['case'], document_title='Related document', expected_related_version=version('Case', ids['case'])), {'document_file': ('related.txt', b'related', 'text/plain')}),
    ):
        change('Case', ids['case'])
        before = snapshot()
        args = {'data': data, 'follow_redirects': False}
        if files:
            args['files'] = files
        assert client.post(url, **args).status_code == 409
        assert snapshot() == before
        data['expected_related_version'] = version('Case', ids['case'])
        response = client.post(url, **args)
        assert response.status_code == 303, response.text
        assert version('Case', ids['case']) > int(data['expected_related_version'])
