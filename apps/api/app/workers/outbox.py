"""Run: python -m app.workers.outbox --once (or continuously without --once)."""
import argparse
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import sqlalchemy as sa

from app.db import get_engine
from app.modules.config.cache import cache_enabled, write_cache
from app.modules.config.store import outbox, releases


def process_one(engine):
    now = datetime.now(timezone.utc)
    owner = str(uuid4())
    with engine.begin() as connection:
        row = connection.execute(sa.select(outbox).where(sa.or_(
            sa.and_(outbox.c.state == 'pending', outbox.c.available_at <= now),
            sa.and_(outbox.c.state == 'working', outbox.c.lease_until < now)))
            .order_by(outbox.c.available_at, outbox.c.id).limit(1).with_for_update()).mappings().first()
        if row is None:
            return False
        # Claim survives process death; only the lease owner may acknowledge delivery.
        connection.execute(outbox.update().where(outbox.c.id == row['id']).values(
            state='working', attempts=row['attempts'] + 1, lease_owner=owner,
            lease_until=now + timedelta(seconds=30)))
        event = dict(row)
    error = None
    try:
        if cache_enabled():
            with engine.connect() as connection:
                release = connection.execute(sa.select(releases.c.content, releases.c.runtime_binding).where(
                    releases.c.id == event['release_id'], releases.c.tenant_id == event['tenant_id'], releases.c.application_id == event['application_id'])).mappings().one()
            # Non-course adapters currently read immutable releases directly from the DB.
            uses_course_cache = not release['runtime_binding'] or release['runtime_binding'].get('type_id') == 'campus.course-query'
            if uses_course_cache and not write_cache(event['tenant_id'], event['release_id'], release['content'], event['application_id']):
                raise RuntimeError('cache_unavailable')
        # With caching disabled, the committed DB pointer alone completes delivery.
    except Exception as exc:
        error = type(exc).__name__
    with engine.begin() as connection:
        where = sa.and_(outbox.c.id == event['id'], outbox.c.lease_owner == owner,
                        outbox.c.state == 'working')
        if error:
            attempts = event['attempts'] + 1
            connection.execute(outbox.update().where(where).values(
                state='failed' if attempts >= 8 else 'pending', last_error=error,
                available_at=now + timedelta(seconds=min(300, 2 ** attempts)),
                lease_until=None, lease_owner=None))
        else:
            connection.execute(outbox.update().where(where).values(
                state='done', processed_at=datetime.now(timezone.utc), last_error=None,
                lease_until=None, lease_owner=None))
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    options = parser.parse_args()
    engine = get_engine()
    try:
        while True:
            try:
                processed = process_one(engine)
            except Exception as exc:
                print('outbox unavailable:', type(exc).__name__, flush=True)
                if options.once:
                    raise SystemExit(1) from None
                time.sleep(2)
                continue
            if options.once:
                break
            if not processed:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
