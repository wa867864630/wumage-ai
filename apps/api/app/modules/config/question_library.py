"""Reviewed course aliases. Library state never overrides a published release."""
from datetime import date, datetime, timezone
import re
from typing import Literal
from uuid import uuid5, NAMESPACE_URL

import sqlalchemy as sa
from pydantic import Field, model_validator

from app.errors import APIError
from app.modules.config.course_design import StrictModel
from app.modules.config.schema import DisplayConfig, IntentRule
from app.modules.config.store import Conflict, KEY, state, LEGACY_APPLICATION_ID
from app.modules.intent.router import normalize, resolve

DateMode = Literal['today', 'tomorrow', 'day_after', 'this_week', 'next_week']
EXPRESSIONS = {'today': '今天', 'tomorrow': '明天', 'day_after': '后天', 'this_week': '本周', 'next_week': '下周'}
metadata = sa.MetaData()
questions = sa.Table('question_candidate', metadata,
    sa.Column('tenant_id', sa.String(64), primary_key=True),
    sa.Column('application_id', sa.String(36), primary_key=True, server_default=LEGACY_APPLICATION_ID),
    sa.Column('id', sa.String(36), primary_key=True),
    sa.Column('text', sa.String(80), nullable=False),
    sa.Column('date_mode', sa.String(16), nullable=False),
    sa.Column('source', sa.String(20), nullable=False),
    sa.Column('status', sa.String(16), nullable=False),
    sa.Column('revision', sa.Integer, nullable=False),
    sa.Column('occurrences', sa.Integer, nullable=False),
    sa.Column('interpretations', sa.JSON, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reviewer', sa.String(64)),
    sa.Column('reviewed_at', sa.DateTime(timezone=True)),
    sa.UniqueConstraint('tenant_id', 'application_id', 'text', name='uq_question_text'))


class QuestionInput(StrictModel):
    text: str = Field(min_length=1, max_length=80)
    date_mode: DateMode

    @model_validator(mode='after')
    def safe_question(self):
        self.text = normalize(self.text)
        if not self.text or re.search(r'\d{4}|@|https?://|密钥|令牌|密码|忽略|指令|系统提示|身份证|电话|邮箱|token|api', self.text, re.I):
            raise ValueError('问法不得包含个人信息、凭证、固定日期或指令')
        if not re.search(r'课表|课程|什么课|哪些课|啥课|有课|上课', self.text):
            raise ValueError('首版仅收录明确表达课表查询的问法')
        rule = IntentRule(id='question-check', terms=[self.text], date_mode=self.date_mode)
        for today in (date(2026, 10, 18), date(2026, 12, 31)):
            candidate = resolve(self.text, today, [rule])
            expected = resolve(EXPRESSIONS[self.date_mode] + '课表', today)
            if candidate.status != 'resolved' or (candidate.start_date, candidate.end_date) != (expected.start_date, expected.end_date):
                raise ValueError('问法存在日期歧义、否定、多操作或日期映射冲突')
        return self


class EditQuestion(QuestionInput):
    expected_revision: int = Field(ge=1)


class ReviewQuestion(StrictModel):
    expected_revision: int = Field(ge=1)
    expected_draft_revision: int = Field(ge=0)
    action: Literal['approve', 'reject', 'disable', 'delete']


class GeneratedQuestions(StrictModel):
    questions: list[QuestionInput] = Field(min_length=1, max_length=20)


class GenerateQuestions(StrictModel):
    count: int = Field(default=10, ge=1, le=20)
    expected_draft_revision: int = Field(ge=0)


def identifier(text):
    return str(uuid5(NAMESPACE_URL, 'course-question:' + normalize(text)))


def rule_id(question_id):
    return 'ql-' + question_id.replace('-', '')


def public(row):
    return {key: value.isoformat() if isinstance(value, datetime) else value for key, value in dict(row).items() if key != 'tenant_id'}


class QuestionLibrary:
    def __init__(self, store):
        self.store = store

    def scoped(self, question_id=None):
        conditions = [questions.c.tenant_id == self.store.tenant_id, questions.c.application_id == self.store.application_id]
        if question_id is not None:
            conditions.append(questions.c.id == question_id)
        return conditions

    def listing(self):
        with self.store.engine.begin() as connection:
            row = self.store._state(connection)
            draft = DisplayConfig.model_validate(row['draft'])
            # Immutable release is read inside this transaction; only exact rule equality counts.
            from app.modules.config.store import releases
            published = connection.execute(sa.select(releases.c.content).where(
                releases.c.id == row['current_release_id'], releases.c.tenant_id == self.store.tenant_id, releases.c.application_id == self.store.application_id,
                releases.c.key == KEY)).scalar_one_or_none() if row['current_release_id'] else None
            active_rules = {rule['id']: rule for rule in (published or {}).get('rules', [])}
            draft_rules = {rule.id: rule.model_dump() for rule in draft.rules}
            records = connection.execute(sa.select(questions).where(*self.scoped(), questions.c.status != 'deleted')
                .order_by(questions.c.updated_at.desc()).limit(500)).mappings().all()
            items = []
            for record in records:
                expected = IntentRule(id=rule_id(record['id']), terms=[record['text']], date_mode=record['date_mode']).model_dump()
                items.append(public(record) | {'in_draft': draft_rules.get(expected['id']) == expected,
                                              'published': active_rules.get(expected['id']) == expected})
            return {'items': items, 'draft_revision': row['revision'], 'limit': 500}

    def add(self, commands, trace_id, *, source='manual', expected_draft_revision=None, generation_info=None):
        with self.store.engine.begin() as connection:
            row = self.store._state(connection, lock=True)
            if expected_draft_revision is not None and row['revision'] != expected_draft_revision:
                raise Conflict('生成期间草稿已变化，请重新生成。')
            inserted = 0
            for command in commands:
                found = connection.execute(sa.select(questions.c.id).where(*self.scoped(), sa.or_(
                    questions.c.id == identifier(command.text), questions.c.text == command.text))).first()
                if found:
                    continue  # Includes tombstones and rejected entries: never resurrect automatically.
                count = connection.scalar(sa.select(sa.func.count()).select_from(questions).where(*self.scoped()))
                if count >= 500:
                    raise APIError(409, 'QUESTION_LIBRARY_FULL', '本阶段每应用最多500条问法记录（含删除记录）。')
                now = datetime.now(timezone.utc)
                connection.execute(questions.insert().values(tenant_id=self.store.tenant_id, application_id=self.store.application_id, id=identifier(command.text),
                    text=command.text, date_mode=command.date_mode, source=source, status='pending', revision=1,
                    occurrences=0, interpretations={}, created_at=now, updated_at=now))
                inserted += 1
            self.store._audit(connection, 'question_add', row['revision'], trace_id,
                              after={'source': source, 'inserted': inserted, 'generation': generation_info})
            return {'inserted': inserted, 'duplicates': len(commands) - inserted}

    def change(self, question_id, command, trace_id):
        with self.store.engine.begin() as connection:
            row = self.store._state(connection, lock=True)
            existing = connection.execute(sa.select(questions).where(*self.scoped(question_id))).mappings().first()
            if existing is None or existing['status'] == 'deleted':
                raise APIError(404, 'QUESTION_NOT_FOUND', '问法不存在。')
            if existing['revision'] != command.expected_revision:
                raise Conflict('问法已变化，请重新加载。')
            content = DisplayConfig.model_validate(row['draft'])
            remaining = [rule for rule in content.rules if rule.id != rule_id(question_id)]
            now = datetime.now(timezone.utc)
            if isinstance(command, EditQuestion):
                # Identity remains stable when editing. Reject collisions with any existing normalized text.
                collision = connection.execute(sa.select(questions.c.id).where(*self.scoped(),
                    sa.or_(questions.c.text == command.text, questions.c.id == identifier(command.text)),
                    questions.c.id != question_id)).first()
                if collision:
                    raise Conflict('相同规范问法已经存在。')
                values = {'text': command.text, 'date_mode': command.date_mode, 'status': 'pending',
                          'reviewer': None, 'reviewed_at': None, 'interpretations': {}, 'occurrences': 0}
                action = 'edit'
            else:
                if row['revision'] != command.expected_draft_revision:
                    raise Conflict('草稿已变化，请重新加载后审核。')
                action = command.action
                statuses = {'approve': 'approved', 'reject': 'rejected', 'disable': 'disabled', 'delete': 'deleted'}
                values = {'status': statuses[action], 'reviewer': self.store.actor_id, 'reviewed_at': now}
                if action == 'approve':
                    QuestionInput(text=existing['text'], date_mode=existing['date_mode'])
                    modes = existing['interpretations']
                    if len([value for value in modes.values() if value]) > 1:
                        raise APIError(422, 'QUESTION_INTERPRETATION_CONFLICT', '该问法存在多种AI日期解释，请修改问法并明确日期后再审核。')
                    remaining.append(IntentRule(id=rule_id(question_id), terms=[existing['text']], date_mode=existing['date_mode']))
            if len(remaining) > 50:
                raise APIError(422, 'QUESTION_RULE_LIMIT', '草稿规则已达到50条上限，请先合并或停用规则。')
            candidate = DisplayConfig.model_validate(content.model_dump() | {'rules': [rule.model_dump() for rule in remaining]})
            if action == 'approve':
                from app.modules.config.review import review
                check = review(content, candidate)
                if not check['can_publish']:
                    raise APIError(422, 'QUESTION_REGRESSION', '问法未通过日期回归，不能审核。')
                for today in (date(2026, 10, 18), date(2026, 12, 31)):
                    actual = resolve(existing['text'], today, candidate.rules)
                    expected = resolve(EXPRESSIONS[existing['date_mode']] + '课表', today)
                    if actual.status != 'resolved' or (actual.start_date, actual.end_date) != (expected.start_date, expected.end_date):
                        raise APIError(422, 'QUESTION_RULE_CONFLICT', '问法与现有草稿规则冲突，请先处理冲突。')
            revision = row['revision']
            if candidate != content:
                revision += 1
                connection.execute(state.update().where(state.c.tenant_id == self.store.tenant_id, state.c.application_id == self.store.application_id, state.c.key == KEY)
                    .values(draft=candidate.model_dump(), revision=revision))
            connection.execute(questions.update().where(*self.scoped(question_id)).values(**values,
                revision=existing['revision'] + 1, updated_at=now))
            self.store._audit(connection, 'question_' + action, revision, trace_id,
                              before=public(existing), after={'id': question_id, **values, 'reviewed_at': values.get('reviewed_at').isoformat() if values.get('reviewed_at') else None})
            return {'draft_revision': revision, 'status': values['status'], 'published': False}

    def observe(self, command, trace_id):
        """Only validated successful query interpretations; recording failure cannot fail queries."""
        with self.store.engine.begin() as connection:
            row = self.store._state(connection, lock=True)
            question_id = identifier(command.text)
            existing = connection.execute(sa.select(questions).where(*self.scoped(), sa.or_(
                questions.c.id == question_id, questions.c.text == command.text))).mappings().first()
            if existing:
                if existing['text'] != command.text:
                    return False
                question_id = existing['id']
                if existing['status'] in ('rejected', 'disabled', 'deleted'):
                    return False
                interpretations = dict(existing['interpretations'])
                interpretations[command.date_mode] = interpretations.get(command.date_mode, 0) + 1
                connection.execute(questions.update().where(*self.scoped(question_id)).values(
                    occurrences=existing['occurrences'] + 1, interpretations=interpretations,
                    revision=existing['revision'] + 1, updated_at=datetime.now(timezone.utc)))
                return True
            count = connection.scalar(sa.select(sa.func.count()).select_from(questions).where(*self.scoped()))
            if count >= 500:
                return False
            now = datetime.now(timezone.utc)
            connection.execute(questions.insert().values(tenant_id=self.store.tenant_id, application_id=self.store.application_id, id=question_id,
                text=command.text, date_mode=command.date_mode, source='learned', status='pending', revision=1,
                occurrences=1, interpretations={command.date_mode: 1}, created_at=now, updated_at=now))
            return True


def learned_question(text, routing, today):
    if routing.rule_id != 'ai-fallback':
        return None
    for mode, expression in EXPRESSIONS.items():
        expected = resolve(expression + '课表', today)
        if (routing.start_date, routing.end_date) == (expected.start_date, expected.end_date):
            try:
                return QuestionInput(text=text, date_mode=mode)
            except ValueError:
                return None
    return None
