"""Read-only business acceptance smoke. Creates/revokes demo sessions, never publishes config."""
import argparse
from datetime import date
import json
from urllib.parse import urlparse

import httpx


class SmokeFailure(RuntimeError):
    """Contains only a fixed check label, never an upstream error body."""


def verify(client, day='2026-10-19'):
    """Accept httpx.Client or TestClient. Never return tokens or personal schedule contents."""
    date.fromisoformat(day)
    checks = []
    tokens = []
    def expect(value, label):
        if not value:
            raise SmokeFailure(label)
        checks.append(label)
    path = f'/api/v1/course-schedule?start_date={day}&end_date={day}'
    try:
        health = client.get('/api/health')
        expect(health.status_code == 200 and health.json().get('authentication') == 'demo', 'demo_environment')
        expect(client.get(path).status_code == 401, 'anonymous_schedule_rejected')
        expect(client.get('/api/v1/me').status_code == 401, 'anonymous_identity_rejected')
        expect(client.get('/api/v1/config/course-display').status_code == 401, 'anonymous_configuration_rejected')
        listed = client.get('/api/v1/auth/demo/students')
        expect(listed.status_code == 200, 'demo_students_available')
        ids = [row['id'] for row in listed.json()['students']]
        expect('local-demo-student' in ids and 'mock-student-002' in ids, 'seeded_students_present')
        course_ids = []
        for student_id in ('local-demo-student', 'mock-student-002'):
            login = client.post('/api/v1/auth/demo/login', json={'student_id': student_id})
            expect(login.status_code == 200, f'login_{student_id}')
            token = login.json()['access_token']; tokens.append(token)
            headers = {'Authorization': 'Bearer ' + token}
            me = client.get('/api/v1/me', headers=headers)
            expect(me.status_code == 200 and me.json()['student_id'] == student_id and me.json()['roles'] == ['student'], f'identity_{student_id}')
            result = client.get(path, headers=headers)
            expect(result.status_code == 200 and result.json()['data']['source'] == 'database_mock', f'database_schedule_{student_id}')
            course_ids.append({row['id'] for row in result.json()['data']['courses']})
            natural = client.post('/api/v1/course-schedule/query', headers=headers, json={'text': day + '课表'})
            expect(natural.status_code == 200 and natural.json()['data']['courses'] == result.json()['data']['courses'], f'natural_matches_date_{student_id}')
            expect(client.get(path + '&student_id=another-student', headers=headers).status_code == 422, f'identity_override_rejected_{student_id}')
            unsupported = client.post('/api/v1/course-schedule/query', headers=headers, json={'text': '帮我提交请假申请'})
            expect(unsupported.status_code == 200 and unsupported.json()['data'] is None, f'unsupported_not_executed_{student_id}')
        expect(all(course_ids) and not course_ids[0].intersection(course_ids[1]), 'student_schedule_isolation')
        for token in tokens:
            headers = {'Authorization': 'Bearer ' + token}
            expect(client.post('/api/v1/auth/demo/logout', headers=headers).status_code == 200, 'logout_success')
            expect(client.get(path, headers=headers).status_code == 401, 'revoked_session_rejected')
        return {'status': 'passed', 'checks': checks, 'count': len(checks), 'kind': 'development-demo-smoke'}
    finally:
        for token in tokens:
            try:
                client.post('/api/v1/auth/demo/logout', headers={'Authorization': 'Bearer ' + token})
            except Exception:
                pass  # Expiry remains bounded if the test target disconnects during cleanup.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--date', default='2026-10-19', type=date.fromisoformat)
    options = parser.parse_args()
    parsed = urlparse(options.url)
    if (parsed.scheme not in ('http', 'https') or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
        parser.error('Only a loopback API origin is allowed; this is not a production test tool.')
    try:
        with httpx.Client(base_url=options.url, timeout=12, trust_env=False, follow_redirects=False) as client:
            report = verify(client, options.date.isoformat() if isinstance(options.date, date) else options.date)
    except Exception as exc:
        # Do not echo server error bodies, connection URLs, tokens, or arbitrary exception text.
        print(json.dumps({'status': 'failed', 'error_type': type(exc).__name__,
                          'failed_check': str(exc) if isinstance(exc, SmokeFailure) else None,
                          'hint': 'Check API restart, sis.mode=database, seeded mock data and the requested weekday.'}))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
