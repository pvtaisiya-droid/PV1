"""Apply a reviewed, sourced catalogue name correction with an atomic audit trail.

Never imports the app or opens its default database. Only name fields in the
specified existing SQLite database may change; product links and flags are kept.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4


FIELDS = {'substance_name', 'substance_name_normalized', 'inn_name'}
ACTION = 'substance_name_repair'


def connect(database, writable=False):
    connection = sqlite3.connect(Path(database).resolve().as_uri() + ('?mode=rw' if writable else '?mode=ro'), uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    if not writable:
        connection.execute('PRAGMA query_only=ON')
    return connection


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def catalogue(connection):
    return [dict(row) for row in connection.execute('SELECT * FROM tblSubstances ORDER BY id')]


def validate_review(review):
    identifiers = set()
    for item in review:
        if not isinstance(item, dict) or not item.get('id') or item['id'] in identifiers:
            raise ValueError('Review requires unique substance identifiers.')
        identifiers.add(item['id'])
        before, after = item.get('before'), item.get('after')
        if (not isinstance(before, dict) or not isinstance(after, dict) or not before
                or set(before) != set(after) or not set(after) <= FIELDS):
            raise ValueError('Only explicitly reviewed name fields may change.')
        if not item.get('verified') or not item.get('sources') or any(
                not isinstance(url, str) or not url.startswith('https://') for url in item['sources']):
            raise ValueError('Every correction requires verified source URLs.')
        if any(not isinstance(value, str) or not value.strip() or len(value) > 255 for value in after.values()):
            raise ValueError('Corrected names must be nonempty strings of at most 255 characters.')
        if 'inn_name' in after and re.search('[\u0400-\u04ff]', after['inn_name']):
            raise ValueError('The English name cannot contain Cyrillic.')
        if before == after:
            raise ValueError('Review contains an unchanged record.')


def prepare_plan(database, review):
    validate_review(review)
    with closing(connect(database)) as connection:
        rows = catalogue(connection)
        by_id = {row['id']: row for row in rows}
        for item in review:
            row = by_id.get(item['id'])
            if not row or row['is_deleted'] or row['version'] != item['expected_version'] or any(
                    row[name] != value for name, value in item['before'].items()):
                raise ValueError('A reviewed substance has changed. Prepare a new review.')
        return dict(format=1, catalogue_digest=digest(rows), changes=review,
                    plan_id='substance-names-' + digest(review)[:24])


def apply_plan(database, plan, *, actor_label, reason):
    if not isinstance(actor_label, str) or not actor_label.strip() or len(actor_label) > 100 or not reason or not reason.strip():
        raise ValueError('An actor label and correction reason are required.')
    if plan.get('format') != 1 or not isinstance(plan.get('changes'), list) or not plan['changes']:
        raise ValueError('Unsupported or empty name correction plan.')
    changes = plan['changes']
    validate_review(changes)
    if plan.get('plan_id') != 'substance-names-' + digest(changes)[:24]:
        raise ValueError('The reviewed plan identifier does not match its contents.')
    expected_events = sum(sum(item['before'][name] != value for name, value in item['after'].items()) for item in changes)
    with closing(connect(database, writable=True)) as connection:
        connection.execute('BEGIN IMMEDIATE')
        try:
            rows = catalogue(connection)
            by_id = {row['id']: row for row in rows}
            if digest(rows) != plan.get('catalogue_digest'):
                applied = all(item['id'] in by_id and by_id[item['id']]['version'] == item['expected_version'] + 1
                    and all(by_id[item['id']][name] == value for name, value in item['after'].items()) for item in changes)
                audits = connection.execute('SELECT count(*) FROM tblAuditTrail WHERE correlation_id=? AND action=?',
                                            (plan['plan_id'], ACTION)).fetchone()[0]
                if applied and audits == expected_events:
                    connection.rollback()
                    return dict(changed_records=0, audit_events=0, already_applied=True, plan_id=plan['plan_id'])
                raise ValueError('Catalogue changed after review. No correction was committed.')
            timestamp = datetime.now(timezone.utc).isoformat(sep=' ')
            for item in changes:
                row = by_id.get(item['id'])
                if not row or row['is_deleted'] or row['version'] != item['expected_version'] or any(
                        row[name] != value for name, value in item['before'].items()):
                    raise ValueError('A reviewed substance has changed. No correction was committed.')
                fields = sorted(item['after'])
                assignments = ', '.join(name + '=?' for name in fields)
                # Column identifiers are strictly constrained by validate_review.
                cursor = connection.execute(f'UPDATE tblSubstances SET {assignments}, version=version+1, updated_at=? WHERE id=? AND version=?',
                    [item['after'][name] for name in fields] + [timestamp, item['id'], item['expected_version']])
                if cursor.rowcount != 1:
                    raise ValueError('Concurrent substance change. No correction was committed.')
                source_reason = reason.strip() + '\nSources: ' + '; '.join(item['sources'])
                for name in fields:
                    if item['before'][name] == item['after'][name]:
                        continue
                    connection.execute('''INSERT INTO tblAuditTrail
                        (id,entity_type,entity_id,action,field_name,old_value,new_value,change_reason,
                         user_id,changed_by,timestamp,changed_at,source_module,comment,correlation_id,
                         created_at,updated_at,is_active,is_deleted,version)
                        VALUES (?,'Substance',?,?,?,?,?,?,NULL,?,?,?,'Substances',?,?,?, ?,1,0,1)''',
                        (str(uuid4()), item['id'], ACTION, name, item['before'][name], item['after'][name], source_reason,
                         actor_label.strip(), timestamp, timestamp,
                         'Executed by ' + actor_label.strip() + '; ' + reason.strip(), plan['plan_id'], timestamp, timestamp))
            if connection.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('Foreign key violation. No correction was committed.')
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return dict(changed_records=len(changes), audit_events=expected_events, already_applied=False, plan_id=plan['plan_id'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'apply'])
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--review', type=Path)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--actor-label')
    parser.add_argument('--reason')
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            if not args.review:
                raise ValueError('--review is required.')
            review = json.loads(args.review.read_text(encoding='utf-8'))
            result = prepare_plan(args.database, review)
            with args.plan.open('x', encoding='utf-8') as output:
                json.dump(result, output, ensure_ascii=False, indent=2)
            print(json.dumps(dict(proposed_changes=len(result['changes']), plan_id=result['plan_id'])))
        else:
            plan = json.loads(args.plan.read_text(encoding='utf-8'))
            print(json.dumps(apply_plan(args.database, plan, actor_label=args.actor_label, reason=args.reason)))
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, 'Name repair refused: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__) + '\n')


if __name__ == '__main__':
    main()
