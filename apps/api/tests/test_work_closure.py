import os
import unittest
from uuid import uuid4
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import sqlalchemy as sa
from fastapi.testclient import TestClient
from app.main import app
from app.errors import APIError
from app.modules.config.store import ConfigStore, Conflict, NotFound, releases
from app.modules.config.application_store import ApplicationStore, CreateApplication
from app.modules.config.schema import PublishDraft, SaveDraft
from app.modules.config.lifecycle import change, ChangeLifecycle
from app.modules.forms.runtime import FormRecords, SubmitForm, form_store, records
from app.modules.forms.drafts import BusinessDrafts, SaveBusinessDraft, drafts
from app.modules.identity.auth import Principal


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Requires isolated schema')
class WorkClosureTests(unittest.TestCase):
    def setUp(self):
        from test_config import PostgresConfigTests
        self.fixture = PostgresConfigTests('test_draft_publish_replay_conflict_and_rollback')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        flag = patch.dict(os.environ, {'FORM_APPLICATIONS_ENABLED': '1', 'APPROVAL_APPLICATIONS_ENABLED': '1'}); flag.start(); self.addCleanup(flag.stop)
        self.store = self.fixture.store
        self.app_id = ApplicationStore(self.store).create(CreateApplication(type_id='platform.form', type_version='1.0.0',
            name='Synthetic work', template_id='form-registration', idempotency_key='work-create-form'), 'trace')['application_id']
        self.target = form_store(self.store, self.app_id)
        self.release = self.target.publish(PublishDraft(expected_revision=0, expected_release_id=None, idempotency_key='work-publish-form'), 'trace')['release_id']
        self.principal = Principal(subject='applicant', tenant_id='local-demo', roles=['student'])
        self.service = self.for_user(self.principal)
        self.drafts = BusinessDrafts(self.service); self.draft_id = str(uuid4())

    def for_user(self, principal):
        return FormRecords(ConfigStore(self.fixture.engine, principal.tenant_id, principal.subject), principal, self.app_id)

    def save_command(self, **kwargs):
        return SaveBusinessDraft(release_id=self.release, expected_revision=0, values={}, idempotency_key='work-save-draft-command', **kwargs)

    def test_incomplete_save_resume_submit_is_one_atomic_record(self):
        command = self.save_command()
        first = self.drafts.save(self.draft_id, command, 'trace')
        self.assertTrue(self.drafts.save(self.draft_id, command, 'trace')['replayed'])
        self.assertEqual(self.service.listing()['total'], 0)
        self.assertEqual(self.drafts.detail(self.draft_id)['status'], 'active')
        submit = SubmitForm(release_id=self.release, values={'content': 'Synthetic complete'}, idempotency_key='work-submit-draft-command', draft_id=self.draft_id, draft_revision=first['revision'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.service.submit(submit, 'trace'), range(2)))
        self.assertEqual(results[0]['record_id'], results[1]['record_id'])
        detail = self.drafts.detail(self.draft_id)
        self.assertEqual(detail['status'], 'submitted'); self.assertEqual(detail['record_id'], results[0]['record_id'])
        self.assertEqual(self.service.listing()['total'], 1)

    def test_isolation_stale_revision_and_no_partial_submission(self):
        self.drafts.save(self.draft_id, self.save_command(), 'trace')
        other = BusinessDrafts(self.for_user(Principal(subject='other', tenant_id='local-demo', roles=['student'])))
        with self.assertRaises(NotFound): other.detail(self.draft_id)
        with self.assertRaises(Conflict): self.drafts.save(self.draft_id, self.save_command().model_copy(update={'idempotency_key': 'work-other-save-command'}), 'trace')
        bad = SubmitForm(release_id=self.release, values={'content': 'not committed'}, idempotency_key='work-submit-stale-command', draft_id=self.draft_id, draft_revision=99)
        with self.assertRaises(Conflict): self.service.submit(bad, 'trace')
        self.assertEqual(self.service.listing()['total'], 0)
        self.assertEqual(self.drafts.detail(self.draft_id)['status'], 'active')

    def test_changed_release_keeps_old_draft_then_explicit_save_updates(self):
        self.drafts.save(self.draft_id, self.save_command(), 'trace')
        second = self.target.publish(PublishDraft(expected_revision=self.target.draft()['revision'], expected_release_id=self.release, idempotency_key='work-second-publish'), 'trace')['release_id']
        with self.assertRaises(Conflict): self.drafts.save(self.draft_id, self.save_command().model_copy(update={'expected_revision': 1, 'idempotency_key': 'work-save-old-version'}), 'trace')
        self.assertEqual(self.drafts.detail(self.draft_id)['release_id'], self.release)
        migrated = self.drafts.save(self.draft_id, self.save_command().model_copy(update={'release_id': second, 'expected_revision': 1, 'idempotency_key': 'work-migrate-draft-command'}), 'trace')
        self.assertEqual(migrated['release_id'], second)

    def lifecycle(self, status, revision=0, key='work-lifecycle-command'):
        return change(self.store, self.app_id, ChangeLifecycle(status=status, expected_revision=revision, reason='Synthetic state change', idempotency_key=key), 'trace')

    def test_disable_blocks_new_business_preserves_reads_and_replay(self):
        command = SubmitForm(release_id=self.release, values={'content': 'before stop'}, idempotency_key='work-before-stop-submit')
        receipt = self.service.submit(command, 'trace')
        self.lifecycle('disabled'); self.assertTrue(self.lifecycle('disabled')['replayed'])
        self.assertFalse(self.service.definition()['accepting_submissions'])
        self.assertTrue(self.service.submit(command, 'trace')['replayed'])
        with self.assertRaises(APIError): self.service.submit(command.model_copy(update={'idempotency_key': 'work-after-stop-submit'}), 'trace')
        self.assertEqual(self.service.detail(receipt['record_id'])['id'], receipt['record_id'])
        self.assertEqual(ApplicationStore(self.store).public_catalog(self.principal), [])
        with self.assertRaises(APIError): self.drafts.save(self.draft_id, self.save_command(), 'trace')

    def test_archive_is_readonly_and_config_rollback_does_not_reactivate(self):
        self.lifecycle('archived')
        from app.modules.forms.routes import SaveTypedForm
        content = self.target.adapter.model.model_validate(self.target.draft()['content'])
        with self.assertRaises(Conflict): self.target.save(SaveTypedForm(content=content, expected_revision=0, idempotency_key='work-archive-save-key'), 'trace')
        with self.assertRaises(Conflict): self.target.publish(PublishDraft(expected_revision=self.target.draft()['revision'], expected_release_id=self.release, idempotency_key='work-archive-publish-key'), 'trace')
        with self.assertRaises(Conflict): self.lifecycle('active', 1, 'work-direct-reactivate')
        self.lifecycle('disabled', 1, 'work-restore-disabled')
        self.target.publish(PublishDraft(expected_revision=self.target.draft()['revision'], expected_release_id=self.release, idempotency_key='work-disabled-publish-key'), 'trace')
        self.assertFalse(self.service.definition()['accepting_submissions'])
        self.lifecycle('active', 2, 'work-enable-explicit')
        self.assertTrue(self.service.definition()['accepting_submissions'])

    def test_pending_approval_blocks_archive_but_disable_allows_completion(self):
        from app.modules.approvals.runtime import ApprovalRecords, Transition
        app_id = ApplicationStore(self.store).create(CreateApplication(type_id='platform.leave-approval', type_version='1.0.0', name='Synthetic leave', template_id='leave-basic', idempotency_key='work-create-leave'), 'trace')['application_id']
        release = form_store(self.store, app_id).publish(PublishDraft(expected_revision=0, expected_release_id=None, idempotency_key='work-publish-leave'), 'trace')['release_id']
        applicant = ApprovalRecords(ConfigStore(self.fixture.engine, 'local-demo', 'applicant'), self.principal, app_id)
        receipt = applicant.submit(SubmitForm(release_id=release, values={'start_date': '2026-10-20', 'end_date': '2026-10-21', 'reason': 'Synthetic leave'}, idempotency_key='work-apply-leave'), 'trace')
        command = ChangeLifecycle(status='archived', expected_revision=0, reason='Synthetic archive', idempotency_key='work-archive-pending')
        with self.assertRaises(Conflict): change(self.store, app_id, command, 'trace')
        change(self.store, app_id, command.model_copy(update={'status': 'disabled', 'idempotency_key': 'work-disable-pending'}), 'trace')
        admin = Principal(subject='local-admin', tenant_id='local-demo', roles=['config_admin'])
        processor = ApprovalRecords(self.store, admin, app_id)
        processor.transition(receipt['record_id'], Transition(action='approved', expected_revision=1, reason='Synthetic approval', idempotency_key='work-approve-disabled'), 'trace')
        change(self.store, app_id, command.model_copy(update={'expected_revision': 1}), 'trace')
        self.assertEqual(applicant.workflow_detail(receipt['record_id'])['workflow']['status'], 'approved')

    def test_cancel_draft_is_durable_and_never_submits(self):
        self.drafts.save(self.draft_id, self.save_command(), 'trace')
        command = self.save_command().model_copy(update={'expected_revision': 1, 'cancel': True, 'idempotency_key': 'work-cancel-draft-command'})
        self.assertEqual(self.drafts.save(self.draft_id, command, 'trace')['status'], 'cancelled')
        self.assertTrue(self.drafts.save(self.draft_id, command, 'trace')['replayed'])
        self.assertEqual(self.service.listing()['total'], 0)

    def test_http_my_work_requires_identity_and_only_lists_owner(self):
        from support import student_headers
        client = TestClient(app); headers = student_headers(self)
        identity = client.get('/api/v1/me', headers=headers).json()
        principal = Principal.model_validate(identity)
        def scoped(principal=None):
            return ConfigStore(self.fixture.engine, principal.tenant_id if principal else 'local-demo', principal.subject if principal else 'local-admin')
        with patch('app.modules.config.routes.get_store', side_effect=scoped):
            path = f'/api/v1/applications/{self.app_id}/business-drafts/{self.draft_id}'
            response = client.put(path, headers=headers, json=self.save_command().model_dump())
            self.assertEqual(response.status_code, 200, response.text)
            work = client.get('/api/v1/my-work', headers=headers)
            self.assertEqual(work.status_code, 200, work.text)
            self.assertEqual(work.json()['items'][0]['id'], self.draft_id)
            self.assertEqual(work.json()['items'][0]['application_name'], 'Synthetic work')
            receipt = client.post(f'/api/v1/applications/{self.app_id}/records', headers=headers, json={
                'release_id': self.release, 'values': {'content': 'HTTP completion'}, 'draft_id': self.draft_id,
                'draft_revision': 1, 'idempotency_key': 'work-http-submit-draft'})
            self.assertEqual(receipt.status_code, 200, receipt.text)
            completed = client.get('/api/v1/my-work', headers=headers).json()
            self.assertEqual(completed['items'], [])
            self.assertEqual(completed['submitted'][0]['id'], receipt.json()['record_id'])
            with patch.dict(os.environ, {'LOCAL_ADMIN_TOKEN': 'a' * 24}):
                admin_work = client.get('/api/v1/my-work', headers={'Authorization': 'Bearer ' + 'a' * 24})
                self.assertEqual(admin_work.status_code, 200, admin_work.text)
                self.assertEqual(admin_work.json()['submitted'], [])
            self.assertEqual(client.get('/api/v1/my-work').status_code, 401)
        self.assertEqual(self.drafts.service.listing()['total'], 0)
