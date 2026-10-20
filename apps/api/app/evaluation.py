"""Frozen synthetic development evaluation. Never label this as real campus evidence."""
from datetime import date, timedelta

from app.modules.intent.router import resolve

EVALUATION_DATE = date(2026, 10, 18)


def cases():
    examples = []
    ranges = [('今天', '2026-10-18', '2026-10-18'), ('明天', '2026-10-19', '2026-10-19'),
              ('后天', '2026-10-20', '2026-10-20'), ('本周', '2026-10-12', '2026-10-18'),
              ('这周', '2026-10-12', '2026-10-18'), ('下周', '2026-10-19', '2026-10-25')]
    for prefix in ('', '查一下', '请帮我查询'):
        for day, start, end in ranges:
            for suffix in ('课表', '的课程表', '有什么课', '有哪些课', '有啥课', '上什么课', '有课吗'):
                examples.append({'text': prefix + day + suffix, 'start': start, 'end': end})
    for day in range(19, 26):
        examples.append({'text': f'2026年10月{day}日的课表',
                         'start': f'2026-10-{day}', 'end': f'2026-10-{day}'})
    # v2 explicitly promotes scoped weekdays from clarification to supported queries.
    for scope, monday in [('本周', date(2026, 10, 12)), ('这星期', date(2026, 10, 12)),
                          ('下周', date(2026, 10, 19)), ('下星期', date(2026, 10, 19)),
                          ('下礼拜', date(2026, 10, 19))]:
        for index, weekday in enumerate('一二三四五六日'):
            expected = (monday + timedelta(days=index)).isoformat()
            for suffix in ('课表', '有什么课'):
                examples.append({'text': scope + weekday + suffix, 'start': expected, 'end': expected})
    for alias, expected in [('今日', '2026-10-18'), ('明日', '2026-10-19'), ('后日', '2026-10-20')]:
        examples.append({'text': alias + '课表', 'start': expected, 'end': expected})
    examples.append({'text': '下周三课表', 'start': '2026-10-21', 'end': '2026-10-21'})
    for text in ('不要查今天课表', '今天和明天课表', '下一次上课', '周五课表', '我要请假',
                 '查询成绩', '今天课表，帮我请假', '2026-02-29课表', '10月19日课表',
                 '【今天课表】【明天课表】', '删除课程', '预约教室', '采购申请'):
        examples.append({'text': text, 'start': None, 'end': None})
    for text in ('下周三和下周五课表', '本周五课表并请假', '不要查下星期一课表',
                 '下周三今天课表', '星期一课表', '下下周三课表', '下周八课表'):
        examples.append({'text': text, 'start': None, 'end': None})
    return examples


def evaluate(rules=None, dataset=None, *, today=EVALUATION_DATE, dataset_name='synthetic-development-v2'):
    records = []
    for example in dataset if dataset is not None else cases():
        routing = resolve(example['text'], today, rules)
        actual = (routing.start_date.isoformat() if routing.start_date else None,
                  routing.end_date.isoformat() if routing.end_date else None)
        expected = (example['start'], example['end'])
        passed = actual == expected and ((routing.status == 'resolved') == bool(example['start']))
        records.append({'text': example['text'], 'passed': passed, 'status': routing.status,
                        'expected': expected, 'actual': actual})
    correct = sum(row['passed'] for row in records)
    from app.modules.intent.fallback import eligible
    examples = dataset if dataset is not None else cases()
    fallback_candidates = sum(eligible(example['text'], resolve(example['text'], today, rules)) for example in examples)
    return {'dataset': dataset_name, 'date': today.isoformat(),
            'total': len(records), 'correct': correct, 'accuracy': correct / len(records) if records else 0,
            'failures': [row for row in records if not row['passed']], 'llm_calls': 0,
            'rule_resolved': sum(row['status'] == 'resolved' for row in records),
            'fallback_candidates': fallback_candidates,
            'unresolved': sum(row['status'] != 'resolved' for row in records),
            'scope': 'offline-rules-only; fallback candidates are not AI successes'}


if __name__ == '__main__':
    import json
    result = evaluate()
    print(json.dumps(result, ensure_ascii=True, indent=2))
    raise SystemExit(bool(result['failures']))
