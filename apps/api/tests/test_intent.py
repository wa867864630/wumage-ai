import os
import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from support import student_headers

from app.main import app, get_provider
from app.modules.config.schema import default_config
from app.modules.intent.router import resolve


class DateRuleTests(unittest.TestCase):
    def test_equivalent_tomorrow_expressions(self):
        for text in ['明天课表', '明天有什么课', '明天有哪些课', '明天有啥课',
                     '明天有什么课呢？', '【明天课表】', '【明天有什么课】',
                     '“明天有什么课？”', '［明天课表］', '查一下明天的课程安排']:
            with self.subTest(text=text):
                result = resolve(text, date(2026, 10, 18))
                self.assertEqual(result.status, 'resolved')
                self.assertEqual(result.start_date, date(2026, 10, 19))
                self.assertEqual(result.end_date, date(2026, 10, 19))
                self.assertEqual(result.rule_id, 'relative-day')

    def test_relative_days_and_weeks(self):
        today = date(2026, 10, 18)  # Sunday: next week must start the following day.
        cases = [
            ('今天有什么课', '2026-10-18', '2026-10-18'),
            ('请帮我查一下明天的课表', '2026-10-19', '2026-10-19'),
            ('后天有课吗', '2026-10-20', '2026-10-20'),
            ('本周课表', '2026-10-12', '2026-10-18'),
            ('这周有哪些课？', '2026-10-12', '2026-10-18'),
            ('下周有什么课', '2026-10-19', '2026-10-25'),
            ('2026年10月19日的课表', '2026-10-19', '2026-10-19'),
            ('２０２６－１０－１９课表', '2026-10-19', '2026-10-19'),
        ]
        for text, start, end in cases:
            with self.subTest(text=text):
                result = resolve(text, today)
                self.assertEqual(result.status, 'resolved')
                self.assertEqual(result.start_date.isoformat(), start)
                self.assertEqual(result.end_date.isoformat(), end)

    def test_year_boundary_leap_day_and_default(self):
        self.assertEqual(resolve('明天课表', date(2026, 12, 31)).start_date, date(2027, 1, 1))
        self.assertEqual(resolve('2028-02-29课表', date(2026, 10, 18)).status, 'resolved')
        self.assertEqual(resolve('2026-02-29课表', date(2026, 10, 18)).status, 'clarification')
        result = resolve('查课表', date(2026, 10, 18))
        self.assertEqual(result.start_date, date(2026, 10, 18))
        self.assertIn('默认', result.message)

    def test_ambiguous_negated_and_multi_action_inputs_do_not_execute(self):
        for text in ['今天和明天的课表', '不要查今天的课表', '今天课表，帮我请假',
                     '周三课表', '10月19日课表', '下一次上课', '   ', '2026-13-01课表',
                     '【不要查明天课表】', '【明天课表】帮我请假', '【今天课表】【明天课表】']:
            with self.subTest(text=text):
                self.assertNotEqual(resolve(text, date(2026, 10, 18)).status, 'resolved')
        self.assertEqual(resolve('我要请假', date(2026, 10, 18)).status, 'unsupported')

    def test_explicit_weekdays_and_day_aliases(self):
        for text, expected in [('本周五课表', '2026-10-16'), ('这星期天课表', '2026-10-18'),
                               ('下周三有什么课', '2026-10-21'), ('下星期一课表', '2026-10-19'),
                               ('下礼拜日有课吗', '2026-10-25'), ('明日课表', '2026-10-19'),
                               ('今日课程表', '2026-10-18'), ('后日有什么课', '2026-10-20')]:
            with self.subTest(text=text):
                result = resolve(text, date(2026, 10, 18))
                self.assertEqual(result.status, 'resolved')
                self.assertEqual(result.start_date.isoformat(), expected)
                self.assertEqual(result.start_date, result.end_date)

    def test_weekday_boundaries_and_ambiguity(self):
        self.assertEqual(resolve('下周一课表', date(2026, 12, 31)).start_date, date(2027, 1, 4))
        self.assertEqual(resolve('本周一课表', date(2026, 12, 31)).start_date, date(2026, 12, 28))
        for text in ('周五课表', '星期一课表', '下下周三课表', '下周八课表',
                     '下周三和下周五课表', '下周三今天课表', '不要查下周三课表', '下周三课表并请假'):
            with self.subTest(text=text):
                self.assertNotEqual(resolve(text, date(2026, 10, 18)).status, 'resolved')

    def test_custom_rule_cannot_reinterpret_explicit_weekday(self):
        from app.modules.config.schema import IntentRule
        rule = IntentRule(id='weekday-conflict', terms=['下周三课表'], date_mode='today')
        self.assertEqual(resolve('下周三课表', date(2026, 10, 18), [rule]).status, 'clarification')
        # A multi-date phrase must not become executable just because an admin adds an alias.
        rule.terms = ['下周三下周五课表']
        self.assertNotEqual(resolve(rule.terms[0], date(2026, 10, 18), [rule]).status, 'resolved')
        for text in ('周五课表', '星期一课表', '下下周三课表'):
            rule.terms = [text]
            self.assertNotEqual(resolve(text, date(2026, 10, 18), [rule]).status, 'resolved')


class NaturalQueryTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {'DATABASE_URL': ''})
        environment.start()
        self.addCleanup(environment.stop)
        self.client = TestClient(app)
        self.client.headers.update(student_headers(self))

    def test_explicit_weekday_uses_scoped_provider_and_same_published_snapshot(self):
        provider = AsyncMock()
        provider.get_schedule.return_value = []
        app.dependency_overrides[get_provider] = lambda: provider
        self.addCleanup(app.dependency_overrides.pop, get_provider)
        provider.source = 'mock'
        with patch('app.main.campus_today', return_value=date(2026, 10, 18)), patch(
            'app.main.published_config', return_value={'release_id': 'weekday-release',
                                                     'content': default_config().model_dump()}) as config:
            response = self.client.post('/api/v1/course-schedule/query', json={'text': '下周三有什么课'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['start_date'], '2026-10-21')
        self.assertEqual(response.json()['ui_hint']['release_id'], 'weekday-release')
        provider.get_schedule.assert_awaited_once_with('local-demo-student', date(2026, 10, 21),
                                                      date(2026, 10, 21), tenant_id='local-demo')
        config.assert_called_once()

    def test_resolved_query_uses_existing_provider_and_release(self):
        with patch('app.main.campus_today', return_value=date(2026, 10, 18)), patch(
            'app.main.published_config', return_value={'release_id': 'test-release',
                                                     'content': default_config().model_dump()}) as config:
            response = self.client.post('/api/v1/course-schedule/query', json={'text': '明天课表'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['data']['start_date'], '2026-10-19')
        self.assertEqual(len(data['data']['courses']), 1)
        self.assertEqual(data['ui_hint']['release_id'], 'test-release')
        self.assertEqual(data['routing']['status'], 'resolved')
        self.assertFalse(data['ai_used'])
        self.assertEqual(data['ai_duration_ms'], 0)
        self.assertEqual(data['trace_id'], response.headers['X-Trace-ID'])
        config.assert_called_once()

    def test_ai_fallback_timing_and_scoped_schedule_response(self):
        today = date(2026, 10, 18)
        routing = resolve('明天课表', today).model_copy(update={'rule_id': 'ai-fallback'})
        with patch('app.main.published_config', return_value={'release_id': 'ai-test', 'content': default_config().model_dump()}), patch(
            'app.modules.intent.fallback.fallback', AsyncMock(return_value=(routing, 1234.56, True))):
            response = self.client.post('/api/v1/course-schedule/query', json={'text': '能给我瞅瞅明天的课表嘛'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ai_used'])
        self.assertEqual(response.json()['ai_duration_ms'], 1234.6)
        self.assertEqual(response.json()['data']['start_date'], '2026-10-19')

    def test_clarification_does_not_query_data_or_configuration(self):
        provider = AsyncMock()
        app.dependency_overrides[get_provider] = lambda: provider
        self.addCleanup(app.dependency_overrides.pop, get_provider)
        with patch('app.main.published_config', return_value={'release_id': None,
                   'content': default_config().model_dump()}) as config:
            for text in ['今天和明天课表', '我要请假']:
                response = self.client.post('/api/v1/course-schedule/query', json={'text': text})
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.json()['data'])
                self.assertIsNone(response.json()['error'])
            self.assertEqual(config.call_count, 2)  # Resolve against the published rules, never a second version.
        provider.get_schedule.assert_not_awaited()

    def test_oversized_empty_and_identity_parameters_rejected(self):
        for body in [{'text': ''}, {'text': '课' * 201}, {'text': '今天课表', 'student_id': 'someone-else'}]:
            with self.subTest(body=body):
                self.assertEqual(self.client.post('/api/v1/course-schedule/query', json=body).status_code, 422)
