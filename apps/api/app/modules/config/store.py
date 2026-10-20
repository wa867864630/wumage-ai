import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4, uuid5, NAMESPACE_URL

import sqlalchemy as sa

from app.modules.config.schema import DisplayConfig, default_config

LEGACY_APPLICATION_ID = '00000000-0000-4000-8000-000000000001'

metadata = sa.MetaData()
state = sa.Table('config_state', metadata,
    sa.Column('tenant_id', sa.String(64), primary_key=True),
    sa.Column('application_id', sa.String(36), primary_key=True, server_default=LEGACY_APPLICATION_ID),
    sa.Column('key', sa.String(64), primary_key=True),
    sa.Column('revision', sa.Integer, nullable=False),
    sa.Column('draft', sa.JSON, nullable=False),
    sa.Column('current_release_id', sa.String(36)))
releases = sa.Table('config_release', metadata,
    sa.Column('id', sa.String(36), primary_key=True),
    sa.Column('tenant_id', sa.String(64), nullable=False),
    sa.Column('application_id', sa.String(36), nullable=False, server_default=LEGACY_APPLICATION_ID),
    sa.Column('key', sa.String(64), nullable=False),
    sa.Column('content', sa.JSON, nullable=False),
    sa.Column('content_hash', sa.String(64), nullable=False),
    sa.Column('runtime_binding', sa.JSON),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('actor_id', sa.String(64), nullable=False),
    sa.Column('operation', sa.String(16), nullable=False),
    sa.Column('source_release_id', sa.String(36)),
    sa.Column('idempotency_key', sa.String(80), nullable=False),
    sa.Column('request_hash', sa.String(64), nullable=False),
    sa.UniqueConstraint('tenant_id', 'application_id', 'key', 'idempotency_key', name='uq_release_request'))
audit = sa.Table('config_audit', metadata,
    sa.Column('id', sa.String(36), primary_key=True),
    sa.Column('tenant_id', sa.String(64), nullable=False),
    sa.Column('application_id', sa.String(36), nullable=False, server_default=LEGACY_APPLICATION_ID),
    sa.Column('actor_id', sa.String(64), nullable=False),
    sa.Column('action', sa.String(32), nullable=False),
    sa.Column('release_id', sa.String(36)),
    sa.Column('revision', sa.Integer, nullable=False),
    sa.Column('trace_id', sa.String(64), nullable=False),
    sa.Column('before_json', sa.JSON),
    sa.Column('after_json', sa.JSON),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))

outbox = sa.Table('outbox_event', metadata,
    sa.Column('id', sa.String(36), primary_key=True),
    sa.Column('tenant_id', sa.String(64), nullable=False),
    sa.Column('application_id', sa.String(36), nullable=False, server_default=LEGACY_APPLICATION_ID),
    sa.Column('release_id', sa.String(36), nullable=False),
    sa.Column('state', sa.String(16), nullable=False),
    sa.Column('attempts', sa.Integer, nullable=False),
    sa.Column('available_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('lease_until', sa.DateTime(timezone=True)),
    sa.Column('lease_owner', sa.String(36)),
    sa.Column('last_error', sa.String(80)),
    sa.Column('processed_at', sa.DateTime(timezone=True)))

TENANT = 'local-demo'
KEY = 'course-display'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


class ConfigStore:
    def __init__(self, engine, tenant_id=TENANT, actor_id='local-admin', application_id=LEGACY_APPLICATION_ID):
        self.engine = engine
        self.tenant_id = tenant_id
        self.actor_id = actor_id
        self.application_id = application_id

    def _state(self, connection, lock=False):
        query = sa.select(state).where(state.c.tenant_id == self.tenant_id, state.c.application_id == self.application_id, state.c.key == KEY)
        if lock:
            query = query.with_for_update()
        row = connection.execute(query).mappings().first()
        if row is None:
            raise NotFound('当前组织尚未初始化配置，请联系管理员。')
        return row

    def current(self):
        # One transaction captures a release ID; immutable contents cannot change underneath it.
        with self.engine.connect() as connection:
            from app.modules.config.cache import cache_enabled, read_cache, write_cache
            use_cache = cache_enabled()
            columns = [state.c.current_release_id, releases.c.content_hash, releases.c.runtime_binding]
            if not use_cache:
                columns.append(releases.c.content)
            row = connection.execute(sa.select(*columns).select_from(state.outerjoin(releases,
                sa.and_(releases.c.id == state.c.current_release_id, releases.c.tenant_id == state.c.tenant_id, releases.c.application_id == state.c.application_id,
                        releases.c.key == state.c.key))).where(
                    state.c.tenant_id == self.tenant_id, state.c.application_id == self.application_id, state.c.key == KEY)).mappings().first()
            if row is None:
                raise NotFound('当前组织尚未初始化配置，请联系管理员。')
            release_id = row['current_release_id']
            content = default_config().model_dump()
            if release_id:
                self.validate_runtime(row['runtime_binding'])
                if not row['content_hash']:
                    raise RuntimeError('Published release is missing')
                content = read_cache(self.tenant_id, release_id, row['content_hash'], self.application_id) if use_cache else row['content']
                if content is None:
                    content = connection.execute(sa.select(releases.c.content).where(
                        releases.c.id == release_id, releases.c.tenant_id == self.tenant_id, releases.c.application_id == self.application_id,
                        releases.c.key == KEY)).scalar_one()
                    write_cache(self.tenant_id, release_id, content, self.application_id)
            return {'release_id': release_id,
                    'content': DisplayConfig.model_validate(content).model_dump(), 'source': self.engine.dialect.name}

    def validate_runtime(self, binding):
        from app.modules.config.type_registry import require_type, BINDING_ID
        if binding is None and self.application_id == LEGACY_APPLICATION_ID:
            return  # Existing immutable course snapshots keep their original bytes.
        if not binding or binding.get('application_id') != self.application_id or binding.get('binding_id') != BINDING_ID:
            raise Conflict('发布版本的应用或数据能力绑定无效。')
        if not isinstance(binding.get('type_digest'), str) or len(binding['type_digest']) != 64:
            raise Conflict('发布版本缺少完整类型摘要。')
        require_type(binding.get('type_id'), binding.get('type_version'), binding.get('type_digest'))
        if binding.get('capability_contract') != 'course.schedule.read@1':
            raise Conflict('发布版本所需的课程能力版本不可用。')

    def draft(self):
        with self.engine.connect() as connection:
            row = self._state(connection)
            return {'revision': row['revision'], 'current_release_id': row['current_release_id'],
                    'content': DisplayConfig.model_validate(row['draft']).model_dump()}

    def _audit(self, connection, action, revision, trace_id, release_id=None, before=None, after=None, audit_id=None):
        connection.execute(audit.insert().values(id=audit_id or str(uuid4()), tenant_id=self.tenant_id, application_id=self.application_id,
            actor_id=self.actor_id, action=action, release_id=release_id, revision=revision,
            trace_id=trace_id, created_at=datetime.now(timezone.utc), before_json=before, after_json=after))

    def save(self, command, trace_id):
        with self.engine.begin() as connection:
            row = self._state(connection, lock=True)
            if row['revision'] != command.expected_revision:
                raise Conflict('草稿已更新，请重新加载后编辑。')
            revision = row['revision'] + 1
            content = command.content.model_dump()
            connection.execute(state.update().where(state.c.tenant_id == self.tenant_id, state.c.application_id == self.application_id, state.c.key == KEY)
                               .values(draft=content, revision=revision))
            self._audit(connection, 'save_draft', revision, trace_id, before=row['draft'], after=content)
            return {'revision': revision, 'current_release_id': row['current_release_id'], 'content': content}

    def accept_course_proposal(self, command, trace_id):
        """Reuse durable audit + draft lock for atomic, tenant/actor-scoped acceptance."""
        from app.modules.config.course_design import evaluate
        from app.errors import APIError
        if self.application_id != LEGACY_APPLICATION_ID:
            raise Conflict('Legacy course proposals only support the original application.')
        fingerprint = digest(command.proposal.model_dump())
        command_id = str(uuid5(NAMESPACE_URL, json.dumps(
            [self.tenant_id, self.actor_id, KEY, 'accept_course_proposal', command.idempotency_key])))
        with self.engine.begin() as connection:
            row = self._state(connection, lock=True)
            previous = connection.execute(sa.select(audit.c.after_json).where(
                audit.c.id == command_id, audit.c.tenant_id == self.tenant_id, audit.c.application_id == self.application_id,
                audit.c.actor_id == self.actor_id, audit.c.action == 'accept_course_proposal')).scalar_one_or_none()
            if previous is not None:
                if previous['request_hash'] != fingerprint:
                    raise Conflict('同一接受请求号不能用于不同提案。')
                return {**previous['result'], 'replayed': True}
            draft = {'revision': row['revision'], 'content': DisplayConfig.model_validate(row['draft']).model_dump()}
            result = evaluate(command.proposal, draft)
            if not result['can_accept']:
                raise APIError(422, 'PROPOSAL_BLOCKED', '提案无实际变化或未通过课表回归，不能接受。')
            revision = row['revision'] + 1
            content = result['candidate']
            connection.execute(state.update().where(state.c.tenant_id == self.tenant_id, state.c.application_id == self.application_id, state.c.key == KEY)
                               .values(draft=content, revision=revision))
            response = {'revision': revision, 'content_hash': digest(content),
                        'current_release_id': row['current_release_id'], 'replayed': False}
            self._audit(connection, 'accept_course_proposal', revision, trace_id, before=row['draft'],
                after={'content': content, 'request_hash': fingerprint, 'result': response}, audit_id=command_id)
            return response

    def publish(self, command, trace_id, rollback=False):
        fingerprint = digest({'operation': 'rollback' if rollback else 'publish',
                              **command.model_dump(exclude={'idempotency_key'})})
        with self.engine.begin() as connection:
            row = self._state(connection, lock=True)
            previous = connection.execute(sa.select(releases).where(
                releases.c.tenant_id == self.tenant_id, releases.c.application_id == self.application_id, releases.c.key == KEY,
                releases.c.idempotency_key == command.idempotency_key)).mappings().first()
            if previous:
                if previous['request_hash'] != fingerprint:
                    raise Conflict('同一幂等键不能用于不同请求。')
                return {'release_id': previous['id'], 'replayed': True}
            if (row['revision'] != command.expected_revision or
                    row['current_release_id'] != command.expected_release_id):
                raise Conflict('草稿或发布版本已变化，请重新加载。')
            content = row['draft']
            if rollback:
                target = connection.execute(sa.select(releases.c.content, releases.c.runtime_binding).where(
                    releases.c.id == command.target_release_id, releases.c.tenant_id == self.tenant_id, releases.c.application_id == self.application_id,
                    releases.c.key == KEY)).mappings().first()
                if target is None:
                    raise NotFound('回滚目标不存在。')
                self.validate_runtime(target['runtime_binding'])
                content = target['content']
            content = DisplayConfig.model_validate(content).model_dump()
            before = default_config().model_dump()
            if row['current_release_id']:
                before = connection.execute(sa.select(releases.c.content).where(
                    releases.c.id == row['current_release_id'], releases.c.tenant_id == self.tenant_id, releases.c.application_id == self.application_id,
                    releases.c.key == KEY)).scalar_one()
            from app.modules.config.review import review
            # Regress against immutable built-in behavior, so an older custom rule can be removed or rolled back.
            if not review(default_config(), DisplayConfig.model_validate(content))['can_publish']:
                raise Conflict('固定问句回归失败，请修复规则后发布。')
            release_id = str(uuid4())
            from app.modules.config.type_registry import course_type, BINDING_ID
            installed = course_type()
            binding = {'application_id': self.application_id, 'type_id': installed['type_id'],
                'type_version': installed['type_version'], 'type_digest': installed['type_digest'],
                'binding_id': BINDING_ID, 'capability_contract': 'course.schedule.read@1'}
            connection.execute(releases.insert().values(id=release_id, tenant_id=self.tenant_id, application_id=self.application_id, key=KEY,
                content=content, content_hash=digest(content), runtime_binding=binding, created_at=datetime.now(timezone.utc),
                actor_id=self.actor_id, operation='rollback' if rollback else 'publish',
                source_release_id=command.target_release_id if rollback else None,
                idempotency_key=command.idempotency_key, request_hash=fingerprint))
            connection.execute(state.update().where(state.c.tenant_id == self.tenant_id, state.c.application_id == self.application_id, state.c.key == KEY)
                               .values(current_release_id=release_id, revision=row['revision'] + 1))
            self._audit(connection, 'rollback' if rollback else 'publish', row['revision'] + 1,
                        trace_id, release_id, before=before, after=content)
            connection.execute(outbox.insert().values(id=str(uuid4()), tenant_id=self.tenant_id, application_id=self.application_id,
                release_id=release_id, state='pending', attempts=0, available_at=datetime.now(timezone.utc)))
            return {'release_id': release_id, 'replayed': False}

    def history(self):
        with self.engine.connect() as connection:
            rows = connection.execute(sa.select(releases.c.id, releases.c.created_at,
                releases.c.operation, releases.c.source_release_id, releases.c.content_hash)
                .where(releases.c.tenant_id == self.tenant_id, releases.c.application_id == self.application_id, releases.c.key == KEY)
                .order_by(releases.c.created_at.desc(), releases.c.id.desc()).limit(20)).mappings()
            return [dict(row) for row in rows]

    def audit_history(self):
        with self.engine.connect() as connection:
            rows = connection.execute(sa.select(audit).where(audit.c.tenant_id == self.tenant_id, audit.c.application_id == self.application_id)
                .order_by(audit.c.created_at.desc(), audit.c.id.desc()).limit(50)).mappings()
            return [dict(row) for row in rows]

    def delivery_status(self):
        with self.engine.connect() as connection:
            rows = connection.execute(sa.select(outbox.c.state, sa.func.count().label('count'))
                .where(outbox.c.tenant_id == self.tenant_id, outbox.c.application_id == self.application_id).group_by(outbox.c.state)).mappings()
            counts = {row['state']: row['count'] for row in rows}
            failed = connection.execute(sa.select(outbox.c.id, outbox.c.release_id, outbox.c.attempts,
                outbox.c.last_error).where(outbox.c.tenant_id == self.tenant_id, outbox.c.application_id == self.application_id,
                outbox.c.state == 'failed').limit(20)).mappings()
            return {'counts': counts, 'failed': [dict(row) for row in failed]}

    def retry_event(self, event_id, trace_id):
        with self.engine.begin() as connection:
            row = connection.execute(sa.select(outbox).where(outbox.c.id == event_id,
                outbox.c.tenant_id == self.tenant_id, outbox.c.application_id == self.application_id).with_for_update()).mappings().first()
            if not row:
                raise NotFound('事件不存在。')
            if row['state'] != 'failed':
                raise Conflict('仅允许重试达到上限的失败事件。')
            connection.execute(outbox.update().where(outbox.c.id == event_id,
                outbox.c.tenant_id == self.tenant_id, outbox.c.application_id == self.application_id).values(state='pending', attempts=0,
                available_at=datetime.now(timezone.utc), lease_until=None, lease_owner=None))
            self._audit(connection, 'retry_outbox', 0, trace_id, row['release_id'])
            return {'state': 'pending'}
