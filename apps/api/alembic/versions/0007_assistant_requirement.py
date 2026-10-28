"""Construction requests are owner-scoped; never executable applications."""
import sqlalchemy as sa
from alembic import op

revision = '0007_assistant_requirement'
down_revision = '0006_application_runtime'


def upgrade():
    op.create_table('assistant_requirement',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('owner_id', sa.String(64), nullable=False),
        sa.Column('title', sa.String(80), nullable=False),
        sa.Column('description', sa.String(1000), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('revision', sa.Integer, nullable=False),
        sa.Column('command_key', sa.String(80), nullable=False),
        sa.Column('command_hash', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('audit', sa.JSON, nullable=False),
        sa.UniqueConstraint('tenant_id', 'owner_id', 'command_key', name='uq_requirement_command'))


def downgrade():
    raise RuntimeError('Construction requests may contain user work; use an explicit recovery plan.')
