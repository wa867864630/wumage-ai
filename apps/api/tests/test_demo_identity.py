import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.main import app
from app.modules.identity import demo


class DemoIdentityTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.options = patch('app.modules.identity.auth.auth_options', return_value={'mode': 'demo'})
        self.demo_options = patch('app.modules.identity.demo.auth_options', return_value={'mode': 'demo'})
        self.options.start(); self.demo_options.start()
        self.addCleanup(self.options.stop); self.addCleanup(self.demo_options.stop)
        self.addCleanup(demo._sessions.clear)

    def login(self):
        with patch.object(demo, 'available_students', return_value=[{
            'id': 'mock-student-002', 'student_no': 'MOCK2026002', 'name': '模拟学生二'}]):
            return self.client.post('/api/v1/auth/demo/login', json={'student_id': 'mock-student-002'})

    def test_session_is_scoped_and_logout_revokes(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        headers = {'Authorization': 'Bearer ' + response.json()['access_token']}
        me = self.client.get('/api/v1/me', headers=headers).json()
        self.assertEqual(me['student_id'], 'mock-student-002')
        self.assertEqual(me['tenant_id'], 'local-demo')
        self.assertEqual(me['roles'], ['student'])
        with patch('app.settings.admin_token', return_value='test-admin-token-at-least-24-characters'):
            self.assertEqual(self.client.get('/api/v1/admin/course-display', headers=headers).status_code, 403)
        self.assertEqual(self.client.post('/api/v1/auth/demo/logout', headers=headers).status_code, 200)
        self.assertEqual(self.client.get('/api/v1/me', headers=headers).status_code, 401)

    def test_expired_and_forged_session_rejected(self):
        token = self.login().json()['access_token']
        with patch.object(demo.time, 'monotonic', return_value=10**15):
            self.assertEqual(self.client.get('/api/v1/me', headers={'Authorization': 'Bearer ' + token}).status_code, 401)
        self.assertEqual(self.client.get('/api/v1/me', headers={'Authorization': 'Bearer demo_forged'}).status_code, 401)

    def test_cannot_supply_roles_tenant_or_unlisted_identity(self):
        with patch.object(demo, 'available_students', return_value=[]):
            self.assertEqual(self.client.post('/api/v1/auth/demo/login', json={'student_id': 'local-admin'}).status_code, 403)
            self.assertEqual(self.client.post('/api/v1/auth/demo/login', json={'student_id': 'x', 'roles': ['config_admin']}).status_code, 422)

    def test_oidc_disables_all_demo_endpoints(self):
        with patch.object(demo, 'auth_options', return_value={'mode': 'oidc'}):
            self.assertEqual(self.client.get('/api/v1/auth/demo/students').status_code, 404)
            self.assertEqual(self.client.post('/api/v1/auth/demo/login', json={'student_id': 'local-demo-student'}).status_code, 404)
            self.assertEqual(self.client.post('/api/v1/auth/demo/logout').status_code, 404)

    def test_anonymous_requests_never_reach_configuration_or_schedule(self):
        with patch('app.main.published_config') as config, patch('app.main.fetch_schedule') as fetch:
            for method, path, kwargs in [
                ('get', '/api/v1/me', {}),
                ('get', '/api/v1/config/course-display', {}),
                ('get', '/api/v1/course-schedule?start_date=2026-10-19&end_date=2026-10-19', {}),
                ('post', '/api/v1/course-schedule/query', {'json': {'text': '明天课表'}}),
            ]:
                response = getattr(self.client, method)(path, **kwargs)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()['error']['code'], 'AUTH_REQUIRED')
            config.assert_not_called(); fetch.assert_not_called()
        self.assertEqual(self.client.get('/api/health').status_code, 200)

    def test_logout_and_expiry_block_both_query_routes(self):
        for expire in (False, True):
            token = self.login().json()['access_token']
            headers = {'Authorization': 'Bearer ' + token}
            if expire:
                with demo._lock:
                    demo._sessions[token] = (0, demo._sessions[token][1])
            else:
                self.client.post('/api/v1/auth/demo/logout', headers=headers)
            with patch('app.main.published_config') as config:
                self.assertEqual(self.client.get('/api/v1/course-schedule?start_date=2026-10-19&end_date=2026-10-19', headers=headers).status_code, 401)
                self.assertEqual(self.client.post('/api/v1/course-schedule/query', headers=headers, json={'text': '明天课表'}).status_code, 401)
                config.assert_not_called()

    def test_invalid_authorization_is_not_anonymous(self):
        for value in ('Bearer ', 'Basic abc', 'Bearer demo_forged', 'Bearer ' + 'x' * 16385):
            self.assertEqual(self.client.get('/api/v1/me', headers={'Authorization': value}).status_code, 401)

    def test_admin_token_with_demo_prefix_still_verifies_exactly(self):
        token = 'demo_' + 'a' * 32
        with patch('app.modules.identity.auth.admin_token', return_value=token):
            response = self.client.get('/api/v1/me', headers={'Authorization': 'Bearer ' + token})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['roles'], ['config_admin'])
            self.assertEqual(self.client.get('/api/v1/me', headers={'Authorization': 'Bearer ' + token + 'x'}).status_code, 401)
