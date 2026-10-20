"""Local demo configuration store, immutable releases, and audit."""
import sqlalchemy as sa
from alembic import op

revision = '0001_config'
down_revision = None


def upgrade():
    op.create_table('config_state',
        sa.Column('tenant_id', sa.String(64), primary_key=True),
        sa.Column('key', sa.String(64), primary_key=True),
        sa.Column('revision', sa.Integer, nullable=False),
        sa.Column('draft', sa.JSON, nullable=False),
        sa.Column('current_release_id', sa.String(36)))
    op.create_table('config_release',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('key', sa.String(64), nullable=False),
        sa.Column('content', sa.JSON, nullable=False),
        sa.Column('content_hash', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('actor_id', sa.String(64), nullable=False),
        sa.Column('operation', sa.String(16), nullable=False),
        sa.Column('source_release_id', sa.String(36)),
        sa.Column('idempotency_key', sa.String(80), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.UniqueConstraint('tenant_id', 'key', 'idempotency_key', name='uq_release_request'))
    op.create_table('config_audit',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('actor_id', sa.String(64), nullable=False),
        sa.Column('action', sa.String(32), nullable=False),
        sa.Column('release_id', sa.String(36)),
        sa.Column('revision', sa.Integer, nullable=False),
        sa.Column('trace_id', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    # Fixed initial content: migration semantics must not change with future application defaults.
    table = sa.table('config_state', sa.column('tenant_id', sa.String), sa.column('key', sa.String),
                     sa.column('revision', sa.Integer), sa.column('draft', sa.JSON))
    op.bulk_insert(table, [{'tenant_id': 'local-demo', 'key': 'course-display', 'revision': 0,
        'draft': {'schema_version': '1', 'columns': [
            {'field': field, 'label': label, 'visible': True}
            for field, label in [('name', '课程'), ('date', '日期'), ('start_time', '开始时间'),
                                 ('end_time', '结束时间'), ('location', '地点'), ('teacher', '教师')]]
        } }])


def downgrade():
    raise RuntimeError('This migration contains configuration history; restore a backup instead of dropping tables.')
