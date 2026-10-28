import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from support import student_headers
from app.modules.config.schema import DisplayConfig, default_config


class ConfigBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_demo_defaults_without_database(self):
        with patch.dict(os.environ, {'DATABASE_URL': ''}):
            response = self.client.get('/api/v1/config/course-display', headers=student_headers(self))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['source'], 'builtin-demo')

    def test_admin_disabled_and_unauthorized(self):
        with patch.dict(os.environ, {'LOCAL_ADMIN_TOKEN': ''}):
            self.assertEqual(self.client.get('/api/v1/admin/course-display').status_code, 503)
        with patch.dict(os.environ, {'LOCAL_ADMIN_TOKEN': 'a' * 24}):
            self.assertEqual(self.client.get('/api/v1/admin/course-display').status_code, 401)
            response = self.client.post('/api/v1/admin/course-display/publish',
                headers={'Authorization': 'Bearer ' + 'a' * 24, 'Origin': 'https://example.com'}, json={})
            self.assertEqual(response.status_code, 403)

    def test_unknown_duplicate_and_all_hidden_fields_rejected(self):
        for columns in [
            [{'field': 'student_id', 'label': '学号'}],
            [{'field': 'name', 'label': '课程'}, {'field': 'name', 'label': '重复'}],
            [{'field': 'name', 'label': '课程', 'visible': False}],
            [{'field': 'name', 'label': '   '}],
        ]:
            with self.subTest(columns=columns), self.assertRaises(ValueError):
                DisplayConfig(columns=columns)

    def test_request_captures_one_release(self):
        config = {'release_id': 'release-one', 'content': default_config().model_dump()}
        with patch('app.main.published_config', return_value=config) as read:
            response = self.client.get('/api/v1/course-schedule?start_date=2026-10-16&end_date=2026-10-16', headers=student_headers(self))
            self.assertEqual(response.json()['ui_hint']['release_id'], 'release-one')
            read.assert_called_once()


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), '需要开发用 PostgreSQL TEST_DATABASE_URL；未以 SQLite 替代')
class PostgresConfigTests(unittest.TestCase):
    def setUp(self):
        import importlib.util
        from pathlib import Path
        from uuid import uuid4
        import sqlalchemy as sa
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from app.modules.config.store import ConfigStore
        from app.migrations import OpenGaussImpl  # noqa: F401

        self.schema = 'config_test_' + uuid4().hex
        self.base_engine = sa.create_engine(os.environ['TEST_DATABASE_URL'])
        with self.base_engine.begin() as connection:
            connection.execute(sa.schema.CreateSchema(self.schema))
        self.engine = sa.create_engine(os.environ['TEST_DATABASE_URL'],
            connect_args={'options': '-csearch_path=' + self.schema})
        self.addCleanup(self.cleanup_schema)
        with self.engine.begin() as connection:
            self.assertEqual(connection.scalar(sa.text('SELECT current_schema()')), self.schema)
            for name in ('0001_config.py', '0002_mvp.py', '0003_biz_schedule.py', '0004_question_library.py', '0005_applications.py', '0006_application_runtime.py', '0007_assistant_requirement.py', '0008_requirement_creation.py', '0009_form_records.py', '0010_approval_instances.py', '0011_work_drafts_lifecycle.py'):
                path = Path(__file__).resolve().parents[1] / 'alembic/versions' / name
                spec = importlib.util.spec_from_file_location('config_migration', path)
                migration = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(migration)
                with Operations.context(MigrationContext.configure(connection)):
                    migration.upgrade()
        self.store = ConfigStore(self.engine)

    def cleanup_schema(self):
        import sqlalchemy as sa
        # Only the unique schema created by this test may be removed.
        if not self.schema.startswith('config_test_') or len(self.schema) != 44:
            raise RuntimeError('Unexpected test schema')
        self.engine.dispose()
        with self.base_engine.begin() as connection:
            connection.execute(sa.schema.DropSchema(self.schema, cascade=True))
        self.base_engine.dispose()

    def test_draft_publish_replay_conflict_and_rollback(self):
        from app.modules.config.schema import PublishDraft, Rollback, SaveDraft
        from app.modules.config.store import Conflict
        initial = self.store.current()
        changed = default_config()
        changed.columns[0].label = '课程名称（修改）'
        draft = self.store.save(SaveDraft(expected_revision=0, content=changed), 'test-trace')
        self.assertEqual(self.store.current(), initial)
        command = PublishDraft(expected_revision=draft['revision'], expected_release_id=None,
                               idempotency_key='first-publish-key')
        first = self.store.publish(command, 'test-trace')
        self.assertEqual(self.store.current()['content']['columns'][0]['label'], '课程名称（修改）')
        self.assertTrue(self.store.publish(command, 'test-trace')['replayed'])
        with self.assertRaises(Conflict):
            self.store.save(SaveDraft(expected_revision=0, content=changed), 'test-trace')
        changed.columns[0].label = '课程二'
        draft = self.store.save(SaveDraft(expected_revision=self.store.draft()['revision'], content=changed), 'test-trace')
        second = self.store.publish(PublishDraft(expected_revision=draft['revision'],
            expected_release_id=first['release_id'], idempotency_key='second-publish-key'), 'test-trace')
        restored = self.store.publish(Rollback(expected_revision=self.store.draft()['revision'],
            expected_release_id=second['release_id'], target_release_id=first['release_id'],
            idempotency_key='rollback-publish-key'), 'test-trace', rollback=True)
        self.assertNotEqual(restored['release_id'], first['release_id'])
        self.assertEqual(len(self.store.history()), 3)
        self.assertEqual(self.store.current()['content']['columns'][0]['label'], '课程名称（修改）')

    def test_concurrent_saves_have_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from app.modules.config.schema import SaveDraft
        from app.modules.config.store import Conflict
        barrier = Barrier(2)
        def save(index):
            content = default_config()
            content.columns[0].label = 'winner-' + str(index)
            barrier.wait(timeout=5)
            try:
                self.store.save(SaveDraft(expected_revision=0, content=content), 'race')
                return True
            except Conflict:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, (1, 2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.store.draft()['revision'], 1)
        self.assertEqual(len(self.store.audit_history()), 1)

    def test_rules_audit_tenant_isolation_and_outbox(self):
        import sqlalchemy as sa
        from app.modules.config.schema import IntentRule, PublishDraft, SaveDraft
        from app.modules.config.store import ConfigStore, NotFound, state, outbox
        from app.modules.intent.router import resolve
        from datetime import date, datetime, timedelta, timezone
        from app.workers.outbox import process_one
        other = ConfigStore(self.engine, tenant_id='other-campus', actor_id='other-admin')
        with self.assertRaises(NotFound):
            other.current()
        with self.engine.begin() as connection:
            connection.execute(state.insert().values(tenant_id='other-campus', key='course-display',
                revision=0, draft=default_config().model_dump()))
        content = default_config()
        content.rules = [IntentRule(id='tomorrow', terms=['明日课程'], date_mode='tomorrow')]
        self.store.save(SaveDraft(expected_revision=0, content=content), 'test-rule')
        self.assertEqual(self.store.current()['content']['rules'], [])
        result = self.store.publish(PublishDraft(expected_revision=1, expected_release_id=None,
            idempotency_key='rule-publish-key'), 'test-rule')
        current = DisplayConfig.model_validate(self.store.current()['content'])
        self.assertEqual(resolve('明日课程', date(2026, 10, 18), current.rules).status, 'resolved')
        self.assertIsNone(other.current()['release_id'])
        self.assertEqual(other.audit_history(), [])
        self.assertEqual(len(self.store.audit_history()), 2)
        with self.engine.begin() as connection:
            connection.execute(outbox.update().values(state='working', lease_owner='dead-worker',
                lease_until=datetime.now(timezone.utc) - timedelta(seconds=1)))
        with patch('app.workers.outbox.cache_enabled', return_value=False):
            self.assertTrue(process_one(self.engine))
            self.assertFalse(process_one(self.engine))
        self.assertEqual(self.store.delivery_status()['counts'], {'done': 1})
        self.assertEqual(self.store.current()['release_id'], result['release_id'])

    def test_cache_failure_does_not_break_published_reads(self):
        from app.modules.config.schema import PublishDraft
        from app.modules.config import cache
        result = self.store.publish(PublishDraft(expected_revision=0, expected_release_id=None,
            idempotency_key='cache-publish-key'), 'test-cache')
        with patch('app.modules.config.cache.cache_enabled', return_value=True), patch(
            'app.modules.config.cache.client', side_effect=ConnectionError('offline')):
            cache._retry_at = 0
            self.assertEqual(self.store.current()['release_id'], result['release_id'])
        cache._retry_at = 0

    def test_management_api_preview_publish_and_query_are_one_bundle(self):
        from app.modules.config.store import ConfigStore
        from unittest.mock import patch
        headers = {'Authorization': 'Bearer ' + 'a' * 24}
        client = TestClient(app)
        with patch.dict(os.environ, {'LOCAL_ADMIN_TOKEN': 'a' * 24}), patch(
            'app.modules.config.routes.get_store', side_effect=lambda principal=None: ConfigStore(self.engine)), patch(
            'app.modules.config.routes.database_is_configured', return_value=True):
            draft = client.get('/api/v1/admin/course-display', headers=headers).json()
            content = draft['content']
            content['columns'][0]['label'] = '发布预览测试'
            content['rules'] = [{'id': 'alias-test', 'enabled': True, 'priority': 100, 'match_type': 'exact',
                                 'terms': ['明日课程'], 'date_mode': 'tomorrow'}]
            preview = client.post('/api/v1/admin/course-display/preview', headers=headers,
                                  json={'content': content, 'text': '明日课程'})
            self.assertEqual(preview.status_code, 200)
            self.assertEqual(preview.json()['routing']['status'], 'resolved')
            self.assertTrue(preview.json()['review']['can_publish'])
            self.assertEqual(self.store.draft()['revision'], 0)
            saved = client.put('/api/v1/admin/course-display/draft', headers=headers,
                               json={'expected_revision': 0, 'content': content})
            self.assertEqual(saved.status_code, 200)
            with patch('app.main.campus_today', return_value=__import__('datetime').date(2026, 10, 18)):
                before = client.post('/api/v1/course-schedule/query', headers=headers, json={'text': '明日课程'})
                self.assertNotEqual(before.json()['routing']['status'], 'resolved')
                published = client.post('/api/v1/admin/course-display/publish', headers=headers,
                    json={'expected_revision': 1, 'expected_release_id': None, 'idempotency_key': 'api-publish-key-123'})
                self.assertEqual(published.status_code, 200)
                after = client.post('/api/v1/course-schedule/query', headers=headers, json={'text': '明日课程'})
                self.assertEqual(after.json()['routing']['status'], 'resolved')
                self.assertEqual(after.json()['ui_hint']['columns'][0]['label'], '发布预览测试')
                self.assertEqual(after.json()['ui_hint']['release_id'], published.json()['release_id'])
            events = client.get('/api/v1/admin/course-display/audit', headers=headers).json()
            self.assertEqual(len(events), 2)
            self.assertIsNotNone(events[0]['after_json'])
            metrics = client.get('/api/v1/admin/metrics', headers=headers)
            self.assertEqual(metrics.status_code, 200)
            self.assertIn('campus_outbox_events', metrics.text)

    def test_failed_outbox_can_be_replayed_without_duplicate_release(self):
        import sqlalchemy as sa
        from datetime import datetime, timedelta, timezone
        from app.modules.config.schema import PublishDraft
        from app.modules.config.store import outbox
        from app.workers.outbox import process_one
        self.store.publish(PublishDraft(expected_revision=0, expected_release_id=None,
            idempotency_key='delivery-failure-key'), 'test-delivery')
        with patch('app.workers.outbox.cache_enabled', return_value=True), patch(
            'app.workers.outbox.write_cache', return_value=None):
            for _ in range(8):
                with self.engine.begin() as connection:
                    connection.execute(outbox.update().values(available_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
                self.assertTrue(process_one(self.engine))
        status = self.store.delivery_status()
        self.assertEqual(status['counts'], {'failed': 1})
        event = status['failed'][0]
        self.assertEqual(event['attempts'], 8)
        self.store.retry_event(event['id'], 'retry-trace')
        with patch('app.workers.outbox.cache_enabled', return_value=False):
            self.assertTrue(process_one(self.engine))
        self.assertEqual(self.store.delivery_status()['counts'], {'done': 1})
        self.assertEqual(len(self.store.history()), 1)
