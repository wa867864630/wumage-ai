import json
import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

from pydantic import SecretStr
from app.errors import APIError
from app.modules.identity.auth import Principal
from app.modules.intent.fallback import fallback
from app.modules.intent.router import resolve


class QueryFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def run_fallback(self, text, reply=None, principal=None):
        today = date(2026, 10, 20)
        model = AsyncMock(return_value=(json.dumps(reply or {'query': '明天课表', 'confidence': 0.95}), {}))
        principal = principal or Principal(subject='test', tenant_id='local-demo', roles=['student'])
        with patch('app.modules.intent.fallback.active_model', return_value=({'timeout_seconds': 60}, SecretStr('fake'))), patch('app.modules.intent.fallback.completion', model):
            result = await fallback(text, resolve(text, today), today, principal)
        return result, model

    async def test_rule_hits_and_unsafe_queries_never_call_ai(self):
        for text in ('明天课表', '不要查明天课表', '今天和明天课表', '周五课表', '明天课表并请假', '我要请假'):
            result, model = await self.run_fallback(text)
            model.assert_not_awaited()
            self.assertEqual(result[1:], (0.0, False))

    async def test_low_match_queries_use_ai_and_keep_explicit_date(self):
        result, model = await self.run_fallback('能给我瞅瞅明天的课表嘛')
        model.assert_awaited_once()
        self.assertEqual(result[0].rule_id, 'ai-fallback')
        self.assertEqual(result[0].start_date, date(2026, 10, 21))
        self.assertTrue(result[2])
        self.assertGreater(result[1], 0)
        self.assertEqual(model.call_args.kwargs['scope'], 'query')
        self.assertEqual(model.call_args.args[0]['timeout_seconds'], 10)

    async def test_low_confidence_wrong_dates_and_invalid_output_stay_unresolved(self):
        for reply in ({'query': '明天课表', 'confidence': 0.5}, {'query': '今天课表', 'confidence': 1}, [], {'query': '明天课表', 'confidence': True}):
            result, _ = await self.run_fallback('能给我瞅瞅明天的课表嘛', reply=reply or [1])
            self.assertNotEqual(result[0].status, 'resolved')
            self.assertTrue(result[2])

    async def test_other_tenants_never_use_local_credentials(self):
        principal = Principal(subject='test', tenant_id='other', roles=['student'])
        result, model = await self.run_fallback('能给我瞅瞅明天的课表嘛', principal=principal)
        model.assert_not_awaited()
        self.assertFalse(result[2])

    async def test_disabled_model_and_provider_failure_keep_clarification(self):
        text = '能给我瞅瞅明天的课表嘛'
        today = date(2026, 10, 20)
        principal = Principal(subject='test', tenant_id='local-demo', roles=['student'])
        with patch('app.modules.intent.fallback.active_model', side_effect=APIError(409, 'AI_DISABLED', 'disabled')):
            result = await fallback(text, resolve(text, today), today, principal)
        self.assertEqual(result[1:], (0.0, False))
        with patch('app.modules.intent.fallback.active_model', return_value=({'timeout_seconds': 60}, SecretStr('fake'))), patch('app.modules.intent.fallback.completion', AsyncMock(side_effect=APIError(504, 'AI_TIMEOUT', 'timeout'))):
            result = await fallback(text, resolve(text, today), today, principal)
        self.assertNotEqual(result[0].status, 'resolved')
        self.assertTrue(result[2])
