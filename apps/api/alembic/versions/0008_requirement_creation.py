"""Requirement editing, durable command results and atomic creation references."""
import sqlalchemy as sa
from alembic import op

revision = '0008_requirement_creation'
down_revision = '0007_assistant_requirement'


def upgrade():
    op.add_column('assistant_requirement', sa.Column('business_kind', sa.String(32), nullable=False, server_default='unknown'))
    op.add_column('assistant_requirement', sa.Column('target_application_id', sa.String(36)))
    op.create_table('assistant_command',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('owner_id', sa.String(64), nullable=False),
        sa.Column('command_key', sa.String(80), nullable=False),
        sa.Column('action', sa.String(24), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('payload', sa.JSON, nullable=False),
        sa.Column('result', sa.JSON),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('tenant_id', 'owner_id', 'command_key', name='uq_assistant_command'))
    op.create_index('ix_requirement_owner_created', 'assistant_requirement', ['tenant_id', 'owner_id', 'created_at', 'id'])


def downgrade():
    raise RuntimeError('Requirement tasks and command history require explicit recovery, not automatic downgrade.')
