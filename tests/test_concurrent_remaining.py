"""HTTP versions and composed transactions for the remaining audit groups."""
import ast
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select


def seed():
    from app import crud, models as m, schemas
    from app.database import SessionLocal
    with SessionLocal() as db:
        partner = crud.create_partner(db, schemas.PartnerCreate(partner_code='REMAIN', partner_name='Remaining'))
        other = crud.create_partner(db, schemas.PartnerCreate(partner_code='OTHER', partner_name='Other'))
        product = crud.create_product(db, schemas.ProductCreate(product_name='Remaining product'))
        substance = crud.create_substance(db, schemas.SubstanceCreate(substance_name='Remaining substance'))
        contract = crud.create_contract(db, schemas.ContractCreate(partner_id=partner.id, contract_number='Remaining contract', contract_date='2026-01-01', valid_until='2099-01-01'))
        case = crud.create_case(db, schemas.CaseCreate(partner_id=partner.id, initial_received_date='2026-10-06'))
        provisional = crud.create_case(db, schemas.CaseCreate(initial_received_date='2026-10-06'))
        cp = crud.add_case_product(db, case, schemas.CaseProductCreate(product_id=product.id))
        reaction = crud.add_reaction(db, case, schemas.ReactionCreate(reported_term='Synthetic reaction'))
        followup = crud.add_followup(db, case, schemas.FollowUpCreate(received_date='2026-10-06', message_direction='partner_to_our_company'))
        role = db.query(m.Role).filter_by(role_code='readonly_auditor').one()
        user = crud.create_app_user(db, login='remaining-user', password='Remaining-user-2026!', full_name='Remaining user', role_ids=[role.id])
        extra_role = db.query(m.Role).filter(m.Role.role_code.notin_(['readonly_auditor', 'admin'])).first()
        crud.assign_user_role(db, user_id=user.id, role_id=extra_role.id)
        task = crud.create_task(db, schemas.TaskCreate(title='Remaining task'))
        plan = m.LiteratureMonitoringPlan(partner_id=partner.id, monitoring_sources='PubMed', start_date=date(2026, 1, 1))
        db.add(plan)
        db.flush()
        log = m.LiteratureSearchLog(plan_id=plan.id, search_date=date(2026, 10, 6), period_start=date(2026, 7, 1), period_end=date(2026, 10, 6), search_source='PubMed')
        db.add(log)
        db.flush()
        result = m.LiteratureResult(plan_id=plan.id, search_log_id=log.id, partner_id=partner.id, publication_title='Synthetic publication')
        wizard = m.PartnerOnboardingWizard(partner_id=partner.id)
        build = m.PSMFPartnerBuild(partner_id=partner.id, build_type='FINAL')
        db.add_all([result, wizard, build])
        db.commit()
        rows = locals()
        ids = {key: rows[key].id for key in ('partner', 'other', 'product', 'substance', 'contract', 'case', 'provisional', 'cp', 'reaction', 'followup', 'role', 'user', 'task', 'plan', 'log', 'result', 'wizard', 'build')}
        ids['block'] = db.query(m.PSMFBlock).filter_by(is_deleted=False).first().id
        ids['settings'] = db.query(m.PSMFSettings).first().id
        ids['admin_role'] = db.query(m.Role).filter_by(role_code='admin').one().id
        return ids


def snapshot():
    from app.database import SessionLocal, Base
    with SessionLocal() as db:
        return {
            table.name: [tuple(row) for row in db.execute(select(table).order_by(*table.primary_key.columns))]
            for table in Base.metadata.tables.values()
            if table.name not in {'tblAuthSessions', 'outlook_oauth_requests'}
        }


def version(kind, identifier):
    from app import models
    from app.database import SessionLocal
    model = getattr(models, kind)
    with SessionLocal() as db:
        return getattr(db.get(model, identifier), model.__mapper__.version_id_col.key)


def change(kind, identifier):
    from app import models
    from app.database import SessionLocal
    model = getattr(models, kind)
    with SessionLocal() as db:
        row = db.get(model, identifier)
        key = model.__mapper__.version_id_col.key
        setattr(row, key, getattr(row, key) + 1)
        db.commit()


def specs():
    # Route, model, ID in seed, field. Read actual FastAPI route definitions so
    # the matrix cannot silently miss a newly protected operation.
    for path in Path('app/routers').glob('*.py'):
        source = path.read_text(encoding='utf-8')
        for node in ast.parse(source).body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            checks = [n for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'protect_record']
            if not checks or node.name in {'create_case_form', 'create_manual_case_form', 'api_create_case'}:
                continue
            check = next((call for call in checks if isinstance(call.args[3], ast.Name) and call.args[3].id == 'expected_version'), checks[0])
            if len(check.args) != 4 or not isinstance(check.args[2], (ast.Name, ast.Attribute)):
                continue
            if not isinstance(check.args[1], ast.Attribute):
                continue
            if not isinstance(check.args[3], ast.Name):
                continue
            route = next(d for d in node.decorator_list if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == 'post')
            model = check.args[1].attr
            yield (path.stem, node.name, route.args[0].value, model, ast.unparse(check.args[2]), check.args[3].id)


SPECS = list(specs())


def request_data(spec, ids):
    file, name, route, model, key, token = spec
    from app.routers import cases, tasks, users_roles, literature_monitoring, partner_onboarding, psmf, products, accounting_data, contract_contacts, substances
    modules = locals()
    source = Path(f'app/routers/{file}.py').read_text(encoding='utf-8')
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name)
    data = dict(partner_id=ids['partner'], product_id=ids['product'], contract_id=ids['contract'], plan_id=ids['plan'], search_log_id=ids['log'],
        case_product_id=ids['cp'], reaction_id=ids['reaction'], role_id=ids['role'], substance_id=ids['substance'],
        monitoring_sources='PubMed', start_date='2026-01-01', search_date='2026-10-06', period_start='2026-07-01', period_end='2026-10-06',
        search_source='PubMed', publication_title='Synthetic publication', reported_term='Synthetic reaction',
        causality_method='who_umc', relatedness='possible', assessor_role='ars', assessor_name='Synthetic assessor',
        message_direction='partner_to_our_company', status_value='completed', login='remaining-user', full_name='Remaining user',
        password='Remaining-user-2026!', reason='Synthetic correction', registration_number='REMAIN-REG',
        partner_code='REMAIN', partner_name='Remaining', year='2026', quarter='4', title='Synthetic task',
        last_name='Synthetic', first_name='Contact', position='PV', identifier_value='EXTERNAL-REMAIN',
        identifier_type='other', organization_name='Synthetic org', company_name='Synthetic company',
        psmf_template_version='1.0', default_empty_state_text='No content', target='rmp', target_id='Synthetic RMP')
    selected_key = {'Case': 'case', 'Task': 'task', 'User': 'user', 'Role': 'role', 'LiteratureMonitoringPlan': 'plan',
        'LiteratureSearchLog': 'log', 'LiteratureResult': 'result', 'PartnerOnboardingWizard': 'wizard', 'PSMFBlock': 'block',
        'PSMFPartnerBuild': 'build', 'Product': 'product', 'Contract': 'contract', 'Partner': 'partner', 'PSMFSettings': 'settings'}[model]
    if 'assign_numbering' in name:
        selected_key = 'provisional'
    if 'correct_numbering' in name:
        data['partner_id'] = ids['other']
    if name == 'assign_role_form':
        data['role_id'] = ids['admin_role']
    if name == 'update_literature_plan_form':
        data['status_value'] = 'active'
    values = dict(case_id=ids[selected_key] if model == 'Case' else ids['case'], task_id=ids['task'], user_id=ids['user'],
        role_id=ids['role'], plan_id=ids['plan'], log_id=ids['log'], result_id=ids['result'], wizard_id=ids['wizard'],
        block_id=ids['block'], build_id=ids['build'], product_id=ids['product'], followup_id=ids['followup'], step=1)
    for name_, value in values.items():
        route = route.replace('{'+name_+'}', str(value))
    data[token] = version(model, ids[selected_key])
    data['expected_plan_version'] = version('LiteratureMonitoringPlan', ids['plan'])
    data['expected_followup_version'] = version('FollowUp', ids['followup'])
    data['product_ids'] = [ids['product']]
    from app.concurrency import snapshot_token, ONBOARDING_INPUTS, LITERATURE_INPUTS
    from app.database import SessionLocal
    with SessionLocal() as db:
        data['expected_snapshot'] = snapshot_token(db, ONBOARDING_INPUTS)
        data['expected_inputs'] = snapshot_token(db, LITERATURE_INPUTS)
    files = {'document_file': ('synthetic.pdf', b'%PDF-1.4\nSynthetic', 'application/pdf')} if 'attachment' in name else None
    if name == 'upload_literature_result_documents_form':
        files = {'article_pdf': ('synthetic.pdf', b'%PDF-1.4\nSynthetic', 'application/pdf')}
    if name.startswith('api_'):
        payload_names = {'api_add_patient': {}, 'api_add_case_product': {'product_id': ids['product']},
            'api_add_reaction': {'reported_term': 'Synthetic reaction'}, 'api_add_followup': {'message_direction': 'partner_to_our_company'},
            'api_add_case_partner': {'partner_id': ids['other']}, 'api_add_case_identifier': {'identifier_value': 'EXTERNAL-REMAIN', 'identifier_type': 'other'},
            'api_assign_numbering_partner': {'partner_id': ids['partner']}, 'api_correct_numbering_partner': {'partner_id': ids['other'], 'reason': 'Correction'},
            'api_create_contract_contact': {'partner_id': ids['partner'], 'last_name': 'Synthetic', 'first_name': 'Contact', 'position': 'PV'},
            'api_create_product_substance': {'product_id': ids['product'], 'substance_id': ids['substance']}}
        return route, {'params': {token: data[token]}, 'json': payload_names[name]}, selected_key
    return route, {'data': data, **({'files': files} if files else {})}, selected_key


@pytest.mark.parametrize('spec', SPECS, ids=lambda spec: spec[1])
def test_remaining_stale_missing_zero_and_reload(client, spec):
    ids = seed()
    url, arguments, key = request_data(spec, ids)
    token = spec[-1]
    channel = 'params' if spec[1].startswith('api_') else 'data'
    change(spec[3], ids[key])
    before = snapshot()
    stale = client.post(url, **arguments, follow_redirects=False)
    assert stale.status_code == 409, stale.text
    assert 'Обновите страницу' in stale.json()['detail']
    for invalid in (None, 0):
        candidate = {**arguments, channel: dict(arguments[channel])}
        if invalid is None:
            candidate[channel].pop(token)
        else:
            candidate[channel][token] = invalid
        response = client.post(url, **candidate, follow_redirects=False)
        assert response.status_code == 422, response.text
    assert snapshot() == before
    url, arguments, key = request_data(spec, ids)
    saved = client.post(url, **arguments, follow_redirects=False)
    assert saved.status_code in (200, 201, 303), saved.text
    assert 'error=' not in saved.headers.get('location', '') and 'validation=' not in saved.headers.get('location', ''), saved.headers.get('location')
    assert version(spec[3], ids[key]) > arguments[channel][token]


LATE = ['add_patient_form', 'add_reaction_form', 'add_followup_form', 'add_causality_assessment_form',
        'update_followup_direction_form', 'update_task_status_form', 'edit_user_form', 'reset_user_password_form',
        'assign_role_form', 'update_role_permissions_form', 'upload_psmf_block_attachment', 'create_product_registration_form',
        'create_accounting_period_form', 'create_contract_contact_form', 'create_product_substance_form',
        'update_literature_plan_form', 'create_literature_result_form', 'upload_literature_result_documents_form']


@pytest.mark.parametrize('name', LATE)
def test_late_conflict_rolls_back_whole_operation(client, monkeypatch, name):
    import importlib
    ids = seed()
    spec = next(spec for spec in SPECS if spec[1] == name)
    url, arguments, key = request_data(spec, ids)
    module = importlib.import_module('app.routers.'+spec[0])
    original = module.protect_record
    state = {}
    def competing(db, model, identifier, expected):
        result = original(db, model, identifier, expected)
        if not state:
            change(model.__name__, identifier)
            state['before'] = snapshot()
        return result
    monkeypatch.setattr(module, 'protect_record', competing)
    if name == 'upload_literature_result_documents_form':
        arguments['files'] = {'article_pdf': ('synthetic.pdf', b'%PDF-1.4\nSynthetic', 'application/pdf')}
    from app.config import get_settings
    root = get_settings().storage_root
    files = {str(path): path.read_bytes() for path in root.rglob('*') if path.is_file()}
    response = client.post(url, **arguments, follow_redirects=False)
    assert response.status_code == 409, response.text
    assert snapshot() == state['before']
    assert {str(path): path.read_bytes() for path in root.rglob('*') if path.is_file()} == files


def test_direct_child_change_invalidates_parent_and_late_closure_rejects_child(client):
    from app import models as m
    from app.database import SessionLocal
    from sqlalchemy.orm.exc import StaleDataError
    ids = seed()
    before = version('Case', ids['case'])
    with SessionLocal() as db:
        db.get(m.CaseProduct, ids['cp']).dose_value = '5'
        db.commit()
    assert version('Case', ids['case']) > before
    with SessionLocal() as stale, SessionLocal() as winner:
        parent = stale.get(m.Case, ids['case'])
        child = stale.get(m.Patient, 'not-present')
        closing = winner.get(m.Case, ids['case'])
        closing.is_locked = True
        closing.workflow_status = 'closed'
        winner.commit()
        stale.add(m.Patient(case_id=parent.id, age_value=42))
        with pytest.raises(StaleDataError):
            stale.commit()
        stale.rollback()
    with SessionLocal() as db:
        assert not db.query(m.Patient).filter_by(case_id=ids['case']).count()
        assert db.get(m.Case, ids['case']).is_locked


def test_composed_literature_upload_failure_preserves_old_file_and_all_rows(client, monkeypatch):
    from app import crud
    from app.config import get_settings
    ids = seed()
    before = snapshot()
    root = get_settings().storage_root
    root.mkdir(parents=True, exist_ok=True)
    old = root / 'old.pdf'
    old.write_bytes(b'old document')
    original = crud.create_attachment
    calls = []
    def failing(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result.id)
        if len(calls) == 2:
            raise RuntimeError('Synthetic failure after second attachment flush')
        return result
    monkeypatch.setattr(crud, 'create_attachment', failing)
    with pytest.raises(RuntimeError, match='Synthetic failure'):
        client.post(f"/literature-monitoring/results/{ids['result']}/documents", data={
            'expected_version': version('LiteratureResult', ids['result'])}, files={
            'article_pdf': ('article.pdf', b'new article', 'application/pdf'),
            'search_screenshot': ('screen.png', b'new screenshot', 'image/png')})
    assert len(calls) == 2
    assert snapshot() == before
    assert [path for path in root.rglob('*') if path.is_file()] == [old]
    assert old.read_bytes() == b'old document'
