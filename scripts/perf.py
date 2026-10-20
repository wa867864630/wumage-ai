"""Bounded read-only smoke benchmark, not production capacity certification."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import getpass
import os
import time
import urllib.request
from urllib.error import HTTPError
from urllib.parse import urlparse

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:8000/api/v1/course-schedule?start_date=2026-10-16&end_date=2026-10-16')
parser.add_argument('--requests', type=int, default=30)
parser.add_argument('--concurrency', type=int, default=3)
parser.add_argument('--warmup', type=int, default=0)
parser.add_argument('--prompt-token', action='store_true', help='Read access token without echo; alternatively set CAMPUS_ACCESS_TOKEN')
options = parser.parse_args()
if not 1 <= options.requests <= 100 or not 1 <= options.concurrency <= 10 or not 0 <= options.warmup <= 10:
    parser.error('Use 1-100 requests and 1-10 workers; larger load needs a test plan.')
parsed = urlparse(options.url)
if (parsed.scheme not in ('http', 'https') or parsed.hostname not in ('localhost', '127.0.0.1', '::1')
        or parsed.username or parsed.password or parsed.fragment):
    parser.error('This development benchmark only permits loopback URLs without credentials.')
token = getpass.getpass('Access token (not echoed): ') if options.prompt_token else os.environ.get('CAMPUS_ACCESS_TOKEN', '')
if not token or len(token) > 16384 or any(character.isspace() for character in token):
    parser.error('Provide a valid access token with --prompt-token or CAMPUS_ACCESS_TOKEN; tokens are never printed.')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward bearer credentials across redirects.


def measure(_):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    started = time.perf_counter()
    try:
        request = urllib.request.Request(options.url, headers={'Authorization': 'Bearer ' + token})
        with opener.open(request, timeout=10) as response:
            response.read()
            return (time.perf_counter() - started) * 1000, response.status
    except HTTPError as error:
        error.close()
        return (time.perf_counter() - started) * 1000, error.code
    except Exception:
        return (time.perf_counter() - started) * 1000, 0


if options.warmup:
    with ThreadPoolExecutor(max_workers=options.concurrency) as pool:
        list(pool.map(measure, range(options.warmup)))
started = time.perf_counter()
with ThreadPoolExecutor(max_workers=options.concurrency) as pool:
    measurements = list(pool.map(measure, range(options.requests)))
duration = time.perf_counter() - started
values = sorted(value for value, _ in measurements)
result = {'requests': len(values), 'concurrency': options.concurrency,
          'warmup_requests': options.warmup,
          'errors': sum(status != 200 for _, status in measurements),
          'http_status_counts': {str(status): sum(item_status == status for _, item_status in measurements)
                                 for status in sorted({item_status for _, item_status in measurements})},
          'p50_ms': round(values[math.ceil(len(values) * .5) - 1], 1),
          'p95_ms': round(values[math.ceil(len(values) * .95) - 1], 1),
          'max_ms': round(values[-1], 1), 'achieved_rps': round(len(values) / duration, 1),
          'kind': 'development-smoke'}
print(json.dumps(result, indent=2))
raise SystemExit(bool(result['errors']))
