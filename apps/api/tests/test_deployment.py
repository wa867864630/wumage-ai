import base64
import hashlib
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from app.main import app
from app.errors import APIError
from app.modules.deployment.routes import Connection, PlanRequest, inspect_server, read_command
from app.modules.deployment.plans import build_plan
from support import student_headers


OPTIONS = {'enabled': True, 'operator_subjects': ['local-admin'], 'allowed_hosts': ['192.0.2.10']}


class DeploymentTests(unittest.TestCase):
    def test_disabled_and_separately_authorized_and_local_only(self):
        headers = {'Authorization': 'Bearer ' + 'a' * 24}
        with patch('app.modules.identity.auth.admin_token', return_value='a' * 24), patch('app.settings.admin_token', return_value='a' * 24), patch(
            'app.modules.deployment.routes.read_local_config', return_value={}), patch('app.modules.identity.auth.auth_options', return_value={'mode': 'demo'}):
            client = TestClient(app, client=('127.0.0.1', 50000), base_url='http://127.0.0.1')
            self.assertEqual(client.get('/api/v1/admin/deployment/capabilities', headers=headers).status_code, 403)
            with patch('app.modules.deployment.routes.read_local_config', return_value={'deployment': {**OPTIONS, 'operator_subjects': []}}):
                self.assertEqual(client.get('/api/v1/admin/deployment/capabilities', headers=headers).status_code, 403)
            with patch('app.modules.deployment.routes.read_local_config', return_value={'deployment': OPTIONS}):
                self.assertEqual(client.get('/api/v1/admin/deployment/capabilities', headers=headers).status_code, 200)
                self.assertEqual(client.get('/api/v1/admin/deployment/capabilities', headers=student_headers(self)).status_code, 403)
                remote = TestClient(app, client=('192.0.2.1', 50000), base_url='http://127.0.0.1')
                self.assertEqual(remote.get('/api/v1/admin/deployment/capabilities', headers=headers).status_code, 403)
                self.assertEqual(client.get('/api/v1/admin/deployment/capabilities', headers={**headers, 'Origin': 'https://attacker.invalid'}).status_code, 403)

    def fake_ssh(self):
        transport = MagicMock()
        transport.get_remote_server_key.return_value.asbytes.return_value = b'fake-server-key'
        transport.is_authenticated.return_value = True
        fake = SimpleNamespace(Transport=MagicMock(return_value=transport), AuthenticationException=type('AuthFailure', (Exception,), {}))
        fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(b'fake-server-key').digest()).decode().rstrip('=')
        return transport, fake, fingerprint

    def test_fingerprint_first_never_sends_password(self):
        transport, fake, fingerprint = self.fake_ssh()
        command = Connection(host='192.0.2.10', username='operator', password='never-log-this')
        self.assertNotIn('never-log-this', repr(command))
        with patch.dict(sys.modules, {'paramiko': fake}), patch('app.modules.deployment.routes.socket.create_connection'):
            result = inspect_server(command, OPTIONS)
        self.assertFalse(result['authenticated'])
        self.assertEqual(result['fingerprint'], fingerprint)
        transport.auth_password.assert_not_called()
        transport.open_session.assert_not_called()
        transport.close.assert_called_once()

    def test_changed_host_key_stops_before_authentication(self):
        transport, fake, fingerprint = self.fake_ssh()
        command = Connection(host='192.0.2.10', username='operator', password='secret', fingerprint=fingerprint)
        with patch.dict(sys.modules, {'paramiko': fake}), patch('app.modules.deployment.routes.socket.create_connection'), self.assertRaises(APIError) as caught:
            inspect_server(command, {**OPTIONS, 'host_fingerprints': {'192.0.2.10:22': 'SHA256:' + 'a' * 43}})
        self.assertEqual(caught.exception.code, 'SSH_HOST_KEY_CHANGED')
        transport.auth_password.assert_not_called()

    def test_success_uses_only_fixed_readonly_checks(self):
        transport, fake, fingerprint = self.fake_ssh()
        command = Connection(host='192.0.2.10', username='operator', password='secret', fingerprint=fingerprint)
        with patch.dict(sys.modules, {'paramiko': fake}), patch('app.modules.deployment.routes.socket.create_connection'), patch(
            'app.modules.deployment.routes.read_command', side_effect=[(0, 'ID=ubuntu\nVERSION_ID="24.04"'), (0, ''), (0, ''), (1, ''), (0, '')]) as read:
            result = inspect_server(command, OPTIONS)
        self.assertEqual(result['status'], 'checked')
        self.assertFalse(result['execution_available'])
        self.assertEqual([call.args[1] for call in read.call_args_list],
                         ['cat /etc/os-release', 'command -v python3', 'command -v systemctl', 'command -v nginx', 'command -v sudo'])
        transport.auth_password.assert_called_once_with('operator', 'secret', fallback=False)

    def test_plan_collects_remote_facts_and_binds_them_to_digest(self):
        import json
        from test_deployment_preflight import facts
        from app.modules.deployment.preflight import probe_command
        transport, fake, fingerprint = self.fake_ssh()
        command = PlanRequest(host='192.0.2.10', username='operator', password='secret', fingerprint=fingerprint,
                              operation='update', release_id='release-2', artifact_sha256='a' * 64)
        data = facts()
        with patch.dict(sys.modules, {'paramiko': fake}), patch('app.modules.deployment.routes.socket.create_connection'), patch(
            'app.modules.deployment.routes.read_command', side_effect=[(0, 'ID=ubuntu\nVERSION_ID="24.04"'),
            (0, ''), (0, ''), (0, ''), (0, ''), (0, json.dumps(data))]) as read:
            inspection = inspect_server(command, OPTIONS)
        self.assertEqual(read.call_args_list[-1].args[1], probe_command('release-2'))
        self.assertEqual(inspection['deployment_facts'], data)
        artifact = {'web_build': 'built', 'unpacked_bytes': 100}
        plan = build_plan(command, inspection, artifact)
        self.assertEqual(plan['preflight']['blockers'], [])
        changed_inspection = {**inspection, 'deployment_facts': {**inspection['deployment_facts'], 'release_exists': True}}
        changed = build_plan(command, changed_inspection, artifact)
        self.assertNotEqual(plan['digest'], changed['digest'])
        self.assertIn('RELEASE_EXISTS', [item['code'] for item in changed['preflight']['blockers']])

    def test_target_restrictions_and_no_arbitrary_commands(self):
        for host in ('127.0.0.1', '169.254.169.254', '::1', 'https://server', 'server.example', '192.0.2.10; id'):
            with self.assertRaises(ValueError):
                Connection(host=host, username='operator')
        with self.assertRaises(ValueError):
            Connection(host='192.0.2.10', username='operator', command='anything')
        with patch('app.modules.deployment.routes.socket.create_connection') as network, self.assertRaises(APIError):
            inspect_server(Connection(host='192.0.2.11', username='operator'), OPTIONS)
        network.assert_not_called()

    def test_output_limit_closes_channel(self):
        transport = MagicMock()
        channel = transport.open_session.return_value
        channel.recv_ready.return_value = True
        channel.recv.return_value = b'x' * 8193
        with self.assertRaises(APIError) as caught:
            read_command(transport, 'cat /etc/os-release')
        self.assertEqual(caught.exception.code, 'SSH_OUTPUT_LIMIT')
        channel.close.assert_called_once()

    def test_continuous_output_cannot_bypass_command_deadline(self):
        transport = MagicMock()
        channel = transport.open_session.return_value
        channel.recv_ready.return_value = True
        channel.recv.return_value = b'x'
        with patch('app.modules.deployment.routes.time.monotonic', side_effect=[0, 1, 6]), self.assertRaises(APIError) as caught:
            read_command(transport, 'cat /etc/os-release')
        self.assertEqual(caught.exception.code, 'SSH_TIMEOUT')
        channel.close.assert_called_once()

    def test_plan_binds_verified_target_and_artifact_without_password(self):
        command = PlanRequest(host='192.0.2.10', username='operator', password='never-retain-this',
                              operation='install', release_id='release-1', artifact_sha256='a' * 64)
        inspection = {'status': 'checked', 'authenticated': True, 'fingerprint': 'SHA256:' + 'b' * 43,
                      'checks': [{'name': 'systemd', 'ok': True}, {'name': 'python3', 'ok': True}, {'name': 'nginx', 'ok': False}]}
        plan = build_plan(command, inspection)
        self.assertEqual(plan['target']['fingerprint'], inspection['fingerprint'])
        self.assertEqual(plan['release_directory'], '/opt/campus-ai/releases/release-1')
        self.assertEqual(plan['missing_dependencies'], ['nginx'])
        self.assertFalse(plan['execution_available'])
        self.assertNotIn('never-retain-this', str(plan))
        self.assertEqual(plan['digest'], build_plan(command, inspection)['digest'])
        changed = command.model_copy(update={'artifact_sha256': 'c' * 64})
        self.assertNotEqual(plan['digest'], build_plan(changed, inspection)['digest'])
        changed = command.model_copy(update={'operation': 'update'})
        self.assertNotEqual(plan['digest'], build_plan(changed, inspection)['digest'])

    def test_plan_rejects_unverified_or_unsupported_server_and_unsafe_inputs(self):
        data = dict(host='192.0.2.10', username='operator', operation='update', release_id='release-1', artifact_sha256='a' * 64)
        command = PlanRequest(**data)
        for inspection in ({'status': 'fingerprint_confirmation_required', 'authenticated': False},
                           {'status': 'unsupported_os', 'authenticated': True},
                           {'status': 'checked', 'authenticated': True, 'checks': [{'name': 'systemd', 'ok': False}]}):
            with self.assertRaises(APIError):
                build_plan(command, inspection)
        for changes in ({'release_id': '../current'}, {'release_id': 'v1;id'}, {'artifact_sha256': 'invalid'},
                        {'operation': 'shell'}, {'install_root': '/etc'}, {'command': 'id'}):
            with self.assertRaises(ValueError):
                PlanRequest(**(data | changes))

    def test_plan_endpoint_rechecks_ssh_and_requires_independent_authorization(self):
        headers = {'Authorization': 'Bearer ' + 'a' * 24}
        data = dict(host='192.0.2.10', username='operator', operation='install', release_id='release-1', artifact_sha256='a' * 64)
        inspection = {'status': 'checked', 'authenticated': True, 'fingerprint': 'SHA256:' + 'b' * 43,
                      'checks': [{'name': name, 'ok': True} for name in ('systemd', 'python3', 'nginx')]}
        with patch('app.modules.identity.auth.admin_token', return_value='a' * 24), patch('app.settings.admin_token', return_value='a' * 24), patch(
            'app.modules.deployment.routes.read_local_config', return_value={'deployment': OPTIONS}), patch(
            'app.modules.identity.auth.auth_options', return_value={'mode': 'demo'}), patch(
            'app.modules.deployment.routes.inspect_server', return_value=inspection) as inspect, patch(
            'app.modules.deployment.routes.local_artifact', return_value={'release_id': 'release-1', 'web_build': 'built', 'verified': True}):
            client = TestClient(app, client=('127.0.0.1', 50000), base_url='http://127.0.0.1')
            response = client.post('/api/v1/admin/deployment/plan', headers=headers, json=data)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()['execution_available'])
            inspect.assert_called_once()
            with patch('app.modules.deployment.routes.local_artifact', return_value=None):
                self.assertEqual(client.post('/api/v1/admin/deployment/plan', headers=headers, json=data).status_code, 409)
            with patch('app.modules.deployment.routes.local_artifact', return_value={'release_id': 'other'}):
                self.assertEqual(client.post('/api/v1/admin/deployment/plan', headers=headers, json=data).status_code, 409)
            inspect.assert_called_once()
            with patch('app.modules.deployment.routes.read_local_config', return_value={'deployment': {**OPTIONS, 'operator_subjects': []}}):
                self.assertEqual(client.post('/api/v1/admin/deployment/plan', headers=headers, json=data).status_code, 403)
            inspect.assert_called_once()
