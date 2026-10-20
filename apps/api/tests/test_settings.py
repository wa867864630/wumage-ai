import os
import unittest
from unittest.mock import patch

from app.settings import SettingsError, admin_token, database_is_configured, database_options


class SettingsTests(unittest.TestCase):
    def test_password_keeps_special_characters(self):
        values = {'database': {'host': 'localhost', 'name': 'test', 'user': 'demo',
                               'password': 'a@b:/#%\\test', 'port': 5432}}
        with patch('app.settings.read_local_config', return_value=values):
            self.assertEqual(database_options()['password'], values['database']['password'])

    def test_empty_password_rejected_without_echoing_configuration(self):
        with patch('app.settings.read_local_config', return_value={'database': {'host': 'localhost'}}):
            with self.assertRaises(SettingsError):
                database_options()

    def test_environment_overrides_local_file(self):
        with patch('app.settings.read_local_config', return_value={'database': {'host': 'localhost'},
                                                                 'local_admin': {'token': 'file-token'}}):
            with patch.dict(os.environ, {'DATABASE_URL': '', 'LOCAL_ADMIN_TOKEN': 'environment-token'}):
                self.assertFalse(database_is_configured())
                self.assertEqual(admin_token(), 'environment-token')

    def test_backend_selection(self):
        config = {'host': 'localhost', 'name': 'test', 'user': 'demo', 'password': 'test'}
        for backend in ('postgresql', 'opengauss'):
            with patch('app.settings.read_local_config', return_value={'database': {**config, 'backend': backend}}):
                self.assertEqual(database_options()['backend'], backend)
        with patch('app.settings.read_local_config', return_value={'database': {**config, 'backend': 'unknown'}}):
            with self.assertRaises(SettingsError):
                database_options()
