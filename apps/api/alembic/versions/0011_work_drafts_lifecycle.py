"""Add owner-scoped unfinished work and independent application lifecycle."""
import sqlalchemy as sa
from alembic import op

revision = '0011_work_drafts_lifecycle'
down_revision = '0010_approval_instances'


def upgrade():
    op.create_table('application_lifecycle',
        sa.Column('tenant_id', sa.String(64), primary_key=True),
        sa.Column('application_id', sa.String(36), primary_key=True),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('revision', sa.Integer, nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('business_draft',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('application_id', sa.String(36), nullable=False),
        sa.Column('owner_id', sa.String(64), nullable=False),
        sa.Column('release_id', sa.String(36), nullable=False),
        sa.Column('values_json', sa.JSON, nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('revision', sa.Integer, nullable=False),
        sa.Column('record_id', sa.String(36)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_business_draft_owner', 'business_draft', ['tenant_id', 'owner_id', 'status', 'updated_at'])


def downgrade():
    raise RuntimeError('Unfinished work requires an explicit recovery plan; do not drop task history.')
