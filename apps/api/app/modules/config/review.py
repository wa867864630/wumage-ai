"""Shared preview and publication checks. Synthetic regressions are not campus UAT."""
from datetime import date

from app.modules.config.schema import DisplayConfig
from app.modules.intent.router import resolve


def review(before: DisplayConfig, after: DisplayConfig):
    today = date(2026, 10, 18)
    examples = ['今天有什么课', '明天课表', '明天有什么课', '后天课表', '本周课表',
                '下周有什么课', '2026-10-19课表', '查课表', '今天和明天课表',
                '不要查今天课表', '我要请假', '2026-02-29课表', '明日课表',
                '本周五课表', '下周三有什么课', '下星期一课表', '周五课表']
    for rule in after.rules:
        if rule.enabled:
            examples.extend(rule.terms if rule.match_type == 'exact' else [''.join(rule.terms)])
    rows = []
    violations = []
    for text in dict.fromkeys(examples):
        old = resolve(text, today, before.rules)
        new = resolve(text, today, after.rules)
        old_result = (old.status, old.start_date, old.end_date)
        new_result = (new.status, new.start_date, new.end_date)
        baseline = resolve(text, today)
        if baseline.status == 'resolved' and new_result != (baseline.status, baseline.start_date, baseline.end_date):
            violations.append(text)
        rows.append({'text': text, 'before': old.model_dump(mode='json'),
                     'after': new.model_dump(mode='json'), 'changed': old_result != new_result})
    changes = []
    if before.application != after.application:
        changes.append('应用名称、描述、分类或可见角色变化')
    old_columns = {column.field: column.model_dump() for column in before.columns}
    new_columns = {column.field: column.model_dump() for column in after.columns}
    if old_columns != new_columns:
        changes.append('展示字段名称、显隐或字段集合变化')
    if [c.field for c in before.columns] != [c.field for c in after.columns]:
        changes.append('字段顺序变化')
    if [r.model_dump() for r in before.rules] != [r.model_dump() for r in after.rules]:
        changes.append('意图匹配规则变化')
    return {'changes': changes, 'regressions': rows, 'violations': violations,
            'can_publish': not violations, 'evaluation_date': today.isoformat(),
            'dataset_kind': 'synthetic-development'}
