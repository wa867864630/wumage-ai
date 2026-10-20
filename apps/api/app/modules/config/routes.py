from fastapi import APIRouter, Depends, Request

from app.db import ConfigUnavailable, get_store
from app.modules.config.schema import DisplayConfig, PreviewRequest, PublishDraft, Rollback, SaveDraft, default_config
from app.settings import SettingsError, database_is_configured
from app.errors import APIError as ConfigError
from app.modules.identity.auth import current_user
from app.observability import snapshot, prometheus

router = APIRouter(prefix='/api/v1', tags=['展示配置（仅本地开发）'])


def admin(request: Request):
    # Disabled unless explicitly configured. Not a substitute for OIDC/RBAC.
    from app.modules.identity.auth import auth_options
    from app.settings import admin_token
    if auth_options().get('mode', 'demo') == 'demo' and len(admin_token()) < 24:
        raise ConfigError(503, 'ADMIN_DISABLED', '请在 config.local.toml 设置至少 24 字符的 local_admin.token。')
    # Prevent browser cross-origin writes; Vite proxy preserves the local browser origin.
    origin = request.headers.get('origin')
    if origin and origin not in {'http://127.0.0.1:5173', 'http://localhost:5173',
                                 'http://127.0.0.1:8000', 'http://localhost:8000'}:
        raise ConfigError(403, 'ORIGIN_DENIED', '只允许本地开发页面访问管理接口。')
    principal = current_user(request)
    if 'config_admin' not in principal.roles:
        raise ConfigError(403 if request.headers.get('authorization') else 401, 'ADMIN_UNAUTHORIZED', '需要配置管理员权限。')
    return principal


def execute(operation, principal=None):
    try:
        return operation(get_store(principal))
    except ConfigUnavailable as exc:
        raise ConfigError(503, 'CONFIG_UNAVAILABLE', str(exc)) from exc
    except ConfigError:
        raise
    except Exception as exc:
        # Never return connection strings, SQL, or raw database errors.
        from app.modules.config.store import Conflict, NotFound
        if isinstance(exc, Conflict):
            raise ConfigError(409, 'CONFIG_CONFLICT', str(exc)) from exc
        if isinstance(exc, NotFound):
            raise ConfigError(404, 'RELEASE_NOT_FOUND', str(exc)) from exc
        raise ConfigError(503, 'CONFIG_UNAVAILABLE', '配置数据库不可用，请检查连接、依赖与迁移。') from exc


def published_config(principal=None):
    # Only an unconfigured local demo may use built-in defaults. Configured DB errors fail closed.
    try:
        configured = database_is_configured()
    except SettingsError as exc:
        raise ConfigError(503, 'CONFIG_UNAVAILABLE', str(exc)) from exc
    if not configured:
        return {'release_id': None, 'content': default_config().model_dump(), 'source': 'builtin-demo'}
    return execute(lambda store: store.current(), principal)


@router.get('/config/course-display')
def published(request: Request):
    return published_config(current_user(request))


@router.get('/admin/course-display', dependencies=[Depends(admin)])
def draft(request: Request):
    return execute(lambda store: store.draft(), request.state.principal)


@router.put('/admin/course-display/draft', dependencies=[Depends(admin)])
def save(command: SaveDraft, request: Request):
    return execute(lambda store: store.save(command, request.state.trace_id), request.state.principal)


@router.post('/admin/course-display/publish', dependencies=[Depends(admin)])
def publish(command: PublishDraft, request: Request):
    return execute(lambda store: store.publish(command, request.state.trace_id), request.state.principal)


@router.post('/admin/course-display/rollback', dependencies=[Depends(admin)])
def rollback(command: Rollback, request: Request):
    return execute(lambda store: store.publish(command, request.state.trace_id, rollback=True), request.state.principal)


@router.get('/admin/course-display/releases', dependencies=[Depends(admin)])
def history(request: Request):
    return execute(lambda store: store.history(), request.state.principal)


@router.post('/admin/course-display/preview', dependencies=[Depends(admin)])
def preview(command: PreviewRequest, request: Request):
    from app.modules.config.review import review
    from app.modules.intent.router import resolve, campus_today
    current = published_config(request.state.principal)
    return {'routing': resolve(command.text, campus_today(), command.content.rules).model_dump(mode='json'),
            'columns': [column.model_dump() for column in command.content.columns],
            'review': review(DisplayConfig.model_validate(current['content']), command.content)}


@router.get('/admin/course-display/audit', dependencies=[Depends(admin)])
def audit_history(request: Request):
    return execute(lambda store: store.audit_history(), request.state.principal)


@router.get('/admin/monitoring', dependencies=[Depends(admin)])
def monitoring(request: Request):
    status = execute(lambda store: store.delivery_status(), request.state.principal)
    return {**snapshot(), 'outbox': status}


@router.get('/admin/metrics', dependencies=[Depends(admin)])
def metrics(request: Request):
    from fastapi.responses import PlainTextResponse
    status = execute(lambda store: store.delivery_status(), request.state.principal)
    gauges = '# TYPE campus_outbox_events gauge\n'
    for name in ('pending', 'working', 'failed', 'done'):
        gauges += f'campus_outbox_events{{state="{name}"}} {status["counts"].get(name, 0)}\n'
    return PlainTextResponse(prometheus() + gauges, media_type='text/plain; version=0.0.4')


@router.post('/admin/course-display/outbox/{event_id}/retry', dependencies=[Depends(admin)])
def retry_event(event_id: str, request: Request):
    return execute(lambda store: store.retry_event(event_id, request.state.trace_id), request.state.principal)
