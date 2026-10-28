"""Single-level approval work references immutable application records and releases."""
import sqlalchemy as sa
from alembic import op

revision = '0010_approval_instances'
down_revision = '0009_form_records'


def upgrade():
    op.create_table('approval_instance',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(64), nullable=False),
        sa.Column('application_id', sa.String(36), nullable=False),
        sa.Column('release_id', sa.String(36), nullable=False),
        sa.Column('applicant_id', sa.String(64), nullable=False),
        sa.Column('approver_id', sa.String(64), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('revision', sa.Integer, nullable=False),
        sa.Column('last_reason', sa.String(1000)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_approval_assigned', 'approval_instance', ['tenant_id', 'application_id', 'approver_id', 'status', 'updated_at'])
    op.create_index('ix_approval_applicant', 'approval_instance', ['tenant_id', 'applicant_id', 'updated_at'])


def downgrade():
    raise RuntimeError('In-flight approvals require an explicit recovery plan; do not drop task history.')
