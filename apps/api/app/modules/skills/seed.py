"""Explicit, additive demo data. No deletes, truncates, or updates to existing rows."""
from datetime import date, datetime, time, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.modules.skills.business import students, schedule

SOURCE_TAG = 'mock-schedule-v1'
DEFAULT_START = date(2026, 9, 1)
DEFAULT_END = date(2027, 1, 31)
STUDENTS = (
    ('local-demo-student', 'MOCK2026001', '模拟学生一'),
    ('mock-student-002', 'MOCK2026002', '模拟学生二'),
    ('mock-student-003', 'MOCK2026003', '模拟学生三'),
)
COURSES = ('程序设计基础（模拟）', '高等数学（模拟）', '大学英语（模拟）',
           '数据结构（模拟）', '计算机网络（模拟）')


def seed_mock_schedule(engine, tenant_id='local-demo', start=DEFAULT_START, end=DEFAULT_END):
    if not tenant_id.strip() or len(tenant_id) > 64:
        raise ValueError('租户 ID 必须为 1～64 字符')
    if not 0 <= (end - start).days <= 365:
        raise ValueError('生成范围必须为 1～366 天')
    # A concurrent initializer may win the unique insert. Retry once after rollback;
    # the winning rows are then observed and preserved, never overwritten.
    for attempt in range(2):
        try:
            return _seed(engine, tenant_id, start, end)
        except IntegrityError:
            if attempt:
                raise


def _seed(engine, tenant_id, start, end):
    now = datetime.now(timezone.utc)
    student_ids = [item[0] for item in STUDENTS]
    with engine.begin() as connection:
        existing = {row['id']: row for row in connection.execute(sa.select(students).where(
            students.c.tenant_id == tenant_id, students.c.id.in_(student_ids))).mappings()}
        for student_id, student_no, _ in STUDENTS:
            row = existing.get(student_id)
            if row and (not row['is_mock'] or row['source_tag'] != SOURCE_TAG or row['student_no'] != student_no):
                raise ValueError('模拟学生 ID 已被其他来源占用，生成已中止，未覆盖数据')
        new_students = [{'tenant_id': tenant_id, 'id': student_id, 'student_no': number,
                         'name': name, 'is_mock': True, 'source_tag': SOURCE_TAG, 'created_at': now}
                        for student_id, number, name in STUDENTS if student_id not in existing]
        if new_students:
            connection.execute(students.insert(), new_students)
        rows = connection.execute(sa.select(schedule.c.id, schedule.c.student_id, schedule.c.course_date,
            schedule.c.start_time).where(schedule.c.tenant_id == tenant_id,
            schedule.c.student_id.in_(student_ids))).all()
        # openGauss may decode SQL DATE as a midnight datetime; normalize before
        # comparing keys so rerunning the seed cannot try to insert the same slots.
        occupied = {(student_id, course_day.date() if isinstance(course_day, datetime) else course_day, begin)
                    for _, student_id, course_day, begin in rows}
        known_ids = {row[0] for row in rows}
        generated = []
        day = start
        skipped = 0
        while day <= end:
            if day.weekday() < 5:
                for index, (student_id, _, _) in enumerate(STUDENTS):
                    slots = [(time(9 + index, 0), time(10 + index, 30))]
                    if index != 1:
                        slots.append((time(14 + index, 0), time(15 + index, 30)))
                    for period, (begin, finish) in enumerate(slots):
                        identity = f'{SOURCE_TAG}:{tenant_id}:{student_id}:{day}:{begin.isoformat()}'
                        course_id = str(uuid5(NAMESPACE_URL, identity))
                        if (student_id, day, begin) in occupied or course_id in known_ids:
                            skipped += 1
                            continue
                        generated.append({'id': course_id, 'tenant_id': tenant_id,
                            'student_id': student_id, 'course_date': day,
                            'course_name': COURSES[(day.weekday() + index + period) % len(COURSES)],
                            'start_time': begin, 'end_time': finish,
                            'location': f'模拟教学楼 {chr(65 + index)}-{201 + period}',
                            'teacher': f'模拟教师{index + period + 1}', 'is_mock': True,
                            'source_tag': SOURCE_TAG, 'created_at': now})
            day += timedelta(days=1)
        if generated:
            connection.execute(schedule.insert(), generated)
        return {'tenant_id': tenant_id, 'start_date': start.isoformat(), 'end_date': end.isoformat(),
                'students_inserted': len(new_students), 'courses_inserted': len(generated),
                'occupied_slots_preserved': skipped, 'source_tag': SOURCE_TAG,
                'default_student_id': 'local-demo-student'}
