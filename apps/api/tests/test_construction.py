import json
import os
import unittest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.modules.config.store import ConfigStore, Conflict, LEGACY_APPLICATION_ID
from app.modules.intent.requirements import RequirementStore, SaveRequirement, PrepareCreation
from app.modules.intent.construction_ai import GenerateConstruction, document


class ConstructionValidationTests(unittest.TestCase):
    def test_document_limits(self):
        command = GenerateConstruction(expected_revision=1, text='登记', idempotency_key='test-construction-key')
        for name, text in [('x.pdf', 'text'), ('../x.txt', 'text'), ('x.txt', '\x00'), ('x.md', 'a\n' * 501), ('x.txt', '中' * 30000)]:
            with self.subTest(name=name, length=len(text)), self.assertRaises(Exception):
                document(command.model_copy(update={'document_name': name, 'document_text': text}))
        self.assertEqual(document(command.model_copy(update={'document_name': '说明.md', 'document_text': '标题\n登记姓名'}))['lines'], 2)
        text = '\n  课程标题\n'
        exact = GenerateConstruction(expected_revision=1, text='设计字段', idempotency_key='test-construction-key', document_name='来源.md', document_text=text)
        self.assertEqual(exact.document_text, text)

    def test_blank_input_and_unpersistable_model_text_rejected(self):
        from app.modules.intent.construction_ai import Proposal
        with self.assertRaises(ValueError):
            GenerateConstruction(expected_revision=1, text='  ', idempotency_key='test-construction-key')
        for content in [{'summary': '\x00'}, {'questions': ['\x00']}, {'content': {'application': {'name': '\x00'}}}]:
            with self.subTest(content=content), self.assertRaises(ValueError):
                Proposal.model_validate({'status': 'proposed', 'binding': '0' * 64, 'summary': 'valid', 'questions': [], 'content': {}, 'evidence': [], **content})


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Requires isolated schema')
class ConstructionDatabaseTests(unittest.TestCase):
    def setUp(self):
        from test_config import PostgresConfigTests
        self.fixture = PostgresConfigTests('test_draft_publish_replay_conflict_and_rollback')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store
        self.requirements = RequirementStore(self.store)
        self.task = self.requirements.save(SaveRequirement(title='新课程应用', description='调整展示列',
            business_kind='course_query', idempotency_key='construction-task-one'), 'trace')['item']

    def prepare(self, **kwargs):
        return self.requirements.prepare(self.task['id'], PrepareCreation(expected_revision=1, name='复制结果',
            template_id={'course_query': 'course-standard', 'registration': 'form-registration', 'leave': 'leave-basic'}[self.task['business_kind']],
            idempotency_key='construction-prepare', **kwargs))

    def test_copy_is_frozen_tenant_scoped_and_has_no_history(self):
        from app.modules.config.schema import SaveDraft
        draft = self.store.draft()['content']; draft['columns'][0]['label'] = '复制字段'
        draft['application']['visible_roles'] = ['teacher']
        self.store.save(SaveDraft(expected_revision=0, content=draft), 'trace')
        prepared = self.prepare(source_application_id=LEGACY_APPLICATION_ID)
        created = self.requirements.confirm(prepared['command_id'], 'trace')
        target = ConfigStore(self.fixture.engine, application_id=created['application_id'])
        self.assertEqual(target.draft()['content']['columns'][0]['label'], '复制字段')
        self.assertEqual(target.draft()['content']['application']['visible_roles'], ['teacher'])
        self.assertIsNone(target.current()['release_id']); self.assertEqual(target.history(), [])
        self.assertTrue(self.requirements.confirm(prepared['command_id'], 'trace')['replayed'])
        other = RequirementStore(ConfigStore(self.fixture.engine, tenant_id='other-tenant'))
        with self.assertRaises(Exception):
            with self.fixture.engine.begin() as connection:
                other.copy_source(connection, created['application_id'], 'campus.course-query')

    def test_copy_source_changed_invalidates_confirmation(self):
        from app.modules.config.schema import SaveDraft
        prepared = self.prepare(source_application_id=LEGACY_APPLICATION_ID)
        content = self.store.draft()['content']; content['columns'][0]['label'] = '已变化'
        self.store.save(SaveDraft(expected_revision=0, content=content), 'trace')
        with self.assertRaises(Conflict):
            self.requirements.confirm(prepared['command_id'], 'trace')

    def generate(self, responder, extra=None):
        def execute(operation, principal):
            return operation(self.store)
        from app.modules.identity.auth import Principal
        principal = Principal(subject='local-admin', tenant_id='local-demo', roles=('config_admin',), student_id=None)
        from app.modules.config.ai_routes import local_ai_operator
        app.dependency_overrides[local_ai_operator] = lambda: principal
        self.addCleanup(app.dependency_overrides.clear)
        async def complete(*args, **kwargs):
            packet = json.loads(args[2][-1]['content'])
            return json.dumps(responder(packet), ensure_ascii=False), {'total_tokens': 20}
        command = {'expected_revision': 1, 'text': '只更改课程列标题', 'idempotency_key': 'construction-generate-one', **(extra or {})}
        patches = [patch('app.modules.intent.construction_ai.execute', side_effect=execute),
            patch('app.modules.intent.construction_ai.completion', side_effect=complete),
            patch('app.modules.intent.construction_ai.active_model', return_value=({'revision': 1}, object())),
            patch('app.modules.intent.construction_ai.model_settings', return_value={'revision': 1})]
        from contextlib import ExitStack
        with ExitStack() as stack:
            mocks = [stack.enter_context(p) for p in patches]
            client = TestClient(app)
            response = client.post(f'/api/v1/assistant/requirements/{self.task["id"]}/generate', json=command)
            self.assertEqual(response.status_code, 200, response.text)
            again = client.post(f'/api/v1/assistant/requirements/{self.task["id"]}/generate', json=command)
            self.assertEqual(again.status_code, 200, again.text)
            self.assertTrue(again.json()['replayed']); self.assertEqual(mocks[1].call_count, 1)
            return response.json()

    @staticmethod
    def proposal(packet):
        content = packet['defaults']; content['columns'][0]['label'] = 'AI 课程名称'
        return {'binding': packet['binding'], 'status': 'proposed', 'summary': '调整展示', 'content': content, 'questions': [], 'evidence': []}

    def test_generated_proposal_requires_confirmation_then_only_creates_draft(self):
        result = self.generate(self.proposal)
        self.assertEqual(result['generation_status'], 'proposed')
        self.assertIsNone(self.requirements.detail(self.task['id'])['item']['target_application_id'])
        prepared = self.prepare(generation_key='construction-generate-one')
        created = self.requirements.confirm(prepared['command_id'], 'trace')
        target = ConfigStore(self.fixture.engine, application_id=created['application_id'])
        self.assertEqual(target.draft()['content']['columns'][0]['label'], 'AI 课程名称')
        self.assertIsNone(target.current()['release_id'])

    def test_permission_mutation_is_rejected_and_not_retried(self):
        def malicious(packet):
            value = self.proposal(packet); value['content']['application']['visible_roles'] = ['config_admin']; return value
        result = self.generate(malicious)
        self.assertEqual(result['generation_status'], 'failed')
        with self.assertRaises(Conflict):
            self.prepare(generation_key='construction-generate-one')

    def test_document_evidence_is_checked_and_original_not_persisted(self):
        from app.modules.intent.requirements import commands
        import sqlalchemy as sa
        def quoted(packet):
            value = self.proposal(packet); value['evidence'] = [{'line': 2, 'quote': '课程标题'}]; return value
        result = self.generate(quoted, {'document_name': '需求.md', 'document_text': '\n  课程标题\n这是不应保留的完整原文'})
        self.assertEqual(result['generation_status'], 'proposed')
        self.assertEqual(result['evidence'][0]['line'], 2)
        with self.fixture.engine.connect() as connection:
            row = connection.execute(sa.select(commands).where(commands.c.action == 'generate')).mappings().one()
        self.assertNotIn('这是不应保留的完整原文', json.dumps(dict(row), default=str, ensure_ascii=False))

    def test_fabricated_document_citation_rejected(self):
        def invented(packet):
            value = self.proposal(packet); value['evidence'] = [{'line': 1, 'quote': '伪造原文'}]; return value
        result = self.generate(invented, {'document_name': '需求.txt', 'document_text': '课程标题'})
        self.assertEqual(result['generation_status'], 'failed')

    def test_generated_leave_can_publish_apply_and_complete_approval(self):
        from app.modules.config.schema import PublishDraft
        from app.modules.forms.runtime import SubmitForm
        from app.modules.unified_editor.runtime import form_store
        from app.modules.approvals.runtime import Transition
        from app.modules.unified_editor.runtime import record_service
        from app.modules.identity.auth import Principal
        self.task = self.requirements.save(SaveRequirement(title='请假设计', description='按自然日单级审批', business_kind='leave', idempotency_key='construction-leave-task'), 'trace')['item']
        def proposal(packet):
            content = packet['defaults']; content['form']['sections'][0]['title'] = 'AI 申请信息'
            return {'binding': packet['binding'], 'status': 'proposed', 'summary': '单级请假设计', 'content': content, 'questions': [], 'evidence': []}
        self.assertEqual(self.generate(proposal)['generation_status'], 'proposed')
        prepared = self.prepare(generation_key='construction-generate-one')
        created = self.requirements.confirm(prepared['command_id'], 'trace')['application_id']
        target = form_store(self.store, created)
        release = target.publish(PublishDraft(expected_revision=0, expected_release_id=None, idempotency_key='construction-leave-publish'), 'trace')['release_id']
        student = Principal(subject='applicant', tenant_id='local-demo', roles=['student'])
        applicant = record_service(ConfigStore(self.fixture.engine, student.tenant_id, student.subject), student, created)
        record = applicant.submit(SubmitForm(release_id=release, values={'start_date': '2026-10-20', 'end_date': '2026-10-21', 'reason': 'Synthetic leave'}, idempotency_key='construction-leave-apply'), 'trace')['record_id']
        admin = Principal(subject='local-admin', tenant_id='local-demo', roles=['config_admin'])
        record_service(self.store, admin, created).transition(record, Transition(action='approved', expected_revision=1, reason='Synthetic completion', idempotency_key='construction-leave-approve'), 'trace')
        self.assertEqual(applicant.workflow_detail(record)['workflow']['status'], 'approved')

    def test_copy_leave_omits_records_and_rebinds_publisher(self):
        from app.modules.config.application_store import ApplicationStore, CreateApplication
        from app.modules.config.schema import PublishDraft
        from app.modules.unified_editor.runtime import form_store
        from app.modules.config.store import releases
        import sqlalchemy as sa
        original = ApplicationStore(self.store).create(CreateApplication(type_id='platform.leave-approval', type_version='1.0.0', name='Original leave', template_id='leave-basic', idempotency_key='construction-copy-source'), 'trace')['application_id']
        source = form_store(ConfigStore(self.fixture.engine, actor_id='source-publisher'), original)
        source_release = source.publish(PublishDraft(expected_revision=0, expected_release_id=None, idempotency_key='construction-source-publish'), 'trace')['release_id']
        self.task = self.requirements.save(SaveRequirement(title='复制请假', description='复制设计', business_kind='leave', idempotency_key='construction-copy-task'), 'trace')['item']
        prepared = self.prepare(source_application_id=original)
        created = self.requirements.confirm(prepared['command_id'], 'trace')['application_id']
        target = form_store(self.store, created)
        self.assertEqual(target.history(), [])
        new_release = target.publish(PublishDraft(expected_revision=0, expected_release_id=None, idempotency_key='construction-target-publish'), 'trace')['release_id']
        with self.fixture.engine.connect() as connection:
            bindings = {row['id']: row['runtime_binding'] for row in connection.execute(sa.select(releases.c.id, releases.c.runtime_binding).where(releases.c.id.in_([source_release, new_release]))).mappings()}
        self.assertEqual(bindings[source_release]['approver_id'], 'source-publisher')
        self.assertEqual(bindings[new_release]['approver_id'], 'local-admin')
