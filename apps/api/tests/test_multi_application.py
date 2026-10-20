import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.errors import APIError
from app.main import app
from app.modules.config.application_store import ApplicationStore, CourseEnvelope, CreateApplication, SaveApplication
from app.modules.config.schema import PublishDraft, Rollback
from app.modules.config.store import ConfigStore, Conflict, NotFound, LEGACY_APPLICATION_ID
from app.modules.config.question_library import QuestionInput, QuestionLibrary, ReviewQuestion
from app.modules.config.type_registry import course_type, require_type
from support import student_headers
import test_config


class TypeRegistryTests(unittest.TestCase):
    def test_compiled_registry_is_stable_and_unknown_versions_fail(self):
        first = course_type()
        self.assertEqual(first['type_digest'], course_type()['type_digest'])
        first['editor_sections'].clear()
        self.assertEqual(len(course_type()['editor_sections']), 2)
        for type_id, version in [('campus.form', '1.0.0'), ('campus.course-query', '2.0.0')]:
            with self.assertRaises(APIError):
                require_type(type_id, version)
        with self.assertRaises(APIError):
            require_type('campus.course-query', '1.0.0', 'wrong-digest')

    def test_routes_require_admin_and_reject_unsafe_envelope(self):
        client = TestClient(app)
        with patch.dict(os.environ, {'LOCAL_ADMIN_TOKEN': 'a' * 24}):
            self.assertEqual(client.get('/api/v1/admin/application-types').status_code, 401)
            self.assertEqual(client.get('/api/v1/admin/applications', headers=student_headers(self)).status_code, 403)
            response = client.get('/api/v1/admin/application-types', headers={'Authorization': 'Bearer ' + 'a' * 24})
            self.assertEqual(response.status_code, 200)
        example = json.loads((Path(__file__).resolve().parents[3] /
            'packages/contracts/application-types/v1/examples/course-application.json').read_text(encoding='utf-8'))
        # Contract example has a fictitious binding: not an authorized installed binding.
        with self.assertRaises(ValueError):
            CourseEnvelope.model_validate(example)


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Requires isolated database schema')
class MultiApplicationTests(unittest.TestCase):
    setUp = test_config.PostgresConfigTests.setUp
    cleanup_schema = test_config.PostgresConfigTests.cleanup_schema

    def create(self, name, key):
        catalog = ApplicationStore(self.store)
        result = catalog.create(CreateApplication(type_id='campus.course-query', type_version='1.0.0',
            name=name, idempotency_key=key), 'test')
        return catalog.config_store(result['application_id'])

    def publish(self, store, key):
        draft = store.draft()
        return store.publish(PublishDraft(expected_revision=draft['revision'],
            expected_release_id=draft['current_release_id'], idempotency_key=key), 'test')

    def test_create_replay_and_original_draft_preserved(self):
        before = self.store.draft()
        catalog = ApplicationStore(self.store)
        command = CreateApplication(type_id='campus.course-query', type_version='1.0.0',
            name='Course copy', idempotency_key='create-application-1')
        first = catalog.create(command, 'test')
        self.assertEqual(catalog.create(command, 'test'), {**first, 'replayed': True})
        with self.assertRaises(Conflict):
            catalog.create(command.model_copy(update={'name': 'different'}), 'test')
        self.assertEqual(self.store.draft(), before)
        self.assertEqual(len(catalog.listing()['items']), 2)
        self.assertEqual(catalog.listing()['items'][0]['application_id'], LEGACY_APPLICATION_ID)

    def test_envelope_save_scope_and_tenant_isolation(self):
        a = self.create('A', 'create-application-a')
        b = self.create('B', 'create-application-b')
        catalog = ApplicationStore(self.store)
        body = catalog.draft(a.application_id)
        body['content']['metadata']['name'] = 'Changed A'
        command = SaveApplication(expected_revision=0, content=CourseEnvelope.model_validate(body['content']))
        with self.assertRaises(APIError):
            catalog.save(b.application_id, command, 'test')
        catalog.save(a.application_id, command, 'test')
        self.assertEqual(a.draft()['content']['application']['name'], 'Changed A')
        self.assertEqual(b.draft()['revision'], 0)
        other = ApplicationStore(ConfigStore(self.engine, tenant_id='other'))
        self.assertEqual(other.listing()['items'], [])
        with self.assertRaises(APIError):
            other.get(a.application_id)

    def test_release_replay_rollback_audit_and_delivery_are_scoped(self):
        a = self.create('A', 'create-application-a')
        b = self.create('B', 'create-application-b')
        ar = self.publish(a, 'same-publish-request')
        br = self.publish(b, 'same-publish-request')
        self.assertNotEqual(ar['release_id'], br['release_id'])
        self.assertEqual(len(a.history()), 1)
        self.assertEqual(len(b.history()), 1)
        self.assertEqual(len(a.audit_history()), 2)
        self.assertEqual(self.store.history(), [])
        with self.assertRaises(NotFound):
            a.publish(Rollback(expected_revision=a.draft()['revision'], expected_release_id=ar['release_id'],
                target_release_id=br['release_id'], idempotency_key='cross-app-rollback'), 'test', rollback=True)
        self.assertEqual(a.delivery_status()['counts']['pending'], 1)
        self.assertEqual(self.store.delivery_status()['counts'], {})

    def test_same_question_can_be_reviewed_independently(self):
        a = self.create('A', 'create-application-a')
        b = self.create('B', 'create-application-b')
        question = QuestionInput(text='明日课程安排呢', date_mode='tomorrow')
        for store in (a, b):
            QuestionLibrary(store).add([question], 'test')
        item = QuestionLibrary(a).listing()['items'][0]
        QuestionLibrary(a).change(item['id'], ReviewQuestion(expected_revision=1,
            expected_draft_revision=0, action='approve'), 'test')
        self.assertTrue(QuestionLibrary(a).listing()['items'][0]['in_draft'])
        self.assertFalse(QuestionLibrary(b).listing()['items'][0]['in_draft'])
        self.assertEqual(QuestionLibrary(self.store).listing()['items'], [])
        self.assertEqual(b.draft()['revision'], 0)

    def test_migration_preserves_content_and_legacy_store_alias(self):
        catalog = ApplicationStore(self.store)
        original = catalog.config_store(LEGACY_APPLICATION_ID)
        self.assertEqual(original.draft(), self.store.draft())
        self.assertEqual(original.current(), self.store.current())
        self.assertEqual(catalog.draft(LEGACY_APPLICATION_ID)['content']['type_id'], 'campus.course-query')

    def test_http_create_edit_revision_and_scope_validation(self):
        client = TestClient(app)
        headers = {'Authorization': 'Bearer ' + 'a' * 24}
        with patch.dict(os.environ, {'LOCAL_ADMIN_TOKEN': 'a' * 24}), patch(
                'app.modules.config.routes.get_store', return_value=self.store):
            response = client.post('/api/v1/admin/applications', headers=headers, json={
                'type_id': 'campus.course-query', 'type_version': '1.0.0', 'name': 'HTTP application',
                'idempotency_key': 'http-application-create'})
            self.assertEqual(response.status_code, 200, response.text)
            app_id = response.json()['application_id']
            path = '/api/v1/admin/applications/' + app_id + '/draft'
            body = client.get(path, headers=headers).json()
            body['content']['metadata']['name'] = 'HTTP changed'
            command = {'expected_revision': 0, 'content': body['content']}
            result = client.put(path, headers=headers, json=command)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['content']['metadata']['name'], 'HTTP changed')
            self.assertEqual(client.put(path, headers=headers, json=command).status_code, 409)
            command['content']['application_id'] = LEGACY_APPLICATION_ID
            self.assertEqual(client.put(path, headers=headers, json=command).status_code, 409)
            command['content']['tenant_id'] = 'other'
            self.assertEqual(client.put(path, headers=headers, json=command).status_code, 422)
            self.assertEqual(client.get('/api/v1/admin/applications/11111111-1111-4111-8111-111111111111/draft',
                headers=headers).status_code, 404)

    def test_concurrent_create_retries_make_one_instance(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier = Barrier(2)
        command = CreateApplication(type_id='campus.course-query', type_version='1.0.0',
            name='Concurrent', idempotency_key='concurrent-create-key')
        def create():
            barrier.wait(timeout=5)
            return ApplicationStore(self.store).create(command, 'test')
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: create(), range(2)))
        self.assertEqual(results[0]['application_id'], results[1]['application_id'])
        self.assertEqual(sorted(item['replayed'] for item in results), [False, True])
        self.assertEqual(len(ApplicationStore(self.store).listing()['items']), 2)

    def test_template_publish_catalog_and_scoped_runtime_http(self):
        from app.modules.config.schema import DisplayConfig, SaveDraft
        from unittest.mock import AsyncMock
        client = TestClient(app)
        headers = {'Authorization': 'Bearer ' + 'a' * 24}
        student = student_headers(self)
        original = self.store.draft()
        with patch.dict(os.environ, {'LOCAL_ADMIN_TOKEN': 'a' * 24}), patch(
                'app.modules.config.routes.get_store', return_value=self.store):
            created = client.post('/api/v1/admin/applications', headers=headers, json={
                'type_id': 'campus.course-query', 'type_version': '1.0.0', 'template_id': 'course-compact',
                'name': 'Published course copy', 'idempotency_key': 'visible-app-create-key'}).json()
            app_id = created['application_id']
            base = '/api/v1/admin/applications/' + app_id + '/configuration'
            config = client.get(base, headers=headers).json()
            self.assertFalse(next(c['visible'] for c in config['content']['columns'] if c['field'] == 'teacher'))
            self.assertIsNone(client.get(base + '/current', headers=headers).json()['content'])
            self.assertNotIn(app_id, [i['id'] for i in client.get('/api/v1/applications', headers=student).json()['applications']])
            self.assertEqual(client.post('/api/v1/applications/' + app_id + '/query', headers=student,
                json={'text': '明天课表'}).status_code, 404)
            config['content']['columns'][0]['label'] = '独立课程列'
            saved = client.put(base + '/draft', headers=headers, json={
                'expected_revision': 0, 'content': config['content']}).json()
            question_path = '/api/v1/admin/applications/' + app_id + '/questions'
            added = client.post(question_path, headers=headers, json={'text': '帮我看看明天课程呀', 'date_mode': 'tomorrow'})
            self.assertEqual(added.status_code, 200, added.text)
            question = client.get(question_path, headers=headers).json()['items'][0]
            reviewed = client.post(question_path + '/' + question['id'] + '/review', headers=headers, json={
                'expected_revision': question['revision'], 'expected_draft_revision': saved['revision'], 'action': 'approve'})
            self.assertEqual(reviewed.status_code, 200, reviewed.text)
            self.assertEqual(QuestionLibrary(self.store).listing()['items'], [])
            saved = client.get(base, headers=headers).json()
            preview = client.post(base + '/preview', headers=headers, json={
                'content': saved['content'], 'text': '明天课表'})
            self.assertEqual(preview.status_code, 200)
            self.assertTrue(preview.json()['review']['can_publish'])
            command = {'expected_revision': saved['revision'], 'expected_release_id': None,
                'idempotency_key': 'visible-publish-key'}
            published = client.post(base + '/publish', headers=headers, json=command)
            self.assertEqual(published.status_code, 200, published.text)
            self.assertTrue(client.post(base + '/publish', headers=headers, json=command).json()['replayed'])
            entry = next(i for i in client.get('/api/v1/applications', headers=student).json()['applications'] if i['id'] == app_id)
            self.assertEqual(entry['name'], 'Published course copy')
            provider = AsyncMock()
            provider.source = 'mock'
            provider.get_schedule.return_value = []
            from app.main import get_provider
            app.dependency_overrides[get_provider] = lambda: provider
            try:
                result = client.post('/api/v1/applications/' + app_id + '/query', headers=student, json={'text': '明天课表'})
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.json()['ui_hint']['release_id'], published.json()['release_id'])
                self.assertEqual(result.json()['ui_hint']['columns'][0]['label'], '独立课程列')
                self.assertFalse(result.json()['ai_used'])
                dates = client.get('/api/v1/applications/' + app_id + '/schedule?start_date=2026-10-20&end_date=2026-10-20', headers=student)
                self.assertEqual(dates.status_code, 200)
                provider.get_schedule.assert_awaited()
                self.assertEqual(provider.get_schedule.call_args.kwargs['tenant_id'], 'local-demo')
            finally:
                app.dependency_overrides.pop(get_provider, None)
            store = ApplicationStore(self.store).config_store(app_id)
            restricted = DisplayConfig.model_validate(store.draft()['content'])
            restricted.application.visible_roles = ['teacher']
            store.save(SaveDraft(expected_revision=store.draft()['revision'], content=restricted), 'test')
            self.publish(store, 'restrict-copy-publish')
            self.assertNotIn(app_id, [i['id'] for i in client.get('/api/v1/applications', headers=student).json()['applications']])
            self.assertEqual(client.post('/api/v1/applications/' + app_id + '/query', headers=student,
                json={'text': '明天课表'}).status_code, 403)
        self.assertEqual(self.store.draft(), original)

    def test_runtime_snapshot_rejects_wrong_application_and_capability(self):
        from app.modules.config.store import releases
        import sqlalchemy as sa
        a = self.create('A', 'create-application-a')
        release = self.publish(a, 'snapshot-binding-key')
        with self.engine.begin() as connection:
            binding = connection.scalar(sa.select(releases.c.runtime_binding).where(releases.c.id == release['release_id']))
            self.assertEqual(binding['application_id'], a.application_id)
            self.assertEqual(binding['capability_contract'], 'course.schedule.read@1')
        for change in ({'application_id': LEGACY_APPLICATION_ID}, {'binding_id': 'unknown'},
                {'capability_contract': 'arbitrary@1'}, {'type_version': '2.0.0'}, {'type_digest': None}):
            with self.engine.begin() as connection:
                connection.execute(releases.update().where(releases.c.id == release['release_id']).values(runtime_binding=binding | change))
            with self.assertRaises((Conflict, APIError)):
                a.current()

    def test_scoped_question_ai_generation_only_adds_to_target(self):
        from unittest.mock import AsyncMock
        from pydantic import SecretStr
        client = TestClient(app, client=('127.0.0.1', 1), base_url='http://127.0.0.1')
        target = self.create('Target', 'scoped-ai-create-key')
        with patch.dict(os.environ, {'LOCAL_ADMIN_TOKEN': 'a' * 24}), patch(
                'app.modules.config.routes.get_store', return_value=self.store), patch(
                'app.modules.config.question_routes.active_model', return_value=({'revision': 1}, SecretStr('fake'))), patch(
                'app.modules.config.question_routes.model_settings', return_value={'revision': 1}), patch(
                'app.modules.config.question_routes.completion', AsyncMock(return_value=(json.dumps({
                    'questions': [{'text': '帮我查查看明天课表呀', 'date_mode': 'tomorrow'}]}), {}))) as model:
            response = client.post('/api/v1/admin/applications/' + target.application_id + '/questions/generate',
                headers={'Authorization': 'Bearer ' + 'a' * 24}, json={'count': 1, 'expected_draft_revision': 0})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn('Target', model.call_args.args[2][1]['content'])
        self.assertEqual(len(QuestionLibrary(target).listing()['items']), 1)
        self.assertEqual(QuestionLibrary(self.store).listing()['items'], [])
        self.assertEqual(target.draft()['revision'], 0)
