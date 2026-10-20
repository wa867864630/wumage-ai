from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.modules.config.routes import admin, execute
from app.modules.config.application_store import ApplicationStore, CreateApplication, SaveApplication
from app.modules.config.type_registry import course_type
from app.modules.config.schema import SaveDraft, PreviewRequest, PublishDraft, Rollback, DisplayConfig
from app.modules.config.question_library import QuestionLibrary, QuestionInput, EditQuestion, ReviewQuestion
from app.modules.config.question_library import GenerateQuestions
from app.modules.config.ai_routes import local_ai_operator

router = APIRouter(prefix='/api/v1/admin', tags=['多应用管理'], dependencies=[Depends(admin)])


@router.get('/application-types')
def types():
    return {'items': [course_type()], 'templates': [
        {'id': 'course-standard', 'type_id': 'campus.course-query', 'name': '标准课程查询', 'description': '展示课程、日期、时间、地点和教师。'},
        {'id': 'course-compact', 'type_id': 'campus.course-query', 'name': '简洁课程查询', 'description': '保留日常查询字段，默认隐藏教师。'}]}


@router.get('/applications')
def listing(request: Request):
    return execute(lambda store: ApplicationStore(store).listing(), request.state.principal)


@router.post('/applications')
def create(command: CreateApplication, request: Request):
    return execute(lambda store: ApplicationStore(store).create(command, request.state.trace_id), request.state.principal)


@router.get('/applications/{application_id}/draft')
def draft(application_id: UUID, request: Request):
    return execute(lambda store: ApplicationStore(store).draft(str(application_id)), request.state.principal)


@router.put('/applications/{application_id}/draft')
def save(application_id: UUID, command: SaveApplication, request: Request):
    return execute(lambda store: ApplicationStore(store).save(str(application_id), command, request.state.trace_id), request.state.principal)


def scoped(request, application_id, operation):
    return execute(lambda store: operation(ApplicationStore(store).config_store(str(application_id))), request.state.principal)


@router.get('/applications/{application_id}/configuration')
def configuration(application_id: UUID, request: Request):
    return scoped(request, application_id, lambda store: store.draft())


@router.put('/applications/{application_id}/configuration/draft')
def save_configuration(application_id: UUID, command: SaveDraft, request: Request):
    return scoped(request, application_id, lambda store: store.save(command, request.state.trace_id))


@router.get('/applications/{application_id}/configuration/current')
def current_configuration(application_id: UUID, request: Request):
    def read(store):
        current = store.current()
        if not current['release_id'] and str(application_id) != '00000000-0000-4000-8000-000000000001':
            return {**current, 'content': None}
        return current
    return scoped(request, application_id, read)


@router.post('/applications/{application_id}/configuration/preview')
def preview_configuration(application_id: UUID, command: PreviewRequest, request: Request):
    from app.modules.config.review import review
    from app.modules.intent.router import resolve, campus_today
    def preview(store):
        current = store.current()
        return {'routing': resolve(command.text, campus_today(), command.content.rules).model_dump(mode='json'),
            'review': review(DisplayConfig.model_validate(current['content']), command.content)}
    return scoped(request, application_id, preview)


@router.post('/applications/{application_id}/configuration/publish')
def publish_configuration(application_id: UUID, command: PublishDraft, request: Request):
    return scoped(request, application_id, lambda store: store.publish(command, request.state.trace_id))


@router.post('/applications/{application_id}/configuration/rollback')
def rollback_configuration(application_id: UUID, command: Rollback, request: Request):
    return scoped(request, application_id, lambda store: store.publish(command, request.state.trace_id, rollback=True))


@router.get('/applications/{application_id}/configuration/releases')
def releases(application_id: UUID, request: Request):
    return scoped(request, application_id, lambda store: store.history())


@router.get('/applications/{application_id}/configuration/audit')
def audit(application_id: UUID, request: Request):
    return scoped(request, application_id, lambda store: store.audit_history())


@router.get('/applications/{application_id}/configuration/monitoring')
def monitoring(application_id: UUID, request: Request):
    from app.observability import snapshot
    return scoped(request, application_id, lambda store: {**snapshot(), 'outbox': store.delivery_status()})


@router.post('/applications/{application_id}/configuration/outbox/{event_id}/retry')
def retry(application_id: UUID, event_id: str, request: Request):
    return scoped(request, application_id, lambda store: store.retry_event(event_id, request.state.trace_id))


@router.get('/applications/{application_id}/questions')
def questions(application_id: UUID, request: Request):
    return scoped(request, application_id, lambda store: QuestionLibrary(store).listing())


@router.post('/applications/{application_id}/questions/generate')
async def generate_questions(application_id: UUID, command: GenerateQuestions, request: Request,
        principal=Depends(local_ai_operator)):
    from app.modules.config.question_routes import generate_questions as generate
    return await generate(command, request, principal, str(application_id))


@router.post('/applications/{application_id}/questions')
def add_question(application_id: UUID, command: QuestionInput, request: Request):
    return scoped(request, application_id, lambda store: QuestionLibrary(store).add([command], request.state.trace_id))


@router.put('/applications/{application_id}/questions/{question_id}')
def edit_question(application_id: UUID, question_id: str, command: EditQuestion, request: Request):
    return scoped(request, application_id, lambda store: QuestionLibrary(store).change(question_id, command, request.state.trace_id))


@router.post('/applications/{application_id}/questions/{question_id}/review')
def review_question(application_id: UUID, question_id: str, command: ReviewQuestion, request: Request):
    return scoped(request, application_id, lambda store: QuestionLibrary(store).change(question_id, command, request.state.trace_id))
