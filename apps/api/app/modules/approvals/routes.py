from uuid import UUID
from typing import Literal
from fastapi import APIRouter, Query, Request
from app.modules.config.routes import execute
from app.modules.identity.auth import current_user
from app.modules.approvals.runtime import ApprovalRecords, Transition

router = APIRouter(prefix='/api/v1/applications', tags=['单级请假审批'])


@router.get('/{application_id}/approvals')
def listing(application_id: UUID, request: Request, scope: Literal['mine', 'assigned', 'all'] = 'mine',
            status: Literal['all', 'pending', 'approved', 'rejected', 'withdrawn'] = 'all', page: int = Query(default=1, ge=1)):
    principal = current_user(request)
    return execute(lambda store: ApprovalRecords(store, principal, str(application_id)).work_items(scope, page, status), principal)


@router.get('/{application_id}/approvals/{record_id}')
def detail(application_id: UUID, record_id: UUID, request: Request):
    principal = current_user(request)
    return execute(lambda store: ApprovalRecords(store, principal, str(application_id)).workflow_detail(str(record_id)), principal)


@router.post('/{application_id}/approvals/{record_id}/transition')
def transition(application_id: UUID, record_id: UUID, command: Transition, request: Request):
    principal = current_user(request)
    return execute(lambda store: ApprovalRecords(store, principal, str(application_id)).transition(str(record_id), command, request.state.trace_id), principal)
