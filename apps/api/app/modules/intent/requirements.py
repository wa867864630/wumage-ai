"""Owner-scoped construction requests, distinct from executable applications."""
from datetime import datetime, timedelta, timezone
import os
from typing import Literal
from uuid import uuid4

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.errors import APIError
from app.modules.config.routes import admin, execute
from app.modules.config.store import Conflict, NotFound, digest

metadata = sa.MetaData()
tasks = sa.Table('assistant_requirement', metadata,
    sa.Column('id', sa.String(36), primary_key=True),
    sa.Column('tenant_id', sa.String(64), nullable=False),
    sa.Column('owner_id', sa.String(64), nullable=False),
    sa.Column('title', sa.String(80), nullable=False),
    sa.Column('description', sa.String(1000), nullable=False),
    sa.Column('status', sa.String(24), nullable=False),
    sa.Column('revision', sa.Integer, nullable=False),
    sa.Column('command_key', sa.String(80), nullable=False),
    sa.Column('command_hash', sa.String(64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('audit', sa.JSON, nullable=False),
    sa.Column('business_kind', sa.String(32), nullable=False, server_default='unknown'),
    sa.Column('target_application_id', sa.String(36)),
    sa.UniqueConstraint('tenant_id', 'owner_id', 'command_key', name='uq_requirement_command'))

commands = sa.Table('assistant_command', metadata,
    sa.Column('id', sa.String(36), primary_key=True),
    sa.Column('tenant_id', sa.String(64), nullable=False),
    sa.Column('owner_id', sa.String(64), nullable=False),
    sa.Column('command_key', sa.String(80), nullable=False),
    sa.Column('action', sa.String(24), nullable=False),
    sa.Column('request_hash', sa.String(64), nullable=False),
    sa.Column('payload', sa.JSON, nullable=False),
    sa.Column('result', sa.JSON),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True)),
    sa.UniqueConstraint('tenant_id', 'owner_id', 'command_key', name='uq_assistant_command'))

BusinessKind = Literal['unknown', 'course_query', 'registration', 'leave', 'purchase', 'venue', 'visit', 'event']


def creation_enabled():
    return os.environ.get('ASSISTANT_CREATION_ENABLED', '1') == '1'


def command_status(row):
    if row['result'] is not None:
        if row['action'] == 'generate':
            return {'proposed': 'succeeded', 'needs_clarification': 'needs_clarification', 'failed': 'failed'}.get(row['result'].get('generation_status'), 'unknown')
        return 'succeeded'
    return 'expired' if row['expires_at'] and row['expires_at'] < datetime.now(timezone.utc) else 'awaiting_confirmation'


class SaveRequirement(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=16, max_length=80)
    business_kind: BusinessKind = 'unknown'


class EditRequirement(SaveRequirement):
    expected_revision: int = Field(ge=1)


class PrepareCreation(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    expected_revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=30)
    template_id: Literal['course-standard', 'course-compact', 'form-registration', 'leave-basic']
    idempotency_key: str = Field(min_length=16, max_length=80)
    source_application_id: str | None = Field(default=None, min_length=36, max_length=36)
    generation_key: str | None = Field(default=None, min_length=16, max_length=80)


class CancelRequirement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=1)


class Requirement(BaseModel):
    id: str
    title: str
    description: str
    status: Literal['pending_capability', 'converted', 'cancelled']
    revision: int
    created_at: datetime
    updated_at: datetime
    business_kind: BusinessKind = 'unknown'
    target_application_id: str | None = None


class RequirementStore:
    def __init__(self, store):
        self.store = store

    def scope(self):
        return (tasks.c.tenant_id == self.store.tenant_id, tasks.c.owner_id == self.store.actor_id)

    def read(self, connection, task_id, lock=False):
        query = sa.select(tasks).where(*self.scope(), tasks.c.id == task_id)
        row = connection.execute(query.with_for_update() if lock else query).mappings().first()
        if row is None:
            raise NotFound('建设需求不存在或不可访问。')
        return row

    def view(self, connection, row):
        from app.modules.config.store import state, releases, KEY
        result = Requirement.model_validate(dict(row)).model_dump(mode='json')
        active = row['status'] == 'pending_capability'
        from app.modules.forms.adapter import enabled as forms_enabled
        from app.modules.approvals.adapter import enabled as approvals_enabled
        supported = row['business_kind'] == 'course_query' or row['business_kind'] == 'registration' and forms_enabled() or row['business_kind'] == 'leave' and approvals_enabled()
        can_create = active and supported and creation_enabled()
        result.update(allowed_actions=['view'] + (['edit', 'cancel'] if active else ['cancel'] if row['status'] == 'converted' else [])
            + (['prepare_creation'] if can_create else []),
            blocked_reason=None if can_create or row['target_application_id'] else '创建入口已关闭。' if not creation_enabled() else '请先明确业务目标，或等待对应类型接入。',
            progress=None)
        if row['target_application_id']:
            current = connection.execute(sa.select(state.c.draft, state.c.revision, state.c.current_release_id,
                releases.c.content_hash).select_from(state.outerjoin(releases, sa.and_(
                    releases.c.id == state.c.current_release_id, releases.c.tenant_id == state.c.tenant_id,
                    releases.c.application_id == state.c.application_id, releases.c.key == state.c.key)))
                .where(state.c.tenant_id == self.store.tenant_id, state.c.application_id == row['target_application_id'], state.c.key == KEY)).mappings().first()
            if current:
                result['allowed_actions'] += ['configure']
                result['progress'] = {'draft_revision': current['revision'], 'release_id': current['current_release_id'],
                    'unpublished_changes': digest(current['draft']) != current['content_hash']}
            else:
                result['blocked_reason'] = '目标应用已不可用，需求记录仍保留。'
        return result

    def listing(self, status='all', page=1, page_size=20):
        with self.store.engine.connect() as connection:
            filters = [*self.scope()] + ([] if status == 'all' else [tasks.c.status == status])
            total = connection.scalar(sa.select(sa.func.count()).select_from(tasks).where(*filters))
            rows = connection.execute(sa.select(tasks).where(*filters).order_by(tasks.c.created_at.desc(), tasks.c.id)
                .offset((page - 1) * page_size).limit(page_size)).mappings().all()
            return {'items': [self.view(connection, row) for row in rows], 'total': total,
                'page': page, 'page_size': page_size, 'limit': 100}

    def detail(self, task_id):
        with self.store.engine.connect() as connection:
            row = self.read(connection, task_id)
            return {'item': self.view(connection, row), 'audit': row['audit']}

    def command_scope(self):
        return (commands.c.tenant_id == self.store.tenant_id, commands.c.owner_id == self.store.actor_id)

    def previous_command(self, connection, key, action, payload):
        previous = connection.execute(sa.select(commands).where(*self.command_scope(), commands.c.command_key == key)).mappings().first()
        if previous and (previous['action'] != action or previous['request_hash'] != digest(payload)):
            raise Conflict('同一请求号不能用于不同操作或内容。')
        return previous

    def record_command(self, connection, key, action, payload, result=None, expires=None):
        command_id = str(uuid4())
        connection.execute(commands.insert().values(id=command_id, tenant_id=self.store.tenant_id,
            owner_id=self.store.actor_id, command_key=key, action=action, request_hash=digest(payload), payload=payload,
            result=result, created_at=datetime.now(timezone.utc), expires_at=expires))
        return command_id

    def command_result(self, key):
        with self.store.engine.connect() as connection:
            row = connection.execute(sa.select(commands).where(*self.command_scope(), commands.c.command_key == key)).mappings().first()
            if row:
                return {'found': True, 'command_id': row['id'], 'action': row['action'],
                    'status': command_status(row), 'result': row['result'],
                    'confirmation': self.confirmation(row) if row['action'] == 'create' else None}
            legacy = connection.execute(sa.select(tasks).where(*self.scope(), tasks.c.command_key == key)).mappings().first()
            return {'found': legacy is not None, 'status': 'succeeded' if legacy else 'not_found',
                'result': {'item': self.view(connection, legacy)} if legacy else None}

    def history(self):
        with self.store.engine.connect() as connection:
            rows = connection.execute(sa.select(commands).where(*self.command_scope())
                .order_by(commands.c.created_at.desc(), commands.c.id).limit(50)).mappings().all()
            return {'items': [{'id': row['id'], 'command_key': row['command_key'], 'action': row['action'],
                'created_at': row['created_at'].isoformat(), 'status': command_status(row),
                'result': row['result'], 'confirmation': self.confirmation(row) if row['action'] == 'create' else None} for row in rows], 'limit': 50}

    def save(self, command, trace):
        payload = command.model_dump(exclude={'idempotency_key'}, exclude_defaults=True)
        # Default unknown preserves fingerprints of commands created before business_kind existed.
        fingerprint = digest(payload)
        with self.store.engine.begin() as connection:
            # Reuse the tenant serialization row, including the existing first-create concurrency guard.
            self.store._state(connection, lock=True)
            recorded = self.previous_command(connection, command.idempotency_key, 'save', payload)
            if recorded:
                return {**recorded['result'], 'replayed': True}
            previous = connection.execute(sa.select(tasks).where(*self.scope(), tasks.c.command_key == command.idempotency_key)).mappings().first()
            if previous:
                if previous['command_hash'] != fingerprint:
                    raise Conflict('同一请求号不能保存不同需求。')
                return {'item': Requirement.model_validate(dict(previous)).model_dump(mode='json'), 'replayed': True}
            count = connection.scalar(sa.select(sa.func.count()).select_from(tasks).where(*self.scope(), tasks.c.status != 'cancelled'))
            if count >= 100:
                raise Conflict('每人最多100条活动需求；取消不再处理的需求后可继续保存。')
            now = datetime.now(timezone.utc)
            row = dict(id=str(uuid4()), tenant_id=self.store.tenant_id, owner_id=self.store.actor_id,
                title=command.title, description=command.description, status='pending_capability', revision=1,
                business_kind=command.business_kind, target_application_id=None,
                command_key=command.idempotency_key, command_hash=fingerprint, created_at=now, updated_at=now,
                audit=[{'action': 'save_requirement', 'actor': self.store.actor_id, 'trace_id': trace, 'at': now.isoformat()}])
            connection.execute(tasks.insert().values(**row))
            result = {'item': self.view(connection, row), 'replayed': False}
            self.record_command(connection, command.idempotency_key, 'save', payload, result)
            return result

    def edit(self, task_id, command, trace):
        payload = {'task_id': task_id, **command.model_dump(exclude={'idempotency_key'})}
        with self.store.engine.begin() as connection:
            self.store._state(connection, lock=True)
            previous = self.previous_command(connection, command.idempotency_key, 'edit', payload)
            if previous:
                return {**previous['result'], 'replayed': True}
            row = self.read(connection, task_id, lock=True)
            if row['revision'] != command.expected_revision or row['status'] != 'pending_capability':
                raise Conflict('需求已改变或已转换/取消，请查看最新版本后重新编辑。')
            now = datetime.now(timezone.utc)
            values = dict(title=command.title, description=command.description, business_kind=command.business_kind,
                revision=row['revision'] + 1, updated_at=now, audit=[*row['audit'], {'action': 'edit_requirement',
                    'actor': self.store.actor_id, 'trace_id': trace, 'at': now.isoformat(),
                    'before': {key: row[key] for key in ('title', 'description', 'business_kind', 'revision')},
                    'after': command.model_dump(exclude={'idempotency_key'})}])
            connection.execute(tasks.update().where(*self.scope(), tasks.c.id == task_id).values(**values))
            result = {'item': self.view(connection, {**row, **values}), 'replayed': False}
            self.record_command(connection, command.idempotency_key, 'edit', payload, result)
            return result

    def prepare(self, task_id, command):
        if not creation_enabled():
            raise Conflict('创建入口已关闭，已保存需求仍可查看。')
        with self.store.engine.begin() as connection:
            self.store._state(connection, lock=True)
            payload = {'task_id': task_id, **command.model_dump(exclude={'idempotency_key'}, exclude_none=True)}
            previous = self.previous_command(connection, command.idempotency_key, 'create', payload)
            if previous:
                return self.confirmation(previous)
            row = self.read(connection, task_id, lock=True)
            if row['revision'] != command.expected_revision or row['status'] != 'pending_capability' or row['target_application_id']:
                raise Conflict('需求已改变或已有目标应用，请刷新后继续。')
            installed, defaults, templates = self.recipe(row['business_kind'])
            if command.generation_key:
                original = connection.execute(sa.select(commands.c.result).where(*self.command_scope(),
                    commands.c.command_key == command.generation_key, commands.c.action == 'generate')).scalar_one_or_none()
                if original and original.get('type_digest') != installed['type_digest']:
                    legacy, legacy_defaults, legacy_templates = self.recipe(row['business_kind'], '1.0.0')
                    if original.get('type_digest') == legacy['type_digest']:
                        installed, defaults, templates = legacy, legacy_defaults, legacy_templates
            if command.template_id not in templates:
                raise APIError(422, 'TYPE_UNAVAILABLE', '模板与需求业务目标不一致。')
            snapshot = {'type_id': installed['type_id'], 'type_version': installed['type_version'], 'type_digest': installed['type_digest'],
                'template_id': command.template_id, 'template_digest': digest({'template_id': command.template_id,
                    'defaults': defaults, 'type_digest': installed['type_digest']}),
                'visible_roles': defaults['application']['visible_roles'],
                'requirement_title': row['title'], 'payload': payload}
            if command.source_application_id:
                if command.generation_key:
                    raise Conflict('复制与 AI 提案只能选择一种来源。')
                source = self.copy_source(connection, command.source_application_id, installed['type_id'])
                snapshot.update(source=source, visible_roles=source['content']['application']['visible_roles'])
            if command.generation_key:
                generated = self.generated_content(connection, command.generation_key, task_id, row['revision'], installed)
                snapshot.update(generated=generated, visible_roles=generated['content']['application']['visible_roles'])
            expires = datetime.now(timezone.utc) + timedelta(minutes=15)
            command_id = self.record_command(connection, command.idempotency_key, 'create', payload, expires=expires)
            connection.execute(commands.update().where(commands.c.id == command_id).values(payload={**payload, 'snapshot': snapshot}))
            return {'command_id': command_id, 'command_key': command.idempotency_key, 'summary': snapshot, 'expires_at': expires.isoformat(), 'result': None}

    def copy_source(self, connection, application_id, type_id):
        from app.modules.config.application_store import ApplicationStore
        from app.modules.config.store import state, KEY
        application = ApplicationStore(self.store).get(application_id)
        from app.modules.config.lifecycle import assert_active
        if application['type_id'] != type_id:
            raise Conflict('只能从相同应用类型复制设计。')
        row = connection.execute(sa.select(state.c.revision, state.c.draft).where(
            state.c.tenant_id == self.store.tenant_id, state.c.application_id == application_id,
            state.c.key == KEY).with_for_update()).mappings().first()
        if row is None:
            raise NotFound('来源草稿不存在。')
        assert_active(connection, self.store.tenant_id, application_id)
        # Drafts contain declarative design only; no runtime binding, records or approver identity.
        ApplicationStore.information(application, row['draft'])
        from app.modules.config.schema import DisplayConfig
        from app.modules.forms.schema import FormConfig
        from app.modules.approvals.schema import LeaveConfig
        model = {'campus.course-query': DisplayConfig, 'platform.form': FormConfig, 'platform.leave-approval': LeaveConfig}[type_id]
        return {'application_id': application_id, 'revision': row['revision'],
                'content_hash': digest(row['draft']), 'content': model.model_validate(row['draft']).model_dump(mode='json')}

    def generated_content(self, connection, key, task_id, revision, installed):
        row = connection.execute(sa.select(commands).where(*self.command_scope(),
            commands.c.command_key == key, commands.c.action == 'generate')).mappings().first()
        if not row or not row['result'] or row['result'].get('generation_status') != 'proposed':
            raise Conflict('AI 提案不存在或尚未通过校验。')
        payload, result = row['payload'], row['result']
        if (payload['task_id'] != task_id or payload['revision'] != revision
                or result['type_digest'] != installed['type_digest']):
            raise Conflict('需求或类型已改变，请重新生成提案。')
        return {'generation_key': key, 'content': result['content'], 'content_hash': digest(result['content']),
                'prompt_version': result['prompt_version'], 'document': payload.get('document')}

    @staticmethod
    def recipe(kind, version=None):
        from app.modules.config.type_registry import course_type
        from app.modules.config.type_registry import require_type
        from app.modules.config.schema import default_config
        if kind == 'course_query':
            return course_type(), default_config().model_dump(), {'course-standard', 'course-compact'}
        if kind == 'registration':
            from app.modules.forms.adapter import form_type, enabled
            from app.modules.forms.schema import default_form
            if enabled():
                return require_type('platform.form', version or '1.1.0'), default_form().model_dump(), {'form-registration'}
        if kind == 'leave':
            from app.modules.approvals.adapter import approval_type, enabled
            from app.modules.approvals.schema import default_leave
            if enabled():
                return require_type('platform.leave-approval', version or '1.1.0'), default_leave().model_dump(), {'leave-basic'}
        raise APIError(422, 'TYPE_UNAVAILABLE', '该需求的类型尚未安装或已暂停，不能套用其他业务模板。')

    @staticmethod
    def confirmation(row):
        return {'command_id': row['id'], 'command_key': row['command_key'], 'summary': row['payload']['snapshot'],
            'expires_at': row['expires_at'].isoformat(), 'result': row['result']}

    def confirm(self, command_id, trace):
        from app.modules.config.application_store import ApplicationStore, CreateApplication
        from app.modules.config.type_registry import require_type
        if not creation_enabled():
            raise Conflict('创建入口已关闭，请保留需求。')
        with self.store.engine.begin() as connection:
            self.store._state(connection, lock=True)
            row = connection.execute(sa.select(commands).where(*self.command_scope(), commands.c.id == command_id,
                commands.c.action == 'create').with_for_update()).mappings().first()
            if row is None:
                raise NotFound('确认不存在或不可访问。')
            if row['result'] is not None:
                return {**row['result'], 'replayed': True}
            if row['expires_at'] < datetime.now(timezone.utc):
                raise Conflict('确认已过期，请重新生成创建摘要。')
            payload = row['payload']
            requirement = self.read(connection, payload['task_id'], lock=True)
            if requirement['revision'] != payload['expected_revision'] or requirement['status'] != 'pending_capability' or requirement['target_application_id']:
                raise Conflict('需求已修改、取消或转换，原确认失效。')
            _, defaults, templates = self.recipe(requirement['business_kind'], payload['snapshot']['type_version'])
            if payload['template_id'] not in templates:
                raise Conflict('需求类型不支持此模板。')
            snapshot = payload['snapshot']
            require_type(snapshot['type_id'], snapshot['type_version'], snapshot['type_digest'])
            current_digest = digest({'template_id': payload['template_id'], 'defaults': defaults, 'type_digest': snapshot['type_digest']})
            if current_digest != snapshot['template_digest']:
                raise Conflict('模板已改变，请重新确认。')
            source = snapshot.get('source')
            if source and self.copy_source(connection, source['application_id'], snapshot['type_id']) != source:
                raise Conflict('来源应用草稿已变化，请重新生成复制摘要。')
            generated = snapshot.get('generated')
            if generated and self.generated_content(connection, generated['generation_key'], requirement['id'],
                    requirement['revision'], {'type_digest': snapshot['type_digest']}) != generated:
                raise Conflict('AI 提案来源已变化，请重新确认。')
            created = ApplicationStore(self.store).create(CreateApplication(type_id=snapshot['type_id'], type_version=snapshot['type_version'],
                template_id=payload['template_id'], name=payload['name'], idempotency_key='task-' + command_id), trace, connection=connection)
            if source or generated:
                from copy import deepcopy
                from app.modules.config.store import state, KEY, ConfigStore
                origin = source or generated
                content = deepcopy(origin['content'])
                content['application']['name'] = payload['name']
                connection.execute(state.update().where(state.c.tenant_id == self.store.tenant_id,
                    state.c.application_id == created['application_id'], state.c.key == KEY).values(draft=content))
                ConfigStore(self.store.engine, self.store.tenant_id, self.store.actor_id,
                    created['application_id'])._audit(connection, 'copy_application_design' if source else 'ai_application_design', 0, trace,
                        after={key: value for key, value in origin.items() if key != 'content'})
            now = datetime.now(timezone.utc)
            changes = dict(target_application_id=created['application_id'], status='converted', revision=requirement['revision'] + 1,
                updated_at=now, audit=[*requirement['audit'], {'action': 'convert_requirement', 'actor': self.store.actor_id,
                    'trace_id': trace, 'at': now.isoformat(), 'command_id': command_id, 'application_id': created['application_id']}])
            connection.execute(tasks.update().where(*self.scope(), tasks.c.id == requirement['id']).values(**changes))
            result = {'item': self.view(connection, {**requirement, **changes}), 'application_id': created['application_id'], 'replayed': False}
            connection.execute(commands.update().where(commands.c.id == command_id).values(result=result))
            return result

    def cancel(self, task_id, command, trace):
        with self.store.engine.begin() as connection:
            self.store._state(connection, lock=True)
            key = f'cancel-{task_id}-{command.expected_revision}'
            payload = {'task_id': task_id, 'expected_revision': command.expected_revision}
            previous = self.previous_command(connection, key, 'cancel', payload)
            if previous:
                return {**previous['result'], 'replayed': True}
            row = connection.execute(sa.select(tasks).where(*self.scope(), tasks.c.id == task_id).with_for_update()).mappings().first()
            if row is None:
                raise NotFound('建设需求不存在或不可访问。')
            if row['status'] == 'cancelled' and row['revision'] == command.expected_revision + 1:
                return {'item': Requirement.model_validate(dict(row)).model_dump(mode='json'), 'replayed': True}
            if row['revision'] != command.expected_revision or row['status'] not in ('pending_capability', 'converted'):
                raise Conflict('需求状态已改变，请刷新后重试。')
            now = datetime.now(timezone.utc)
            changes = dict(status='cancelled', revision=row['revision'] + 1, updated_at=now,
                audit=[*row['audit'], {'action': 'cancel_requirement', 'actor': self.store.actor_id, 'trace_id': trace, 'at': now.isoformat()}])
            connection.execute(tasks.update().where(*self.scope(), tasks.c.id == task_id).values(**changes))
            result = {'item': self.view(connection, {**dict(row), **changes}), 'replayed': False}
            self.record_command(connection, key, 'cancel', payload, result)
            return result


router = APIRouter(prefix='/api/v1/assistant/requirements', tags=['应用建设需求'], dependencies=[Depends(admin)])


@router.get('')
def listing(request: Request, status: Literal['all', 'pending_capability', 'converted', 'cancelled'] = 'all',
            page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100)):
    return execute(lambda store: RequirementStore(store).listing(status, page, page_size), request.state.principal)


@router.post('')
def save(command: SaveRequirement, request: Request):
    return execute(lambda store: RequirementStore(store).save(command, request.state.trace_id), request.state.principal)


@router.get('/commands/{command_key}')
def command_result(command_key: str, request: Request):
    return execute(lambda store: RequirementStore(store).command_result(command_key), request.state.principal)


@router.get('/command-history')
def command_history(request: Request):
    return execute(lambda store: RequirementStore(store).history(), request.state.principal)


@router.get('/{task_id}')
def detail(task_id: str, request: Request):
    return execute(lambda store: RequirementStore(store).detail(task_id), request.state.principal)


@router.put('/{task_id}')
def edit(task_id: str, command: EditRequirement, request: Request):
    return execute(lambda store: RequirementStore(store).edit(task_id, command, request.state.trace_id), request.state.principal)


@router.post('/{task_id}/prepare')
def prepare(task_id: str, command: PrepareCreation, request: Request):
    return execute(lambda store: RequirementStore(store).prepare(task_id, command), request.state.principal)


@router.post('/confirmations/{command_id}/execute')
def confirm(command_id: str, request: Request):
    return execute(lambda store: RequirementStore(store).confirm(command_id, request.state.trace_id), request.state.principal)


@router.post('/{task_id}/cancel')
def cancel(task_id: str, command: CancelRequirement, request: Request):
    return execute(lambda store: RequirementStore(store).cancel(task_id, command, request.state.trace_id), request.state.principal)
