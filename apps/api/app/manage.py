"""Explicit operations; never initialize a tenant implicitly in a request."""
import argparse
import json
from datetime import date
import sqlalchemy as sa
from app.db import LATEST_REVISION, get_engine
from app.modules.config.schema import default_config
from app.modules.config.store import state


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    seed = sub.add_parser('seed-tenant')
    seed.add_argument('tenant_id')
    sub.add_parser('check')
    mock = sub.add_parser('seed-mock-schedule', help='Add business mock students and courses without overwriting existing data')
    mock.add_argument('--tenant-id', default='local-demo')
    mock.add_argument('--start-date', type=date.fromisoformat, default=date(2026, 9, 1))
    mock.add_argument('--end-date', type=date.fromisoformat, default=date(2027, 1, 31))
    options = parser.parse_args(argv)
    engine = get_engine()
    try:
        if options.command == 'check':
            with engine.connect() as connection:
                revision = connection.execute(sa.text('SELECT version_num FROM alembic_version')).scalar_one()
            print(json.dumps({'database': 'ok', 'migration': revision, 'ready': revision == LATEST_REVISION}))
        elif options.command == 'seed-mock-schedule':
            from app.modules.skills.seed import seed_mock_schedule
            print(json.dumps(seed_mock_schedule(engine, options.tenant_id, options.start_date, options.end_date), ensure_ascii=True))
        else:
            if not options.tenant_id.strip() or len(options.tenant_id) > 64:
                raise ValueError('Tenant ID must be 1-64 characters')
            with engine.begin() as connection:
                found = connection.execute(sa.select(state.c.tenant_id).where(
                    state.c.tenant_id == options.tenant_id, state.c.key == 'course-display')).first()
                if not found:
                    connection.execute(state.insert().values(tenant_id=options.tenant_id,
                        key='course-display', revision=0, draft=default_config().model_dump()))
            print('Tenant configuration exists; no previous content overwritten.')
    except Exception as exc:
        print('Operation failed:', type(exc).__name__, '(check connectivity, dependencies and migration)')
        raise SystemExit(1) from None
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
