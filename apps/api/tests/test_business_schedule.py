import asyncio
from datetime import date
import os
import unittest
from unittest.mock import patch

import sqlalchemy as sa
from fastapi.testclient import TestClient
from support import student_headers

import test_config as config_tests
from app.errors import APIError
from app.main import app
from app.modules.config.store import ConfigStore
from app.modules.skills.business import students, schedule
from app.modules.skills.database_schedule import DatabaseScheduleProvider
from app.modules.skills.seed import seed_mock_schedule


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), '需要开发数据库，仅在随机测试 Schema 中操作')
class BusinessScheduleTests(unittest.TestCase):
    setUp = config_tests.PostgresConfigTests.setUp
    cleanup_schema = config_tests.PostgresConfigTests.cleanup_schema

    def test_seed_is_repeatable_and_preserves_existing_edits(self):
        start, end = date(2026, 10, 19), date(2026, 10, 23)
        first = seed_mock_schedule(self.engine, start=start, end=end)
        self.assertEqual(first['students_inserted'], 3)
        self.assertEqual(first['courses_inserted'], 25)
        with self.engine.begin() as connection:
            row = connection.execute(sa.select(schedule.c.id, schedule.c.start_time).limit(1)).first()
            course_id = row.id
            adjusted_time = row.start_time.replace(minute=15)
            connection.execute(schedule.update().where(schedule.c.id == course_id).values(
                teacher='手工调整的模拟教师', start_time=adjusted_time))
        second = seed_mock_schedule(self.engine, start=start, end=end)
        self.assertEqual(second['students_inserted'], 0)
        self.assertEqual(second['courses_inserted'], 0)
        self.assertEqual(second['occupied_slots_preserved'], 25)
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(sa.select(sa.func.count()).select_from(schedule)), 25)
            self.assertEqual(connection.scalar(sa.select(schedule.c.teacher).where(schedule.c.id == course_id)), '手工调整的模拟教师')
            self.assertEqual(connection.scalar(sa.select(schedule.c.start_time).where(schedule.c.id == course_id)), adjusted_time)
            self.assertEqual(set(connection.scalars(sa.select(schedule.c.is_mock))), {True})
            self.assertEqual(set(connection.scalars(sa.select(students.c.is_mock))), {True})
        self.assertTrue(students.name.startswith('biz_'))
        self.assertTrue(schedule.name.startswith('biz_'))

    def test_provider_filters_student_and_tenant_without_generating_data(self):
        monday = date(2026, 10, 19)
        seed_mock_schedule(self.engine, start=monday, end=monday)
        seed_mock_schedule(self.engine, tenant_id='other-campus', start=monday, end=monday)
        provider = DatabaseScheduleProvider(self.engine)
        first = asyncio.run(provider.get_schedule('local-demo-student', monday, monday))
        second = asyncio.run(provider.get_schedule('mock-student-002', monday, monday))
        other = asyncio.run(provider.get_schedule('local-demo-student', monday, monday, tenant_id='other-campus'))
        self.assertEqual(len(first), 2)
        self.assertEqual(len(second), 1)
        self.assertNotEqual(first[0].name, second[0].name)
        self.assertFalse({item.id for item in first} & {item.id for item in other})
        self.assertEqual(asyncio.run(provider.get_schedule('local-demo-student', date(2026, 10, 24), date(2026, 10, 24))), [])
        with self.assertRaises(APIError) as error:
            asyncio.run(provider.get_schedule('missing-student', monday, monday))
        self.assertEqual(error.exception.code, 'IDENTITY_NOT_LINKED')
        with self.assertRaises(APIError):
            asyncio.run(provider.get_schedule('local-demo-student', monday, monday, tenant_id='missing-campus'))

    def test_seed_refuses_to_claim_non_mock_students(self):
        seed_mock_schedule(self.engine, start=date(2026, 10, 24), end=date(2026, 10, 24))
        with self.engine.begin() as connection:
            connection.execute(students.update().where(students.c.id == 'local-demo-student').values(is_mock=False))
        with self.assertRaises(ValueError):
            seed_mock_schedule(self.engine, start=date(2026, 10, 19), end=date(2026, 10, 19))
        with self.engine.connect() as connection:
            self.assertEqual(connection.scalar(sa.select(sa.func.count()).select_from(schedule)), 0)

    def test_database_mode_query_lifecycle(self):
        monday = date(2026, 10, 19)
        seed_mock_schedule(self.engine, start=monday, end=monday)
        with patch('app.main.read_local_config', return_value={'sis': {'mode': 'database'}}), patch(
            'app.modules.skills.database_schedule.DatabaseScheduleProvider', return_value=DatabaseScheduleProvider(self.engine)), patch(
            'app.modules.config.routes.get_store', side_effect=lambda principal=None: ConfigStore(self.engine)), patch(
            'app.modules.config.routes.database_is_configured', return_value=True):
            with TestClient(app) as client:
                response = client.get('/api/v1/course-schedule?start_date=2026-10-19&end_date=2026-10-19', headers=student_headers(self))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['data']['source'], 'database_mock')
                self.assertEqual(len(response.json()['data']['courses']), 2)
                self.assertEqual(client.get('/api/health').json()['data_source'], 'database')
                with patch('app.db.get_engine', return_value=self.engine), patch(
                    'app.modules.identity.auth.auth_options', return_value={'mode': 'demo'}), patch(
                    'app.modules.identity.demo.auth_options', return_value={'mode': 'demo'}):
                    listed = client.get('/api/v1/auth/demo/students').json()['students']
                    self.assertEqual(len(listed), 3)
                    login = client.post('/api/v1/auth/demo/login', json={'student_id': 'mock-student-002'})
                    self.assertEqual(login.status_code, 200)
                    headers = {'Authorization': 'Bearer ' + login.json()['access_token']}
                    scoped = client.get('/api/v1/course-schedule?start_date=2026-10-19&end_date=2026-10-19', headers=headers)
                    self.assertEqual(scoped.status_code, 200)
                    self.assertEqual(len(scoped.json()['data']['courses']), 1)
                    client.post('/api/v1/auth/demo/logout', headers=headers)
                    # Reuse the live-service acceptance driver against an isolated real DB schema.
                    import importlib.util
                    from pathlib import Path
                    spec = importlib.util.spec_from_file_location('verify_mvp', Path(__file__).resolve().parents[3] / 'scripts/verify_mvp.py')
                    verifier = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(verifier)
                    report = verifier.verify(client)
                    self.assertEqual(report['status'], 'passed')
                    self.assertGreaterEqual(report['count'], 20)
