"""Single-level approval state machine; applicant record, task and audit share a transaction."""
from datetime import date, datetime, timezone
import json
from typing import Literal
from uuid import uuid5, NAMESPACE_URL

import sqlalchemy as sa
from pydantic import Field, model_validator
from app.errors import APIError
from app.modules.config.store import Conflict, NotFound, digest, audit, releases, KEY
from app.modules.forms.schema import Strict
from app.modules.forms.runtime import FormRecords, records
from app.modules.approvals.adapter import enabled

metadata = sa.MetaData()
instances = sa.Table('approval_instance', metadata,
    sa.Column('id', sa.String(36), primary_key=True),
    sa.Column('tenant_id', sa.String(64), nullable=False),
    sa.Column('application_id', sa.String(36), nullable=False),
    sa.Column('release_id', sa.String(36), nullable=False),
    sa.Column('applicant_id', sa.String(64), nullable=False),
    sa.Column('approver_id', sa.String(64), nullable=False),
    sa.Column('status', sa.String(20), nullable=False),
    sa.Column('revision', sa.Integer, nullable=False),
    sa.Column('last_reason', sa.String(1000)),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))


class Transition(Strict):
    action: Literal['approved', 'rejected', 'withdrawn', 'reassign_to_me']
    expected_revision: int = Field(ge=1)
    reason: str = Field(default='', max_length=1000)
    idempotency_key: str = Field(min_length=16, max_length=80)

    @model_validator(mode='after')
    def rejection_reason(self):
        if self.action in ('rejected', 'reassign_to_me') and not self.reason:
            raise ValueError('拒绝或接管申请时请填写原因')
        return self


class ApprovalRecords(FormRecords):
    type_id = 'platform.leave-approval'

    def accepting(self):
        return enabled()

    def after_submit(self, connection, record_id, release_id, config, values):
        binding = connection.scalar(sa.select(releases.c.runtime_binding).where(releases.c.tenant_id == self.store.tenant_id,
            releases.c.application_id == self.store.application_id, releases.c.id == release_id, releases.c.key == KEY))
        self.store.validate_runtime(binding)
        if binding['approver_id'] == self.principal.subject:
            raise APIError(422, 'SELF_APPROVAL_FORBIDDEN', '审批人是当前身份，不能申请后自行审批。请用申请人身份提交。')
        now = datetime.now(timezone.utc)
        connection.execute(instances.insert().values(id=record_id, tenant_id=self.store.tenant_id,
            application_id=self.store.application_id, release_id=release_id, applicant_id=self.principal.subject,
            approver_id=binding['approver_id'], status='pending', revision=1, last_reason=None, created_at=now, updated_at=now))

    def task_scope(self):
        return [instances.c.tenant_id == self.store.tenant_id, instances.c.application_id == self.store.application_id]

    def work_items(self, scope='mine', page=1, status='all'):
        if scope == 'mine':
            self.authorized()
            filters = [*self.task_scope(), instances.c.applicant_id == self.principal.subject]
        else:
            if 'config_admin' not in self.principal.roles:
                raise APIError(403, 'APPROVAL_FORBIDDEN', '审批工作需要当前有效的管理员权限。')
            filters = [*self.task_scope()] + ([instances.c.approver_id == self.principal.subject] if scope == 'assigned' else [])
        if status != 'all':
            filters.append(instances.c.status == status)
        with self.store.engine.connect() as connection:
            total = connection.scalar(sa.select(sa.func.count()).select_from(instances).where(*filters))
            rows = connection.execute(sa.select(instances).where(*filters).order_by(instances.c.updated_at.desc(), instances.c.id)
                .offset((page - 1) * 20).limit(20)).mappings().all()
            return {'items': [dict(row) for row in rows], 'total': total, 'page': page, 'page_size': 20}

    def workflow_detail(self, record_id):
        with self.store.engine.connect() as connection:
            row = connection.execute(sa.select(instances).where(*self.task_scope(), instances.c.id == record_id)).mappings().first()
            if row is None or row['applicant_id'] != self.principal.subject and 'config_admin' not in self.principal.roles:
                raise NotFound('审批记录不存在或不可访问。')
            is_admin = 'config_admin' in self.principal.roles
            detail = self.detail(record_id, all_records=is_admin)
            events = connection.execute(sa.select(audit.c.actor_id, audit.c.created_at, audit.c.after_json).where(
                audit.c.tenant_id == self.store.tenant_id, audit.c.application_id == self.store.application_id,
                audit.c.action == 'approval_transition', audit.c.after_json['record_id'].as_string() == record_id)
                .order_by(audit.c.created_at.desc()).limit(100)).mappings().all()
            detail['workflow'] = dict(row)
            detail['events'] = [{'actor_id': event['actor_id'], 'created_at': event['created_at'],
                'action': event['after_json']['action'], 'reason': event['after_json'].get('reason', '')}
                for event in events]
            detail['events_limit'] = 100
            detail['duration_days'] = (date.fromisoformat(detail['values']['end_date']) - date.fromisoformat(detail['values']['start_date'])).days + 1
            return detail

    def transition(self, record_id, command, trace_id):
        if command.action != 'withdrawn' and 'config_admin' not in self.principal.roles:
            raise APIError(403, 'APPROVAL_FORBIDDEN', '当前身份无审批权限。')
        if command.action == 'withdrawn':
            self.authorized()
        fingerprint = digest({'record_id': record_id, **command.model_dump(exclude={'idempotency_key'})})
        command_id = str(uuid5(NAMESPACE_URL, json.dumps([self.store.tenant_id, self.store.application_id,
            self.principal.subject, 'approval_transition', command.idempotency_key])))
        with self.store.engine.begin() as connection:
            row = connection.execute(sa.select(instances).where(*self.task_scope(), instances.c.id == record_id).with_for_update()).mappings().first()
            if row is None:
                raise NotFound('审批记录不存在或不可访问。')
            if command.action == 'withdrawn' and row['applicant_id'] != self.principal.subject:
                raise NotFound('审批记录不存在或不可访问。')
            if command.action in ('approved', 'rejected') and row['approver_id'] != self.principal.subject:
                raise APIError(403, 'NOT_ASSIGNED_APPROVER', '该任务未分配给当前身份。')
            if command.action != 'withdrawn' and row['applicant_id'] == self.principal.subject:
                raise APIError(403, 'SELF_APPROVAL_FORBIDDEN', '申请人不能审批或接管本人申请。')
            previous = connection.scalar(sa.select(audit.c.after_json).where(audit.c.id == command_id,
                audit.c.tenant_id == self.store.tenant_id, audit.c.application_id == self.store.application_id,
                audit.c.actor_id == self.principal.subject, audit.c.action == 'approval_transition'))
            if previous:
                if previous['request_hash'] != fingerprint:
                    raise Conflict('同一操作请求号不能用于不同审批动作。')
                return {**previous['result'], 'replayed': True}
            if row['status'] != 'pending' or row['revision'] != command.expected_revision:
                raise Conflict('任务已被处理或已更新，请刷新核对；未重复执行。')
            if command.action == 'reassign_to_me' and row['approver_id'] == self.principal.subject:
                raise Conflict('任务已分配给当前身份，无需重复接管。')
            snapshot = connection.scalar(sa.select(releases.c.runtime_binding).where(releases.c.tenant_id == row['tenant_id'],
                releases.c.application_id == row['application_id'], releases.c.id == row['release_id'], releases.c.key == KEY))
            self.store.validate_runtime(snapshot)
            now = datetime.now(timezone.utc)
            changes = {'revision': row['revision'] + 1, 'updated_at': now, 'last_reason': command.reason or None,
                **({'approver_id': self.principal.subject} if command.action == 'reassign_to_me' else {'status': command.action})}
            connection.execute(instances.update().where(*self.task_scope(), instances.c.id == record_id).values(**changes))
            result = {'id': record_id, 'status': changes.get('status', row['status']), 'revision': changes['revision'],
                'approver_id': changes.get('approver_id', row['approver_id']), 'replayed': False}
            self.store._audit(connection, 'approval_transition', changes['revision'], trace_id, row['release_id'],
                before={key: row[key] for key in ('status', 'revision', 'approver_id')},
                after={'record_id': record_id, 'action': command.action, 'reason': command.reason,
                    'request_hash': fingerprint, 'result': result}, audit_id=command_id)
            return result
