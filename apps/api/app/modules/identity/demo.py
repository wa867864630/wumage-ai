"""Development-only, bounded, process-local student sessions; never campus SSO."""
import secrets
import time
from threading import Lock

import sqlalchemy as sa
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.errors import APIError
from app.modules.identity.auth import Principal, auth_options

router = APIRouter(prefix='/api/v1/auth/demo', tags=['demo identity'])
_sessions = {}
_lock = Lock()
TTL = 3600


def require_demo():
    if auth_options().get('mode', 'demo') != 'demo':
        raise APIError(404, 'DEMO_DISABLED', '正式认证模式不提供模拟登录。')


def available_students():
    require_demo()
    from app.db import get_engine
    from app.modules.skills.business import students
    from app.modules.skills.seed import SOURCE_TAG, STUDENTS
    try:
        with get_engine().connect() as connection:
            return [dict(row) for row in connection.execute(sa.select(
                students.c.id, students.c.student_no, students.c.name).where(
                students.c.tenant_id == 'local-demo', students.c.is_mock.is_(True),
                students.c.source_tag == SOURCE_TAG,
                students.c.id.in_([item[0] for item in STUDENTS])
            ).order_by(students.c.student_no)).mappings()]
    except Exception as exc:
        raise APIError(503, 'DEMO_DATA_UNAVAILABLE', '模拟学生不可用，请检查数据库并生成模拟数据。') from exc


def resolve_session(token):
    with _lock:
        entry = _sessions.get(token)
        if entry and entry[0] > time.monotonic():
            return entry[1].model_copy(deep=True)
        _sessions.pop(token, None)
    raise APIError(401, 'INVALID_TOKEN', '模拟会话已过期或退出，请重新选择学生。')


class Login(BaseModel):
    model_config = ConfigDict(extra='forbid')
    student_id: str = Field(min_length=1, max_length=64)


@router.get('/students')
def list_students():
    return {'students': available_students()}


@router.post('/login')
def login(body: Login):
    require_demo()
    student = next((row for row in available_students() if row['id'] == body.student_id), None)
    if not student:
        raise APIError(403, 'DEMO_STUDENT_FORBIDDEN', '只能选择本地演示租户中已初始化的模拟学生。')
    principal = Principal(subject=student['id'], tenant_id='local-demo',
                          roles=['student'], student_id=student['id'])
    now = time.monotonic()
    with _lock:
        for key in list(_sessions):
            if _sessions[key][0] <= now:
                del _sessions[key]
        if len(_sessions) >= 1000:
            raise APIError(429, 'DEMO_SESSION_LIMIT', '演示会话数量已达上限，请稍后重试。')
        token = 'demo_' + secrets.token_urlsafe(32)
        _sessions[token] = (now + TTL, principal)
    return {'access_token': token, 'expires_in': TTL, 'student': student}


@router.post('/logout')
def logout(request: Request):
    require_demo()
    token = request.headers.get('authorization', '').removeprefix('Bearer ')
    with _lock:
        _sessions.pop(token, None)
    return {'logged_out': True}
