from alembic import context
from app.migrations import OpenGaussImpl  # noqa: F401

from app.db import get_engine
from app.modules.config.store import metadata
from app.modules.skills.business import metadata as business_metadata
from app.modules.config.question_library import metadata as question_metadata
from app.modules.config.application_store import applications  # noqa: F401
from app.modules.intent.requirements import metadata as requirement_metadata
from app.modules.forms.runtime import metadata as form_metadata
from app.modules.approvals.runtime import metadata as approval_metadata
from app.modules.forms.drafts import metadata as drafts_metadata
from app.modules.config.lifecycle import metadata as lifecycle_metadata


with get_engine().connect() as connection:
    context.configure(connection=connection, target_metadata=[metadata, business_metadata, question_metadata, requirement_metadata, form_metadata, approval_metadata, drafts_metadata, lifecycle_metadata])
    with context.begin_transaction():
        context.run_migrations()
