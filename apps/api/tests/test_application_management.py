import unittest
import os
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.modules.config.schema import DisplayConfig, default_config
from support import student_headers
import test_config as config_tests


class ApplicationTests(unittest.TestCase):
    def test_old_configs_get_application_defaults_without_rewriting_history(self):
        content = default_config().model_dump()
        content.pop('application')
        normalized = DisplayConfig.model_validate(content)
        self.assertEqual(normalized.application.name, '课表助手')
        self.assertNotIn('application', content)

    def test_catalog_and_query_use_published_access_scope(self):
        content = default_config()
        content.application.name = '课程服务'
        content.application.visible_roles = ['teacher']
        headers = student_headers(self)
        client = TestClient(app)
        snapshot = {'release_id': 'access-test', 'content': content.model_dump()}
        with patch('app.modules.config.applications.published_config', return_value=snapshot), patch(
                'app.modules.config.application_store.ApplicationStore.public_catalog', return_value=[]):
            response = client.get('/api/v1/applications', headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('schedule', [item['id'] for item in response.json()['applications']])
            self.assertNotIn('应用管理', [item['category'] for item in response.json()['applications']])
        with patch('app.main.published_config', return_value=snapshot), patch('app.main.fetch_schedule') as fetch:
            self.assertEqual(client.post('/api/v1/course-schedule/query', headers=headers, json={'text': '今天课表'}).status_code, 403)
            fetch.assert_not_called()
        with patch('app.main.published_config', return_value=snapshot):
            self.assertEqual(client.get('/api/v1/course-schedule?start_date=2026-10-19&end_date=2026-10-19', headers=headers).status_code, 403)

    def test_application_schema_rejects_invalid_roles_and_arbitrary_code(self):
        for extra in ({'visible_roles': []}, {'visible_roles': ['root']}, {'visible_roles': ['student', 'student']}, {'command': 'arbitrary-code'}):
            content = default_config().model_dump()
            content['application'].update(extra)
            with self.assertRaises(ValueError):
                DisplayConfig.model_validate(content)


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), '需要实库隔离 Schema')
class ApplicationStoreTests(unittest.TestCase):
    setUp = config_tests.PostgresConfigTests.setUp
    cleanup_schema = config_tests.PostgresConfigTests.cleanup_schema

    def test_temporary_design_publish_and_rollback_are_separate(self):
        from app.modules.config.schema import SaveDraft, PublishDraft, Rollback
        from app.modules.config.store import ConfigStore
        first = self.store.publish(PublishDraft(expected_revision=0, expected_release_id=None,
            idempotency_key='initial-app-release-01'), 'app-initial')
        initial = self.store.current()
        draft = DisplayConfig.model_validate(self.store.draft()['content'])
        draft.application.name = '新课程应用'
        draft.application.category = '校园事务'
        draft.application.visible_roles = ['teacher']
        self.store.save(SaveDraft(expected_revision=1, content=draft), 'app-save')
        # Simulate leaving and reopening the editor with a new store instance.
        reopened = ConfigStore(self.engine)
        self.assertEqual(reopened.draft()['content']['application']['name'], '新课程应用')
        self.assertEqual(reopened.current()['release_id'], initial['release_id'])
        self.assertEqual(reopened.current()['content']['application']['name'], '课表助手')
        second = reopened.publish(PublishDraft(expected_revision=2, expected_release_id=first['release_id'],
            idempotency_key='updated-app-release-02'), 'app-publish')
        self.assertEqual(reopened.current()['content']['application']['name'], '新课程应用')
        self.assertEqual(reopened.current()['content']['application']['visible_roles'], ['teacher'])
        reverted = reopened.publish(Rollback(expected_revision=3, expected_release_id=second['release_id'],
            idempotency_key='rollback-app-release-03', target_release_id=first['release_id']), 'app-rollback', rollback=True)
        self.assertNotEqual(reverted['release_id'], first['release_id'])
        self.assertEqual(reopened.current()['content']['application']['name'], '课表助手')
        self.assertEqual(len(reopened.history()), 3)
