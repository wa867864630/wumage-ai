import copy
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.main import app
from app.errors import APIError
from app.modules.config.application_store import ApplicationStore, CreateApplication
from app.modules.config.schema import PublishDraft
from app.modules.config.store import ConfigStore, Conflict, NotFound
from app.modules.forms.runtime import form_store, SubmitForm, records
from app.modules.forms.routes import SaveTypedForm
from app.modules.identity.auth import Principal
from app.modules.approvals.schema import LeaveConfig, default_leave
from app.modules.approvals.runtime import ApprovalRecords, Transition, instances
from app.modules.approvals.adapter import ApprovalAdapter
from app.modules.intent.application_router import ResolveApplication, resolve_application
from support import student_headers


class ApprovalSchemaTests(unittest.TestCase):
    def test_fixed_actor_no_self_approval_no_cycles_and_core_fields(self):
        base = default_leave().model_dump()
        for mutation in ('self', 'self-zero', 'actor', 'loop', 'missing-reason', 'unknown-code'):
            value = copy.deepcopy(base)
            if mutation == 'self': value['workflow']['nodes'][1]['allow_self_approval'] = True
            elif mutation == 'self-zero': value['workflow']['nodes'][1]['allow_self_approval'] = 0
            elif mutation == 'actor': value['workflow']['nodes'][1]['actor']['resolver_id'] = 'arbitrary.person'
            elif mutation == 'loop': value['workflow']['nodes'][1]['on_approve'] = 'start'
            elif mutation == 'missing-reason': value['model']['fields'][2]['nullable'] = True
            else: value['workflow']['script'] = 'not-allowed'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): LeaveConfig.model_validate(value)
        with self.assertRaises(APIError): ApprovalAdapter.validate_values(default_leave(), {'start_date': '2026-10-21', 'end_date': '2026-10-20', 'reason': 'Synthetic'})
        with self.assertRaises(ValueError): Transition(action='rejected', expected_revision=1, reason=' ', idempotency_key='invalid-reason-command')

    def test_capability_routing_uses_authorized_leave_app_before_current_course(self):
        apps = [{'id': 'schedule', 'name': '课表助手', 'type_id': 'campus.course-query'}, {'id': 'leave', 'name': '学院请假审批', 'type_id': 'platform.leave-approval'}]
        result = resolve_application(ResolveApplication(text='我要请假', current_application_id='schedule'), apps)
        self.assertEqual(result.application_id, 'leave'); self.assertEqual(result.type_id, 'platform.leave-approval'); self.assertEqual(result.status, 'selected')
        self.assertIsNone(result.query_text)
        progress = resolve_application(ResolveApplication(text='查看我的申请进度', current_application_id='leave'), apps)
        self.assertEqual(progress.runtime_action, 'records')


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Requires isolated PostgreSQL/openGauss schema')
class ApprovalDatabaseTests(unittest.TestCase):
    def setUp(self):
        from test_config import PostgresConfigTests
        self.fixture = PostgresConfigTests('test_draft_publish_replay_conflict_and_rollback'); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        flag = patch.dict(os.environ, {'APPROVAL_APPLICATIONS_ENABLED': '1'}); flag.start(); self.addCleanup(flag.stop)
        self.app_id = ApplicationStore(self.fixture.store).create(CreateApplication(type_id='platform.leave-approval', type_version='1.0.0',
            template_id='leave-basic', name='Synthetic leave', idempotency_key='create-leave-template'), 'trace')['application_id']
        self.target = form_store(self.fixture.store, self.app_id)
        self.release = self.target.publish(PublishDraft(expected_revision=0, expected_release_id=None, idempotency_key='publish-leave-template'), 'trace')['release_id']
        self.student = Principal(subject='student-one', tenant_id='local-demo', roles=['student'])
        self.admin = Principal(subject='local-admin', tenant_id='local-demo', roles=['config_admin'])
        self.other_admin = Principal(subject='other-admin', tenant_id='local-demo', roles=['config_admin'])

    def service(self, principal=None, app_id=None):
        user = principal or self.student
        return ApprovalRecords(ConfigStore(self.fixture.engine, user.tenant_id, user.subject), user, app_id or self.app_id)

    def apply(self, key='submit-leave-request'):
        return self.service().submit(SubmitForm(release_id=self.release, values={'start_date': '2026-10-20', 'end_date': '2026-10-21', 'reason': 'Synthetic reason'}, idempotency_key=key), 'trace')['record_id']

    def command(self, action, key='approval-action-command', revision=1, reason='Synthetic explanation'):
        return Transition(action=action, expected_revision=revision, reason=reason, idempotency_key=key)

    def test_apply_approve_details_and_audit_json_scope(self):
        record_id = self.apply()
        self.assertEqual(self.service().work_items()['items'][0]['status'], 'pending')
        self.assertEqual(self.service(self.admin).work_items(scope='assigned')['total'], 1)
        result = self.service(self.admin).transition(record_id, self.command('approved'), 'trace')
        self.assertEqual(result['status'], 'approved')
        self.assertTrue(self.service(self.admin).transition(record_id, self.command('approved'), 'trace')['replayed'])
        detail = self.service().workflow_detail(record_id)
        self.assertEqual(detail['workflow']['status'], 'approved'); self.assertEqual(detail['duration_days'], 2)
        self.assertEqual(len(detail['events']), 1); self.assertEqual(detail['events'][0]['action'], 'approved')
        with self.assertRaises(Conflict): self.service().transition(record_id, self.command('withdrawn', 'withdraw-after-approved'), 'trace')

    def test_reject_and_withdraw_terminal_states_and_mismatched_replay(self):
        first = self.apply(); admin = self.service(self.admin)
        self.assertEqual(admin.transition(first, self.command('rejected'), 'trace')['status'], 'rejected')
        with self.assertRaises(Conflict): admin.transition(first, self.command('approved'), 'trace')
        second = self.apply('withdraw-second-leave')
        self.assertEqual(self.service().transition(second, self.command('withdrawn', 'withdraw-before-approval'), 'trace')['status'], 'withdrawn')
        with self.assertRaises(Conflict): admin.transition(second, self.command('approved', 'approve-withdrawn-leave'), 'trace')

    def test_cross_user_cross_tenant_assignee_and_role_revocation(self):
        record_id = self.apply()
        other = self.service(Principal(subject='student-two', tenant_id='local-demo', roles=['student']))
        with self.assertRaises(NotFound): other.workflow_detail(record_id)
        with self.assertRaises(NotFound): other.transition(record_id, self.command('withdrawn'), 'trace')
        with self.assertRaises(APIError): self.service(self.other_admin).transition(record_id, self.command('approved'), 'trace')
        revoked = self.service(Principal(subject='local-admin', tenant_id='local-demo', roles=['student']))
        with self.assertRaises(APIError): revoked.transition(record_id, self.command('approved'), 'trace')
        with self.assertRaises(APIError): self.service(Principal(subject='student-one', tenant_id='other-tenant', roles=['student']))

    def test_concurrent_approval_withdrawal_and_takeover_are_serialized(self):
        record_id = self.apply()
        def attempt(operation):
            try:
                if operation == 'approve': return self.service(self.admin).transition(record_id, self.command('approved', 'race-approve-key'), 'trace')['status']
                return self.service().transition(record_id, self.command('withdrawn', 'race-withdraw-key'), 'trace')['status']
            except Conflict: return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ['approve', 'withdraw']))
        self.assertEqual(results.count('conflict'), 1)
        next_id = self.apply('takeover-request-key')
        taken = self.service(self.other_admin).transition(next_id, self.command('reassign_to_me', 'takeover-action-key'), 'trace')
        self.assertEqual(taken['approver_id'], self.other_admin.subject)
        with self.assertRaises(APIError): self.service(self.admin).transition(next_id, self.command('approved', 'old-assignee-action', revision=2), 'trace')
        self.assertEqual(self.service(self.other_admin).transition(next_id, self.command('approved', 'new-assignee-action', revision=2), 'trace')['status'], 'approved')

    def test_self_approval_dates_and_atomic_failure_leave_no_partial_records(self):
        import sqlalchemy as sa
        command = SubmitForm(release_id=self.release, values={'start_date': '2026-10-20', 'end_date': '2026-10-21', 'reason': 'Synthetic'}, idempotency_key='self-approval-request')
        with self.assertRaises(APIError): self.service(self.admin).submit(command, 'trace')
        service = self.service()
        with patch.object(service.store, '_audit', side_effect=RuntimeError('Synthetic failed audit')):
            with self.assertRaises(RuntimeError): service.submit(command, 'trace')
        with self.fixture.engine.connect() as connection:
            self.assertEqual(connection.scalar(sa.select(sa.func.count()).select_from(records)), 0)
            self.assertEqual(connection.scalar(sa.select(sa.func.count()).select_from(instances)), 0)
        record_id = self.apply()
        admin = self.service(self.admin)
        with patch.object(admin.store, '_audit', side_effect=RuntimeError('Synthetic failed decision audit')):
            with self.assertRaises(RuntimeError): admin.transition(record_id, self.command('approved'), 'trace')
        self.assertEqual(self.service().workflow_detail(record_id)['workflow']['status'], 'pending')

    def test_old_approver_fixed_after_new_publication_and_pause(self):
        record_id = self.apply()
        other_store = form_store(ConfigStore(self.fixture.engine, actor_id=self.other_admin.subject), self.app_id)
        current = other_store.draft()
        newer = other_store.publish(PublishDraft(expected_revision=current['revision'], expected_release_id=self.release, idempotency_key='new-publisher-new-release'), 'trace')['release_id']
        self.assertNotEqual(newer, self.release)
        self.assertEqual(self.service().workflow_detail(record_id)['workflow']['approver_id'], self.admin.subject)
        with patch.dict(os.environ, {'APPROVAL_APPLICATIONS_ENABLED': '0'}):
            self.assertEqual(self.service(self.admin).transition(record_id, self.command('approved'), 'trace')['status'], 'approved')
            with self.assertRaises(Conflict): self.apply('paused-application-key')

    def test_http_apply_approve_query_and_schema_guard(self):
        client = TestClient(app); student = student_headers(self); admin = {'Authorization': 'Bearer ' + 'a' * 24}
        with patch.dict(os.environ, {'LOCAL_ADMIN_TOKEN': 'a' * 24}), patch('app.modules.config.routes.get_store', side_effect=lambda principal=None: ConfigStore(self.fixture.engine, principal.tenant_id, principal.subject)):
            definition = client.get(f'/api/v1/applications/{self.app_id}/form', headers=student)
            self.assertEqual(definition.json()['type_id'], 'platform.leave-approval')
            submitted = client.post(f'/api/v1/applications/{self.app_id}/records', headers=student, json={'release_id': self.release,
                'values': {'start_date': '2026-10-20', 'end_date': '2026-10-21', 'reason': 'HTTP synthetic'}, 'idempotency_key': 'http-apply-leave-key'})
            self.assertEqual(submitted.status_code, 200)
            record_id = submitted.json()['record_id']; base = f'/api/v1/applications/{self.app_id}/approvals'
            self.assertEqual(client.get(base + '?scope=assigned', headers=student).status_code, 403)
            self.assertEqual(client.get(base + '?scope=assigned', headers=admin).json()['total'], 1)
            approved = client.post(base + f'/{record_id}/transition', headers=admin, json=self.command('approved').model_dump())
            self.assertEqual(approved.json()['status'], 'approved')
            detail = client.get(base + f'/{record_id}', headers=student)
            self.assertEqual(detail.status_code, 200); self.assertEqual(detail.json()['workflow']['status'], 'approved')
