from alembic import context
from app.migrations import OpenGaussImpl  # noqa: F401

from app.db import get_engine
from app.modules.config.store import metadata
from app.modules.skills.business import metadata as business_metadata
from app.modules.config.question_library import metadata as question_metadata
from app.modules.config.application_store import applications  # noqa: F401


with get_engine().connect() as connection:
    context.configure(connection=connection, target_metadata=[metadata, business_metadata, question_metadata])
    with context.begin_transaction():
        context.run_migrations()
