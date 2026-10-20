import unittest
from datetime import date

from app.evaluation import evaluate


class EvaluationDatasetTests(unittest.TestCase):
    def test_dataset_date_and_name_are_used(self):
        result = evaluate(dataset=[{'text': '明天课表', 'start': '2027-01-01', 'end': '2027-01-01'}],
                          today=date(2026, 12, 31), dataset_name='frozen-uat')
        self.assertEqual(result['correct'], 1)
        self.assertEqual(result['date'], '2026-12-31')
        self.assertEqual(result['dataset'], 'frozen-uat')
        self.assertEqual(result['llm_calls'], 0)

    def test_fallback_candidate_is_not_counted_as_resolved(self):
        result = evaluate(dataset=[{'text': '能给我瞅瞅明天的课表嘛', 'start': '2026-10-19', 'end': '2026-10-19'}])
        self.assertEqual(result['fallback_candidates'], 1)
        self.assertEqual(result['unresolved'], 1)
        self.assertEqual(result['correct'], 0)
        self.assertEqual(result['llm_calls'], 0)
