"""Incomplete work is separate from submitted records and never starts approval."""
from datetime import datetime, timezone
import json
from uuid import UUID, NAMESPACE_URL, uuid5
import sqlalchemy as sa
from fastapi import APIRouter, Request
from pydantic import Field
from app.errors import APIError
from app.modules.config.routes import execute
from app.modules.config.store import Conflict, NotFound, audit, digest, releases, KEY
from app.modules.forms.schema import Strict, validate_values
from app.modules.identity.auth import current_user

metadata = sa.MetaData()
drafts = sa.Table('business_draft', metadata,
    sa.Column('id', sa.String(36), primary_key=True),
    sa.Column('tenant_id', sa.String(64), nullable=False),
    sa.Column('application_id', sa.String(36), nullable=False),
    sa.Column('owner_id', sa.String(64), nullable=False),
    sa.Column('release_id', sa.String(36), nullable=False),
    sa.Column('values_json', sa.JSON, nullable=False),
    sa.Column('status', sa.String(16), nullable=False),
    sa.Column('revision', sa.Integer, nullable=False),
    sa.Column('record_id', sa.String(36)),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))


class SaveBusinessDraft(Strict):
    release_id: str = Field(min_length=36, max_length=36)
    expected_revision: int = Field(ge=0)
    values: dict = Field(max_length=30)
    idempotency_key: str = Field(min_length=16, max_length=80)
    cancel: bool = False


class BusinessDrafts:
    def __init__(self, service):
        self.service = service
        self.store = service.store
        self.principal = service.principal

    def scope(self):
        return (drafts.c.tenant_id == self.store.tenant_id, drafts.c.application_id == self.store.application_id,
                drafts.c.owner_id == self.principal.subject)

    def read(self, connection, draft_id, lock=False):
        query = sa.select(drafts).where(*self.scope(), drafts.c.id == draft_id)
        row = connection.execute(query.with_for_update() if lock else query).mappings().first()
        if row is None:
            raise NotFound('填写草稿不存在或不可访问。')
        return row

    def detail(self, draft_id):
        self.service.authorized()
        with self.store.engine.connect() as connection:
            row = self.read(connection, draft_id)
            content = connection.scalar(sa.select(releases.c.content).where(releases.c.tenant_id == self.store.tenant_id,
                releases.c.application_id == self.store.application_id, releases.c.key == KEY, releases.c.id == row['release_id']))
            if content is None:
                raise NotFound('原表单发布版本不可用。')
            return {**dict(row), 'content': content}

    def save(self, draft_id, command, trace):
        current = self.service.authorized()
        fingerprint = digest({'draft_id': draft_id, **command.model_dump(exclude={'idempotency_key'})})
        command_id = str(uuid5(NAMESPACE_URL, json.dumps([self.store.tenant_id, self.store.application_id,
            self.principal.subject, 'business_draft', command.idempotency_key])))
        if len(json.dumps(command.values, ensure_ascii=False).encode()) > 65536:
            raise APIError(422, 'DRAFT_TOO_LARGE', '草稿内容最多64KiB。')
        with self.store.engine.begin() as connection:
            app = self.store._state(connection, lock=True)
            previous = connection.scalar(sa.select(audit.c.after_json).where(audit.c.id == command_id,
                audit.c.tenant_id == self.store.tenant_id, audit.c.application_id == self.store.application_id,
                audit.c.actor_id == self.principal.subject, audit.c.action == 'save_business_draft'))
            if previous:
                if previous['request_hash'] != fingerprint:
                    raise Conflict('同一草稿请求号不能用于不同内容。')
                return {**previous['result'], 'replayed': True}
            from app.modules.config.lifecycle import assert_active
            if not command.cancel:
                assert_active(connection, self.store.tenant_id, self.store.application_id)
                if not self.service.accepting():
                    raise Conflict('该业务已暂停新填写。')
            old = connection.execute(sa.select(drafts).where(*self.scope(), drafts.c.id == draft_id).with_for_update()).mappings().first()
            if old and (old['revision'] != command.expected_revision or old['status'] != 'active') or not old and command.expected_revision != 0:
                raise Conflict('填写草稿已变化或结束，请核对最新状态。')
            if not old and command.cancel:
                raise NotFound('尚未保存此草稿。')
            if not command.cancel and (command.release_id != current['release_id'] or app['current_release_id'] != command.release_id):
                raise Conflict('表单已更新，原草稿仍保留。请加载新表单，核对字段后显式保存为新版本草稿。')
            config = self.store.adapter.model.model_validate(current['content'])
            # Incomplete required fields may remain empty; populated values still obey exact types/options/bounds.
            partial = config.model_copy(deep=True)
            for field in partial.model.fields:
                field.nullable = True
            values = ({key: value for key, value in validate_values(partial, command.values).items() if key in command.values}
                      if not command.cancel else old['values_json'])
            now = datetime.now(timezone.utc)
            if not old:
                count = connection.scalar(sa.select(sa.func.count()).select_from(drafts).where(*self.scope(), drafts.c.status == 'active'))
                if count >= 50:
                    raise Conflict('本应用最多保留50个未完成草稿，请先取消或完成旧草稿。')
            result = {'draft_id': draft_id, 'revision': command.expected_revision + 1,
                'status': 'cancelled' if command.cancel else 'active', 'release_id': old['release_id'] if command.cancel else command.release_id,
                'values': values, 'replayed': False}
            changes = dict(revision=result['revision'], status=result['status'], release_id=old['release_id'] if command.cancel else command.release_id,
                values_json=values, updated_at=now)
            if old:
                connection.execute(drafts.update().where(*self.scope(), drafts.c.id == draft_id).values(**changes))
            else:
                connection.execute(drafts.insert().values(id=draft_id, tenant_id=self.store.tenant_id,
                    application_id=self.store.application_id, owner_id=self.principal.subject, created_at=now, **changes))
            self.store._audit(connection, 'save_business_draft', result['revision'], trace, after={
                'draft_id': draft_id, 'request_hash': fingerprint, 'result': result}, audit_id=command_id)
            return result

    def submitted(self, connection, command, record_id):
        if command.draft_id is None:
            return
        row = self.read(connection, command.draft_id, lock=True)
        if row['revision'] != command.draft_revision or row['status'] != 'active' or row['release_id'] != command.release_id:
            raise Conflict('填写草稿已变化、结束或版本不一致，请重新核对后提交。')
        # Final submitted values may include last edits; they are fully validated in the same transaction.
        connection.execute(drafts.update().where(*self.scope(), drafts.c.id == command.draft_id).values(
            status='submitted', record_id=record_id, revision=row['revision'] + 1, updated_at=datetime.now(timezone.utc)))


router = APIRouter(prefix='/api/v1', tags=['我的未完成工作'])


@router.get('/applications/{application_id}/business-drafts/{draft_id}')
def detail(application_id: UUID, draft_id: UUID, request: Request):
    principal = current_user(request)
    from app.modules.forms.routes import record_service
    return execute(lambda store: BusinessDrafts(record_service(store, principal, str(application_id))).detail(str(draft_id)), principal)


@router.put('/applications/{application_id}/business-drafts/{draft_id}')
def save(application_id: UUID, draft_id: UUID, command: SaveBusinessDraft, request: Request):
    principal = current_user(request)
    from app.modules.forms.routes import record_service
    return execute(lambda store: BusinessDrafts(record_service(store, principal, str(application_id))).save(str(draft_id), command, request.state.trace_id), principal)


@router.get('/my-work')
def my_work(request: Request, page: int = 1):
    principal = current_user(request)
    if page < 1 or page > 100000:
        raise APIError(422, 'PAGE_INVALID', '页码无效。')
    def listing(store):
        from app.modules.config.application_store import applications
        from app.modules.approvals.runtime import instances
        from app.modules.forms.runtime import records
        with store.engine.connect() as connection:
            name = releases.c.content['application']['name'].as_string().label('application_name')
            query = sa.select(drafts, applications.c.type_id, name).join(applications, sa.and_(
                applications.c.tenant_id == drafts.c.tenant_id, applications.c.application_id == drafts.c.application_id)).outerjoin(releases,
                    sa.and_(releases.c.tenant_id == drafts.c.tenant_id, releases.c.application_id == drafts.c.application_id,
                        releases.c.id == drafts.c.release_id)).where(
                    drafts.c.tenant_id == store.tenant_id, drafts.c.owner_id == principal.subject, drafts.c.status == 'active')
            total = connection.scalar(sa.select(sa.func.count()).select_from(query.subquery()))
            rows = connection.execute(query.order_by(drafts.c.updated_at.desc(), drafts.c.id).offset((page - 1) * 20).limit(20)).mappings().all()
            submitted = connection.execute(sa.select(records.c.id, records.c.application_id, records.c.created_at, applications.c.type_id, name)
                .join(applications, sa.and_(applications.c.tenant_id == records.c.tenant_id,
                    applications.c.application_id == records.c.application_id)).outerjoin(releases,
                        sa.and_(releases.c.tenant_id == records.c.tenant_id, releases.c.application_id == records.c.application_id,
                            releases.c.id == records.c.release_id)).where(records.c.tenant_id == store.tenant_id,
                    records.c.owner_id == principal.subject).order_by(records.c.created_at.desc()).limit(100)).mappings().all()
            assigned = []
            if 'config_admin' in principal.roles:
                assigned = connection.execute(sa.select(instances.c.id, instances.c.application_id, instances.c.updated_at, name).outerjoin(releases,
                    sa.and_(releases.c.tenant_id == instances.c.tenant_id, releases.c.application_id == instances.c.application_id,
                        releases.c.id == instances.c.release_id)).where(
                    instances.c.tenant_id == store.tenant_id, instances.c.approver_id == principal.subject,
                    instances.c.status == 'pending').order_by(instances.c.updated_at.desc()).limit(100)).mappings().all()
            return {'items': [{key: row[key] for key in ('id', 'application_id', 'application_name', 'type_id', 'release_id', 'revision', 'updated_at')} for row in rows],
                'total': total, 'page': page, 'page_size': 20, 'assigned': [dict(row) for row in assigned], 'assigned_limit': 100,
                'submitted': [dict(row) for row in submitted], 'submitted_limit': 100}
    return execute(listing, principal)
