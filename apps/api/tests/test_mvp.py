import asyncio
from datetime import date
import os
import unittest
import importlib.util
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from support import student_headers

from app.main import app
from app.errors import APIError
from app.modules.config.schema import DisplayConfig, IntentRule, default_config
from app.modules.config.review import review
from app.modules.identity.auth import Principal, current_user, require_schedule
from app.modules.intent.router import resolve
from app.modules.skills.schedule import HttpScheduleProvider


class PublishedRuleTests(unittest.TestCase):
    def test_exact_keywords_priority_conflict_and_date_guard(self):
        today = date(2026, 10, 18)
        exact = IntentRule(id='alias', terms=['明日课程'], date_mode='tomorrow')
        self.assertEqual(resolve('明日课程', today, [exact]).start_date, date(2026, 10, 19))
        keywords = IntentRule(id='keywords', terms=['明日', '课程'], date_mode='tomorrow', match_type='keywords')
        self.assertEqual(resolve('帮我查询明日的课程', today, [keywords]).status, 'resolved')
        self.assertNotEqual(resolve('明日课程并删除', today, [keywords]).status, 'resolved')
        conflict = IntentRule(id='conflict', terms=['明日课程'], date_mode='today')
        self.assertEqual(resolve('明日课程', today, [exact, conflict]).status, 'clarification')
        conflict.priority = 101
        self.assertEqual(resolve('明日课程', today, [exact, conflict]).start_date, today)
        conflict.terms = ['明天课表']
        self.assertEqual(resolve('明天课表', today, [conflict]).status, 'clarification')

    def test_preview_regression_blocks_reinterpreting_builtin_dates(self):
        content = default_config()
        content.rules = [IntentRule(id='bad-date', terms=['明天课表'], date_mode='today')]
        self.assertFalse(review(default_config(), content)['can_publish'])
        with self.assertRaises(ValueError):
            IntentRule(id='unsafe', terms=['不要查询明天课表'])

    def test_synthetic_evaluation(self):
        from app.evaluation import evaluate
        result = evaluate()
        self.assertGreaterEqual(result['total'], 100)
        self.assertEqual(result['failures'], [])
        self.assertEqual(result['llm_calls'], 0)


class AuthBoundaryTests(unittest.TestCase):
    def test_unlinked_identity_and_role_limits(self):
        from starlette.requests import Request
        request = Request({'type': 'http', 'headers': []})
        for principal, code in [
            (Principal(subject='student', tenant_id='campus', roles=['student']), 'IDENTITY_NOT_LINKED'),
            (Principal(subject='operator', tenant_id='campus', roles=['platform_ops'], student_id='x'), 'SCHEDULE_FORBIDDEN'),
        ]:
            with patch('app.modules.identity.auth.current_user', return_value=principal), self.assertRaises(APIError) as error:
                require_schedule(request)
            self.assertEqual(error.exception.code, code)

    def test_rate_limit_is_scoped_to_trusted_identity(self):
        from collections import OrderedDict
        from app.rate_limit import check
        user = Principal(subject='student-a', tenant_id='campus-a', roles=['student'])
        other = Principal(subject='student-a', tenant_id='campus-b', roles=['student'])
        with patch('app.rate_limit._windows', OrderedDict()):
            for _ in range(120):
                check(user)
            with self.assertRaises(APIError) as error:
                check(user)
            self.assertEqual(error.exception.status, 429)
            check(other)

    @unittest.skipUnless(importlib.util.find_spec('jwt'), '需要 PyJWT[crypto] 才能运行真实签名验证')
    def test_real_signed_tokens_and_expiry(self):
        import jwt
        import time
        from cryptography.hazmat.primitives.asymmetric import rsa
        from types import SimpleNamespace
        from app.modules.identity.auth import verify_oidc
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        options = {'issuer': 'https://sso.example.edu', 'audience': 'campus-api',
                   'jwks_url': 'https://sso.example.edu/jwks'}
        claims = {'sub': 'student-a', 'tenant_id': 'campus-a', 'student_id': 'a', 'roles': ['student'],
                  'iss': options['issuer'], 'aud': options['audience'], 'exp': int(time.time()) + 60}
        with patch('app.modules.identity.auth.jwks_client') as keys:
            keys.return_value.get_signing_key_from_jwt.return_value = SimpleNamespace(key=private.public_key())
            token = jwt.encode(claims, private, algorithm='RS256')
            self.assertEqual(verify_oidc(token, options).student_id, 'a')
            for changes in ({'exp': 1}, {'aud': 'wrong'}, {'iss': 'https://wrong.example.edu'}, {'tenant_id': None}):
                with self.subTest(changes=changes), self.assertRaises(APIError):
                    verify_oidc(jwt.encode({**claims, **changes}, private, algorithm='RS256'), options)

    def test_oidc_never_accepts_local_token_or_falls_back(self):
        with patch('app.modules.identity.auth.auth_options', return_value={'mode': 'oidc'}):
            response = TestClient(app).get('/api/v1/course-schedule?start_date=2026-10-19&end_date=2026-10-19')
            self.assertEqual(response.status_code, 401)
        principal = Principal(subject='student-a', tenant_id='campus-a', roles=['student'], student_id='a', mode='oidc')
        with patch('app.modules.identity.auth.auth_options', return_value={'mode': 'oidc'}), patch(
            'app.modules.identity.auth.verify_oidc', return_value=principal), patch(
            'app.main.published_config', return_value={'release_id': 'one', 'content': default_config().model_dump()}) as config:
            response = TestClient(app).get('/api/v1/course-schedule?start_date=2026-10-19&end_date=2026-10-19',
                                            headers={'Authorization': 'Bearer signed-test-token'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(config.call_args.args[0].tenant_id, 'campus-a')
            self.assertIn('Server-Timing', response.headers)
        with patch('app.modules.identity.auth.auth_options', return_value={'mode': 'oidc'}), patch(
            'app.modules.identity.auth.verify_oidc', return_value=principal):
            response = TestClient(app).get('/api/v1/admin/course-display', headers={'Authorization': 'Bearer signed-test-token'})
            self.assertEqual(response.status_code, 403)

    def test_no_client_identity_override(self):
        with patch.dict(os.environ, {'DATABASE_URL': ''}):
            response = TestClient(app).get('/api/v1/course-schedule?start_date=2026-10-19&end_date=2026-10-19&student_id=victim', headers=student_headers(self))
            self.assertEqual(response.status_code, 422)


class UpstreamTests(unittest.IsolatedAsyncioTestCase):
    async def provider(self, handler):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        return HttpScheduleProvider({'url': 'https://sis.example.edu/schedule', 'token': 'server-secret'}, client)

    async def test_empty_result_is_success(self):
        provider = await self.provider(lambda request: httpx.Response(200, json={
            'tenant_id': 'campus-a', 'student_id': 'student-a', 'courses': []}))
        self.assertEqual(await provider.get_schedule('student-a', date(2026, 10, 19),
            date(2026, 10, 19), tenant_id='campus-a'), [])

    async def test_wrong_student_not_returned(self):
        provider = await self.provider(lambda request: httpx.Response(200, json={
            'tenant_id': 'campus-a', 'student_id': 'student-b', 'courses': []}))
        with self.assertRaises(APIError) as error:
            await provider.get_schedule('student-a', date(2026, 10, 19), date(2026, 10, 19), tenant_id='campus-a')
        self.assertEqual(error.exception.code, 'SIS_UNAVAILABLE')

    async def test_timeout_circuit_and_unlinked(self):
        calls = []
        def timeout(request):
            calls.append(1)
            raise httpx.ReadTimeout('simulated')
        provider = await self.provider(timeout)
        for _ in range(4):
            with self.assertRaises(APIError):
                await provider.get_schedule('student-a', date(2026, 10, 19), date(2026, 10, 19))
        self.assertEqual(len(calls), 3)
        provider = await self.provider(lambda request: httpx.Response(404))
        with self.assertRaises(APIError) as error:
            await provider.get_schedule('student-a', date(2026, 10, 19), date(2026, 10, 19))
        self.assertEqual(error.exception.code, 'IDENTITY_NOT_LINKED')
