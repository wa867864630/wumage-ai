"""Optional disposable Redis cache. No local second-level cache or published pointer cache."""
import hashlib
import json
import time
from functools import lru_cache
from threading import Lock

from app.settings import read_local_config

_lock = Lock()
_retry_at = 0.0


def cache_enabled():
    return bool(read_local_config().get('redis', {}).get('url'))


@lru_cache
def client():
    import redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry
    return redis.Redis.from_url(read_local_config()['redis']['url'], decode_responses=True,
        socket_connect_timeout=0.15, socket_timeout=0.15, retry_on_timeout=False,
        retry=Retry(NoBackoff(), 0), max_connections=10)


def key(tenant, release, application_id='00000000-0000-4000-8000-000000000001'):
    # Tenant IDs may include colons; encode them without ambiguous cache namespaces.
    scope = hashlib.sha256(tenant.encode()).hexdigest()
    application_scope = hashlib.sha256(application_id.encode()).hexdigest()
    return f'config:v2:{scope}:{application_scope}:course-query-v1:{release}'


def command(operation):
    global _retry_at
    if not cache_enabled():
        return None
    with _lock:
        if time.monotonic() < _retry_at:
            return None
    try:
        return operation(client())
    except Exception:
        from app.observability import increment
        increment('cache_errors')
        with _lock:
            _retry_at = time.monotonic() + 10
        return None


def read_cache(tenant, release, expected_hash, application_id='00000000-0000-4000-8000-000000000001'):
    raw = command(lambda redis: redis.get(key(tenant, release, application_id)))
    if raw is None:
        from app.observability import increment
        increment('cache_misses')
        return None
    try:
        value = json.loads(raw)
        canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        if hashlib.sha256(canonical.encode()).hexdigest() != expected_hash:
            return None
        from app.modules.config.schema import DisplayConfig
        DisplayConfig.model_validate(value)
        from app.observability import increment
        increment('cache_hits')
        return value
    except (ValueError, TypeError):
        return None


def write_cache(tenant, release, content, application_id='00000000-0000-4000-8000-000000000001'):
    return command(lambda redis: redis.set(key(tenant, release, application_id), json.dumps(content), ex=86400))
