"""Review queue for course question aliases; no published rule changes."""
import sqlalchemy as sa
from alembic import op

revision = '0004_question_library'
down_revision = '0003_biz_schedule'


def upgrade():
    op.create_table('question_candidate',
        sa.Column('tenant_id', sa.String(64), primary_key=True),
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('text', sa.String(80), nullable=False),
        sa.Column('date_mode', sa.String(16), nullable=False),
        sa.Column('source', sa.String(20), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('revision', sa.Integer, nullable=False),
        sa.Column('occurrences', sa.Integer, nullable=False),
        sa.Column('interpretations', sa.JSON, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('reviewer', sa.String(64)),
        sa.Column('reviewed_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('tenant_id', 'text', name='uq_question_text'))


def downgrade():
    raise RuntimeError('Question reviews are audit data; use an explicitly approved recovery plan.')
