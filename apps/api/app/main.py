"""Campus MVP API. Explicit demo defaults; real adapters require operator configuration."""

from datetime import date
from contextlib import asynccontextmanager
import asyncio
import logging
import time
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from app.modules.skills.schedule import Course, HttpScheduleProvider, MockScheduleProvider, ScheduleProvider
from app.modules.config.routes import ConfigError, published_config, router as config_router
from app.modules.config.schema import Column
from app.modules.intent.router import Routing, campus_today, resolve
from app.modules.identity.auth import Principal, auth_options, current_user, require_schedule
from app.settings import read_local_config
from app.observability import increment, observe
from app.rate_limit import check as check_rate
from app.telemetry import setup as setup_telemetry, span


@asynccontextmanager
async def lifespan(application):
    setup_telemetry()
    logger = logging.getLogger('campus.requests')
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger.addHandler(handler)
    options = read_local_config().get('sis', {})
    mode = options.get('mode', 'mock')
    if mode not in ('mock', 'database', 'http'):
        raise ConfigError(503, 'SIS_CONFIGURATION', 'sis.mode 必须为 mock、database 或 http。')
    if mode == 'database':
        from app.modules.skills.database_schedule import DatabaseScheduleProvider
        provider = DatabaseScheduleProvider()
    else:
        provider = HttpScheduleProvider(options) if mode == 'http' else MockScheduleProvider()
    application.state.provider = provider
    try:
        yield
    finally:
        if isinstance(provider, HttpScheduleProvider):
            await provider.aclose()
        del application.state.provider

app = FastAPI(title="校园助手 · 课表 MVP", version="0.2.0", lifespan=lifespan)
app.include_router(config_router)
from app.modules.config.course_design_routes import router as course_design_router
app.include_router(course_design_router)
from app.modules.config.ai_routes import router as ai_model_router
app.include_router(ai_model_router)
from app.modules.config.question_routes import router as question_router
app.include_router(question_router)
from app.modules.identity.demo import router as demo_router
app.include_router(demo_router)
from app.modules.config.applications import router as application_router, require_application
app.include_router(application_router)
from app.modules.config.application_routes import router as application_management_router
app.include_router(application_management_router)
from app.modules.intent.application_router import router as assistant_router
app.include_router(assistant_router)
from app.modules.intent.requirements import router as requirement_router
app.include_router(requirement_router)
from app.modules.intent.construction_ai import router as construction_ai_router
app.include_router(construction_ai_router)
from app.modules.config.lifecycle import router as lifecycle_router
app.include_router(lifecycle_router)
from app.modules.unified_editor.routes import router as form_router
app.include_router(form_router)
from app.modules.deployment.routes import router as deployment_router
app.include_router(deployment_router)


class UIHint(BaseModel):
    type: str = "course_schedule"
    version: str = "1"
    release_id: str | None = None
    columns: list[Column] = []


class ScheduleData(BaseModel):
    source: str = "mock"
    identity: str = "本地模拟学生（未接入真实校园认证）"
    start_date: date
    end_date: date
    courses: list[Course]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ScheduleResponse(BaseModel):
    data: ScheduleData | None = None
    ui_hint: UIHint = UIHint()
    error: ErrorDetail | None = None
    trace_id: str
    routing: Routing | None = None
    ai_used: bool = False
    ai_duration_ms: float = 0


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=200)


@app.middleware("http")
async def trace_request(request: Request, call_next):
    started = time.perf_counter()
    request.state.trace_id = uuid4().hex
    with span('http.request') as active:
        if active:
            request.state.trace_id = f'{active.get_span_context().trace_id:032x}'
        try:
            response = await call_next(request)
        except Exception as exc:
            response = await unexpected_error(request, exc)
    elapsed = time.perf_counter() - started
    observe(elapsed)
    increment('requests')
    if response.status_code >= 400:
        increment('errors')
    response.headers["X-Trace-ID"] = request.state.trace_id
    response.headers['Server-Timing'] = f'app;dur={elapsed * 1000:.1f}'
    if hasattr(request.state, 'upstream_ms'):
        response.headers['Server-Timing'] += f', upstream;dur={request.state.upstream_ms:.1f}'
    if hasattr(request.state, 'config_ms'):
        response.headers['Server-Timing'] += f', config;dur={request.state.config_ms:.1f}'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    if response.status_code == 429:
        response.headers['Retry-After'] = '60'
    logging.getLogger('campus.requests').info('trace=%s method=%s route=%s status=%s duration_ms=%.1f release=%s rule=%s',
        request.state.trace_id, request.method,
        getattr(request.scope.get('route'), 'path', 'unknown'), response.status_code, elapsed * 1000,
        getattr(request.state, 'release_id', None), getattr(request.state, 'rule_id', None))
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={
        "data": None, "ui_hint": {"type": "text", "version": "1"},
        "error": {"code": "INVALID_PARAMETER", "message": "参数不合法，请检查字段、日期格式和版本号。",
                  "details": [{"path": '/'.join(str(part) for part in item['loc']),
                               "message": item['msg']} for item in exc.errors()]},
        "trace_id": request.state.trace_id,
    })


@app.exception_handler(ConfigError)
async def config_error(request: Request, exc: ConfigError):
    return JSONResponse(status_code=exc.status, content={
        'data': None, 'error': {'code': exc.code, 'message': exc.message,
                              **({'details': exc.details} if exc.details else {})},
        'trace_id': request.state.trace_id,
    })


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    logging.getLogger('campus.requests').error('trace=%s failure_type=%s',
        getattr(request.state, 'trace_id', 'unknown'), type(exc).__name__)
    return JSONResponse(status_code=500, content={'data': None,
        'error': {'code': 'INTERNAL_ERROR', 'message': '服务暂时异常，请凭请求编号联系管理员。'},
        'trace_id': getattr(request.state, 'trace_id', 'unknown')})


@app.get("/api/health")
async def health():
    from app.modules.config.ai_settings import model_settings
    try:
        ai_enabled = model_settings()['enabled'] and auth_options().get('mode', 'demo') == 'demo'
    except ConfigError:
        ai_enabled = False
    return {'status': 'ok', 'mode': 'demo' if auth_options().get('mode', 'demo') == 'demo' else 'oidc',
            'authentication': auth_options().get('mode', 'demo'),
            'data_source': read_local_config().get('sis', {}).get('mode', 'mock'), 'ai_enabled': ai_enabled}


@app.get('/api/v1/me')
def me(request: Request):
    principal = current_user(request)
    return principal.model_dump()


@app.get('/api/ready')
def ready():
    from app.settings import database_is_configured
    if database_is_configured():
        try:
            import sqlalchemy as sa
            from app.db import LATEST_REVISION, get_engine
            with get_engine().connect() as connection:
                version = connection.scalar(sa.text('SELECT version_num FROM alembic_version'))
            if version != LATEST_REVISION:
                raise RuntimeError('Migration is not current')
        except Exception as exc:
            raise ConfigError(503, 'DATABASE_NOT_READY', '数据库不可用或迁移未完成。') from exc
    return {'status': 'ready', 'authentication': auth_options().get('mode', 'demo'),
            'data_source': read_local_config().get('sis', {}).get('mode', 'mock')}


def get_provider(request: Request) -> ScheduleProvider:
    return getattr(request.app.state, 'provider', MockScheduleProvider())


async def fetch_schedule(request: Request, start: date, end: date, provider: ScheduleProvider,
                         routing: Routing | None = None, config=None, principal=None):
    principal = principal or require_schedule(request)
    if config is None:
        await run_in_threadpool(require_active_application, None, principal)
        started = time.perf_counter()
        with span('config.read'):
            config = await run_in_threadpool(published_config, principal)
        request.state.config_ms = (time.perf_counter() - started) * 1000
    request.state.release_id = config['release_id']
    require_application(config['content'], principal)
    started = time.perf_counter()
    try:
        with span('schedule.read'):
            courses = await asyncio.wait_for(provider.get_schedule(principal.student_id, start, end,
                                                tenant_id=principal.tenant_id), timeout=4)
    except asyncio.TimeoutError as exc:
        raise ConfigError(503, 'SIS_UNAVAILABLE', '教务查询超时，请稍后重试。') from exc
    request.state.upstream_ms = (time.perf_counter() - started) * 1000
    increment('schedule_queries')
    return ScheduleResponse(data=ScheduleData(start_date=start, end_date=end, courses=courses,
        source=provider.source, identity='本地模拟学生' if principal.mode == 'demo' else '校园认证用户'),
        ui_hint=UIHint(release_id=config['release_id'], columns=config['content']['columns']),
        trace_id=request.state.trace_id, routing=routing)


@app.post('/api/v1/course-schedule/query', response_model=ScheduleResponse)
async def natural_query(command: QueryRequest, request: Request,
                        provider: Annotated[ScheduleProvider, Depends(get_provider)],
                        principal: Annotated[Principal, Depends(require_schedule)]):
    check_rate(principal)
    await run_in_threadpool(require_active_application, None, principal)
    started = time.perf_counter()
    with span('config.read'):
        config = await run_in_threadpool(published_config, principal)
    request.state.config_ms = (time.perf_counter() - started) * 1000
    request.state.release_id = config['release_id']
    return await run_natural_query(command, request, provider, principal, config)


async def run_natural_query(command, request, provider, principal, config, application_id=None):
    from app.modules.config.schema import DisplayConfig
    bundle = DisplayConfig.model_validate(config['content'])
    require_application(config['content'], principal)
    today = campus_today()
    with span('intent.resolve'):
        routing = resolve(command.text, today, bundle.rules)
    from app.modules.intent.fallback import fallback
    routing, ai_ms, ai_used = await fallback(command.text, routing, today, principal)
    request.state.rule_id = routing.rule_id
    increment('routing_' + routing.status)
    if routing.status != 'resolved':
        return ScheduleResponse(trace_id=request.state.trace_id, routing=routing,
                                ui_hint=UIHint(type='text', release_id=config['release_id']),
                                ai_used=ai_used, ai_duration_ms=round(ai_ms, 1))
    result = await fetch_schedule(request, routing.start_date, routing.end_date, provider, routing, config, principal)
    if ai_used and routing.rule_id == 'ai-fallback':
        from app.modules.config.question_library import learned_question, QuestionLibrary
        from app.settings import database_is_configured
        from app.modules.config.routes import execute
        candidate = learned_question(command.text, routing, today)
        if candidate is not None and database_is_configured():
            try:
                def learn(store):
                    if application_id is not None:
                        from app.modules.config.application_store import ApplicationStore
                        store = ApplicationStore(store).config_store(application_id)
                    return QuestionLibrary(store).observe(candidate, request.state.trace_id)
                recorded = await run_in_threadpool(execute, learn, principal)
                increment('question_learning_recorded' if recorded else 'question_learning_skipped')
            except Exception:
                increment('question_learning_failed')
    return result.model_copy(update={'ai_used': ai_used, 'ai_duration_ms': round(ai_ms, 1)})


def require_active_application(application_id, principal):
    from app.settings import database_is_configured
    if database_is_configured():
        from app.modules.config.routes import execute
        from app.modules.config.lifecycle import assert_active
        from app.modules.config.store import LEGACY_APPLICATION_ID
        def check(store):
            with store.engine.connect() as connection:
                assert_active(connection, store.tenant_id, str(application_id) if application_id else LEGACY_APPLICATION_ID)
        execute(check, principal)


def application_snapshot(application_id, principal):
    require_active_application(application_id, principal)
    from app.modules.config.routes import execute
    from app.modules.config.application_store import ApplicationStore
    config = execute(lambda store: ApplicationStore(store).config_store(str(application_id)).current(), principal)
    if not config['release_id']:
        raise ConfigError(404, 'APPLICATION_UNPUBLISHED', '应用尚未发布，暂不能使用。')
    require_application(config['content'], principal)
    return config


@app.post('/api/v1/applications/{application_id}/query', response_model=ScheduleResponse)
async def application_query(application_id: UUID, command: QueryRequest, request: Request,
        provider: Annotated[ScheduleProvider, Depends(get_provider)],
        principal: Annotated[Principal, Depends(require_schedule)]):
    check_rate(principal)
    config = await run_in_threadpool(application_snapshot, application_id, principal)
    request.state.release_id = config['release_id']
    return await run_natural_query(command, request, provider, principal, config, str(application_id))


@app.get('/api/v1/applications/{application_id}/schedule', response_model=ScheduleResponse)
async def application_schedule(application_id: UUID, request: Request,
        start_date: Annotated[date, Query()], end_date: Annotated[date, Query()],
        provider: Annotated[ScheduleProvider, Depends(get_provider)],
        principal: Annotated[Principal, Depends(require_schedule)]):
    check_rate(principal)
    if set(request.query_params) - {'start_date', 'end_date'} or not 0 <= (end_date - start_date).days <= 30:
        raise ConfigError(422, 'INVALID_DATE_RANGE', '请仅填写起止日期，查询范围最多31天。')
    config = await run_in_threadpool(application_snapshot, application_id, principal)
    return await fetch_schedule(request, start_date, end_date, provider, config=config, principal=principal)


@app.get("/api/v1/course-schedule", response_model=ScheduleResponse,
         responses={422: {"model": ScheduleResponse}})
async def schedule(
    request: Request,
    start_date: Annotated[date, Query()],
    end_date: Annotated[date, Query()],
    provider: Annotated[ScheduleProvider, Depends(get_provider)],
    principal: Annotated[Principal, Depends(require_schedule)],
):
    check_rate(principal)
    if set(request.query_params) - {'start_date', 'end_date'}:
        raise ConfigError(422, 'INVALID_PARAMETER', '不接受学生身份或其他未声明的查询参数。')
    if not 0 <= (end_date - start_date).days <= 30:
        return JSONResponse(status_code=422, content=ScheduleResponse(
            error=ErrorDetail(code="INVALID_DATE_RANGE", message="结束日期不能早于开始日期，查询范围最多 31 天。"),
            trace_id=request.state.trace_id,
        ).model_dump(mode="json"))
    return await fetch_schedule(request, start_date, end_date, provider, principal=principal)
