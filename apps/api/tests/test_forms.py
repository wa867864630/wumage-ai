import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.main import app
from app.errors import APIError
from app.modules.config.application_store import ApplicationStore, CreateApplication
from app.modules.config.schema import PublishDraft, Rollback
from app.modules.config.store import ConfigStore, Conflict, NotFound
from app.modules.forms.schema import FormConfig, SaveForm, default_form, validate_values
from app.modules.forms.runtime import FormRecords, SubmitForm, form_store
from app.modules.identity.auth import Principal
from support import student_headers


class FormSchemaTests(unittest.TestCase):
    def test_structure_and_reference_rejections(self):
        base = default_form().model_dump()
        for change in ('missing-control', 'duplicate-field', 'wrong-component', 'script', 'nullable-conflict'):
            import copy
            value = copy.deepcopy(base)
            if change == 'missing-control': value['form']['sections'][0]['fields'] = []
            elif change == 'duplicate-field': value['model']['fields'].append(value['model']['fields'][0])
            elif change == 'wrong-component': value['form']['sections'][0]['fields'][0]['component'] = 'number'
            elif change == 'script': value['model']['fields'][0]['script'] = 'never-execute'
            else: value['model']['fields'][0]['nullable'] = True
            with self.subTest(change=change), self.assertRaises(ValueError): FormConfig.model_validate(value)

    def numeric_boolean(self):
        value = default_form().model_dump()
        field = value['model']['fields'][0]; control = value['form']['sections'][0]['fields'][0]
        field.update(data_type='decimal', constraints={'minimum': 0, 'maximum': 10, 'precision': 2})
        control['component'] = 'number'
        return value

    def test_zero_false_dates_enum_and_precision(self):
        value = self.numeric_boolean(); config = FormConfig.model_validate(value)
        self.assertEqual(validate_values(config, {'content': 0}), {'content': 0})
        for invalid in (False, '0', -1, 11, 1.234, float('inf'), 10 ** 500):
            with self.subTest(value_type=type(invalid).__name__), self.assertRaises(APIError): validate_values(config, {'content': invalid})
        value['model']['fields'][0].update(data_type='boolean', constraints={}); value['form']['sections'][0]['fields'][0]['component'] = 'checkbox'
        self.assertEqual(validate_values(FormConfig.model_validate(value), {'content': False}), {'content': False})
        value['model']['fields'][0]['data_type'] = 'date'; value['form']['sections'][0]['fields'][0]['component'] = 'date'
        self.assertEqual(validate_values(FormConfig.model_validate(value), {'content': '2026-10-20'})['content'], '2026-10-20')
        with self.assertRaises(APIError): validate_values(FormConfig.model_validate(value), {'content': '2026-02-30'})
        value['model']['fields'][0].update(data_type='enum_list', options=[{'value': 'a', 'label': 'A'}]); value['form']['sections'][0]['fields'][0]['component'] = 'multiselect'
        config = FormConfig.model_validate(value)
        self.assertEqual(validate_values(config, {'content': ['a']}), {'content': ['a']})
        for invalid in (['b'], ['a', 'a'], 'a'):
            with self.assertRaises(APIError): validate_values(config, {'content': invalid})

    def test_values_cannot_override_identity_or_inject_unregistered_fields(self):
        with self.assertRaises(APIError): validate_values(default_form(), {'content': 'ok', 'owner_id': 'other'})
        with self.assertRaises(APIError): validate_values(default_form(), {'content': 'bad\x00text'})
        self.assertNotIn('minimum', default_form().model_dump()['model']['fields'][0]['constraints'])


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Requires isolated PostgreSQL/openGauss schema')
class FormDatabaseTests(unittest.TestCase):
    def setUp(self):
        from test_config import PostgresConfigTests
        self.fixture = PostgresConfigTests('test_draft_publish_replay_conflict_and_rollback')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.flags = patch.dict(os.environ, {'FORM_APPLICATIONS_ENABLED': '1'}); self.flags.start(); self.addCleanup(self.flags.stop)
        self.app_id = ApplicationStore(self.fixture.store).create(CreateApplication(type_id='platform.form', type_version='1.0.0',
            template_id='form-registration', name='Synthetic registration', idempotency_key='form-create-request-key'), 'trace')['application_id']
        self.admin = Principal(subject='local-admin', tenant_id='local-demo', roles=['config_admin'])
        self.student = Principal(subject='student-one', tenant_id='local-demo', roles=['student'])
        self.target = form_store(self.fixture.store, self.app_id)

    def publish(self, key='form-publish-one'):
        draft = self.target.draft()
        return self.target.publish(PublishDraft(expected_revision=draft['revision'], expected_release_id=draft['current_release_id'], idempotency_key=key), 'trace')['release_id']

    def service(self, principal=None, app_id=None):
        user = principal or self.student
        return FormRecords(ConfigStore(self.fixture.engine, user.tenant_id, user.subject), user, app_id or self.app_id)

    def test_unpublished_role_filters_and_course_interface_guard(self):
        with self.assertRaises(APIError): self.service().definition()
        with self.assertRaises(APIError): ApplicationStore(self.fixture.store).config_store(self.app_id)
        self.assertNotIn(self.app_id, [row['id'] for row in ApplicationStore(self.fixture.store).public_catalog(self.student)])
        draft = self.target.draft(); value = FormConfig.model_validate(draft['content']); value.application.visible_roles = ['config_admin']
        self.target.save(SaveForm(expected_revision=0, content=value, idempotency_key='role-restricted-draft'), 'trace'); self.publish()
        with self.assertRaises(APIError): self.service().definition()
        self.assertEqual(self.service(self.admin).definition()['application_id'], self.app_id)

    def test_submit_concurrency_replay_owner_tenant_and_app_isolation(self):
        release = self.publish(); service = self.service()
        command = SubmitForm(release_id=release, values={'content': 'Synthetic record'}, idempotency_key='submit-record-key-one')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: service.submit(command, 'trace'), range(2)))
        self.assertEqual(results[0]['record_id'], results[1]['record_id'])
        self.assertEqual(sum(not result['replayed'] for result in results), 1)
        record_id = results[0]['record_id']
        self.assertEqual(service.by_command(command.idempotency_key)['record_id'], record_id)
        other = self.service(Principal(subject='student-two', tenant_id='local-demo', roles=['student']))
        self.assertEqual(other.listing()['total'], 0)
        with self.assertRaises(NotFound): other.detail(record_id)
        with self.assertRaises(APIError): other.listing(all_records=True)
        self.assertEqual(self.service(self.admin).listing(all_records=True)['total'], 1)
        with self.assertRaises(Conflict): service.submit(command.model_copy(update={'values': {'content': 'Different'}}), 'trace')
        with self.assertRaises(APIError): self.service(Principal(subject='student-one', tenant_id='other-tenant', roles=['student']))
        another = ApplicationStore(self.fixture.store).create(CreateApplication(type_id='platform.form', type_version='1.0.0',
            template_id='form-registration', name='Other registration', idempotency_key='form-create-other-key'), 'trace')['application_id']
        second = form_store(self.fixture.store, another); draft = second.draft(); second.publish(PublishDraft(expected_revision=0, expected_release_id=None, idempotency_key='publish-other-form'), 'trace')
        with self.assertRaises(NotFound): self.service(app_id=another).detail(record_id)

    def test_saved_draft_and_record_versions_survive_updates_and_rollback(self):
        original = self.publish(); service = self.service()
        command = SubmitForm(release_id=original, values={'content': 'Historical value'}, idempotency_key='historical-record-key')
        record_id = service.submit(command, 'trace')['record_id']
        draft = self.target.draft(); content = FormConfig.model_validate(draft['content'])
        content.model.fields[0].label = 'Changed label'; content.form.sections[0].fields[0].label = 'Changed label'
        save = SaveForm(expected_revision=draft['revision'], content=content, idempotency_key='stable-save-form-key')
        first = self.target.save(save, 'trace'); self.assertTrue(self.target.save(save, 'trace')['replayed'])
        self.assertEqual(self.target.draft()['revision'], first['revision'])
        newer = self.publish('form-publish-newer')
        self.assertEqual(service.detail(record_id)['content']['model']['fields'][0]['label'], '登记内容')
        self.assertTrue(service.submit(command, 'trace')['replayed'])
        with self.assertRaises(Conflict): service.submit(command.model_copy(update={'idempotency_key': 'stale-release-record-key'}), 'trace')
        current = self.target.draft()
        restored = self.target.publish(Rollback(expected_revision=current['revision'], expected_release_id=newer, target_release_id=original, idempotency_key='restore-original-form'), 'trace', rollback=True)
        self.assertNotEqual(restored['release_id'], original)
        self.assertEqual(service.detail(record_id)['release_id'], original)

    def test_submission_audit_is_atomic_and_pause_preserves_reads(self):
        release = self.publish(); service = self.service()
        command = SubmitForm(release_id=release, values={'content': 'Synthetic'}, idempotency_key='audit-atomic-form-key')
        with patch.object(service.store, '_audit', side_effect=RuntimeError('Synthetic audit failure')):
            with self.assertRaises(RuntimeError): service.submit(command, 'trace')
        self.assertEqual(service.listing()['total'], 0)
        record_id = service.submit(command, 'trace')['record_id']
        with patch.dict(os.environ, {'FORM_APPLICATIONS_ENABLED': '0'}):
            self.assertFalse(service.definition()['accepting_submissions'])
            self.assertEqual(service.detail(record_id)['id'], record_id)
            with self.assertRaises(Conflict): service.submit(command.model_copy(update={'idempotency_key': 'paused-new-record-key'}), 'trace')

    def test_bad_new_type_binding_does_not_hide_healthy_course_apps(self):
        import sqlalchemy as sa
        from app.modules.config.application_store import applications
        self.publish()
        catalog = ApplicationStore(self.fixture.store)
        healthy = catalog.create(CreateApplication(type_id='campus.course-query', type_version='1.0.0', name='Healthy course', idempotency_key='healthy-course-key'), 'trace')['application_id']
        course = catalog.config_store(healthy); course.publish(PublishDraft(expected_revision=0, expected_release_id=None, idempotency_key='healthy-course-publish'), 'trace')
        with self.fixture.engine.begin() as connection:
            connection.execute(applications.update().where(applications.c.application_id == self.app_id).values(type_digest='0' * 64))
        self.assertEqual([item['id'] for item in catalog.public_catalog(self.student)], [healthy])
        invalid = next(item for item in catalog.listing()['items'] if item['application_id'] == self.app_id)
        self.assertFalse(invalid['available'])
        with self.assertRaises(APIError): form_store(self.fixture.store, self.app_id)

    def test_http_submission_reload_and_admin_records(self):
        client = TestClient(app)
        headers = student_headers(self)
        release = self.publish()
        with patch.dict(os.environ, {'LOCAL_ADMIN_TOKEN': 'a' * 24}), patch('app.modules.config.routes.get_store', side_effect=lambda principal=None: ConfigStore(self.fixture.engine, principal.tenant_id, principal.subject)):
            definition = client.get(f'/api/v1/applications/{self.app_id}/form', headers=headers)
            self.assertEqual(definition.status_code, 200)
            command = {'release_id': release, 'values': {'content': 'HTTP synthetic'}, 'idempotency_key': 'http-form-submit-key'}
            first = client.post(f'/api/v1/applications/{self.app_id}/records', headers=headers, json=command)
            self.assertEqual(first.status_code, 200)
            self.assertTrue(client.post(f'/api/v1/applications/{self.app_id}/records', headers=headers, json=command).json()['replayed'])
            record_id = first.json()['record_id']
            self.assertEqual(client.get(f'/api/v1/applications/{self.app_id}/records/{record_id}', headers=headers).json()['values'], command['values'])
            self.assertEqual(client.get(f'/api/v1/admin/applications/{self.app_id}/form/records', headers=headers).status_code, 403)
            self.assertEqual(client.get(f'/api/v1/admin/applications/{self.app_id}/form/records', headers={'Authorization': 'Bearer ' + 'a' * 24}).json()['total'], 1)

    def test_http_requirement_create_configure_publish_use_and_resume(self):
        client = TestClient(app); user_headers = student_headers(self); admin_headers = {'Authorization': 'Bearer ' + 'a' * 24}
        with patch.dict(os.environ, {'LOCAL_ADMIN_TOKEN': 'a' * 24}), patch('app.modules.config.routes.get_store', side_effect=lambda principal=None: ConfigStore(self.fixture.engine, principal.tenant_id, principal.subject)):
            created = client.post('/api/v1/assistant/requirements', headers=admin_headers, json={
                'title': '登记建设需求', 'description': '独立登记与本人查询', 'business_kind': 'registration', 'idempotency_key': 'registration-requirement-key'}).json()['item']
            prepared = client.post(f"/api/v1/assistant/requirements/{created['id']}/prepare", headers=admin_headers, json={
                'expected_revision': created['revision'], 'name': '登记闭环', 'template_id': 'form-registration', 'idempotency_key': 'registration-prepare-key'})
            self.assertEqual(prepared.status_code, 200)
            converted = client.post(f"/api/v1/assistant/requirements/confirmations/{prepared.json()['command_id']}/execute", headers=admin_headers, json={})
            self.assertEqual(converted.status_code, 200)
            app_id = converted.json()['application_id']; base = f'/api/v1/admin/applications/{app_id}/form'
            draft = client.get(base, headers=admin_headers).json()
            self.assertEqual(draft['content']['application']['name'], '登记闭环')
            command = {'expected_revision': draft['revision'], 'content': draft['content'], 'idempotency_key': 'registration-save-key'}
            saved = client.put(base + '/draft', headers=admin_headers, json=command)
            self.assertEqual(saved.status_code, 200)
            trial = client.post(base + '/preview', headers=admin_headers, json={'expected_revision': saved.json()['revision'], 'content': saved.json()['content'], 'values': {'content': '试填'}})
            self.assertTrue(trial.json()['valid'])
            published = client.post(base + '/publish', headers=admin_headers, json={'expected_revision': saved.json()['revision'], 'expected_release_id': None, 'idempotency_key': 'registration-publication-key'})
            self.assertEqual(published.status_code, 200)
            definition = client.get(f'/api/v1/applications/{app_id}/form', headers=user_headers).json()
            submitted = client.post(f'/api/v1/applications/{app_id}/records', headers=user_headers, json={'release_id': definition['release_id'], 'values': {'content': '独立记录'}, 'idempotency_key': 'registration-complete-submit-key'})
            self.assertEqual(submitted.status_code, 200)
            self.assertEqual(client.get(base + '/records', headers=admin_headers).json()['total'], 1)
            progress = client.get(f"/api/v1/assistant/requirements/{created['id']}", headers=admin_headers).json()['item']
            self.assertEqual(progress['target_application_id'], app_id)
            self.assertEqual(progress['progress']['release_id'], published.json()['release_id'])
            self.assertFalse(progress['progress']['unpublished_changes'])
