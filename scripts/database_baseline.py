"""Encrypted application-table snapshots; restore only into a new disposable schema."""
import argparse
from datetime import date, datetime, time, timezone
import importlib.util
import json
from pathlib import Path
import re
import sys
from uuid import uuid4

from baseline import ROOT, BaselineError, canonical, digest, inventory

sys.path.insert(0, str(ROOT / 'apps/api'))
import sqlalchemy as sa
from cryptography.fernet import Fernet
from app.db import LATEST_REVISION, get_engine
from app.modules.config.store import state, releases, audit, outbox
from app.modules.config.application_store import applications
from app.modules.config.question_library import questions
from app.modules.skills.business import students, schedule
from app.modules.intent.requirements import tasks, commands
from app.modules.forms.runtime import records as form_records
from app.modules.approvals.runtime import instances as approval_instances
from app.modules.forms.drafts import drafts as business_drafts
from app.modules.config.lifecycle import lifecycle

TABLES = (students, schedule, applications, state, releases, audit, outbox, questions, tasks, commands, form_records, approval_instances, lifecycle, business_drafts)
TASKS_V7 = sa.Table('assistant_requirement', sa.MetaData(), *[
    sa.Column(column.name, column.type, primary_key=column.primary_key, nullable=column.nullable)
    for column in tasks.columns if column.name not in ('business_kind', 'target_application_id')])
MAX_BYTES = 64 * 1024 * 1024
MAX_ROWS = 100000


def migrations(revision=LATEST_REVISION):
    return {name: record['sha256'] for name, record in inventory().items()
        if name.startswith('apps/api/alembic/versions/') and name.endswith('.py')
        and int(Path(name).name[:4]) <= int(revision[:4])}


def encode_row(table, row):
    result = dict(row)
    for column in table.columns:
        value = result[column.name]
        # openGauss can return SQL DATE as datetime; preserve SQL date semantics.
        if isinstance(column.type, sa.Date) and not isinstance(column.type, sa.DateTime) and isinstance(value, datetime):
            value = value.date()
        if value is not None and isinstance(column.type, (sa.DateTime, sa.Date, sa.Time)):
            value = value.isoformat()
        result[column.name] = value
    return result


def decode_row(table, row):
    result = dict(row)
    if set(result) != set(table.c.keys()):
        raise BaselineError('Unexpected snapshot columns')
    for column in table.columns:
        value = result[column.name]
        if value is not None:
            if isinstance(column.type, sa.DateTime):
                result[column.name] = datetime.fromisoformat(value)
            elif isinstance(column.type, sa.Date):
                result[column.name] = date.fromisoformat(value)
            elif isinstance(column.type, sa.Time):
                result[column.name] = time.fromisoformat(value)
    return result


def read_tables(connection, selected=TABLES):
    result = {}
    total = 0
    for table in selected:
        rows = connection.execute(sa.select(table).order_by(*table.primary_key.columns).limit(MAX_ROWS + 1)).mappings().all()
        total += len(rows)
        if total > MAX_ROWS:
            raise BaselineError('Snapshot row budget exceeded')
        result[table.name] = [encode_row(table, row) for row in rows]
    if len(canonical(result)) > MAX_BYTES:
        raise BaselineError('Snapshot byte budget exceeded')
    return result


def snapshot(engine):
    with engine.begin() as connection:
        connection.execute(sa.text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        revision = connection.scalar(sa.text('SELECT version_num FROM alembic_version'))
        if revision != LATEST_REVISION:
            raise BaselineError('Database revision does not match this tool')
        rows = read_tables(connection)
    return {'format': 'campus-application-backup-v1', 'created_at': datetime.now(timezone.utc).isoformat(),
        'revision': revision, 'source_digest': digest(canonical(inventory())), 'migrations': migrations(),
        'tables': rows, 'tables_digest': digest(canonical(rows)),
        'scope': 'fourteen application tables only; excludes DB roles/extensions, local settings, model keys and caches'}


def validate(value):
    if (not isinstance(value, dict) or value.get('format') != 'campus-application-backup-v1'
            or value.get('revision') not in ('0006_application_runtime', '0007_assistant_requirement', '0008_requirement_creation', '0009_form_records', '0010_approval_instances', LATEST_REVISION)
            or value.get('migrations') != migrations(value['revision'])
            or not isinstance(value.get('tables'), dict)
            or set(value['tables']) != {table.name for table in tables_for(value['revision'])}
            or value.get('tables_digest') != digest(canonical(value['tables']))):
        raise BaselineError('Snapshot format, migration or integrity mismatch')
    if sum(len(rows) for rows in value['tables'].values()) > MAX_ROWS:
        raise BaselineError('Snapshot row budget exceeded')
    for table in tables_for(value['revision']):
        for row in value['tables'][table.name]:
            decode_row(table, row)
    return value


def tables_for(revision):
    if revision == '0006_application_runtime':
        return TABLES[:8]
    if revision == '0007_assistant_requirement':
        return (*TABLES[:8], TASKS_V7)
    if revision == '0008_requirement_creation':
        return TABLES[:10]
    if revision == '0009_form_records':
        return TABLES[:11]
    if revision == '0010_approval_instances':
        return TABLES[:12]
    return TABLES


def save(value, folder):
    validate(value)
    folder.mkdir(parents=True, exist_ok=True)
    key_path = folder / 'restore-key.secret'
    if not key_path.exists():
        with key_path.open('xb') as stream:
            stream.write(Fernet.generate_key())
    cipher = Fernet(key_path.read_bytes())
    payload = canonical(value)
    if len(payload) > MAX_BYTES:
        raise BaselineError('Snapshot byte budget exceeded')
    target = folder / ('application-' + uuid4().hex + '.backup.secret')
    with target.open('xb') as stream:
        stream.write(cipher.encrypt(payload))
    if load(target, key_path) != value:
        raise BaselineError('Encrypted snapshot verification failed')
    return target, key_path


def load(path, key_path):
    if path.stat().st_size > MAX_BYTES * 2:
        raise BaselineError('Encrypted snapshot exceeds budget')
    data = Fernet(key_path.read_bytes()).decrypt(path.read_bytes())
    if len(data) > MAX_BYTES:
        raise BaselineError('Decrypted snapshot exceeds budget')
    return validate(json.loads(data))


def safe_schema(name):
    if not re.fullmatch(r'baseline_drill_[a-f0-9]{32}', name):
        raise BaselineError('Refusing non-disposable schema')
    return name


def restore_drill(value, engine):
    validate(value)  # Validate before creating or deleting anything.
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.migrations import OpenGaussImpl  # noqa: F401
    schema = safe_schema('baseline_drill_' + uuid4().hex)
    created = False
    # One connection owns the isolated schema. SET LOCAL never changes pooled session defaults.
    with engine.connect() as connection:
        try:
            with connection.begin():
                connection.execute(sa.schema.CreateSchema(schema))
            created = True
            with connection.begin():
                connection.execute(sa.text('SET LOCAL search_path TO ' + safe_schema(schema)))
                if connection.scalar(sa.text('SELECT current_schema()')) != schema:
                    raise BaselineError('Disposable schema was not selected')
                for name in sorted(value['migrations']):
                    spec = importlib.util.spec_from_file_location('baseline_migration', ROOT / name)
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    with Operations.context(MigrationContext.configure(connection)):
                        module.upgrade()
                selected = tables_for(value['revision'])
                for table in reversed(selected):
                    connection.execute(table.delete())
                for table in selected:
                    rows = [decode_row(table, row) for row in value['tables'][table.name]]
                    if rows:
                        connection.execute(table.insert(), rows)
                restored = read_tables(connection, selected)
                if restored != value['tables']:
                    raise BaselineError('Restored rows differ from snapshot')
                # Stamp only the newly created schema, never the original database schema.
                connection.execute(sa.text('CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)'))
                connection.execute(sa.text('INSERT INTO alembic_version VALUES (:revision)'), {'revision': value['revision']})
            return {'status': 'passed', 'tables_digest': value['tables_digest'],
                'row_counts': {name: len(rows) for name, rows in restored.items()},
                'scope': 'migration replay and exact application-row recovery in disposable schema; not full DB disaster recovery'}
        finally:
            if connection.in_transaction():
                connection.rollback()
            if created:
                with connection.begin():
                    connection.execute(sa.schema.DropSchema(safe_schema(schema), cascade=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['capture', 'drill'])
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--key', type=Path)
    parser.add_argument('--report', type=Path, default=ROOT / '.local/backups/latest-drill.json')
    args = parser.parse_args()
    try:
        if args.operation == 'capture':
            value = snapshot(get_engine())
            archive, key = save(value, ROOT / '.local/backups')
            result = {'status': 'passed', 'archive': str(archive), 'key_file': str(key),
                'tables_digest': value['tables_digest'], 'source_digest': value['source_digest'],
                'scope': value['scope'], 'restore_tested': False}
        else:
            if not args.archive or not args.key:
                parser.error('--archive and --key are required for a drill')
            value = load(args.archive, args.key)
            result = restore_drill(value, get_engine())
            result.update({'archive': str(args.archive), 'source_digest': value['source_digest']})
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_bytes(canonical(result) + b'\n')
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        # Never expose driver exception text: it can contain connection credentials or data.
        result = {'status': 'failed', 'error_type': type(exc).__name__, 'message': 'Backup/drill failed; inspect environment without printing credentials'}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_bytes(canonical(result) + b'\n')
        print(json.dumps(result))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
