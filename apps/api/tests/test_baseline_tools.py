"""Recovery tools must reject tampering and keep restores out of existing schemas."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))
import baseline
import database_baseline as database


class SourceBaselineTests(unittest.TestCase):
    def test_secret_exclusion_and_isolated_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'README.md').write_text('recover me', encoding='utf-8')
            (root / 'config.local.toml').write_text('excluded', encoding='utf-8')
            (root / 'apps').mkdir()
            (root / 'apps/key.secret').write_text('excluded', encoding='utf-8')
            (root / 'apps/code.py').write_text('value = 1', encoding='utf-8')
            archive, manifest = baseline.capture(root)
            self.assertEqual(set(manifest['files']), {'README.md', 'apps/code.py'})
            self.assertEqual(baseline.drill(archive, root)['status'], 'passed')
            self.assertEqual((root / 'README.md').read_text(), 'recover me')
            self.assertFalse(list((root / '.tmp/baseline-drills').iterdir()))

    def test_tampered_and_escaping_archives_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'README.md').write_text('original')
            archive, manifest = baseline.capture(root)
            for name, data in [('README.md', b'changed'), ('../escape.py', b'escape')]:
                target = root / 'tampered.zip'
                with zipfile.ZipFile(target, 'w') as output:
                    output.writestr(name, data)
                    output.writestr(baseline.MANIFEST, baseline.canonical(manifest))
                with self.assertRaises(baseline.BaselineError):
                    baseline.drill(target, root)

    def test_non_disposable_database_schema_rejected(self):
        for name in ('public', 'baseline_drill_', 'baseline_drill_' + 'a' * 32 + ';DROP SCHEMA public'):
            with self.assertRaises(baseline.BaselineError):
                database.safe_schema(name)

    def test_encryption_integrity_and_json_round_trip(self):
        from cryptography.fernet import InvalidToken
        value = {'format': 'campus-application-backup-v1', 'revision': database.LATEST_REVISION,
            'migrations': database.migrations(), 'tables': {table.name: [] for table in database.TABLES}}
        value['tables_digest'] = baseline.digest(baseline.canonical(value['tables']))
        with tempfile.TemporaryDirectory() as folder:
            archive, key = database.save(value, Path(folder))
            self.assertEqual(database.load(archive, key), value)
            damaged = bytearray(archive.read_bytes())
            damaged[len(damaged) // 2] ^= 1
            archive.write_bytes(damaged)
            with self.assertRaises(InvalidToken):
                database.load(archive, key)
        row = {column.name: None for column in database.state.columns}
        row['draft'] = {'date': {'__type__': 'date', 'value': 'user supplied JSON'}}
        self.assertEqual(database.decode_row(database.state, database.encode_row(database.state, row)), row)

    def test_opengauss_date_returned_as_datetime(self):
        from datetime import datetime
        row = {column.name: None for column in database.schedule.columns}
        row['course_date'] = datetime(2026, 10, 20)
        self.assertEqual(database.encode_row(database.schedule, row)['course_date'], '2026-10-20')


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Requires isolated PostgreSQL/openGauss development database')
class DatabaseRecoveryTests(unittest.TestCase):
    def test_migrations_and_row_recovery_leave_original_unchanged(self):
        import sqlalchemy as sa
        from datetime import date, datetime, time, timezone
        from test_config import PostgresConfigTests
        fixture = PostgresConfigTests('test_draft_publish_replay_conflict_and_rollback')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with fixture.engine.begin() as connection:
            connection.execute(sa.text('CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)'))
            connection.execute(sa.text('INSERT INTO alembic_version VALUES (:revision)'), {'revision': database.LATEST_REVISION})
            connection.execute(database.students.insert().values(tenant_id='recovery-test', id='student-one',
                student_no='one', name='Synthetic student', is_mock=True, source_tag='recovery-test',
                created_at=datetime.now(timezone.utc)))
            connection.execute(database.schedule.insert().values(id='recovery-row', tenant_id='recovery-test',
                student_id='student-one', course_date=date(2026, 10, 20), course_name='Synthetic course',
                start_time=time(9), end_time=time(10), location='test', teacher='test', is_mock=True,
                source_tag='recovery-test', created_at=datetime.now(timezone.utc)))
        value = database.snapshot(fixture.engine)
        from app.modules.intent.requirements import RequirementStore, SaveRequirement
        RequirementStore(fixture.store).save(SaveRequirement(title='Recovery request', description='Synthetic only', idempotency_key='recovery-request-key'), 'trace')
        value = database.snapshot(fixture.engine)
        result = database.restore_drill(value, fixture.base_engine)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(database.snapshot(fixture.engine)['tables'], value['tables'])
        old = dict(value, revision='0006_application_runtime', migrations=database.migrations('0006_application_runtime'))
        old['tables'] = {name: rows for name, rows in value['tables'].items() if name in {t.name for t in database.tables_for('0006_application_runtime')}}
        old['tables_digest'] = baseline.digest(baseline.canonical(old['tables']))
        self.assertEqual(database.restore_drill(old, fixture.base_engine)['status'], 'passed')
        older = dict(value, revision='0007_assistant_requirement', migrations=database.migrations('0007_assistant_requirement'))
        older['tables'] = {table.name: [{key: row[key] for key in table.c.keys()} for row in value['tables'][table.name]]
            for table in database.tables_for('0007_assistant_requirement')}
        older['tables_digest'] = baseline.digest(baseline.canonical(older['tables']))
        self.assertEqual(database.restore_drill(older, fixture.base_engine)['status'], 'passed')
        broken = dict(value, migrations={})
        with patch.object(fixture.base_engine, 'connect', side_effect=AssertionError('must validate first')):
            with self.assertRaises(baseline.BaselineError):
                database.restore_drill(broken, fixture.base_engine)
