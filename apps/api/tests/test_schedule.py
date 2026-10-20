import unittest
import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from support import student_headers


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'DATABASE_URL': ''})
        environment.start()
        self.addCleanup(environment.stop)
        self.client = TestClient(app)
        self.client.headers.update(student_headers(self))

    def test_weekday_and_trace(self):
        response = self.client.get('/api/v1/course-schedule?start_date=2026-10-16&end_date=2026-10-16')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['data']['source'], 'mock')
        self.assertEqual(len(body['data']['courses']), 1)
        self.assertEqual(body['trace_id'], response.headers['X-Trace-ID'])

    def test_weekend_is_successful_empty_result(self):
        response = self.client.get('/api/v1/course-schedule?start_date=2026-10-17&end_date=2026-10-18')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['courses'], [])
        self.assertIsNone(response.json()['error'])

    def test_invalid_ranges_and_dates(self):
        for start, end in [('2026-10-18', '2026-10-16'), ('2026-10-01', '2026-11-01'), ('invalid', '2026-10-16')]:
            with self.subTest(start=start, end=end):
                response = self.client.get('/api/v1/course-schedule', params={'start_date': start, 'end_date': end})
                self.assertEqual(response.status_code, 422)
                self.assertIsNone(response.json()['data'])
                self.assertTrue(response.json()['error']['code'])
