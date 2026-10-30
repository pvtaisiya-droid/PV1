from sqlalchemy import select
import pytest

from tests.test_concurrent_forms import Forms, target


def psmf_target(client, kind, action):
    from app.database import SessionLocal
    from app.models import PSMFComponent, PSMFComponentVersion, PSMFBlock, PSMFBlockVersion

    assert client.get('/psmf').status_code == 200
    state = {'approve': 'under_review', 'return-revision': 'under_review',
             'new-version': 'approved'}.get(action, 'draft')
    with SessionLocal() as db:
        component = PSMFComponent(code='CONCURRENT-TEXT', title='Concurrent text', status=state)
        db.add(component)
        db.flush()
        if kind == 'components':
            row = component
            version = PSMFComponentVersion(component_id=row.id, version='0.1', content='Original',
                                           status=state, is_locked=state != 'draft')
        else:
            row = PSMFBlock(component_id=component.id, code='CONCURRENT-BLOCK', title_ru='Concurrent block',
                            block_type='PARTNER_BLOCK', status=state)
            db.add(row)
            db.flush()
            version = PSMFBlockVersion(block_id=row.id, version='0.1', content='Original',
                                      status=state, is_locked=state != 'draft')
        db.add(version)
        db.flush()
        row.current_version_id = version.id
        db.commit()
        row_id, version_id = row.id, version.id
    page = (f'/psmf?tab=chapters&component_id={row_id}' if kind == 'components'
            else f'/psmf/blocks/{row_id}/edit')
    if action == 'return-revision':
        page = '/psmf?tab=approval'
    return type(row), type(version), row_id, version_id, page, f'/psmf/{kind}/{row_id}/{action}'


def state_snapshot():
    from app.database import SessionLocal
    from app.models import AuditTrail, PSMFComponent, PSMFComponentVersion, PSMFBlock, PSMFBlockVersion
    with SessionLocal() as db:
        return [db.execute(select(model.__table__).order_by(model.id)).all()
                for model in (PSMFComponent, PSMFComponentVersion, PSMFBlock, PSMFBlockVersion, AuditTrail)]


ACTIONS = [('components', action) for action in ('save', 'submit', 'approve', 'return-revision', 'new-version')]
ACTIONS += [('blocks', action) for action in ('save', 'submit', 'approve', 'return-revision', 'new-version', 'partner-review')]


@pytest.mark.parametrize('kind,action', ACTIONS)
@pytest.mark.parametrize('changed', ['parent', 'text'])
def test_psmf_forms_reject_changed_parent_or_text_and_accept_reload(client, kind, action, changed):
    from app.database import SessionLocal

    model, text_model, row_id, version_id, page, url = psmf_target(client, kind, action)
    html = client.get(page)
    assert html.status_code == 200, html.text
    tokens = Forms(html.text).forms[url]
    data = {**tokens, 'content': 'Reloaded content', 'return_comment': 'Clarification',
            'approval_comment': 'Reviewed'}
    with SessionLocal() as db:
        if changed == 'parent':
            db.get(model, row_id).version += 1
        else:
            db.get(text_model, version_id).content = 'Another user edited the text'
        db.commit()
    before = state_snapshot()
    for key in ('expected_version', 'expected_content_version'):
        missing = {k: v for k, v in data.items() if k != key}
        assert client.post(url, data=missing, follow_redirects=False).status_code == 422
        invalid = {**data, key: 0}
        assert client.post(url, data=invalid, follow_redirects=False).status_code == 422
    result = client.post(url, data=data, follow_redirects=False)
    assert result.status_code == 409, result.text
    assert 'Обновите страницу' in result.json()['detail']
    assert state_snapshot() == before
    fresh = Forms(client.get(page).text).forms[url]
    with SessionLocal() as db:
        source_before = db.get(text_model, version_id)
        source_content, source_updated = source_before.content, source_before.updated_at
    result = client.post(url, data={**data, **fresh}, follow_redirects=False)
    assert result.status_code == 303, result.text
    with SessionLocal() as db:
        row, version = db.get(model, row_id), db.get(text_model, version_id)
        if action == 'save':
            assert version.content == 'Reloaded content'
        elif action == 'new-version':
            assert len(row.versions) == 2 and row.status == 'draft'
            assert row.current_version_id != version_id
            assert version.content == source_content and version.updated_at == source_updated
        elif action == 'partner-review':
            assert version.partner_review_status == 'approved'
        else:
            expected = {'submit': 'under_review', 'approve': 'approved', 'return-revision': 'draft'}[action]
            assert row.status == version.status == expected
            assert version.is_locked is (expected != 'draft')


@pytest.mark.parametrize('kind', ['components', 'blocks'])
def test_psmf_late_text_conflict_rolls_back_parent_and_audit(client, monkeypatch, kind):
    from app import psmf
    from app.database import SessionLocal

    model, text_model, row_id, version_id, page, url = psmf_target(client, kind, 'save')
    tokens = Forms(client.get(page).text).forms[url]
    name = 'save_component_draft' if kind == 'components' else 'save_block_draft'
    original = getattr(psmf, name)
    snapshots = []

    def racing_save(db, row, **kwargs):
        with SessionLocal() as other:
            other.get(text_model, version_id).content = 'Committed winner'
            other.commit()
        snapshots.append(state_snapshot())
        return original(db, row, **kwargs)

    monkeypatch.setattr(psmf, name, racing_save)
    result = client.post(url, data={**tokens, 'content': 'Late loser'}, follow_redirects=False)
    assert result.status_code == 409, result.text
    assert state_snapshot() == snapshots[0]


def test_contact_late_conflict_rolls_back_audit_and_deletion(client, monkeypatch):
    from app import crud
    from app.database import SessionLocal
    from app.models import AuditTrail

    model, row_id, page, url, _, data = target('contact')
    tokens = Forms(client.get(page).text).forms[url]
    original = crud.delete_contract_contact
    counts = []

    def racing_delete(db, row, **kwargs):
        with SessionLocal() as other:
            other.get(model, row_id).last_name = 'Committed winner'
            other.commit()
            counts.append(other.query(AuditTrail).count())
        return original(db, row, **kwargs)

    monkeypatch.setattr(crud, 'delete_contract_contact', racing_delete)
    result = client.post(url.removesuffix('edit') + 'delete', data=tokens, follow_redirects=False)
    assert result.status_code == 409, result.text
    with SessionLocal() as db:
        row = db.get(model, row_id)
        assert row.last_name == 'Committed winner' and not row.is_deleted and row.is_active
        assert db.query(AuditTrail).count() == counts[0]


@pytest.mark.parametrize('kind', ['components', 'blocks'])
@pytest.mark.parametrize('changed', ['parent', 'text'])
def test_psmf_new_version_late_conflict_does_not_insert_duplicate_or_stale_copy(client, monkeypatch, kind, changed):
    from app import psmf
    from app.database import SessionLocal

    model, text_model, row_id, version_id, page, url = psmf_target(client, kind, 'new-version')
    tokens = Forms(client.get(page).text).forms[url]
    name = 'create_new_component_version' if kind == 'components' else 'create_new_block_version'
    original = getattr(psmf, name)
    snapshots = []

    def racing_create(db, row, **kwargs):
        with SessionLocal() as other:
            if changed == 'parent':
                original(other, other.get(model, row_id), created_by='Winner')
            else:
                other.get(text_model, version_id).content = 'Changed source'
                other.commit()
        snapshots.append(state_snapshot())
        return original(db, row, **kwargs)

    monkeypatch.setattr(psmf, name, racing_create)
    result = client.post(url, data=tokens, follow_redirects=False)
    assert result.status_code == 409, result.text
    assert state_snapshot() == snapshots[0]


def test_psmf_text_version_migration_preserves_business_rows_and_audit(client, tmp_path):
    from contextlib import closing
    import sqlite3
    from app.database import engine
    from tests.test_migrations import migrate_test_database
    import os
    import subprocess
    import sys

    psmf_target(client, 'components', 'save')
    path = tmp_path / 'psmf-migration.db'
    with closing(sqlite3.connect(engine.url.database)) as source, closing(sqlite3.connect(path)) as copy:
        source.backup(copy)
    env = os.environ.copy()
    env['DATABASE_URL'] = f'sqlite:///{path.as_posix()}'
    env['PV_STORAGE_ROOT'] = str(tmp_path / 'psmf-migration-storage')
    result = subprocess.run([sys.executable, '-m', 'alembic', 'downgrade', '20261002_0027'],
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    with closing(sqlite3.connect(path)) as db:
        columns = [r[1] for r in db.execute('PRAGMA table_info(psmf_component_versions)')]
        assert 'row_version' not in columns
        before = db.execute('SELECT * FROM psmf_component_versions ORDER BY id').fetchall()
        parents = db.execute('SELECT * FROM psmf_components ORDER BY id').fetchall()
        audit = db.execute('SELECT * FROM tblAuditTrail ORDER BY id').fetchall()
    for _ in range(2):
        result = migrate_test_database(path)
        assert result.returncode == 0, result.stderr
        with closing(sqlite3.connect(path)) as db:
            selection = ','.join(f'"{c}"' for c in columns)
            assert db.execute(f'SELECT {selection} FROM psmf_component_versions ORDER BY id').fetchall() == before
            assert db.execute('SELECT * FROM psmf_components ORDER BY id').fetchall() == parents
            assert db.execute('SELECT * FROM tblAuditTrail ORDER BY id').fetchall() == audit
            assert db.execute('SELECT DISTINCT row_version FROM psmf_component_versions').fetchall() == [(1,)]
            assert db.execute('SELECT version_num FROM alembic_version').fetchone() == ('20261002_0028',)
            assert db.execute('PRAGMA foreign_key_check').fetchall() == []
            assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)


def test_contact_delete_validates_both_rendered_forms_and_current_token(client):
    import re
    from app.database import SessionLocal
    from app.models import AuditTrail

    model, row_id, page, edit_url, _, _ = target('contact')
    url = edit_url.removesuffix('edit') + 'delete'
    fragments = re.findall(r'<form\b[^>]*action="' + re.escape(url) + r'"[^>]*>.*?</form>',
                           client.get(page).text, flags=re.S)
    assert len(fragments) == 2  # partner preview and main contact table
    tokens = [Forms(fragment).forms[url]['expected_version'] for fragment in fragments]
    assert tokens[0] == tokens[1]
    response = client.get(f'/api/contract-contacts/{row_id}')
    assert response.status_code == 200 and response.json()['version'] == int(tokens[0])
    with SessionLocal() as db:
        count = db.query(AuditTrail).count()
    for data in ({}, {'expected_version': 0}, {'expected_version': -1}):
        assert client.post(url, data=data, follow_redirects=False).status_code == 422
    with SessionLocal() as db:
        assert db.query(AuditTrail).count() == count and not db.get(model, row_id).is_deleted
    result = client.post(url, data={'expected_version': tokens[0], 'delete_reason': 'Verified deletion'},
                         follow_redirects=False)
    assert result.status_code == 303, result.text
    with SessionLocal() as db:
        row = db.get(model, row_id)
        assert row.is_deleted and not row.is_active and row.version > int(tokens[0])
        assert row.delete_reason == 'Verified deletion'
        events = db.query(AuditTrail).filter_by(entity_type='ContractContact', entity_id=row_id, action='soft_delete').all()
        assert len(events) == 1


@pytest.mark.parametrize('column,expected_success', [('INTEGER NOT NULL DEFAULT 7', True),
                                                    ('INTEGER NOT NULL DEFAULT 0', False),
                                                    ('TEXT NOT NULL DEFAULT 7', False)])
def test_psmf_migration_adoption_validates_existing_counter(client, tmp_path, column, expected_success):
    from contextlib import closing
    import sqlite3
    import os
    import subprocess
    import sys
    from app.database import engine
    from tests.test_migrations import migrate_test_database

    psmf_target(client, 'components', 'save')
    path = tmp_path / 'adopt-counter.db'
    with closing(sqlite3.connect(engine.url.database)) as source, closing(sqlite3.connect(path)) as copy:
        source.backup(copy)
    env = os.environ.copy()
    env['DATABASE_URL'] = f'sqlite:///{path.as_posix()}'
    env['PV_STORAGE_ROOT'] = str(tmp_path / 'adopt-counter-storage')
    result = subprocess.run([sys.executable, '-m', 'alembic', 'downgrade', '20261002_0027'],
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    with closing(sqlite3.connect(path)) as db:
        db.execute(f'ALTER TABLE psmf_component_versions ADD COLUMN row_version {column}')
        db.commit()
        before = db.execute('SELECT * FROM psmf_component_versions ORDER BY id').fetchall()
        audit = db.execute('SELECT * FROM tblAuditTrail ORDER BY id').fetchall()
    result = migrate_test_database(path)
    assert (result.returncode == 0) is expected_success, result.stderr
    if not expected_success:
        assert 'row_version must be a non-null integer' in result.stderr or 'invalid row versions' in result.stderr
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('SELECT * FROM psmf_component_versions ORDER BY id').fetchall() == before
        assert db.execute('SELECT * FROM tblAuditTrail ORDER BY id').fetchall() == audit
        expected_head = '20261002_0028' if expected_success else '20261002_0027'
        assert db.execute('SELECT version_num FROM alembic_version').fetchone() == (expected_head,)
