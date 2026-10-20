import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.errors import APIError
from app.main import app
from app.modules.config import ai_settings, ai_model
from app.modules.config.ai_settings import ModelOptions, SaveModel, model_settings, save_model, active_model
from app.modules.config.course_design import context
from app.modules.config.schema import default_config
from support import student_headers


class AISettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        for name, filename in [('SETTINGS_PATH', 'model.secret'), ('KEY_PATH', 'key.secret')]:
            patcher = patch.object(ai_settings, name, root / filename)
            patcher.start(); self.addCleanup(patcher.stop)

    def save(self, **changes):
        values = dict(expected_revision=model_settings()['revision'], enabled=True, base_url='https://model.example/v1',
                      model='test-model', api_key='synthetic-secret-not-a-real-key')
        return save_model(SaveModel(**(values | changes)))

    def test_secret_encrypted_not_returned_and_blank_preserves(self):
        result = self.save()
        self.assertTrue(result['api_key_configured'])
        self.assertNotIn('synthetic-secret', json.dumps(result))
        self.assertNotIn('synthetic-secret', ai_settings.SETTINGS_PATH.read_text())
        self.save(api_key='')
        self.assertEqual(active_model()[1].get_secret_value(), 'synthetic-secret-not-a-real-key')
        self.assertEqual(model_settings()['revision'], 2)

    def test_conflict_and_changed_endpoint_never_reuse_credentials(self):
        self.save()
        with self.assertRaises(APIError):
            self.save(expected_revision=0)
        with self.assertRaises(APIError):
            self.save(base_url='https://another.example/v1', api_key='')
        self.assertEqual(model_settings()['revision'], 1)
        result = self.save(base_url='https://another.example/v1', enabled=False, api_key='')
        self.assertFalse(result['api_key_configured'])

    def test_corrupt_settings_fail_closed_without_unhandled_errors(self):
        self.save()
        valid = json.loads(ai_settings.SETTINGS_PATH.read_text())
        for data in (None, [], 1, valid | {'revision': -1}, valid | {'revision': True}):
            with self.subTest(data_type=type(data).__name__):
                ai_settings.SETTINGS_PATH.write_text(json.dumps(data), encoding='utf-8')
                with self.assertRaises(APIError) as error:
                    model_settings()
                self.assertEqual(error.exception.code, 'AI_SETTINGS_UNAVAILABLE')

    def test_loaded_settings_use_validated_normalized_endpoint(self):
        self.save()
        data = json.loads(ai_settings.SETTINGS_PATH.read_text())
        data['base_url'] = ' https://model.example/v1/ '
        ai_settings.SETTINGS_PATH.write_text(json.dumps(data), encoding='utf-8')
        self.assertEqual(model_settings()['base_url'], 'https://model.example/v1')

    def test_clear_disable_and_url_validation(self):
        self.save()
        result = self.save(enabled=False, api_key='', clear_api_key=True)
        self.assertFalse(result['api_key_configured'])
        with self.assertRaises(APIError):
            active_model()
        for url in ('http://remote.example/v1', 'https://user:password@model.example/v1',
                    'https://model.example/v1?key=secret', 'https://model.example/v1/chat/completions'):
            with self.assertRaises(ValidationError):
                ModelOptions(base_url=url)

    def test_model_settings_only_local_operator_and_no_secret_in_http(self):
        client = TestClient(app, client=('127.0.0.1', 50000), base_url='http://127.0.0.1')
        with patch.dict(os.environ, {'LOCAL_ADMIN_TOKEN': 'a' * 24}):
            headers = {'Authorization': 'Bearer ' + 'a' * 24}
            self.assertEqual(client.get('/api/v1/admin/ai-model').status_code, 401)
            self.assertEqual(client.get('/api/v1/admin/ai-model', headers=student_headers(self)).status_code, 403)
            remote = TestClient(app, client=('192.0.2.1', 1), base_url='http://127.0.0.1')
            self.assertEqual(remote.get('/api/v1/admin/ai-model', headers=headers).status_code, 403)
            self.save()
            result = client.get('/api/v1/admin/ai-model', headers=headers)
            self.assertEqual(result.status_code, 200)
            self.assertNotIn('key_cipher', result.text)
            self.assertNotIn('synthetic-secret', result.text)
            settings = result.json()
            settings.pop('api_key_configured')
            settings['expected_revision'] = settings.pop('revision')
            settings['api_key'] = ''
            saved = client.put('/api/v1/admin/ai-model', headers=headers, json=settings)
            self.assertEqual(saved.status_code, 200)
            self.assertTrue(saved.json()['api_key_configured'])


class AITransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ai_model._calls.clear(); ai_model._active.clear()
        self.options = ModelOptions(enabled=True, base_url='https://model.example/v1', model='test').model_dump()

    async def test_request_json_mode_usage_and_auth(self):
        def handler(request):
            self.assertEqual(request.url.path, '/v1/chat/completions')
            self.assertEqual(request.headers['Authorization'], 'Bearer fake-key')
            body = json.loads(request.content)
            self.assertEqual(body['response_format'], {'type': 'json_object'})
            self.assertEqual(body['max_tokens'], 4096)
            self.assertNotIn('tools', body)
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok":true}'}}],
                                              'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'unexpected': 'do-not-return'}})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch.object(ai_model.httpx, 'AsyncClient', return_value=client):
            content, usage = await ai_model.completion(self.options, SecretStr('fake-key'), [], 'operator')
        self.assertEqual(json.loads(content), {'ok': True})
        self.assertEqual(usage, {'prompt_tokens': 10, 'completion_tokens': 5})
        self.assertFalse(ai_model._active)

    async def test_provider_error_never_echoes_body_and_budget_counts_attempt(self):
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(401, text='leaked fake-key')))
        with patch.object(ai_model.httpx, 'AsyncClient', return_value=client), self.assertRaises(APIError) as error:
            await ai_model.completion(self.options | {'calls_per_hour': 1}, SecretStr('fake-key'), [], 'operator')
        self.assertNotIn('fake-key', error.exception.message)
        with self.assertRaises(APIError) as budget:
            await ai_model.completion(self.options | {'calls_per_hour': 1}, SecretStr('fake-key'), [], 'operator')
        self.assertEqual(budget.exception.code, 'AI_BUDGET_EXCEEDED')

    async def test_timeout_releases_slot(self):
        def handler(request):
            raise httpx.ReadTimeout('private endpoint and key must not be returned', request=request)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch.object(ai_model.httpx, 'AsyncClient', return_value=client), self.assertRaises(APIError) as error:
            await ai_model.completion(self.options, SecretStr('fake'), [], 'operator')
        self.assertEqual(error.exception.code, 'AI_TIMEOUT')
        self.assertFalse(ai_model._active)

    async def test_malformed_provider_shapes_are_sanitized_and_release_slot(self):
        good_choice = {'finish_reason': 'stop', 'message': {'content': '{}'}}
        for payload in (None, [], {'choices': []}, {'choices': [[]]},
                        {'choices': [{'message': []}]}, {'choices': [good_choice], 'usage': [1]}):
            with self.subTest(payload=payload):
                client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=json.dumps(payload))))
                with patch.object(ai_model.httpx, 'AsyncClient', return_value=client), self.assertRaises(APIError) as error:
                    await ai_model.completion(self.options, SecretStr('fake-key'), [], 'operator')
                self.assertEqual(error.exception.code, 'AI_RESPONSE_INVALID')
                self.assertNotIn('fake-key', error.exception.message)
                self.assertFalse(ai_model._active)


class AIGenerationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, client=('127.0.0.1', 50000), base_url='http://127.0.0.1')
        self.draft = {'revision': 0, 'content': default_config().model_dump(), 'current_release_id': None}
        self.packet = context(self.draft)
        self.store = MagicMock()
        self.store.draft.return_value = self.draft
        self.options = ModelOptions(enabled=True, base_url='https://model.example/v1', model='test').model_dump() | {'revision': 1}
        for target, values in [
            ('app.modules.config.routes.get_store', {'return_value': self.store}),
            ('app.modules.config.ai_routes.active_model', {'return_value': (self.options, SecretStr('fake-key'))}),
            ('app.modules.config.ai_routes.model_settings', {'return_value': {'revision': 1}}),
            ('app.modules.config.ai_routes.context', {'return_value': self.packet})]:
            patcher = patch(target, **values); patcher.start(); self.addCleanup(patcher.stop)
        token = patch.dict(os.environ, {'LOCAL_ADMIN_TOKEN': 'a' * 24}); token.start(); self.addCleanup(token.stop)
        self.headers = {'Authorization': 'Bearer ' + 'a' * 24}

    def generate(self):
        return self.client.post('/api/v1/admin/course-display/designer/generate', headers=self.headers,
                                json={'text': 'adjust location column', 'expected_revision': 0})

    def test_generated_proposal_is_validated_previewed_not_saved(self):
        model = AsyncMock(return_value=(json.dumps(self.packet['example_proposal']), {'total_tokens': 50}))
        with patch('app.modules.config.ai_routes.completion', model):
            response = self.generate()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['preview']['can_accept'])
        self.store.save.assert_not_called()
        self.store.accept_course_proposal.assert_not_called()
        self.store._audit.assert_called_once()
        self.assertNotIn('fake-key', response.text)

    def test_blank_request_rejected_before_model_call(self):
        with patch('app.modules.config.ai_routes.completion', AsyncMock()) as model:
            response = self.client.post('/api/v1/admin/course-display/designer/generate', headers=self.headers,
                                        json={'text': ' \n\t ', 'expected_revision': 0})
        self.assertEqual(response.status_code, 422)
        model.assert_not_called()

    def test_changed_bindings_and_invalid_model_output_rejected(self):
        for content in ('not json', json.dumps(self.packet['example_proposal'] | {'base_revision': 1})):
            with patch('app.modules.config.ai_routes.completion', AsyncMock(return_value=(content, {}))):
                self.assertEqual(self.generate().status_code, 502)

    def test_stale_before_call_and_config_changed_after_call(self):
        self.store.draft.return_value = self.draft | {'revision': 1}
        with patch('app.modules.config.ai_routes.completion', AsyncMock()) as model:
            self.assertEqual(self.generate().status_code, 409)
            model.assert_not_called()
        self.store.draft.return_value = self.draft
        with patch('app.modules.config.ai_routes.completion', AsyncMock(return_value=(json.dumps(self.packet['example_proposal']), {}))), patch(
            'app.modules.config.ai_routes.model_settings', return_value={'revision': 2}):
            self.assertEqual(self.generate().status_code, 409)
