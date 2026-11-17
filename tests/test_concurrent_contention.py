import pytest

from tests.test_concurrent_remaining import seed, snapshot


@pytest.mark.parametrize('serialized_route', [False, True])
def test_busy_snapshot_write_is_a_reload_conflict(client, serialized_route):
    from app.concurrency import lock_snapshot_inputs
    from app.unit_of_work import atomic_write
    from app.database import SessionLocal
    from app.models import Partner
    from sqlalchemy.orm.exc import StaleDataError

    ids = seed()
    @atomic_write(serialize=True)
    def save(*, db):
        raise AssertionError('A busy database must reject before the operation')
    before = snapshot()
    with SessionLocal() as winner, SessionLocal() as contender:
        winner.connection().exec_driver_sql('BEGIN IMMEDIATE')
        contender.connection().exec_driver_sql('PRAGMA busy_timeout=1')
        with pytest.raises(StaleDataError, match='Reload'):
            if serialized_route:
                save(db=contender)
            else:
                lock_snapshot_inputs(contender, ('Partner',))
        contender.rollback()
        winner.rollback()
    assert snapshot() == before


def test_reset_import_can_start_from_missing_current_text(client):
    from app.models import PSMFComponent, PSMFComponentVersion
    from app.database import SessionLocal, engine
    from tools.psmf_import_from_docs import get_components, reset_component_versions

    with SessionLocal() as db:
        component = PSMFComponent(code='EMPTY-IMPORT', title='Empty section', scope='GLOBAL', component_type='MAIN_SECTION')
        db.add(component)
        db.commit()
        identifier = component.id
    mapping = next(row for row in get_components(engine.url.database) if row['id'] == identifier)
    assert mapping['current_version_id'] is None
    mapping.update(approved_content='Synthetic approved text', draft_content='Synthetic draft text')
    reset_component_versions(engine.url.database, [mapping], apply=True)
    with SessionLocal() as db:
        component = db.get(PSMFComponent, identifier)
        assert db.get(PSMFComponentVersion, component.current_version_id).content == 'Synthetic draft text'
        assert component.version > mapping['expected_parent_version']
