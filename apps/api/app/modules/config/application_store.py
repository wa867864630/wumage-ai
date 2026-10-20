"""Application identity; configuration uses the existing release/state/audit store."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.errors import APIError
from app.modules.config.schema import Column, IntentRule, DisplayConfig, SaveDraft, default_config
from app.modules.config.store import ConfigStore, Conflict, KEY, LEGACY_APPLICATION_ID, metadata, state, releases, digest
from app.modules.config.type_registry import BINDING_ID, TYPE_ID, TYPE_VERSION, require_type

applications = sa.Table('application', metadata,
    sa.Column('tenant_id', sa.String(64), primary_key=True),
    sa.Column('application_id', sa.String(36), primary_key=True),
    sa.Column('type_id', sa.String(96), nullable=False),
    sa.Column('type_version', sa.String(32), nullable=False),
    sa.Column('type_digest', sa.String(64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_by', sa.String(64), nullable=False),
    sa.Column('creation_key', sa.String(80), nullable=False),
    sa.Column('creation_hash', sa.String(64), nullable=False),
    sa.UniqueConstraint('tenant_id', 'creation_key', name='uq_application_creation'))


class CreateApplication(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    type_id: Literal['campus.course-query']
    type_version: Literal['1.0.0']
    name: str = Field(min_length=1, max_length=30)
    template_id: Literal['course-standard', 'course-compact'] = 'course-standard'
    idempotency_key: str = Field(min_length=16, max_length=80)


class CoursePayload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['1']
    columns: list[Column]
    rules: list[IntentRule]


class Metadata(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=30)
    description: str = Field(min_length=1, max_length=160)
    category: Literal['学习服务', '校园事务', '校园生活']


class Access(BaseModel):
    model_config = ConfigDict(extra='forbid')
    use_roles: list[Literal['student', 'teacher', 'config_admin']] = Field(min_length=1, max_length=3)


class Bindings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schedule_reader: Literal['00000000-0000-4000-8000-000000000002']


class CourseEnvelope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    envelope_version: Literal['1.0.0']
    application_id: UUID
    type_id: Literal['campus.course-query']
    type_version: Literal['1.0.0']
    metadata: Metadata
    access: Access
    bindings: Bindings
    config: CoursePayload

    @model_validator(mode='after')
    def validate_course_semantics(self):
        self.display()
        return self

    def display(self):
        return DisplayConfig.model_validate({**self.config.model_dump(), 'application': {
            **self.metadata.model_dump(), 'visible_roles': self.access.use_roles}})


class SaveApplication(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0)
    content: CourseEnvelope


class ApplicationStore:
    def __init__(self, store):
        self.store = store

    def scoped(self, application_id=None):
        result = [applications.c.tenant_id == self.store.tenant_id]
        if application_id is not None:
            result.append(applications.c.application_id == application_id)
        return result

    def get(self, application_id):
        with self.store.engine.connect() as connection:
            row = connection.execute(sa.select(applications).where(*self.scoped(application_id))).mappings().first()
        if row is None:
            raise APIError(404, 'APPLICATION_NOT_FOUND', '应用不存在。')
        legacy = row['application_id'] == LEGACY_APPLICATION_ID and row['type_digest'] == 'legacy'
        require_type(row['type_id'], row['type_version'], None if legacy else row['type_digest'])
        return dict(row)

    def config_store(self, application_id):
        self.get(application_id)
        return ConfigStore(self.store.engine, self.store.tenant_id, self.store.actor_id, application_id)

    def listing(self):
        with self.store.engine.connect() as connection:
            rows = connection.execute(sa.select(applications, state.c.draft, state.c.revision, state.c.current_release_id)
                .join(state, sa.and_(state.c.tenant_id == applications.c.tenant_id,
                    state.c.application_id == applications.c.application_id, state.c.key == KEY))
                .where(*self.scoped()).order_by(applications.c.created_at, applications.c.application_id).limit(101)).mappings().all()
        return {'items': [{'application_id': row['application_id'], 'type_id': row['type_id'],
            'type_version': row['type_version'], 'name': DisplayConfig.model_validate(row['draft']).application.name,
            'revision': row['revision'], 'current_release_id': row['current_release_id'],
            'legacy': row['application_id'] == LEGACY_APPLICATION_ID} for row in rows[:100]], 'limit': 100}

    def public_catalog(self, principal):
        with self.store.engine.connect() as connection:
            rows = connection.execute(sa.select(applications, releases.c.content, releases.c.runtime_binding,
                    releases.c.id.label('release_id')).join(state, sa.and_(state.c.tenant_id == applications.c.tenant_id,
                    state.c.application_id == applications.c.application_id, state.c.key == KEY))
                .join(releases, sa.and_(releases.c.id == state.c.current_release_id,
                    releases.c.tenant_id == state.c.tenant_id, releases.c.application_id == state.c.application_id,
                    releases.c.key == state.c.key)).where(*self.scoped(),
                    applications.c.application_id != LEGACY_APPLICATION_ID)
                .order_by(applications.c.created_at, applications.c.application_id).limit(100)).mappings().all()
        result = []
        for row in rows:
            info = DisplayConfig.model_validate(row['content']).application
            if not set(info.visible_roles).intersection(principal.roles):
                continue
            require_type(row['type_id'], row['type_version'], row['type_digest'])
            ConfigStore(self.store.engine, self.store.tenant_id, self.store.actor_id,
                row['application_id']).validate_runtime(row['runtime_binding'])
            result.append({'id': row['application_id'], 'application_id': row['application_id'],
                'icon': '▦', 'name': info.name, 'category': info.category, 'desc': info.description,
                'ready': True, 'type_id': row['type_id'], 'release_id': row['release_id']})
        return result

    def create(self, command, trace_id):
        installed = require_type(command.type_id, command.type_version)
        payload = command.model_dump(exclude={'idempotency_key'})
        if command.template_id == 'course-standard':
            payload.pop('template_id')  # Preserve first-version creation command fingerprints.
        fingerprint = digest(payload)
        # Serialize creates with the tenant's original config row; normal saves lock only their own app.
        with self.store.engine.begin() as connection:
            self.store._state(connection, lock=True)
            previous = connection.execute(sa.select(applications).where(*self.scoped(),
                applications.c.creation_key == command.idempotency_key)).mappings().first()
            if previous:
                if previous['creation_hash'] != fingerprint:
                    raise Conflict('同一创建请求号不能用于不同应用。')
                return {'application_id': previous['application_id'], 'replayed': True}
            count = connection.scalar(sa.select(sa.func.count()).select_from(applications).where(*self.scoped()))
            if count >= 100:
                raise Conflict('当前每组织最多100个应用。')
            application_id = str(uuid4())
            content = default_config().model_dump()
            content['application']['name'] = command.name
            if command.template_id == 'course-compact':
                for column in content['columns']:
                    column['visible'] = column['field'] != 'teacher'
            connection.execute(applications.insert().values(tenant_id=self.store.tenant_id,
                application_id=application_id, type_id=command.type_id, type_version=command.type_version,
                type_digest=installed['type_digest'], created_at=datetime.now(timezone.utc),
                created_by=self.store.actor_id, creation_key=command.idempotency_key, creation_hash=fingerprint))
            connection.execute(state.insert().values(tenant_id=self.store.tenant_id, application_id=application_id,
                key=KEY, revision=0, draft=content))
            target = ConfigStore(self.store.engine, self.store.tenant_id, self.store.actor_id, application_id)
            target._audit(connection, 'create_application', 0, trace_id, after={'type_id': command.type_id})
            return {'application_id': application_id, 'replayed': False}

    def draft(self, application_id):
        result = self.config_store(application_id).draft()
        display = result['content']
        info = display['application']
        envelope = {'envelope_version': '1.0.0', 'application_id': application_id,
            'type_id': TYPE_ID, 'type_version': TYPE_VERSION,
            'metadata': {key: info[key] for key in ('name', 'description', 'category')},
            'access': {'use_roles': info['visible_roles']}, 'bindings': {'schedule_reader': BINDING_ID},
            'config': {key: display[key] for key in ('schema_version', 'columns', 'rules')}}
        return {**result, 'content': envelope}

    def save(self, application_id, command, trace_id):
        if str(command.content.application_id) != application_id:
            raise APIError(409, 'APPLICATION_SCOPE_MISMATCH', '配置不属于当前应用。')
        self.config_store(application_id).save(SaveDraft(expected_revision=command.expected_revision,
            content=command.content.display()), trace_id)
        return self.draft(application_id)
