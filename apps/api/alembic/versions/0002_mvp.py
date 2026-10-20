"""Additive audit detail and durable cache delivery; existing releases unchanged."""
import sqlalchemy as sa
from alembic import op

revision = '0002_mvp'
down_revision = '0001_config'


def upgrade():
    op.add_column('config_audit', sa.Column('before_json', sa.JSON))
    op.add_column('config_audit', sa.Column('after_json', sa.JSON))
    op.create_table('outbox_event',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('release_id', sa.String(36), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('attempts', sa.Integer, nullable=False),
        sa.Column('available_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('lease_owner', sa.String(36)),
        sa.Column('last_error', sa.String(80)),
        sa.Column('processed_at', sa.DateTime(timezone=True)))
    op.create_index('ix_outbox_ready', 'outbox_event', ['state', 'available_at'])


def downgrade():
    raise RuntimeError('Preserve audit and delivery history; restore an approved backup instead.')
