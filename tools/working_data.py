"""Create one isolated working installation from an existing, offline SQLite DB.

The original DB/storage are the rollback set. Never initialize or stamp a DB,
overwrite a destination, copy demo data, or print record contents/credentials.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from tools.backup_snapshot import (FILE_COLUMNS, database_report, database_state,
                                   inventory, quote, readonly, write_json)


def relocated(value, source_root: Path, source_storage: Path, target_storage: Path):
    if not isinstance(value, str) or not value:
        return value
    original = (source_root / value).resolve()
    if original.is_relative_to(source_storage):
        return str(target_storage / original.relative_to(source_storage))
    return value


def relocated_field(table, column, value, source_root, source_storage, target_storage):
    if column in FILE_COLUMNS:
        return relocated(value, source_root, source_storage, target_storage)
    if (table, column) != ('tblE2BImportBatches', 'file_manifest_json') or not value:
        return value
    document = json.loads(value)
    changed = False
    def walk(item):
        nonlocal changed
        if isinstance(item, list):
            return [walk(v) for v in item]
        if isinstance(item, dict):
            result = {}
            for key, old in item.items():
                new = relocated(old, source_root, source_storage, target_storage) if key in FILE_COLUMNS else walk(old)
                changed = changed or new != old
                result[key] = new
            return result
        return item
    result = walk(document)
    return json.dumps(result, ensure_ascii=False, sort_keys=True) if changed else value


def rows_digest(rows):
    def encode(row):
        return json.dumps([{'bytes': v.hex()} if isinstance(v, bytes) else v
                           for v in row], ensure_ascii=False, separators=(',', ':'))
    values = sorted(encode(row) for row in rows)
    return hashlib.sha256('\n'.join(values).encode('utf-8')).hexdigest()


def compare(source, target, source_root, source_storage, target_storage):
    """Compare every old field, allowing only the documented storage relocation."""
    result = {}
    with closing(readonly(source)) as before, closing(readonly(target)) as after:
        tables = [r[0] for r in before.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for table in tables:
            if table == 'alembic_version':
                continue
            columns = [r[1] for r in before.execute(f'PRAGMA table_info({quote(table)})')]
            projection = ','.join(map(quote, columns))
            expected = [tuple(relocated_field(table, column, v, source_root, source_storage, target_storage)
                              for column, v in zip(columns, row))
                        for row in before.execute(f'SELECT {projection} FROM {quote(table)}')]
            actual = after.execute(f'SELECT {projection} FROM {quote(table)}').fetchall()
            expected_hash, actual_hash = rows_digest(expected), rows_digest(actual)
            result[table] = dict(rows=len(expected), columns=len(columns),
                                 expected_sha256=expected_hash, actual_sha256=actual_hash,
                                 preserved=len(expected) == len(actual) and expected_hash == actual_hash)
    if not all(row['preserved'] for row in result.values()):
        raise ValueError('Old fields changed: ' + ', '.join(k for k, v in result.items() if not v['preserved']))
    return result


def environment(destination):
    env = os.environ.copy()
    for name in ('PV_AS2_CONFIG', 'PV_RESTORE_PATH_MAP', 'PYTHONPATH'):
        env.pop(name, None)
    env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
               DATABASE_URL='sqlite:///' + (destination / 'pv1.db').as_posix(),
               PV_STORAGE_ROOT=str(destination / 'storage'), PV_APP_MODE='prod',
               PV_USER_SWITCH_ENABLED='false', PV_ALLOW_QUERY_USER_SWITCH='false',
               PV_AUTH_COOKIE_SECURE='false', PV_AUTH_COOKIE_NAME='pv_session',
               PV_INSTANCE_LABEL='Объединённая рабочая версия — ваши данные')
    return env


def prepare(source_root, source, storage, destination, report_dir, *, offline=False):
    source_root, source, storage, destination, report_dir = [Path(p).resolve() for p in
        (source_root, source, storage, destination, report_dir)]
    if not offline:
        raise ValueError('Stop the verified source writers and specify --offline')
    if not source.is_file() or not storage.is_dir():
        raise ValueError('Existing source DB and storage required; no empty substitutes')
    if destination.exists():
        raise ValueError('Destination already exists; never overwrite or create another copy')
    if any(destination.is_relative_to(p) or p.is_relative_to(destination) for p in (source_root, storage)):
        raise ValueError('Source and destination must be separate')
    report_dir.mkdir(parents=True, exist_ok=True)
    before = database_state(source)
    source_files = inventory(storage)
    baseline = database_report(source, source_root, storage, storage)
    if baseline['integrity'] != ['ok'] or baseline['foreign_key_violations']:
        raise ValueError('Source database integrity or foreign keys are invalid')
    destination.mkdir(parents=True, exist_ok=False)
    target = destination / 'pv1.db'
    with closing(readonly(source)) as old, closing(sqlite3.connect(target)) as new:
        old.backup(new)
    shutil.copytree(storage, destination / 'storage')
    changes = Counter()
    with sqlite3.connect(target) as db:
        for table in baseline['row_counts']:
            columns = {r[1] for r in db.execute(f'PRAGMA table_info({quote(table)})')}
            selected = columns & FILE_COLUMNS
            if table == 'tblE2BImportBatches' and 'file_manifest_json' in columns:
                selected.add('file_manifest_json')
            for column in sorted(selected):
                for rowid, value in db.execute(f'SELECT rowid,{quote(column)} FROM {quote(table)}').fetchall():
                    new = relocated_field(table, column, value, source_root, storage, destination / 'storage')
                    if new != value:
                        db.execute(f'UPDATE {quote(table)} SET {quote(column)}=? WHERE rowid=?', (new, rowid))
                        changes[table + '.' + column] += 1
    env = environment(destination)
    # Assert the effective imported settings before running any migration.
    check = "from pathlib import Path;from app.database import engine;from app.config import get_settings;" \
            "import sys;assert Path(engine.url.database).resolve()==Path(sys.argv[1]);" \
            "assert get_settings().storage_root==Path(sys.argv[2]);print('isolated configuration verified')"
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    commands = [[sys.executable, '-c', check, str(target), str(destination / 'storage')],
                [sys.executable, '-m', 'app.manage', 'upgrade-db'],
                [sys.executable, '-m', 'app.manage', 'check-db']]
    command_results = []
    for index, command in enumerate(commands):
        result = subprocess.run(command, cwd=PROJECT, env=env, capture_output=True,
                                text=True, encoding='utf-8', creationflags=flags, timeout=180)
        (report_dir / f'migration-{index}.log').write_text(result.stdout + result.stderr, encoding='utf-8')
        command_results.append(dict(action=['assert-config', 'upgrade-db', 'check-db'][index], exit_code=result.returncode))
        if result.returncode:
            raise ValueError(f'Migration/check failed; see {report_dir / f"migration-{index}.log"}')
    preserved = compare(source, target, source_root, storage, destination / 'storage')
    recovered = database_report(target, PROJECT, destination / 'storage', destination / 'storage')
    original_missing = {(r['table'], r['id'], r['column'], r['path'])
                        for r in baseline['references'] if r['status'] != 'present'}
    target_missing = {(r['table'], r['id'], r['column'], r['path'])
                      for r in recovered['references'] if r['status'] != 'present'}
    copied_files = inventory(destination / 'storage')
    if (database_state(source) != before or inventory(storage) != source_files
            or copied_files != source_files):
        raise ValueError('Source changed or storage copy differs; do not start the target')
    if recovered['integrity'] != ['ok'] or recovered['foreign_key_violations'] or original_missing != target_missing:
        raise ValueError('Target integrity/reference verification failed')
    result = dict(format='pv1-working-transfer-1', verified_at=datetime.now(timezone.utc).isoformat(),
                  source_database=str(source), source_storage=str(storage),
                  target_database=str(target), target_storage=str(destination / 'storage'),
                  original_database_state=before, migrated_database_state=database_state(target),
                  source_revision=baseline['revision'], target_revision=recovered['revision'],
                  source_unchanged=True, all_original_fields_preserved=True,
                  path_relocations=dict(changes), tables=preserved, commands=command_results,
                  target_row_counts=recovered['row_counts'], integrity=recovered['integrity'],
                  foreign_key_violations=recovered['foreign_key_violations'],
                  storage_files=len(source_files), storage_bytes=sum(v['size'] for v in source_files.values()),
                  storage_inventory=source_files, storage_byte_identical=True,
                  available_references_preserved=sum(r['status'] == 'present' for r in recovered['references']),
                  preexisting_unresolved_references=len(original_missing), new_unresolved_references=0,
                  preexisting_missing=[dict(table=t, id=i, column=c, path=p) for t, i, c, p in sorted(original_missing)],
                  recovery_complete=not original_missing,
                  rollback='Original offline database and storage; no extra full backup created')
    write_json(report_dir / 'transfer-verification.json', result)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('source-root', 'source', 'storage', 'destination', 'report-dir'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--offline', action='store_true')
    args = p.parse_args()
    try:
        result = prepare(args.source_root, args.source, args.storage, args.destination, args.report_dir, offline=args.offline)
    except Exception as exc:
        p.exit(1, f'{type(exc).__name__}: {exc}\nOriginal data retained; do not use incomplete target.\n')
    print(json.dumps({k: result[k] for k in ('source_revision', 'target_revision', 'storage_files',
                     'all_original_fields_preserved', 'new_unresolved_references', 'preexisting_unresolved_references')}, ensure_ascii=False))
