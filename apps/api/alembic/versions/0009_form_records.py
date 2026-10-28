"""Immutable user form records pinned to application releases."""
import sqlalchemy as sa
from alembic import op

revision = '0009_form_records'
down_revision = '0008_requirement_creation'


def upgrade():
    op.create_table('form_record',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('application_id', sa.String(36), nullable=False),
        sa.Column('release_id', sa.String(36), nullable=False),
        sa.Column('owner_id', sa.String(64), nullable=False),
        sa.Column('values_json', sa.JSON, nullable=False),
        sa.Column('command_key', sa.String(80), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'application_id', 'owner_id', 'command_key', name='uq_form_submission'))
    op.create_index('ix_form_record_owner', 'form_record', ['tenant_id', 'application_id', 'owner_id', 'created_at', 'id'])


def downgrade():
    raise RuntimeError('Form records are business data; use an explicitly verified recovery plan.')
