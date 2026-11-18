import copy
import hashlib
import json
import sqlite3

import pytest

from tools.substance_name_repair import ACTION, apply_plan, prepare_plan


@pytest.fixture
def reference_database(tmp_path):
    database = tmp_path / 'references.db'
    with sqlite3.connect(database) as db:
        db.executescript('''CREATE TABLE tblSubstances (
            id TEXT PRIMARY KEY,substance_name TEXT,substance_name_normalized TEXT,
            inn_name TEXT,version INTEGER,is_deleted INTEGER,is_active INTEGER,updated_at TEXT);
            CREATE TABLE tblAuditTrail (
            id TEXT PRIMARY KEY,entity_type TEXT,entity_id TEXT,action TEXT,field_name TEXT,
            old_value TEXT,new_value TEXT,change_reason TEXT,user_id TEXT,changed_by TEXT,
            timestamp TEXT,changed_at TEXT,source_module TEXT,comment TEXT,correlation_id TEXT,
            created_at TEXT,updated_at TEXT,is_active INTEGER,is_deleted INTEGER,version INTEGER);
            CREATE TABLE unrelated (id TEXT PRIMARY KEY,value TEXT);
            INSERT INTO unrelated VALUES ('one','retain');''')
        db.executemany('INSERT INTO tblSubstances VALUES (?,?,?,?,1,0,1,?)', [
            ('a', 'Russian name A', 'russian name a', 'Wrong English A', '2026-01-01'),
            ('b', 'Russian name B', 'russian name b', 'Wrong English B', '2026-01-01'),
            ('c', 'Russian name C', 'russian name c', 'Keep valid English', '2026-01-01'),
        ])
    return database


def review():
    return [dict(id=identifier, expected_version=1, before={'inn_name': 'Wrong English ' + letter},
                 after={'inn_name': 'Correct English ' + letter}, verified=True,
                 sources=['https://example.org/official/' + identifier])
            for identifier, letter in [('a', 'A'), ('b', 'B')]]


def state(database):
    with sqlite3.connect(database) as db:
        return {name: db.execute('SELECT * FROM ' + name + ' ORDER BY id').fetchall()
                for name in ['tblSubstances', 'tblAuditTrail', 'unrelated']}


def apply(database, plan):
    return apply_plan(database, plan, actor_label='Codex maintenance', reason='Explicit synthetic correction request')


def test_prepare_is_read_only_and_application_is_audited_and_idempotent(reference_database):
    before = state(reference_database)
    checksum = hashlib.sha256(reference_database.read_bytes()).hexdigest()
    plan = prepare_plan(reference_database, review())
    assert plan == prepare_plan(reference_database, review())
    assert state(reference_database) == before
    assert hashlib.sha256(reference_database.read_bytes()).hexdigest() == checksum
    result = apply(reference_database, plan)
    assert result['changed_records'] == result['audit_events'] == 2
    with sqlite3.connect(reference_database) as db:
        assert db.execute('SELECT inn_name,version FROM tblSubstances WHERE id="a"').fetchone() == ('Correct English A', 2)
        assert db.execute('SELECT substance_name,substance_name_normalized FROM tblSubstances WHERE id="a"').fetchone() == ('Russian name A', 'russian name a')
        audit = db.execute('SELECT old_value,new_value,changed_by,user_id,change_reason FROM tblAuditTrail WHERE entity_id="a"').fetchone()
        assert audit[:4] == ('Wrong English A', 'Correct English A', 'Codex maintenance', None)
        assert 'https://example.org/official/a' in audit[4]
    after = state(reference_database)
    assert after['unrelated'] == before['unrelated']
    assert after['tblSubstances'][2] == before['tblSubstances'][2]
    assert apply(reference_database, plan)['already_applied']
    assert state(reference_database) == after


@pytest.mark.parametrize('change', ['version', 'business_value'])
def test_stale_plan_never_overwrites_new_catalogue_state(reference_database, change):
    plan = prepare_plan(reference_database, review())
    with sqlite3.connect(reference_database) as db:
        if change == 'version':
            db.execute('UPDATE tblSubstances SET version=2 WHERE id="b"')
        else:
            db.execute('UPDATE tblSubstances SET inn_name="A newer verified English name" WHERE id="b"')
    before = state(reference_database)
    with pytest.raises(ValueError, match='Catalogue changed'):
        apply(reference_database, plan)
    assert state(reference_database) == before


def test_failure_in_second_update_rolls_back_first_update_and_audit(reference_database):
    plan = prepare_plan(reference_database, review())
    with sqlite3.connect(reference_database) as db:
        db.execute('''CREATE TRIGGER fail_second BEFORE UPDATE ON tblSubstances
                      WHEN NEW.id='b' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END''')
    before = state(reference_database)
    with pytest.raises(sqlite3.IntegrityError):
        apply(reference_database, plan)
    assert state(reference_database) == before


@pytest.mark.parametrize('invalid', ['unknown_field', 'cyrillic_english', 'no_source', 'unverified', 'blank_name', 'changed_old_value'])
def test_invalid_review_is_rejected_without_writing(reference_database, invalid):
    values = review()
    if invalid == 'unknown_field':
        values[0]['before'] = {'is_active': 1}
        values[0]['after'] = {'is_active': 0}
    elif invalid == 'cyrillic_english':
        values[0]['after']['inn_name'] = '\u0420\u0443\u0441\u0441\u043a\u043e\u0435'
    elif invalid == 'no_source':
        values[0]['sources'] = []
    elif invalid == 'unverified':
        values[0]['verified'] = False
    elif invalid == 'blank_name':
        values[0]['after']['inn_name'] = ' '
    else:
        values[0]['before']['inn_name'] = 'Incorrect old value'
    before = state(reference_database)
    with pytest.raises(ValueError):
        prepare_plan(reference_database, values)
    assert state(reference_database) == before


def test_tampered_plan_and_missing_actor_are_rejected(reference_database):
    plan = prepare_plan(reference_database, review())
    before = state(reference_database)
    tampered = copy.deepcopy(plan)
    tampered['changes'][0]['after']['inn_name'] = 'A different name'
    with pytest.raises(ValueError, match='identifier'):
        apply(reference_database, tampered)
    with pytest.raises(ValueError, match='actor'):
        apply_plan(reference_database, plan, actor_label=None, reason='A reason')
    assert state(reference_database) == before


def test_missing_database_is_never_created(tmp_path):
    missing = tmp_path / 'missing.db'
    with pytest.raises(sqlite3.OperationalError):
        prepare_plan(missing, review())
    assert not missing.exists()
