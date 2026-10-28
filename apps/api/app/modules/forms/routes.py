from uuid import UUID
from fastapi import APIRouter, Depends, Query, Request
from app.modules.config.routes import admin, execute
from app.modules.identity.auth import current_user
from app.modules.config.schema import PublishDraft, Rollback
from app.modules.forms.schema import SaveForm, PreviewForm, validate_values
from app.modules.forms.runtime import form_store, FormRecords, SubmitForm
from app.modules.approvals.schema import LeaveConfig
from app.modules.forms.schema import FormConfig


class SaveTypedForm(SaveForm):
    content: FormConfig | LeaveConfig


class PreviewTypedForm(PreviewForm):
    content: FormConfig | LeaveConfig

router = APIRouter(prefix='/api/v1', tags=['简单登记表单'])


def record_service(store, principal, application_id):
    from app.modules.config.application_store import ApplicationStore
    if ApplicationStore(store).get(application_id)['type_id'] == 'platform.leave-approval':
        from app.modules.approvals.runtime import ApprovalRecords
        return ApprovalRecords(store, principal, application_id)
    return FormRecords(store, principal, application_id)


@router.get('/admin/applications/{application_id}/form', dependencies=[Depends(admin)])
def draft(application_id: UUID, request: Request):
    return execute(lambda store: form_store(store, str(application_id)).draft(), request.state.principal)


@router.put('/admin/applications/{application_id}/form/draft', dependencies=[Depends(admin)])
def save(application_id: UUID, command: SaveTypedForm, request: Request):
    from app.errors import APIError
    def run(store):
        target = form_store(store, str(application_id))
        try:
            target.adapter.model.model_validate(command.content.model_dump())
        except ValueError:
            raise APIError(422, 'APPLICATION_CONFIG_INVALID', '配置不符合当前应用的精确类型。') from None
        return target.save(command, request.state.trace_id)
    return execute(run, request.state.principal)


@router.post('/admin/applications/{application_id}/form/preview', dependencies=[Depends(admin)])
def preview(application_id: UUID, command: PreviewTypedForm, request: Request):
    from app.modules.config.store import Conflict, digest
    def run(store):
        target = form_store(store, str(application_id))
        if target.draft()['revision'] != command.expected_revision:
            raise Conflict('草稿已改变，请重新加载后预览。')
        from app.errors import APIError
        try:
            config = target.adapter.model.model_validate(command.content.model_dump())
        except ValueError:
            raise APIError(422, 'APPLICATION_CONFIG_INVALID', '配置不符合当前应用的精确类型。') from None
        return {'valid': True, 'content_hash': digest(config.model_dump()), 'values': target.adapter.validate_values(config, command.values)}
    return execute(run, request.state.principal)


@router.post('/admin/applications/{application_id}/form/publish', dependencies=[Depends(admin)])
def publish(application_id: UUID, command: PublishDraft, request: Request):
    return execute(lambda store: form_store(store, str(application_id)).publish(command, request.state.trace_id), request.state.principal)


@router.post('/admin/applications/{application_id}/form/rollback', dependencies=[Depends(admin)])
def rollback(application_id: UUID, command: Rollback, request: Request):
    return execute(lambda store: form_store(store, str(application_id)).publish(command, request.state.trace_id, rollback=True), request.state.principal)


@router.get('/admin/applications/{application_id}/form/releases', dependencies=[Depends(admin)])
def history(application_id: UUID, request: Request):
    return execute(lambda store: form_store(store, str(application_id)).history(), request.state.principal)


@router.get('/admin/applications/{application_id}/form/audit', dependencies=[Depends(admin)])
def audit_history(application_id: UUID, request: Request):
    return execute(lambda store: form_store(store, str(application_id)).audit_history(), request.state.principal)


@router.get('/applications/{application_id}/form')
def definition(application_id: UUID, request: Request):
    principal = current_user(request)
    return execute(lambda store: record_service(store, principal, str(application_id)).definition(), principal)


@router.post('/applications/{application_id}/records')
def submit(application_id: UUID, command: SubmitForm, request: Request):
    principal = current_user(request)
    from app.rate_limit import check
    check(principal)
    return execute(lambda store: record_service(store, principal, str(application_id)).submit(command, request.state.trace_id), principal)


@router.get('/applications/{application_id}/records')
def listing(application_id: UUID, request: Request, page: int = Query(default=1, ge=1)):
    principal = current_user(request)
    return execute(lambda store: record_service(store, principal, str(application_id)).listing(page), principal)


@router.get('/applications/{application_id}/records/{record_id}')
def detail(application_id: UUID, record_id: UUID, request: Request):
    principal = current_user(request)
    return execute(lambda store: record_service(store, principal, str(application_id)).detail(str(record_id)), principal)


@router.get('/applications/{application_id}/records/by-command/{command_key}')
def by_command(application_id: UUID, command_key: str, request: Request):
    principal = current_user(request)
    return execute(lambda store: record_service(store, principal, str(application_id)).by_command(command_key), principal)


@router.get('/admin/applications/{application_id}/form/records', dependencies=[Depends(admin)])
def admin_records(application_id: UUID, request: Request, page: int = Query(default=1, ge=1)):
    principal = request.state.principal
    return execute(lambda store: record_service(store, principal, str(application_id)).listing(page, all_records=True), principal)


@router.get('/admin/applications/{application_id}/form/records/{record_id}', dependencies=[Depends(admin)])
def admin_detail(application_id: UUID, record_id: UUID, request: Request):
    principal = request.state.principal
    return execute(lambda store: record_service(store, principal, str(application_id)).detail(str(record_id), all_records=True), principal)
