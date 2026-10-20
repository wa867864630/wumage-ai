"""Pin application runtime contracts on new immutable releases."""
import sqlalchemy as sa
from alembic import op

revision = '0006_application_runtime'
down_revision = '0005_applications'


def upgrade():
    op.add_column('config_release', sa.Column('runtime_binding', sa.JSON))


def downgrade():
    raise RuntimeError('Runtime bindings are release history; use a reviewed recovery plan.')
