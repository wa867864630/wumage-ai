"""Small, deterministic grammar. Unrecognized text never executes a skill."""
import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

RULESET_VERSION = 'course-query-v1.2'
WEEKDAY = r'(?:本|这|下)(?:周|星期|礼拜)[一二三四五六日天]'
DAY_OFFSETS = {'今天': 0, '今日': 0, '明天': 1, '明日': 1, '后天': 2, '后日': 2}


class Routing(BaseModel):
    status: Literal['resolved', 'clarification', 'unsupported']
    message: str
    ruleset_version: str = RULESET_VERSION
    rule_id: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    suggestions: list[str] = []


def campus_today() -> date:
    return datetime.now(ZoneInfo('Asia/Shanghai')).date()


def normalize(text: str) -> str:
    normalized = re.sub(r'\s+', '', unicodedata.normalize('NFKC', text)).strip('，。？！,.?!')
    # Only unwrap matching pairs around the entire question; never remove inner clauses.
    wrappers = {'【': '】', '[': ']', '“': '”', '「': '」', '『': '』', '"': '"'}
    for _ in range(4):
        if len(normalized) >= 2 and wrappers.get(normalized[0]) == normalized[-1]:
            normalized = normalized[1:-1].strip('，。？！,.?!')
        else:
            break
    return normalized


def resolve(text: str, today: date, rules=None) -> Routing:
    normalized = normalize(text)
    custom = []
    # Custom rules remain bounded literals; no user-provided regex or executable expressions.
    unsafe = re.search(r'不|别|取消|请假|采购|预约|修改|删除|成绩|学号|权限|[，,；;]|和|以及|并且', normalized)
    # Configured aliases must not silently pick a week for an unscoped weekday,
    # or shorten unsupported expressions such as 下下周 to 下周.
    if re.search(r'(?<![本这下])(?:周|星期|礼拜)[一二三四五六日天]|下下(?:周|星期|礼拜)', normalized):
        unsafe = True
    date_tokens = re.findall(WEEKDAY + r'|今天|今日|明天|明日|后天|后日|本周|这周|下周|\d{4}[-年]\d{1,2}[-月]\d{1,2}日?', normalized)
    if len(set(date_tokens)) > 1:
        unsafe = True
    if rules and not unsafe:
        for rule in rules:
            if not rule.enabled:
                continue
            terms = rule.terms
            if rule.match_type == 'exact':
                matched = normalized in terms
            else:
                # Every token must appear, and the entire input must consist of these tokens
                # plus an explicit whitelist of connective words. No ignored extra commands.
                pattern = '|'.join(re.escape(term) for term in sorted(terms, key=len, reverse=True))
                remainder = re.sub(pattern, '', normalized)
                remainder = re.sub(r'请帮我|帮我|查询一下|查一下|查询|查看|看看|请|我的|的|呢|呀|啊', '', remainder)
                matched = all(term in normalized for term in terms) and not remainder
            if matched:
                custom.append(rule)
    if custom:
        best = max(rule.priority for rule in custom)
        winners = [rule for rule in custom if rule.priority == best]
        if len({rule.date_mode for rule in winners}) > 1:
            return Routing(status='clarification', message='存在相同优先级的冲突规则，请选择日期查询。')
        winner = sorted(winners, key=lambda rule: rule.id)[0]
        expression = {'today': '今天', 'tomorrow': '明天', 'day_after': '后天',
                      'this_week': '本周', 'next_week': '下周'}[winner.date_mode]
        builtin = resolve(normalized, today)
        result = resolve(expression + '课表', today)
        # Config may extend language, but cannot reinterpret a recognized date.
        if builtin.status == 'resolved' and (builtin.start_date, builtin.end_date) != (result.start_date, result.end_date):
            return Routing(status='clarification', message='配置规则与问句日期冲突，请选择日期查询。')
        result.rule_id = 'custom:' + winner.id
        return result
    if not normalized:
        return Routing(status='clarification', message='请输入想查询的日期，或使用下方日期选择。',
                       suggestions=['今天有什么课', '明天课表', '本周课表'])
    # Whole-input matching prevents ignoring negations, additional commands, or multiple dates.
    prefix = r'(?:请帮我|帮我|麻烦|请)?(?:查询一下|查一下|查询|查看|查|看看)?(?:我的)?'
    when = r'(?:' + WEEKDAY + r'|今天|今日|明天|明日|后天|后日|本周|这周|下周|\d{4}-\d{1,2}-\d{1,2}|\d{4}年\d{1,2}月\d{1,2}日)'
    suffix = r'(?:的?(?:课表|课程表|课程安排)|有(?:什么|哪些|啥)课|上什么课|有课吗)'
    match = re.fullmatch(prefix + r'(?P<when>' + when + r')?' + suffix + r'(?:呢|呀|啊)?', normalized)
    if match:
        expression = match.group('when')
        if expression is None:
            if not re.search(r'课表|课程表|课程安排', normalized):
                return Routing(status='clarification', message='请明确查询日期。')
            expression = '今天'
            message = '未指定日期，默认查询今天；你可以使用日期选择修改范围。'
            rule = 'default-today'
        else:
            message = '已按校园时区 Asia/Shanghai 解析日期。'
            rule = 'explicit-date'
        if expression in DAY_OFFSETS:
            start = end = today + timedelta(days=DAY_OFFSETS[expression])
            rule = rule if rule == 'default-today' else 'relative-day'
        elif re.fullmatch(WEEKDAY, expression):
            monday = today - timedelta(days=today.weekday())
            week_offset = 7 if expression.startswith('下') else 0
            weekday = '一二三四五六日'.index(expression[-1].replace('天', '日'))
            start = end = monday + timedelta(days=week_offset + weekday)
            rule = 'explicit-weekday'
            message = '按校园时区解析具体星期；本周为周一至周日，过去的日期不会自动改为下周。'
        elif expression in ('本周', '这周', '下周'):
            start = today - timedelta(days=today.weekday())
            if expression == '下周':
                start += timedelta(days=7)
            end = start + timedelta(days=6)
            rule = 'relative-week'
            message = '按校园时区解析，周一为一周的开始，周日为结束。'
        else:
            try:
                parts = re.findall(r'\d+', expression)
                start = end = date(*map(int, parts))
            except ValueError:
                return Routing(status='clarification', message='日期不存在，请检查日期或使用下方日期选择。',
                               suggestions=['今天课表', '本周课表'])
        return Routing(status='resolved', message=message, rule_id=rule, start_date=start, end_date=end)
    if re.search(r'课表|课程|什么课|哪些课|有课|上课', normalized):
        if re.search(r'(?:周|星期|礼拜)[一二三四五六日天]', normalized):
            return Routing(status='clarification', message='请明确是本周还是下周的哪一天，例如“本周五课表”或“下周五课表”。',
                           suggestions=['本周五课表', '下周五课表'])
        return Routing(status='clarification', message='这句话的日期或操作尚不明确。请分别查询一个范围，或使用下方日期选择；当前仅支持课表查询。',
                       suggestions=['今天有什么课', '明天课表', '本周课表', '下周课表'])
    return Routing(status='unsupported', message='当前仅支持查询课表；请假、采购、预约等办理功能尚未开放。',
                   suggestions=['今天有什么课', '本周课表'])
