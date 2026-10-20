from alembic.ddl.postgresql import PostgresqlImpl


class OpenGaussImpl(PostgresqlImpl):
    """Simple table/index migrations; extended PostgreSQL DDL needs vendor testing."""
    __dialect__ = 'opengauss'
