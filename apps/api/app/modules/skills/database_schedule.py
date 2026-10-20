"""Database-backed simulated business data, scoped by trusted tenant and student."""
import sqlalchemy as sa
from starlette.concurrency import run_in_threadpool

from app.db import get_engine
from app.errors import APIError
from app.modules.skills.business import students, schedule
from app.modules.skills.schedule import Course


class DatabaseScheduleProvider:
    source = 'database_mock'

    def __init__(self, engine=None):
        self.engine = engine

    async def get_schedule(self, student_id, start, end, tenant_id='local-demo'):
        try:
            return await run_in_threadpool(self._read, tenant_id, student_id, start, end)
        except APIError:
            raise
        except Exception as exc:
            raise APIError(503, 'SCHEDULE_DATABASE_UNAVAILABLE', '业务课表数据库暂不可用，请检查迁移与连接。') from exc

    def _read(self, tenant_id, student_id, start, end):
        engine = self.engine if self.engine is not None else get_engine()
        with engine.connect() as connection:
            found = connection.execute(sa.select(students.c.id).where(
                students.c.tenant_id == tenant_id, students.c.id == student_id,
                students.c.is_mock.is_(True))).first()
            if found is None:
                raise APIError(409, 'IDENTITY_NOT_LINKED', '当前身份尚未关联业务库中的模拟学生。')
            rows = connection.execute(sa.select(schedule).where(
                schedule.c.tenant_id == tenant_id, schedule.c.student_id == student_id,
                schedule.c.is_mock.is_(True), schedule.c.course_date.between(start, end))
                .order_by(schedule.c.course_date, schedule.c.start_time, schedule.c.id).limit(501)).mappings().all()
            if len(rows) > 500:
                raise APIError(422, 'SCHEDULE_RESULT_TOO_LARGE', '课程数量过多，请缩小查询日期范围。')
            return [Course(id=row['id'], date=row['course_date'], name=row['course_name'],
                start_time=row['start_time'].strftime('%H:%M'), end_time=row['end_time'].strftime('%H:%M'),
                location=row['location'], teacher=row['teacher']) for row in rows]
