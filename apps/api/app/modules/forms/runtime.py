"""Tenant/application/release/owner-scoped form submissions, backed by shared publication snapshots."""
from datetime import datetime, timezone
from uuid import uuid4
import json

import sqlalchemy as sa
from pydantic import Field
from app.errors import APIError
from app.modules.config.store import ConfigStore, Conflict, NotFound, digest, releases, state, KEY
from app.modules.config.application_store import ApplicationStore
from app.modules.forms.adapter import FormAdapter, enabled
from app.modules.forms.schema import Strict, FormConfig, validate_values

metadata = sa.MetaData()
records = sa.Table('form_record', metadata,
    sa.Column('id', sa.String(36), primary_key=True),
    sa.Column('tenant_id', sa.String(64), nullable=False),
    sa.Column('application_id', sa.String(36), nullable=False),
    sa.Column('release_id', sa.String(36), nullable=False),
    sa.Column('owner_id', sa.String(64), nullable=False),
    sa.Column('values_json', sa.JSON, nullable=False),
    sa.Column('command_key', sa.String(80), nullable=False),
    sa.Column('request_hash', sa.String(64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint('tenant_id', 'application_id', 'owner_id', 'command_key', name='uq_form_submission'))


class SubmitForm(Strict):
    release_id: str = Field(min_length=36, max_length=36)
    values: dict = Field(max_length=30)
    idempotency_key: str = Field(min_length=16, max_length=80)
    draft_id: str | None = Field(default=None, min_length=36, max_length=36)
    draft_revision: int | None = Field(default=None, ge=1)


def form_store(store, application_id, expected_type=None):
    info = ApplicationStore(store).get(application_id)
    if expected_type is not None and info['type_id'] != expected_type:
        raise APIError(422, 'APPLICATION_TYPE_MISMATCH', '运行接口与应用类型不一致。')
    if info['type_id'] == 'platform.form':
        adapter = FormAdapter()
    elif info['type_id'] == 'platform.leave-approval':
        from app.modules.approvals.adapter import ApprovalAdapter
        adapter = ApprovalAdapter()
    else:
        raise APIError(422, 'APPLICATION_TYPE_MISMATCH', '该接口仅用于表单或审批类型。')
    return ConfigStore(store.engine, store.tenant_id, store.actor_id, application_id, adapter=adapter)


class FormRecords:
    type_id = 'platform.form'

    def __init__(self, store, principal, application_id):
        self.store = form_store(store, application_id, expected_type=self.type_id)
        self.principal = principal

    def scope(self, own=True):
        return [records.c.tenant_id == self.store.tenant_id, records.c.application_id == self.store.application_id,
            *([records.c.owner_id == self.principal.subject] if own else [])]

    def authorized(self):
        current = self.store.current()
        if not current['release_id']:
            raise APIError(404, 'APPLICATION_UNPUBLISHED', '表单应用尚未发布。')
        config = self.store.adapter.model.model_validate(current['content'])
        if not set(config.application.visible_roles).intersection(self.principal.roles):
            raise APIError(404, 'APPLICATION_NOT_FOUND', '应用不可访问。')
        return current

    def definition(self):
        current = self.authorized()
        from app.modules.config.lifecycle import status
        with self.store.engine.connect() as connection:
            active = status(connection, self.store.tenant_id, self.store.application_id)['status'] == 'active'
        return {'application_id': self.store.application_id, 'type_id': self.type_id,
            'release_id': current['release_id'], 'content': current['content'], 'accepting_submissions': active and self.accepting()}

    def accepting(self):
        return enabled()

    def after_submit(self, connection, record_id, release_id, config, values):
        pass

    def submit(self, command, trace_id):
        current = self.authorized()
        raw = command.model_dump(exclude={'idempotency_key'}, exclude_none=True)
        if (command.draft_id is None) != (command.draft_revision is None):
            raise APIError(422, 'DRAFT_REFERENCE_INVALID', '草稿引用和修订必须同时提供。')
        if len(json.dumps(raw, ensure_ascii=False).encode()) > 64 * 1024:
            raise APIError(422, 'FORM_VALUES_TOO_LARGE', '单次登记内容超过64KiB。')
        fingerprint = digest(raw)
        with self.store.engine.begin() as connection:
            # Lock the app state: publication and submissions agree on a single current release.
            self.store._state(connection, lock=True)
            previous = connection.execute(sa.select(records).where(*self.scope(), records.c.command_key == command.idempotency_key)).mappings().first()
            if previous:
                if previous['request_hash'] != fingerprint:
                    raise Conflict('同一提交请求号不能用于不同内容。')
                return {'record_id': previous['id'], 'release_id': previous['release_id'], 'replayed': True}
            if not self.accepting():
                raise Conflict('登记提交已暂停，仍可查看本人已有记录。')
            from app.modules.config.lifecycle import assert_active
            assert_active(connection, self.store.tenant_id, self.store.application_id)
            actual = connection.scalar(sa.select(state.c.current_release_id).where(state.c.tenant_id == self.store.tenant_id,
                state.c.application_id == self.store.application_id, state.c.key == KEY))
            if command.release_id != current['release_id'] or actual != command.release_id:
                raise Conflict('表单已更新，请保留输入并重新加载，核对新字段后再提交。')
            config = self.store.adapter.model.model_validate(current['content'])
            values = self.store.adapter.validate_values(config, command.values)
            record_id = str(uuid4())
            connection.execute(records.insert().values(id=record_id, tenant_id=self.store.tenant_id,
                application_id=self.store.application_id, release_id=command.release_id, owner_id=self.principal.subject,
                values_json=values, command_key=command.idempotency_key, request_hash=fingerprint, created_at=datetime.now(timezone.utc)))
            self.after_submit(connection, record_id, command.release_id, config, values)
            from app.modules.forms.drafts import BusinessDrafts
            BusinessDrafts(self).submitted(connection, command, record_id)
            self.store._audit(connection, 'submit_form', 0, trace_id, command.release_id, after={'record_id': record_id})
            return {'record_id': record_id, 'release_id': command.release_id, 'replayed': False}

    def listing(self, page=1, all_records=False):
        if all_records and 'config_admin' not in self.principal.roles:
            raise APIError(403, 'FORM_RECORDS_FORBIDDEN', '查看全部登记记录需要管理员权限。')
        # Admin management may inspect records even after use-role removal; users require current app access.
        if not all_records:
            self.authorized()
        with self.store.engine.connect() as connection:
            filters = self.scope(own=not all_records)
            total = connection.scalar(sa.select(sa.func.count()).select_from(records).where(*filters))
            rows = connection.execute(sa.select(records).where(*filters).order_by(records.c.created_at.desc(), records.c.id)
                .offset((page - 1) * 20).limit(20)).mappings().all()
            return {'total': total, 'page': page, 'page_size': 20, 'items': [
                {key: row[key] for key in ('id', 'release_id', 'created_at', 'owner_id')} for row in rows]}

    def by_command(self, key):
        self.authorized()
        with self.store.engine.connect() as connection:
            row = connection.execute(sa.select(records.c.id).where(*self.scope(), records.c.command_key == key)).scalar_one_or_none()
            return {'found': row is not None, 'record_id': row}

    def detail(self, record_id, all_records=False):
        if all_records and 'config_admin' not in self.principal.roles:
            raise APIError(403, 'FORM_RECORDS_FORBIDDEN', '需要管理员权限。')
        if not all_records:
            self.authorized()
        with self.store.engine.connect() as connection:
            row = connection.execute(sa.select(records).where(*self.scope(own=not all_records), records.c.id == record_id)).mappings().first()
            if row is None:
                raise NotFound('登记记录不存在或不可访问。')
            snapshot = connection.execute(sa.select(releases).where(releases.c.tenant_id == row['tenant_id'],
                releases.c.application_id == row['application_id'], releases.c.id == row['release_id'], releases.c.key == KEY)).mappings().one()
            self.store.validate_runtime(snapshot['runtime_binding'])
            return {'id': row['id'], 'release_id': row['release_id'], 'owner_id': row['owner_id'],
                'values': row['values_json'], 'created_at': row['created_at'], 'content': snapshot['content']}
