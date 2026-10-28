import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.main import app
from app.modules.intent.application_router import ResolveApplication, resolve_application
from app.modules.intent.requirements import RequirementStore, SaveRequirement, CancelRequirement, EditRequirement, PrepareCreation, commands, tasks
from app.modules.config.store import ConfigStore, Conflict, NotFound
from support import student_headers


class RequirementBoundaryTests(unittest.TestCase):
    def test_role_aware_guidance_and_cross_business_context(self):
        apps = [{'id': 'schedule', 'name': '课表助手'}]
        for admin in (False, True):
            result = resolve_application(ResolveApplication(text='我要请假', current_application_id='schedule'), apps, admin)
            self.assertNotEqual(result.status, 'query')
            self.assertEqual(result.construction_subject, '请假' if admin else None)
        result = resolve_application(ResolveApplication(text='创建这个应用', construction_subject='请假'), apps, True)
        self.assertEqual(result.construction_subject, '请假')
        self.assertIsNone(resolve_application(ResolveApplication(text='不要创建请假应用'), apps, True).construction_subject)
        result = resolve_application(ResolveApplication(text='我要请假'), [{'id': 'leave', 'name': '请假'}], True)
        self.assertIsNone(result.construction_subject, 'Do not offer construction if an authorized name already matches')
        creation = resolve_application(ResolveApplication(text='创建课程查询应用'), apps, True)
        self.assertEqual(creation.construction_subject, '课程查询')
        selected = resolve_application(ResolveApplication(text='创建课程查询应用', selected_application_id='schedule'), apps, True)
        self.assertEqual(selected.status, 'selected')
        self.assertIsNone(selected.query_text, 'Choosing an existing app must not query a model with creation text')

    def test_students_cannot_read_or_write_construction_requests(self):
        client = TestClient(app)
        headers = student_headers(self)
        with patch.dict(os.environ, {'LOCAL_ADMIN_TOKEN': 'a' * 24}):
            self.assertEqual(client.get('/api/v1/assistant/requirements', headers=headers).status_code, 403)
            self.assertEqual(client.post('/api/v1/assistant/requirements', headers=headers,
                json={'title': 'test', 'description': 'test', 'idempotency_key': 'test-request-one'}).status_code, 403)


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Requires isolated development schema')
class RequirementDatabaseTests(unittest.TestCase):
    def setUp(self):
        from test_config import PostgresConfigTests
        self.fixture = PostgresConfigTests('test_draft_publish_replay_conflict_and_rollback')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = RequirementStore(self.fixture.store)
        self.command = SaveRequirement(title='请假应用', description='希望支持请假申请及单级审批', idempotency_key='requirement-key-one')

    def course(self, key='course-requirement-key'):
        return self.store.save(SaveRequirement(title='课程查询需求', description='查询课程安排', business_kind='course_query', idempotency_key=key), 'trace')['item']

    def preparation(self, item, key='course-create-key-one'):
        return self.store.prepare(item['id'], PrepareCreation(expected_revision=item['revision'], name='独立课程查询', template_id='course-standard', idempotency_key=key))

    def test_edit_replay_conflict_details_and_command_scope(self):
        task = self.course()
        command = EditRequirement(title='修改后的课程需求', description='补充日常查课', business_kind='course_query', expected_revision=1, idempotency_key='edit-requirement-key')
        result = self.store.edit(task['id'], command, 'trace')
        self.assertEqual(result['item']['revision'], 2)
        self.assertTrue(self.store.edit(task['id'], command, 'trace')['replayed'])
        self.assertEqual(self.store.command_result(command.idempotency_key)['status'], 'succeeded')
        other = RequirementStore(ConfigStore(self.fixture.engine, actor_id='another-admin'))
        self.assertFalse(other.command_result(command.idempotency_key)['found'])
        self.assertEqual(other.history()['items'], [])
        with self.assertRaises(NotFound):
            other.detail(task['id'])
        with self.assertRaises(Conflict):
            self.store.edit(task['id'], command.model_copy(update={'idempotency_key': 'edit-another-command'}), 'trace')
        self.assertEqual(self.store.detail(task['id'])['audit'][-1]['before']['title'], task['title'])

    def test_confirm_concurrent_replay_publish_and_resume(self):
        from app.modules.config.application_store import ApplicationStore
        from app.modules.config.schema import PublishDraft, SaveDraft
        task = self.course()
        prepared = self.preparation(task)
        self.assertEqual(self.preparation(task)['command_id'], prepared['command_id'])
        self.assertIsNone(self.store.detail(task['id'])['item']['target_application_id'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.store.confirm(prepared['command_id'], 'trace'), range(2)))
        self.assertEqual(results[0]['application_id'], results[1]['application_id'])
        app_id = results[0]['application_id']
        self.assertEqual(sum(not result['replayed'] for result in results), 1)
        self.assertEqual(len(ApplicationStore(self.fixture.store).listing()['items']), 2)
        self.assertEqual(self.store.command_result('course-create-key-one')['status'], 'succeeded')
        target = ConfigStore(self.fixture.engine, application_id=app_id)
        target.publish(PublishDraft(expected_revision=0, expected_release_id=None, idempotency_key='publish-created-course'), 'trace')
        detail = self.store.detail(task['id'])['item']
        self.assertTrue(detail['progress']['release_id'])
        self.assertFalse(detail['progress']['unpublished_changes'])
        draft = target.draft()
        content = draft['content']; content['application']['description'] = '修改未发布'
        target.save(SaveDraft(expected_revision=draft['revision'], content=content), 'trace')
        self.assertTrue(self.store.detail(task['id'])['item']['progress']['unpublished_changes'])
        self.store.cancel(task['id'], CancelRequirement(expected_revision=2), 'trace')
        self.assertIsNotNone(target.current()['release_id'], 'Cancelling guidance does not cancel or delete an application')

    def test_confirmation_stale_cancel_expiry_and_kill_switch(self):
        from datetime import datetime, timedelta, timezone
        import sqlalchemy as sa
        from app.errors import APIError
        task = self.course()
        prepared = self.preparation(task)
        other = RequirementStore(ConfigStore(self.fixture.engine, actor_id='another-admin'))
        with self.assertRaises(NotFound):
            other.confirm(prepared['command_id'], 'trace')
        with patch.dict(os.environ, {'ASSISTANT_CREATION_ENABLED': '0'}):
            self.assertNotIn('prepare_creation', self.store.detail(task['id'])['item']['allowed_actions'])
            with self.assertRaises(Conflict):
                self.store.confirm(prepared['command_id'], 'trace')
        with self.fixture.engine.begin() as connection:
            connection.execute(commands.update().where(commands.c.id == prepared['command_id']).values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
        with self.assertRaises(Conflict):
            self.store.confirm(prepared['command_id'], 'trace')
        another = self.preparation(task, 'another-prepare-key')
        self.store.cancel(task['id'], CancelRequirement(expected_revision=1), 'trace')
        with self.assertRaises(Conflict):
            self.store.confirm(another['command_id'], 'trace')
        unsupported = self.store.save(self.command, 'trace')['item']
        with self.assertRaises(APIError):
            self.preparation(unsupported, 'unsupported-create-key')

    def test_conversion_rollback_leaves_no_orphan_application(self):
        from app.modules.config.application_store import ApplicationStore
        task = self.course()
        prepared = self.preparation(task)
        with patch.object(self.store, 'view', side_effect=RuntimeError('Synthetic failure after application insert')):
            with self.assertRaises(RuntimeError):
                self.store.confirm(prepared['command_id'], 'trace')
        self.assertEqual(len(ApplicationStore(self.fixture.store).listing()['items']), 1)
        self.assertIsNone(self.store.detail(task['id'])['item']['target_application_id'])
        self.assertEqual(self.store.command_result('course-create-key-one')['status'], 'awaiting_confirmation')
        self.assertTrue(self.store.confirm(prepared['command_id'], 'trace')['application_id'])

    def test_activity_quota_pagination_and_cancel_releases_capacity(self):
        import sqlalchemy as sa
        from uuid import uuid4
        task = self.course()
        with self.fixture.engine.begin() as connection:
            seed = dict(connection.execute(sa.select(tasks).where(tasks.c.id == task['id'])).mappings().one())
            connection.execute(tasks.insert(), [{**seed, 'id': str(uuid4()), 'command_key': str(uuid4())} for _ in range(99)])
        self.assertEqual(self.store.listing(page=2, page_size=20)['total'], 100)
        self.assertEqual(len(self.store.listing(page=2, page_size=20)['items']), 20)
        with self.assertRaises(Conflict):
            self.course('quota-next-command')
        self.store.cancel(task['id'], CancelRequirement(expected_revision=1), 'trace')
        self.assertEqual(self.store.listing(status='cancelled')['total'], 1)
        self.assertTrue(self.course('quota-next-command')['id'])


    def test_concurrent_save_replay_conflict_cancel_and_isolation(self):
        before = self.fixture.store.draft()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.store.save(self.command, 'test-trace'), range(2)))
        self.assertEqual(results[0]['item']['id'], results[1]['item']['id'])
        self.assertEqual(sum(not result['replayed'] for result in results), 1)
        task = results[0]['item']
        other = RequirementStore(ConfigStore(self.fixture.engine, actor_id='another-admin'))
        self.assertEqual(other.listing()['items'], [])
        tenant = RequirementStore(ConfigStore(self.fixture.engine, tenant_id='another-tenant'))
        self.assertEqual(tenant.listing()['items'], [])
        with self.assertRaises(NotFound):
            other.cancel(task['id'], CancelRequirement(expected_revision=1), 'trace')
        with self.assertRaises(Conflict):
            self.store.save(self.command.model_copy(update={'title': 'changed'}), 'trace')
        with self.assertRaises(Conflict):
            self.store.cancel(task['id'], CancelRequirement(expected_revision=8), 'trace')
        self.assertEqual(RequirementStore(self.fixture.store).listing()['items'][0]['description'], self.command.description)
        first = self.store.cancel(task['id'], CancelRequirement(expected_revision=1), 'trace')
        self.assertEqual(first['item']['status'], 'cancelled')
        self.assertTrue(self.store.cancel(task['id'], CancelRequirement(expected_revision=1), 'trace')['replayed'])
        self.assertEqual(self.fixture.store.draft(), before)

    def test_http_save_reload_cancel_and_origin(self):
        client = TestClient(app)
        headers = {'Authorization': 'Bearer ' + 'a' * 24}
        with patch.dict(os.environ, {'LOCAL_ADMIN_TOKEN': 'a' * 24}), patch('app.modules.config.routes.get_store', return_value=self.fixture.store):
            payload = self.command.model_dump()
            first = client.post('/api/v1/assistant/requirements', headers=headers, json=payload)
            self.assertEqual(first.status_code, 200)
            self.assertTrue(client.post('/api/v1/assistant/requirements', headers=headers, json=payload).json()['replayed'])
            items = client.get('/api/v1/assistant/requirements', headers=headers).json()['items']
            self.assertEqual(len(items), 1)
            response = client.post('/api/v1/assistant/requirements/' + items[0]['id'] + '/cancel', headers=headers, json={'expected_revision': 1})
            self.assertEqual(response.json()['item']['status'], 'cancelled')
            self.assertEqual(client.post('/api/v1/assistant/requirements', headers={**headers, 'Origin': 'https://example.com'}, json=payload).status_code, 403)
