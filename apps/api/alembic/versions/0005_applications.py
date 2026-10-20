"""Application identity and scope; preserve all existing configuration snapshots."""
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = '0005_applications'
down_revision = '0004_question_library'
LEGACY = '00000000-0000-4000-8000-000000000001'


def upgrade():
    op.create_table('application',
        sa.Column('tenant_id', sa.String(64), primary_key=True),
        sa.Column('application_id', sa.String(36), primary_key=True),
        sa.Column('type_id', sa.String(96), nullable=False),
        sa.Column('type_version', sa.String(32), nullable=False),
        sa.Column('type_digest', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sa.String(64), nullable=False),
        sa.Column('creation_key', sa.String(80), nullable=False),
        sa.Column('creation_hash', sa.String(64), nullable=False),
        sa.UniqueConstraint('tenant_id', 'creation_key', name='uq_application_creation'))
    connection = op.get_bind()
    tenants = connection.execute(sa.text('SELECT DISTINCT tenant_id FROM config_state')).scalars().all()
    unexpected = connection.scalar(sa.text("SELECT count(*) FROM config_state WHERE key <> 'course-display'"))
    if unexpected:
        raise RuntimeError('Unknown configuration streams require an explicit application mapping')
    table = sa.table('application', sa.column('tenant_id'), sa.column('application_id'), sa.column('type_id'),
        sa.column('type_version'), sa.column('type_digest'), sa.column('created_at', sa.DateTime(timezone=True)),
        sa.column('created_by'), sa.column('creation_key'), sa.column('creation_hash'))
    for tenant in tenants:
        # Nonempty marker also works in openGauss Oracle-compatible string mode.
        connection.execute(table.insert().values(tenant_id=tenant, application_id=LEGACY,
            type_id='campus.course-query', type_version='1.0.0', type_digest='legacy',
            created_at=datetime.now(timezone.utc), created_by='migration', creation_key='legacy-schedule-migration',
            creation_hash='legacy'))
    for name in ('config_state', 'config_release', 'config_audit', 'outbox_event', 'question_candidate'):
        op.add_column(name, sa.Column('application_id', sa.String(36), nullable=False, server_default=LEGACY))
        op.create_index('ix_' + name + '_application', name, ['tenant_id', 'application_id'])
    for name, columns in (('config_state', ['tenant_id', 'application_id', 'key']),
                          ('question_candidate', ['tenant_id', 'application_id', 'id'])):
        primary = sa.inspect(connection).get_pk_constraint(name)['name']
        op.drop_constraint(primary, name, type_='primary')
        op.create_primary_key(name + '_pkey', name, columns)
    op.drop_constraint('uq_release_request', 'config_release', type_='unique')
    op.create_unique_constraint('uq_release_request', 'config_release',
        ['tenant_id', 'application_id', 'key', 'idempotency_key'])
    op.drop_constraint('uq_question_text', 'question_candidate', type_='unique')
    op.create_unique_constraint('uq_question_text', 'question_candidate', ['tenant_id', 'application_id', 'text'])


def downgrade():
    raise RuntimeError('Applications and audit history require an explicit recovery plan, not destructive downgrade.')
