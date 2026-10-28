import os
import unittest
from uuid import uuid4
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app
from app.errors import APIError
from app.modules.config.application_store import ApplicationStore, CreateApplication
from app.modules.config.store import ConfigStore, NotFound
from app.modules.config.schema import PublishDraft
from app.modules.forms.routes import SaveTypedForm
from app.modules.forms.schema import FormConfig
from app.modules.approvals.schema import LeaveConfig
from app.modules.forms.runtime import SubmitForm
from app.modules.forms.drafts import BusinessDrafts, SaveBusinessDraft
from app.modules.approvals.runtime import Transition
from app.modules.identity.auth import Principal
from app.modules.unified_editor.runtime import form_store, record_service
from support import student_headers


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Requires isolated database schema')
class UnifiedEditorTests(unittest.TestCase):
    def setUp(self):
        from test_config import PostgresConfigTests
        self.fixture = PostgresConfigTests('test_draft_publish_replay_conflict_and_rollback')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.flags = patch.dict(os.environ, {'FORM_APPLICATIONS_ENABLED': '1', 'APPROVAL_APPLICATIONS_ENABLED': '1'})
        self.flags.start(); self.addCleanup(self.flags.stop)
        self.store = ConfigStore(self.fixture.engine, 'local-demo', 'local-admin')
        self.student = Principal(subject='unified-student', tenant_id='local-demo', roles=['student'])
        self.admin = Principal(subject='local-admin', tenant_id='local-demo', roles=['config_admin'])

    def create(self, type_id, version='1.1.0'):
        return ApplicationStore(self.store).create(CreateApplication(type_id=type_id, type_version=version,
            name='Unified test', template_id='leave-basic' if type_id == 'platform.leave-approval' else 'form-registration',
            idempotency_key='unified-create-' + uuid4().hex), 'trace')['application_id']

    def service(self, app_id, principal=None):
        principal = principal or self.student
        return record_service(ConfigStore(self.fixture.engine, principal.tenant_id, principal.subject), principal, app_id)

    def publish(self, target, key):
        draft = target.draft()
        return target.publish(PublishDraft(expected_revision=draft['revision'], expected_release_id=draft['current_release_id'], idempotency_key=key), 'trace')['release_id']

    def test_new_form_draft_submit_replay_and_old_version_coexist(self):
        app_id = self.create('platform.form'); old_id = self.create('platform.form', '1.0.0')
        target = form_store(self.store, app_id); release = self.publish(target, 'unified-form-publish')
        service = self.service(app_id); draft_id = str(uuid4())
        saved = BusinessDrafts(service).save(draft_id, SaveBusinessDraft(release_id=release, expected_revision=0, values={}, idempotency_key='unified-partial-save'), 'trace')
        command = SubmitForm(release_id=release, values={'content': 'Unified value'}, draft_id=draft_id, draft_revision=saved['revision'], idempotency_key='unified-submit-replay')
        record = service.submit(command, 'trace'); self.assertTrue(service.submit(command, 'trace')['replayed'])
        self.assertEqual(BusinessDrafts(service).detail(draft_id)['record_id'], record['record_id'])
        self.assertEqual(service.detail(record['record_id'])['values']['content'], 'Unified value')
        old = form_store(self.store, old_id); self.publish(old, 'legacy-coexist-publish')
        self.assertEqual(ApplicationStore(self.store).get(old_id)['type_version'], '1.0.0')
        self.assertEqual({item['id'] for item in ApplicationStore(self.store).public_catalog(self.student)}, {app_id, old_id})
        outsider = Principal(subject='other-student', tenant_id='local-demo', roles=['student'])
        with self.assertRaises(NotFound): self.service(app_id, outsider).detail(record['record_id'])

    def test_workflow_names_publish_new_applications_keep_old_snapshot(self):
        app_id = self.create('platform.leave-approval'); target = form_store(self.store, app_id)
        content = target.draft()['content']; content['workflow']['name'] = 'Original process'
        next(node for node in content['workflow']['nodes'] if node['type'] == 'approval')['name'] = 'Original review'
        target.save(SaveTypedForm(expected_revision=0, content=LeaveConfig.model_validate(content), idempotency_key='unified-save-workflow'), 'trace')
        release = self.publish(target, 'unified-leave-publish')
        applicant = self.service(app_id)
        record_id = applicant.submit(SubmitForm(release_id=release, values={'start_date': '2026-10-20', 'end_date': '2026-10-21', 'reason': 'Synthetic leave'}, idempotency_key='unified-leave-apply'), 'trace')['record_id']
        content['workflow']['name'] = 'New process'
        target.save(SaveTypedForm(expected_revision=target.draft()['revision'], content=LeaveConfig.model_validate(content), idempotency_key='unified-save-new-workflow'), 'trace')
        self.publish(target, 'unified-leave-republish')
        self.assertEqual(applicant.workflow_detail(record_id)['content']['workflow']['name'], 'Original process')
        approved = self.service(app_id, self.admin).transition(record_id, Transition(action='approved', expected_revision=1, reason='', idempotency_key='unified-leave-approve'), 'trace')
        self.assertEqual(approved['status'], 'approved')

    def test_http_new_version_design_validation_and_submission(self):
        app_id = self.create('platform.form'); client = TestClient(app); headers = student_headers(self)
        base = f'/api/v1/admin/applications/{app_id}/form'; admin_headers = {'Authorization': 'Bearer ' + 'a' * 24}
        with patch.dict(os.environ, {'LOCAL_ADMIN_TOKEN': 'a' * 24}), patch('app.modules.config.routes.get_store', side_effect=lambda principal=None: ConfigStore(self.fixture.engine, principal.tenant_id, principal.subject)):
            draft = client.get(base, headers=admin_headers).json()
            payload = {'expected_revision': 0, 'content': draft['content']}
            self.assertEqual(client.post(base + '/validate', headers=headers, json=payload).status_code, 403)
            self.assertTrue(client.post(base + '/validate', headers=admin_headers, json=payload).json()['valid'])
            self.assertEqual(client.post(base + '/validate', headers=admin_headers, json={**payload, 'expected_revision': 2}).status_code, 409)
            result = client.post(base + '/publish', headers=admin_headers, json={'expected_revision': 0, 'expected_release_id': None, 'idempotency_key': 'unified-http-publish'})
            self.assertEqual(result.status_code, 200, result.text)
            release = result.json()['release_id']
            snapshot = client.get(base + '/releases/' + release, headers=admin_headers)
            self.assertEqual(snapshot.status_code, 200)
            self.assertEqual(snapshot.json()['id'], release)
            self.assertEqual(snapshot.json()['content'], draft['content'])
            self.assertEqual(client.get(base + '/releases/' + release, headers=headers).status_code, 403)
            other_id = self.create('platform.form')
            self.assertEqual(client.get(f'/api/v1/admin/applications/{other_id}/form/releases/{release}', headers=admin_headers).status_code, 404)
            submitted = client.post(f'/api/v1/applications/{app_id}/records', headers=headers, json={'release_id': release, 'values': {'content': 'HTTP unified'}, 'idempotency_key': 'unified-http-submit'})
            self.assertEqual(submitted.status_code, 200, submitted.text)
            self.assertEqual(client.get(f'/api/v1/applications/{app_id}/form', headers=headers).status_code, 200)
