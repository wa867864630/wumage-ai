"""Single-process development limit. Scale-out requires a shared limiter."""
import time
from collections import OrderedDict
from threading import Lock

from app.errors import APIError

_lock = Lock()
_windows = OrderedDict()


def check(principal):
    key = (principal.tenant_id, principal.subject)
    now = time.monotonic()
    with _lock:
        start, count = _windows.get(key, (now, 0))
        if now - start >= 60:
            start, count = now, 0
        _windows[key] = (start, count + 1)
        _windows.move_to_end(key)
        if len(_windows) > 10000:
            _windows.popitem(last=False)
        if count >= 120:
            raise APIError(429, 'RATE_LIMITED', '查询过于频繁，请稍后重试。')
