import copy
import io
import json
import shlex
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.errors import APIError
from app.modules.deployment.preflight import PROBE, SERVICES, assess_preflight, parse_probe, probe_command


def facts():
    return {'paths_safe': True, 'permissions_safe': True, 'root_exists': True, 'release_exists': False,
            'current_exists': True, 'current_safe': True, 'root_writable': True,
            'free_bytes': 5 * 1024**3, 'config_private_readable': True, 'python_supported': True, 'venv_available': True,
            'services': {name: {'load': 'loaded', 'active': 'active'} for name in SERVICES},
            'ports_known': True, 'api_port_busy': False, 'http_port_busy': False, 'https_port_busy': False}


class PreflightTests(unittest.TestCase):
    def test_probe_command_contains_only_fixed_program_and_validated_release(self):
        self.assertEqual(shlex.split(probe_command('release-1')), ['python3', '-c', PROBE, 'release-1'])
        for release in ('../current', 'v1;id', 'v1\nwhoami', 'a' * 65):
            with self.assertRaises(ValueError):
                probe_command(release)

    def test_parser_rejects_failed_partial_and_untrusted_output(self):
        expected = facts()
        self.assertEqual(parse_probe(0, json.dumps(expected)), expected)
        wrong_bool = {**expected, 'paths_safe': 'true'}
        extra = {**expected, 'configuration_password': 'must-not-be-returned'}
        wrong_state = copy.deepcopy(expected)
        wrong_state['services'][SERVICES[0]]['load'] = 'arbitrary output'
        for status, output in ((1, json.dumps(expected)), (0, '{}'), (0, 'not json'),
                               (0, json.dumps(wrong_bool)), (0, json.dumps(extra)), (0, json.dumps(wrong_state))):
            with self.assertRaises(APIError) as caught:
                parse_probe(status, output)
            self.assertEqual(caught.exception.code, 'DEPLOYMENT_PROBE_FAILED')
            self.assertNotIn('must-not-be-returned', str(caught.exception))

    def test_update_requires_safe_current_and_reports_all_blockers(self):
        data = facts()
        self.assertEqual(assess_preflight('update', data, {'unpacked_bytes': 100})['blockers'], [])
        for key, value, code in [('paths_safe', False, 'UNSAFE_DIRECTORY'),
                                 ('permissions_safe', False, 'UNSAFE_DIRECTORY'),
                                 ('release_exists', True, 'RELEASE_EXISTS'),
                                 ('free_bytes', 0, 'DISK_SPACE_LOW'),
                                 ('config_private_readable', False, 'CONFIG_NOT_READY'),
                                 ('venv_available', False, 'VENV_UNAVAILABLE'),
                                 ('python_supported', False, 'PYTHON_VERSION_UNSUPPORTED'),
                                 ('root_writable', False, 'DIRECTORY_NOT_WRITABLE'),
                                 ('ports_known', False, 'PORTS_UNKNOWN'),
                                 ('current_safe', False, 'CURRENT_RELEASE_INVALID'),
                                 ('http_port_busy', True, 'WEB_PORT_REVIEW_REQUIRED')]:
            with self.subTest(key=key):
                result = assess_preflight('update', {**data, key: value}, None)
                self.assertIn(code, [item['code'] for item in result['blockers']])
        data['services'][SERVICES[0]]['load'] = 'unknown'
        result = assess_preflight('update', data, None)
        self.assertIn('SERVICES_UNKNOWN', [item['code'] for item in result['blockers']])
        self.assertIn('APPLICATION_SERVICE_MISSING', [item['code'] for item in result['blockers']])

    def test_install_distinguishes_prepared_directory_from_existing_deployment(self):
        data = facts()
        data.update(current_exists=False, current_safe=False)
        data['services'] = {name: {'load': 'not-found', 'active': 'inactive'} for name in SERVICES}
        self.assertEqual(assess_preflight('install', data, None)['blockers'], [])
        data.update(current_exists=True, api_port_busy=True)
        data['services'][SERVICES[0]]['load'] = 'loaded'
        codes = {item['code'] for item in assess_preflight('install', data, None)['blockers']}
        self.assertTrue({'INSTALL_DIRECTORY_EXISTS', 'APPLICATION_SERVICE_EXISTS', 'API_PORT_CONFLICT'} <= codes)

    def test_disk_budget_and_unknown_probe_never_look_ready(self):
        result = assess_preflight('update', facts(), {'unpacked_bytes': 2 * 1024**3})
        self.assertEqual(result['required_free_bytes'], 6 * 1024**3)
        self.assertIn('DISK_SPACE_LOW', [item['code'] for item in result['blockers']])
        self.assertEqual(assess_preflight('install', None, None)['blockers'][0]['code'], 'PROBE_UNAVAILABLE')

    def test_fixed_remote_program_reads_metadata_only_with_fake_filesystem(self):
        root = '/opt/campus-ai'
        def lstat(path):
            if path == root + '/current':
                return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=1000)
            if path == root + '/shared/config.local.toml':
                return SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=1000)
            if path == root + '/releases/release-2':
                raise FileNotFoundError()
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=1000)
        opened = []
        def open_proc(path):
            opened.append(path)
            if path not in ('/proc/net/tcp', '/proc/net/tcp6'):
                raise AssertionError('Unexpected file content read')
            return io.StringIO('header\n0: 0100007F:1F40 00000000:0000 0A\n')
        output = io.StringIO()
        with patch('sys.argv', ['-c', 'release-2']), patch('sys.stdout', output), patch('os.lstat', side_effect=lstat), patch(
            'os.getuid', return_value=1000, create=True), patch('os.path.exists', return_value=True), patch(
            'os.path.realpath', return_value=root + '/releases/release-1'), patch('os.access', return_value=True), patch(
            'shutil.disk_usage', return_value=SimpleNamespace(free=5 * 1024**3)), patch('builtins.open', side_effect=open_proc), patch(
            'importlib.util.find_spec', return_value=object()), patch('subprocess.run', return_value=SimpleNamespace(
                returncode=0, stdout=b'LoadState=loaded\nActiveState=active\n')) as run:
            exec(compile(PROBE, '<deployment-read-only-probe>', 'exec'), {})
        result = parse_probe(0, output.getvalue())
        self.assertTrue(result['current_safe'])
        self.assertFalse(result['release_exists'])
        self.assertTrue(result['api_port_busy'])
        self.assertTrue(result['config_private_readable'])
        self.assertEqual(opened, ['/proc/net/tcp', '/proc/net/tcp6'])
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ['/usr/bin/systemctl', 'show', '--no-pager', '--property=LoadState,ActiveState', name] for name in SERVICES])
