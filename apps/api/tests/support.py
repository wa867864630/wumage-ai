"""Issue real process-local demo credentials without depending on seeded development data."""
from unittest.mock import patch
from app.modules.identity import demo


def student_headers(test, student_id='local-demo-student'):
    with patch.object(demo, 'auth_options', return_value={'mode': 'demo'}), patch.object(
        demo, 'available_students', return_value=[{'id': student_id, 'student_no': 'MOCK-TEST', 'name': '测试学生'}]):
        token = demo.login(demo.Login(student_id=student_id))['access_token']
    def cleanup():
        with demo._lock:
            demo._sessions.pop(token, None)
    test.addCleanup(cleanup)
    return {'Authorization': 'Bearer ' + token}
