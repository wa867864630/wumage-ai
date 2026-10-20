import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.errors import APIError
from app.main import app
from app.modules.config.course_design import AcceptProposal, CourseProposal, context, evaluate
from app.modules.config.schema import default_config, SaveDraft
from app.modules.config.store import Conflict, ConfigStore
from support import student_headers
import test_config as config_tests


def baseline():
    return {'revision': 0, 'content': default_config().model_dump(), 'current_release_id': None}


def proposal(draft=None):
    return context(draft or baseline())['example_proposal']


class CourseDesignTests(unittest.TestCase):
    def test_preview_is_pure_and_preserves_roles_rules_order(self):
        draft = baseline()
        before = json.dumps(draft)
        result = evaluate(CourseProposal.model_validate(proposal(draft), strict=True), draft)
        self.assertTrue(result['can_accept'])
        self.assertEqual(json.dumps(draft), before)
        self.assertEqual(result['candidate']['application'], draft['content']['application'])
        self.assertEqual(result['candidate']['rules'], draft['content']['rules'])
        self.assertEqual([c['field'] for c in result['candidate']['columns']], [c['field'] for c in draft['content']['columns']])
        self.assertTrue(any('上课地点' in change for change in result['changes']))

    def test_stale_revision_hash_and_catalog_rejected(self):
        for key, value in [('base_revision', 1), ('base_content_hash', 'a' * 64), ('catalog_hash', 'b' * 64)]:
            data = proposal(); data[key] = value
            with self.subTest(key=key), self.assertRaises(Conflict):
                evaluate(CourseProposal.model_validate(data, strict=True), baseline())

    def test_scope_unknown_properties_and_strict_types(self):
        for changes in ({'visible_roles': ['config_admin']}, {'application_id': 'leave'}, {'base_revision': '0'}):
            with self.assertRaises(ValidationError):
                CourseProposal.model_validate(proposal() | changes, strict=True)
        for value in ({'field': 'student_id', 'label': '学号', 'visible': True},
                      {'field': 'teacher', 'label': '教师', 'visible': 'false'},
                      {'field': 'teacher', 'label': '教师', 'visible': True, 'sql': 'SELECT 1'}):
            data = proposal(); data['operations'][0]['value'] = value
            with self.assertRaises(ValidationError):
                CourseProposal.model_validate(data, strict=True)

    def test_duplicate_operations_and_invalid_column_set(self):
        cases = [[{'operation': 'reorder_columns', 'fields': ['name']}],
                 [proposal()['operations'][0]] * 2]
        for operations in cases:
            data = proposal(); data['operations'] = operations
            with self.assertRaises(APIError):
                evaluate(CourseProposal.model_validate(data, strict=True), baseline())

    def test_noop_and_clarification_cannot_be_accepted(self):
        data = proposal()
        data['operations'][0]['value']['label'] = '地点'
        self.assertFalse(evaluate(CourseProposal.model_validate(data, strict=True), baseline())['can_accept'])
        data['status'] = 'needs_clarification'; data['operations'] = []
        with self.assertRaises(APIError):
            evaluate(CourseProposal.model_validate(data, strict=True), baseline())

    def test_business_validation_all_hidden_and_rule_regression(self):
        data = proposal()
        data['operations'] = [{'operation': 'set_column', 'value': {**column, 'visible': False}} for column in baseline()['content']['columns']]
        with self.assertRaises(APIError):
            evaluate(CourseProposal.model_validate(data, strict=True), baseline())
        data['operations'] = [{'operation': 'replace_rules', 'rules': [{'id': 'wrong-date', 'terms': ['明天课表'], 'date_mode': 'today'}]}]
        self.assertFalse(evaluate(CourseProposal.model_validate(data, strict=True), baseline())['can_accept'])
        data['operations'][0]['rules'][0]['terms'] = ['不要查今天课表']
        with self.assertRaises(ValidationError):
            CourseProposal.model_validate(data, strict=True)

    def test_http_auth_duplicate_json_and_payload_budget(self):
        client = TestClient(app)
        path = '/api/v1/admin/course-display/designer/preview'
        with patch.dict(os.environ, {'LOCAL_ADMIN_TOKEN': 'a' * 24}):
            headers = {'Authorization': 'Bearer ' + 'a' * 24, 'Content-Type': 'application/json'}
            self.assertEqual(client.get('/api/v1/admin/course-display/designer/context').status_code, 401)
            self.assertEqual(client.post(path, headers=student_headers(self), json=proposal()).status_code, 403)
            self.assertEqual(client.post(path, headers=headers, content='{"status":"proposed","status":"proposed"}').status_code, 422)
            self.assertEqual(client.post(path, headers=headers, content='{"x":NaN}').status_code, 422)
            self.assertEqual(client.post(path, headers=headers, content=' ' * (128 * 1024 + 1)).status_code, 413)
            invalid = proposal(); invalid['operations'][0]['value']['visible'] = 'not-a-boolean'
            failed = client.post(path, headers=headers, json=invalid)
            self.assertEqual(failed.status_code, 422)
            self.assertIn('/operations/0/value/visible', [item['path'] for item in failed.json()['error']['details']])
            self.assertNotIn('not-a-boolean', failed.text)
            with patch('app.modules.config.routes.get_store') as get_store:
                get_store.return_value.draft.return_value = baseline()
                result = client.post(path, headers=headers, json=proposal())
                self.assertEqual(result.status_code, 200)
                self.assertTrue(result.json()['can_accept'])
                get_store.return_value.save.assert_not_called()


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), '需要实库隔离 Schema')
class CourseDesignStoreTests(unittest.TestCase):
    setUp = config_tests.PostgresConfigTests.setUp
    cleanup_schema = config_tests.PostgresConfigTests.cleanup_schema

    def command(self):
        return AcceptProposal(proposal=CourseProposal.model_validate(proposal(self.store.draft()), strict=True),
                              idempotency_key='accept-course-design-001')

    def test_acceptance_is_draft_only_and_replays_without_revision_increment(self):
        initial = self.store.current()
        command = self.command()
        first = self.store.accept_course_proposal(command, 'proposal-trace')
        self.assertEqual(first['revision'], 1)
        self.assertEqual(self.store.current(), initial)
        self.assertEqual(self.store.draft()['content']['columns'][4]['label'], '上课地点')
        self.assertTrue(self.store.accept_course_proposal(command, 'retry-trace')['replayed'])
        self.assertEqual(self.store.draft()['revision'], 1)
        self.assertEqual(len(self.store.audit_history()), 1)
        changed = command.model_copy(deep=True); changed.proposal.summary = '不同请求'
        with self.assertRaises(Conflict):
            self.store.accept_course_proposal(changed, 'changed-request')

    def test_stale_acceptance_never_overwrites_and_audit_failure_rolls_back(self):
        command = self.command()
        with patch.object(self.store, '_audit', side_effect=RuntimeError('simulated audit failure')):
            with self.assertRaises(RuntimeError):
                self.store.accept_course_proposal(command, 'failure')
        self.assertEqual(self.store.draft()['revision'], 0)
        self.store.save(SaveDraft(expected_revision=0, content=default_config()), 'manual-save')
        with self.assertRaises(Conflict):
            self.store.accept_course_proposal(command, 'stale')
        self.assertEqual(self.store.draft()['revision'], 1)

    def test_concurrent_identical_acceptance_commits_once(self):
        command = self.command()
        barrier = Barrier(2)
        def accept():
            barrier.wait()
            return ConfigStore(self.engine).accept_course_proposal(command, 'concurrent')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: accept(), range(2)))
        self.assertEqual(sorted(item['replayed'] for item in results), [False, True])
        self.assertEqual(self.store.draft()['revision'], 1)
        self.assertEqual(len(self.store.audit_history()), 1)

    def test_actor_and_tenant_cannot_replay_another_acceptance(self):
        command = self.command()
        self.store.accept_course_proposal(command, 'first')
        with self.assertRaises(Conflict):
            ConfigStore(self.engine, actor_id='another-admin').accept_course_proposal(command, 'other-actor')
        from app.modules.config.store import NotFound
        with self.assertRaises(NotFound):
            ConfigStore(self.engine, tenant_id='uninitialized-tenant').accept_course_proposal(command, 'other-tenant')

    def test_http_accept_and_reload_use_current_draft_after_later_edits(self):
        client = TestClient(app)
        with patch.dict(os.environ, {'LOCAL_ADMIN_TOKEN': 'a' * 24}), patch('app.modules.config.routes.get_store', return_value=self.store):
            headers = {'Authorization': 'Bearer ' + 'a' * 24}
            received = client.get('/api/v1/admin/course-display/designer/context', headers=headers)
            self.assertEqual(received.status_code, 200)
            body = {'proposal': received.json()['example_proposal'], 'idempotency_key': 'http-accept-proposal-001'}
            result = client.post('/api/v1/admin/course-display/designer/accept', headers=headers, json=body)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()['revision'], 1)
            self.store.save(SaveDraft(expected_revision=1, content=default_config()), 'later-edit')
            replay = client.post('/api/v1/admin/course-display/designer/accept', headers=headers, json=body)
            self.assertTrue(replay.json()['replayed'])
            self.assertEqual(replay.json()['revision'], 1)
            self.assertEqual(self.store.draft()['revision'], 2)
            self.assertEqual(self.store.draft()['content'], default_config().model_dump())
