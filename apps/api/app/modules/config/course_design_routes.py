from fastapi import APIRouter, Depends, Request
from fastapi.routing import APIRoute

from app.modules.config.course_design import AcceptProposal, CourseProposal, context, evaluate, read_command
from app.modules.config.routes import admin, execute


class CourseDesignRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def bounded_handler(request: Request):
            if request.method == 'POST':
                # Authenticate before reading/validating arbitrary model output.
                admin(request)
                model = AcceptProposal if request.url.path.endswith('/accept') else CourseProposal
                await read_command(request, model)
            return await handler(request)
        return bounded_handler


router = APIRouter(prefix='/api/v1/admin/course-display/designer', tags=['课表配置提案'],
                   route_class=CourseDesignRoute, dependencies=[Depends(admin)])


@router.get('/context')
def proposal_context(request: Request):
    return execute(lambda store: context(store.draft()), request.state.principal)


@router.post('/preview')
def preview_proposal(command: CourseProposal, request: Request):
    return execute(lambda store: evaluate(command, store.draft()), request.state.principal)


@router.post('/accept')
def accept_proposal(command: AcceptProposal, request: Request):
    return execute(lambda store: store.accept_course_proposal(command, request.state.trace_id), request.state.principal)
