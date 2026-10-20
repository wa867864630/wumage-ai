"""Business tables only; no automatic seed and no changes to core system data."""
import sqlalchemy as sa
from alembic import op

revision = '0003_biz_schedule'
down_revision = '0002_mvp'


def upgrade():
    op.create_table('biz_student',
        sa.Column('tenant_id', sa.String(64), primary_key=True),
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('student_no', sa.String(40), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('is_mock', sa.Boolean, nullable=False),
        sa.Column('source_tag', sa.String(40), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'student_no', name='uq_biz_student_number'),
        comment='业务数据表：学生档案；is_mock 标识模拟数据，非系统核心表')
    op.create_table('biz_course_schedule',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('student_id', sa.String(64), nullable=False),
        sa.Column('course_date', sa.Date, nullable=False),
        sa.Column('course_name', sa.String(200), nullable=False),
        sa.Column('start_time', sa.Time, nullable=False),
        sa.Column('end_time', sa.Time, nullable=False),
        sa.Column('location', sa.String(200), nullable=False),
        sa.Column('teacher', sa.String(100), nullable=False),
        sa.Column('is_mock', sa.Boolean, nullable=False),
        sa.Column('source_tag', sa.String(40), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id', 'student_id'], ['biz_student.tenant_id', 'biz_student.id'],
                                name='fk_biz_schedule_student'),
        sa.UniqueConstraint('tenant_id', 'student_id', 'course_date', 'start_time', name='uq_biz_schedule_slot'),
        sa.CheckConstraint('end_time > start_time', name='ck_biz_schedule_times'),
        comment='业务数据表：学生课程安排；is_mock 标识模拟数据，非系统核心表')
    op.create_index('ix_biz_schedule_student_date', 'biz_course_schedule', ['tenant_id', 'student_id', 'course_date'])


def downgrade():
    raise RuntimeError('Business tables may contain user data; use an explicitly approved recovery plan.')
