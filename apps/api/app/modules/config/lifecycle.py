"""Lifecycle is independent of config publication; rollback cannot reactivate an app."""
from datetime import datetime, timezone
import json
from typing import Literal
from uuid import NAMESPACE_URL, uuid5, UUID
import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from app.errors import APIError
from app.modules.config.routes import admin, execute
from app.modules.config.store import Conflict, NotFound, audit, digest, state, KEY

metadata = sa.MetaData()
lifecycle = sa.Table('application_lifecycle', metadata,
    sa.Column('tenant_id', sa.String(64), primary_key=True),
    sa.Column('application_id', sa.String(36), primary_key=True),
    sa.Column('status', sa.String(16), nullable=False),
    sa.Column('revision', sa.Integer, nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))


def status(connection, tenant, application_id):
    row = connection.execute(sa.select(lifecycle.c.status, lifecycle.c.revision).where(
        lifecycle.c.tenant_id == tenant, lifecycle.c.application_id == application_id)).mappings().first()
    return dict(row) if row else {'status': 'active', 'revision': 0}


def assert_active(connection, tenant, application_id):
    if status(connection, tenant, application_id)['status'] != 'active':
        raise APIError(409, 'APPLICATION_INACTIVE', '应用已停用或归档，不能发起新业务；已有记录和待办仍可核对。')


def assert_editable(connection, tenant, application_id):
    if status(connection, tenant, application_id)['status'] == 'archived':
        raise Conflict('归档应用只读；请先恢复为停用状态，再编辑并核对启用。')


class ChangeLifecycle(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    status: Literal['active', 'disabled', 'archived']
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=16, max_length=80)


def change(store, application_id, command, trace):
    # Do not require the runtime adapter merely to stop a broken application.
    from app.modules.config.application_store import applications
    from app.modules.approvals.runtime import instances
    fingerprint = digest(command.model_dump(exclude={'idempotency_key'}))
    command_id = str(uuid5(NAMESPACE_URL, json.dumps([store.tenant_id, store.actor_id, application_id, 'lifecycle', command.idempotency_key])))
    with store.engine.begin() as connection:
        target = connection.execute(sa.select(state.c.revision).where(state.c.tenant_id == store.tenant_id,
            state.c.application_id == application_id, state.c.key == KEY).with_for_update()).first()
        if target is None or not connection.scalar(sa.select(applications.c.application_id).where(
                applications.c.tenant_id == store.tenant_id, applications.c.application_id == application_id)):
            raise NotFound('应用不存在或不可访问。')
        previous = connection.scalar(sa.select(audit.c.after_json).where(audit.c.id == command_id,
            audit.c.tenant_id == store.tenant_id, audit.c.application_id == application_id, audit.c.actor_id == store.actor_id))
        if previous:
            if previous['request_hash'] != fingerprint:
                raise Conflict('同一生命周期请求号不能用于不同内容。')
            return {**previous['result'], 'replayed': True}
        before = status(connection, store.tenant_id, application_id)
        if before['revision'] != command.expected_revision:
            raise Conflict('应用状态已变化，请刷新后重新确认。')
        if before['status'] == command.status:
            raise Conflict('应用已经处于目标状态。')
        if before['status'] == 'archived' and command.status == 'active':
            raise Conflict('请先恢复为停用，核对配置后再启用。')
        if command.status == 'archived':
            pending = connection.scalar(sa.select(sa.func.count()).select_from(instances).where(
                instances.c.tenant_id == store.tenant_id, instances.c.application_id == application_id, instances.c.status == 'pending'))
            if pending:
                raise Conflict('仍有待审批申请，请先停用并处理完待办，再归档。')
        result = {'status': command.status, 'revision': before['revision'] + 1, 'replayed': False}
        values = dict(status=command.status, revision=result['revision'], updated_at=datetime.now(timezone.utc))
        if before['revision']:
            connection.execute(lifecycle.update().where(lifecycle.c.tenant_id == store.tenant_id,
                lifecycle.c.application_id == application_id).values(**values))
        else:
            connection.execute(lifecycle.insert().values(tenant_id=store.tenant_id, application_id=application_id, **values))
        # Audit on the actual target, not the legacy application used to obtain the engine.
        from app.modules.config.store import ConfigStore
        ConfigStore(store.engine, store.tenant_id, store.actor_id, application_id)._audit(connection,
            'application_lifecycle', result['revision'], trace, before=before,
            after={'request_hash': fingerprint, 'result': result, 'reason': command.reason}, audit_id=command_id)
        return result


router = APIRouter(prefix='/api/v1/admin/applications', tags=['应用生命周期'], dependencies=[Depends(admin)])


@router.post('/{application_id}/lifecycle')
def transition(application_id: UUID, command: ChangeLifecycle, request: Request):
    return execute(lambda store: change(store, str(application_id), command, request.state.trace_id), request.state.principal)
